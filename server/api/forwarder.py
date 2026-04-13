"""
Loki-PrintServer — Device Forwarding Manager
Handles multiple forwarding strategies depending on device type:

  1. USB/IP  — Full USB passthrough (Linux/Windows clients)
  2. Serial  — TCP-to-serial bridge via ser2net/socat (plotters, serial devices)
  3. IPP     — CUPS printer sharing (USB printers → appear as network printers)

Each shared device gets forwarding enabled for ALL applicable methods,
so every client platform can connect using its best available method.
"""
import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger("loki-printserver.forwarder")

# TCP port range for serial forwarding (one port per device)
SERIAL_PORT_BASE = 7580

# Persistent state file — survives container restarts (stored in named volume)
STATE_FILE = Path(os.getenv("LOKI_DATA_DIR", "/etc/loki-printserver")) / "shared_devices.json"


@dataclass
class ForwardState:
    bus_id: str
    usbip_shared: bool = False
    serial_port: Optional[int] = None
    serial_proc: Optional[asyncio.subprocess.Process] = None
    serial_dev: Optional[str] = None  # e.g. /dev/ttyUSB0
    ipp_shared: bool = False
    cups_name: Optional[str] = None
    # Metadata saved for auto-restore
    device_class: str = ""
    vendor_id: str = ""
    product_id: str = ""
    product_name: str = ""
    auto_share: bool = False  # user explicitly set this device to auto-share


@dataclass
class _SavedDevice:
    """Lightweight snapshot persisted to disk (no live processes)."""
    bus_id: str
    device_class: str = ""
    vendor_id: str = ""
    product_id: str = ""
    product_name: str = ""
    auto_share: bool = False


class ForwardingManager:
    """Manages all forwarding methods for shared USB devices."""

    def __init__(self):
        self._states: dict[str, ForwardState] = {}
        self._next_serial_port = SERIAL_PORT_BASE

    # ── Persistence ────────────────────────────────────────────────────────────

    def save_state(self):
        """Persist the list of shared devices to disk."""
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            data = [
                asdict(_SavedDevice(
                    bus_id=s.bus_id,
                    device_class=s.device_class,
                    vendor_id=s.vendor_id,
                    product_id=s.product_id,
                    product_name=s.product_name,
                    auto_share=s.auto_share,
                ))
                for s in self._states.values()
            ]
            STATE_FILE.write_text(json.dumps(data, indent=2))
        except Exception as e:
            logger.error(f"Could not save shared-device state: {e}")

    def load_saved_devices(self) -> list[_SavedDevice]:
        """Load previously shared devices from disk."""
        try:
            if STATE_FILE.exists():
                raw = json.loads(STATE_FILE.read_text())
                return [_SavedDevice(**d) for d in raw]
        except Exception as e:
            logger.warning(f"Could not load saved shared-device state: {e}")
        return []

    def mark_auto_share(self, bus_id: str, enabled: bool):
        """Toggle the auto-share flag for a device and persist."""
        state = self._states.get(bus_id)
        if state:
            state.auto_share = enabled
            self.save_state()

    async def share_device(self, bus_id: str, device_class: str,
                           serial_dev: Optional[str] = None,
                           vendor_id: str = "", product_id: str = "",
                           product_name: str = "",
                           auto_share: bool = False):
        """Enable all applicable forwarding for a device."""
        state = self._states.setdefault(bus_id, ForwardState(bus_id=bus_id))
        state.device_class = device_class
        state.vendor_id = vendor_id
        state.product_id = product_id
        state.product_name = product_name
        if auto_share:
            state.auto_share = True

        # 1) USB/IP — always try (works for Linux/Windows clients)
        await self._share_usbip(state, bus_id)

        # 2) Serial forwarding — for serial adapters & plotters
        if serial_dev or self._find_serial_device(bus_id):
            dev = serial_dev or self._find_serial_device(bus_id)
            await self._start_serial_forward(state, dev)

        # 3) IPP/CUPS — for USB printer class devices
        if "Printer" in device_class or self._is_printer_class(vendor_id, product_id):
            await self._share_cups(state, bus_id, product_name or f"Loki-{bus_id}")

        self.save_state()
        return state

    async def unshare_device(self, bus_id: str):
        """Stop all forwarding for a device."""
        state = self._states.get(bus_id)
        if not state:
            return

        if state.usbip_shared:
            await self._unshare_usbip(state, bus_id)

        if state.serial_proc:
            await self._stop_serial_forward(state)

        if state.ipp_shared:
            await self._unshare_cups(state)

        del self._states[bus_id]
        self.save_state()

    def get_state(self, bus_id: str) -> Optional[ForwardState]:
        return self._states.get(bus_id)

    def get_all_shared(self) -> list[ForwardState]:
        return list(self._states.values())

    def get_forward_info(self, bus_id: str) -> dict:
        """Return available forwarding methods for a device."""
        state = self._states.get(bus_id)
        if not state:
            return {"shared": False}
        return {
            "shared": True,
            "auto_share": state.auto_share,
            "usbip": state.usbip_shared,
            "serial": {
                "available": state.serial_port is not None,
                "port": state.serial_port,
                "device": state.serial_dev,
            },
            "ipp": {
                "available": state.ipp_shared,
                "cups_name": state.cups_name,
            },
        }

    # ── USB/IP ────────────────────────────────────────────────────────────────

    async def _share_usbip(self, state: ForwardState, bus_id: str):
        usbip_id = await self._resolve_usbip_busid(bus_id)
        if not usbip_id:
            logger.warning(f"Cannot resolve usbip busid for {bus_id}")
            return

        proc = await asyncio.create_subprocess_exec(
            "usbip", "bind", "--busid", usbip_id,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode == 0 or b"already bound" in stderr:
            state.usbip_shared = True
            logger.info(f"USB/IP: bound {bus_id} (usbip: {usbip_id})")
        else:
            logger.warning(f"USB/IP bind failed for {bus_id}: {stderr.decode().strip()}")

    async def _unshare_usbip(self, state: ForwardState, bus_id: str):
        usbip_id = await self._resolve_usbip_busid(bus_id)
        if usbip_id:
            proc = await asyncio.create_subprocess_exec(
                "usbip", "unbind", "--busid", usbip_id,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
        state.usbip_shared = False

    async def _resolve_usbip_busid(self, bus_id: str) -> Optional[str]:
        try:
            proc = await asyncio.create_subprocess_exec(
                "usbip", "list", "--local",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            for line in stdout.decode().splitlines():
                m = re.search(r"busid\s+(\S+)", line)
                if m:
                    return m.group(1)
        except Exception as e:
            logger.error(f"usbip resolve failed: {e}")
        return bus_id

    # ── Serial Forwarding ─────────────────────────────────────────────────────

    async def _start_serial_forward(self, state: ForwardState, serial_dev: str):
        """Start a TCP-to-serial bridge using socat."""
        if state.serial_proc and state.serial_proc.returncode is None:
            return  # already running

        port = self._next_serial_port
        self._next_serial_port += 1

        # Detect baud rate (default 9600, plotters often use 9600 or 115200)
        baud = self._detect_baud(serial_dev)

        if shutil.which("socat"):
            cmd = [
                "socat",
                f"TCP-LISTEN:{port},reuseaddr,fork",
                f"FILE:{serial_dev},b{baud},raw,echo=0",
            ]
        elif shutil.which("ser2net"):
            # ser2net fallback
            cmd = ["ser2net", "-n", "-C",
                   f"{port}:raw:0:{serial_dev}:{baud} 8DATABITS NONE 1STOPBIT"]
        else:
            logger.warning("Neither socat nor ser2net found — serial forwarding unavailable")
            return

        try:
            state.serial_proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            state.serial_port = port
            state.serial_dev = serial_dev
            logger.info(f"Serial: {serial_dev} → TCP:{port} (baud {baud})")
        except Exception as e:
            logger.error(f"Serial forward start failed: {e}")

    async def _stop_serial_forward(self, state: ForwardState):
        if state.serial_proc and state.serial_proc.returncode is None:
            state.serial_proc.terminate()
            try:
                await asyncio.wait_for(state.serial_proc.wait(), timeout=3)
            except asyncio.TimeoutError:
                state.serial_proc.kill()
        state.serial_proc = None
        state.serial_port = None
        state.serial_dev = None

    def _detect_baud(self, serial_dev: str) -> int:
        """Try to read current baud from stty, fallback to 9600."""
        try:
            r = subprocess.run(
                ["stty", "-F", serial_dev, "speed"],
                capture_output=True, text=True, timeout=2
            )
            if r.returncode == 0:
                baud = int(r.stdout.strip())
                if baud in (1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200):
                    return baud
        except Exception:
            pass
        return 9600

    # ── CUPS / IPP Printer Sharing ────────────────────────────────────────────

    async def _share_cups(self, state: ForwardState, bus_id: str, name: str):
        """Share a USB printer via CUPS so it appears as a network printer."""
        if not shutil.which("lpstat"):
            logger.warning("CUPS not available — IPP printer sharing disabled")
            return

        cups_name = re.sub(r"[^a-zA-Z0-9_-]", "_", name)

        # Find the CUPS device URI for this USB printer
        uri = await self._find_cups_uri(bus_id)
        if not uri:
            logger.warning(f"Could not find CUPS URI for {bus_id}")
            return

        # Add and share via CUPS
        cmds = [
            ["lpadmin", "-p", cups_name, "-v", uri, "-E",
             "-m", "everywhere", "-o", "printer-is-shared=true"],
            ["cupsctl", "--share-printers"],
        ]
        for cmd in cmds:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                logger.warning(f"CUPS command failed: {' '.join(cmd)}: {stderr.decode().strip()}")
                return

        state.ipp_shared = True
        state.cups_name = cups_name
        logger.info(f"IPP: Shared printer '{cups_name}' (URI: {uri})")

    async def _unshare_cups(self, state: ForwardState):
        if state.cups_name:
            proc = await asyncio.create_subprocess_exec(
                "lpadmin", "-x", state.cups_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            state.ipp_shared = False
            state.cups_name = None

    async def _find_cups_uri(self, bus_id: str) -> Optional[str]:
        """Find CUPS device URI matching a USB bus_id."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "lpinfo", "-v",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            for line in stdout.decode().splitlines():
                if "usb://" in line:
                    parts = line.strip().split(maxsplit=1)
                    if len(parts) == 2:
                        return parts[1]
        except Exception as e:
            logger.error(f"CUPS URI lookup failed: {e}")
        return None

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _find_serial_device(self, bus_id: str) -> Optional[str]:
        """Find /dev/ttyUSB* or /dev/ttyACM* for a given bus_id."""
        import glob
        for pattern in ("/dev/ttyUSB*", "/dev/ttyACM*", "/dev/usb/lp*"):
            for dev in sorted(glob.glob(pattern)):
                return dev
        return None

    def _is_printer_class(self, vendor_id: str, product_id: str) -> bool:
        """Check if device is a known printer by vendor:product ID."""
        known_printers = {
            "03f0", "04a9", "04b8", "04e8",  # HP, Canon, Epson, Samsung
            "0922", "04f9",  # Dymo, Brother
        }
        return vendor_id.lower() in known_printers
