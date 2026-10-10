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
import time
from dataclasses import asdict, dataclass
from pathlib import Path

logger = logging.getLogger("loki-printserver.forwarder")

# TCP port range for serial forwarding (one port per device)
SERIAL_PORT_BASE = 7580
# USB/IP export with no client for this long → give the device back to macOS.
USBIP_IDLE_RESTORE_SECS = 20.0


def _usbip_client_attached(usbip_busid: str | None) -> bool | None:
    """True if a client holds the export, False if idle, None if unknown."""
    if not usbip_busid:
        return None
    path = Path(f"/sys/bus/usb/devices/{usbip_busid}/usbip_status")
    try:
        if not path.is_file():
            return None
        status = int(path.read_text(encoding="utf-8").strip().split()[0])
    except (OSError, ValueError):
        return None
    if status == 1:
        return True
    if status == 0:
        return False
    return None

# Persistent state file — survives container restarts (stored in named volume)
STATE_FILE = Path(os.getenv("LOKI_DATA_DIR", "/etc/loki-printserver")) / "shared_devices.json"


@dataclass
class ForwardState:
    bus_id: str
    usbip_shared: bool = False
    serial_port: int | None = None
    serial_proc: asyncio.subprocess.Process | None = None
    serial_dev: str | None = None  # e.g. /dev/ttyUSB0
    ipp_shared: bool = False
    cups_name: str | None = None
    # Metadata saved for auto-restore
    device_class: str = ""
    vendor_id: str = ""
    product_id: str = ""
    product_name: str = ""
    auto_share: bool = False  # user explicitly set this device to auto-share
    attach_mode: str = ""  # "bridge" | "usbip" | "serial"
    usbip_busid: str | None = None


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
        self._usb_bridges: dict = {}
        self._plotcut_busy: set[str] = set()
        self._usbip_idle_since: dict[str, float] = {}

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
                           serial_dev: str | None = None,
                           vendor_id: str = "", product_id: str = "",
                           product_name: str = "",
                           auto_share: bool = False):
        """Enable all applicable forwarding for a device."""
        from .usb_bridge import USBBridge, needs_raw_usb

        state = self._states.setdefault(bus_id, ForwardState(bus_id=bus_id))
        state.device_class = device_class
        state.vendor_id = vendor_id
        state.product_id = product_id
        state.product_name = product_name
        if auto_share:
            state.auto_share = True
        if vendor_id and product_id:
            await self.prune_duplicate_vidpid({bus_id})

        # 0) Raw USB bridge — for vendor-specific devices (e.g. Mimaki) where
        #    the host application uses direct IOKit USB access, not serial.
        #    IMPORTANT: The bridge keeps the device open via pyusb (usbfs).
        #    We must NEVER fall through to usbip/usbserial for these devices,
        #    as that would steal the device from pyusb and break bulk transfers.
        if needs_raw_usb(vendor_id):
            if state.serial_dev and state.serial_dev.startswith("usb-bridge:"):
                logger.info(
                    f"Raw USB bridge already active for {bus_id} on TCP:{state.serial_port}"
                )
                self.save_state()
                return state

            port = self._next_serial_port
            self._next_serial_port += 1
            try:
                bridge = USBBridge(
                    vid=int(vendor_id, 16),
                    pid=int(product_id, 16),
                    tcp_port=port,
                )
                if await bridge.start():
                    state.serial_port = port
                    state.serial_dev = f"usb-bridge:{vendor_id}:{product_id}"
                    state.attach_mode = "bridge"
                    self._usb_bridges[bus_id] = bridge
                    logger.info(
                        f"Raw USB bridge active for {bus_id} ({vendor_id}:{product_id}) "
                        f"→ TCP:{port}"
                    )
                    self.save_state()
                    return state
            except Exception as e:
                logger.error(f"Raw USB bridge failed for {bus_id}: {e}")
            # Do NOT fall through to serial/usbip — they would steal the device
            self.save_state()
            return state

        # 1) Serial forwarding — preferred for macOS clients (CDC-ACM bridge).
        #    USB/IP bind and usbserial are MUTUALLY EXCLUSIVE for the same
        #    device — usbip-host steals the device from usbserial, destroying
        #    the /dev/ttyUSB* node.  So we try serial FIRST.
        dev = serial_dev or self._find_serial_device(bus_id)
        if not dev and vendor_id and product_id:
            dev = await self._try_bind_usbserial(bus_id, vendor_id, product_id)
        if dev:
            await self._start_serial_forward(state, dev)
            logger.info(f"Serial forwarding active for {bus_id} — skipping USB/IP bind")
        else:
            # 2) USB/IP — fallback for Linux/Windows clients (full USB passthrough)
            await self._share_usbip(state, bus_id)

        # 3) IPP/CUPS — for USB printer class devices
        if "Printer" in device_class or self._is_printer_class(vendor_id, product_id):
            await self._share_cups(state, bus_id, product_name or f"Loki-{bus_id}")

        self.save_state()
        return state

    async def unshare_device(self, bus_id: str, *, persist: bool = True):
        """Stop all forwarding for a device.

        Args:
            persist: If True, update the saved state file.  Set to False
                     during shutdown so auto-restore data survives.
        """
        state = self._states.get(bus_id)
        if not state:
            return

        bridge = self._usb_bridges.pop(bus_id, None)
        if bridge:
            await bridge.stop()

        if state.usbip_shared:
            await self._unshare_usbip(state, bus_id)

        if state.serial_proc:
            await self._stop_serial_forward(state)

        if state.ipp_shared:
            await self._unshare_cups(state)

        del self._states[bus_id]
        if persist:
            self.save_state()

    def get_state(self, bus_id: str) -> ForwardState | None:
        return self._states.get(bus_id)

    def get_usb_bridge(self, bus_id: str):
        """Return the live USBBridge for a bus_id, or None."""
        return self._usb_bridges.get(bus_id)

    def mark_plotcut_busy(self, bus_id: str, busy: bool) -> None:
        """Block attach-mode / usbip bind while a Plot-Cut job owns the device."""
        if busy:
            self._plotcut_busy.add(bus_id)
        else:
            self._plotcut_busy.discard(bus_id)

    def is_plotcut_busy(self, bus_id: str) -> bool:
        return bus_id in self._plotcut_busy

    async def release_idle_usbip(self) -> None:
        """Restore the macOS path after the Windows USB/IP client is gone.

        A shutdown does not call detach. Once the export has no client for
        USBIP_IDLE_RESTORE_SECS, Mimaki goes back to the USB bridge and
        CH340/Vevor back to socat.
        """
        from .usb_bridge import needs_raw_usb

        now = time.monotonic()
        for state in list(self._states.values()):
            if not state.usbip_shared or state.attach_mode != "usbip":
                self._usbip_idle_since.pop(state.bus_id, None)
                continue
            if self.is_plotcut_busy(state.bus_id):
                continue
            attached = _usbip_client_attached(state.usbip_busid)
            if attached is True:
                self._usbip_idle_since.pop(state.bus_id, None)
                continue
            if attached is None:
                continue
            since = self._usbip_idle_since.setdefault(state.bus_id, now)
            if now - since < USBIP_IDLE_RESTORE_SECS:
                continue
            self._usbip_idle_since.pop(state.bus_id, None)
            logger.info(
                "USB/IP client gone for %s — restoring macOS path", state.bus_id
            )
            try:
                if needs_raw_usb(state.vendor_id):
                    await self._switch_to_bridge(state)
                else:
                    await self._switch_usbip_to_serial(state)
            except Exception as e:
                logger.warning("Could not restore macOS path for %s: %s", state.bus_id, e)

    def find_state(self, bus_id: str, vendor_id: str = "",
                   product_id: str = "") -> ForwardState | None:
        """Resolve share state by bus_id, then by VID:PID if USB re-enumerated."""
        state = self._states.get(bus_id)
        if state:
            return state
        if vendor_id and product_id:
            for s in self._states.values():
                if s.vendor_id == vendor_id and s.product_id == product_id:
                    if s.bus_id != bus_id:
                        self._rebind_bus_id(s, bus_id)
                    return s
        return None

    def _rebind_bus_id(self, state: ForwardState, new_bus_id: str):
        old = state.bus_id
        if old == new_bus_id:
            return
        logger.info(f"USB re-enumerated {old} → {new_bus_id} ({state.vendor_id}:{state.product_id})")
        self._states.pop(old, None)
        if old in self._usb_bridges:
            self._usb_bridges[new_bus_id] = self._usb_bridges.pop(old)
        state.bus_id = new_bus_id
        self._states[new_bus_id] = state

    def get_all_shared(self) -> list[ForwardState]:
        return list(self._states.values())

    def unique_shared_count(self) -> int:
        """Count unique shared peripherals by VID:PID (not leftover bus_ids)."""
        seen: set[tuple[str, str]] = set()
        for state in self._states.values():
            if state.vendor_id and state.product_id:
                seen.add((state.vendor_id.lower(), state.product_id.lower()))
            else:
                seen.add(("bus", state.bus_id))
        return len(seen)

    async def prune_duplicate_vidpid(self, visible_bus_ids: set[str] | None = None):
        """Drop stale share states that share a VID:PID with a newer bus_id."""
        groups: dict[tuple[str, str], list[ForwardState]] = {}
        for state in self._states.values():
            if not state.vendor_id or not state.product_id:
                continue
            key = (state.vendor_id.lower(), state.product_id.lower())
            groups.setdefault(key, []).append(state)

        dropped: list[str] = []
        for states in groups.values():
            if len(states) <= 1:
                continue
            keep = self._pick_canonical_state(states, visible_bus_ids)
            for state in states:
                if state.bus_id == keep.bus_id:
                    continue
                self._absorb_stale_state(keep, state)
                dropped.append(state.bus_id)

        if dropped:
            logger.info(f"Pruned stale share states for the same VID:PID: {dropped}")
            self.save_state()

    def _pick_canonical_state(
        self, states: list[ForwardState], visible_bus_ids: set[str] | None,
    ) -> ForwardState:
        visible = [s for s in states if visible_bus_ids and s.bus_id in visible_bus_ids]
        if visible:
            return visible[0]
        active = [
            s for s in states
            if s.bus_id in self._usb_bridges or s.usbip_shared or s.serial_proc
        ]
        if active:
            return active[0]
        return states[0]

    def _absorb_stale_state(self, keep: ForwardState, stale: ForwardState):
        """Move live resources from a leftover bus_id onto the kept state."""
        stale_bridge = self._usb_bridges.pop(stale.bus_id, None)
        if stale_bridge and keep.bus_id not in self._usb_bridges:
            self._usb_bridges[keep.bus_id] = stale_bridge
            keep.serial_port = keep.serial_port or stale.serial_port
            keep.serial_dev = keep.serial_dev or stale.serial_dev
            keep.attach_mode = keep.attach_mode or stale.attach_mode
        if stale.usbip_shared and not keep.usbip_shared:
            keep.usbip_shared = True
            keep.usbip_busid = keep.usbip_busid or stale.usbip_busid
            keep.attach_mode = keep.attach_mode or stale.attach_mode
        if stale.auto_share:
            keep.auto_share = True
        if stale.serial_proc and not keep.serial_proc:
            keep.serial_proc = stale.serial_proc
            keep.serial_port = keep.serial_port or stale.serial_port
            keep.serial_dev = keep.serial_dev or stale.serial_dev
        self._states.pop(stale.bus_id, None)

    def get_forward_info(self, bus_id: str) -> dict:
        """Return available forwarding methods for a device."""
        state = self._states.get(bus_id)
        if not state:
            return {"shared": False}
        return {
            "shared": True,
            "auto_share": state.auto_share,
            "attach_mode": state.attach_mode or None,
            "usbip": state.usbip_shared,
            "usbip_busid": state.usbip_busid,
            "serial": {
                "available": bool(
                    state.serial_port
                    and state.serial_proc is not None
                    and state.serial_proc.returncode is None
                ),
                "port": state.serial_port,
                "device": state.serial_dev,
            },
            "ipp": {
                "available": state.ipp_shared,
                "cups_name": state.cups_name,
            },
        }

    async def set_attach_mode(self, bus_id: str, mode: str) -> dict:
        """Switch a raw-USB device between Mac bridge and Windows USB/IP.

        usbip and the pyusb bridge cannot own the same device at once.
        """
        from .usb_bridge import needs_raw_usb

        state = self._states.get(bus_id)
        if not state:
            raise ValueError(f"Device {bus_id} is not shared")
        if self.is_plotcut_busy(bus_id):
            raise RuntimeError(
                f"Plot-Cut-Auftrag läuft auf {bus_id} — Attach-Modus gerade nicht möglich"
            )
        if needs_raw_usb(state.vendor_id):
            if mode == "usbip":
                await self._switch_to_usbip(state)
            elif mode in ("bridge", "serial"):
                await self._switch_to_bridge(state)
            else:
                raise ValueError(f"Unknown attach mode: {mode}")
        else:
            if mode == "usbip":
                await self._switch_serial_to_usbip(state)
            elif mode in ("serial", "bridge"):
                await self._switch_usbip_to_serial(state)
            else:
                raise ValueError(f"Unknown attach mode: {mode}")
        return self.get_forward_info(bus_id)

    async def _switch_to_usbip(self, state: ForwardState):
        if state.usbip_shared:
            logger.info(f"USB/IP already active for {state.bus_id}")
            return
        bridge = self._usb_bridges.pop(state.bus_id, None)
        if bridge:
            await bridge.stop()
            await asyncio.sleep(1.0)
        await self._share_usbip(state, state.bus_id)
        if not state.usbip_shared:
            logger.error(f"USB/IP bind failed for {state.bus_id}, restoring bridge")
            await self._switch_to_bridge(state)
            raise RuntimeError(f"usbip bind failed for {state.bus_id}")
        state.usbip_busid = await self._resolve_usbip_busid(state.bus_id)
        state.attach_mode = "usbip"
        self._usbip_idle_since.pop(state.bus_id, None)
        logger.info(f"Switched {state.bus_id} to USB/IP (busid={state.usbip_busid})")

    async def _switch_to_bridge(self, state: ForwardState):
        from .usb_bridge import USBBridge

        if state.bus_id in self._usb_bridges and not state.usbip_shared:
            state.attach_mode = "bridge"
            return
        if state.usbip_shared:
            await self._unshare_usbip(state, state.bus_id)
            state.usbip_busid = None
            await asyncio.sleep(1.0)
        port = state.serial_port
        if not port:
            port = self._next_serial_port
            self._next_serial_port += 1
            state.serial_port = port
        bridge = USBBridge(
            vid=int(state.vendor_id, 16),
            pid=int(state.product_id, 16),
            tcp_port=port,
        )
        if not await bridge.start():
            raise RuntimeError(f"Could not restart USB bridge for {state.bus_id}")
        self._usb_bridges[state.bus_id] = bridge
        state.serial_dev = f"usb-bridge:{state.vendor_id}:{state.product_id}"
        state.attach_mode = "bridge"
        logger.info(f"Switched {state.bus_id} back to USB bridge TCP:{port}")

    async def _switch_serial_to_usbip(self, state: ForwardState):
        """Stop socat/usbserial and export the device via USB/IP (Windows COM)."""
        if state.usbip_shared:
            state.attach_mode = "usbip"
            return
        await self._stop_serial_forward(state, keep_port=True)
        await self._unbind_kernel_interfaces(state.bus_id)
        await asyncio.sleep(0.8)
        await self._share_usbip(state, state.bus_id)
        if not state.usbip_shared:
            logger.error(f"USB/IP bind failed for {state.bus_id}, restoring serial")
            await self._switch_usbip_to_serial(state)
            raise RuntimeError(f"usbip bind failed for {state.bus_id}")
        state.usbip_busid = await self._resolve_usbip_busid(state.bus_id)
        state.attach_mode = "usbip"
        self._usbip_idle_since.pop(state.bus_id, None)
        logger.info(f"Switched {state.bus_id} serial → USB/IP (busid={state.usbip_busid})")

    async def _switch_usbip_to_serial(self, state: ForwardState):
        """Give a CH340/serial plotter back to usbserial + socat (macOS)."""
        if state.usbip_shared:
            await self._unshare_usbip(state, state.bus_id)
            state.usbip_busid = None
            await asyncio.sleep(1.0)
        if state.serial_proc and state.serial_proc.returncode is None:
            state.attach_mode = "serial"
            return
        dev = self._find_serial_device(state.bus_id)
        if not dev and state.vendor_id and state.product_id:
            dev = await self._try_bind_usbserial(
                state.bus_id, state.vendor_id, state.product_id
            )
        if not dev:
            logger.warning(f"Could not restore serial for {state.bus_id}")
            state.attach_mode = ""
            return
        await self._start_serial_forward(state, dev)
        state.attach_mode = "serial"
        logger.info(f"Switched {state.bus_id} USB/IP → serial TCP:{state.serial_port}")

    async def _unbind_kernel_interfaces(self, bus_id: str) -> None:
        """Detach kernel drivers (usbserial/ch341) so usbip-host can claim the device."""
        import glob

        sysfs_id = await self._resolve_usbip_busid(bus_id)
        if not sysfs_id:
            return
        for intf in glob.glob(f"/sys/bus/usb/devices/{sysfs_id}/{sysfs_id}:*"):
            driver = os.path.join(intf, "driver")
            if not os.path.islink(driver):
                continue
            name = os.path.basename(intf)
            unbind = os.path.join(os.path.realpath(driver), "unbind")
            try:
                Path(unbind).write_text(name)
                logger.info(f"Unbound kernel driver from {name}")
            except OSError as e:
                logger.debug(f"unbind {name}: {e}")

    # ── USB/IP ────────────────────────────────────────────────────────────────

    async def pause_usbip_for_plotcut(self, bus_id: str) -> bool:
        """Temporarily unbind USB/IP so Plot Cut can write. Returns True if it was bound."""
        state = self._states.get(bus_id)
        if not state or not state.usbip_shared:
            return False
        logger.info(f"Plot Cut: pausing USB/IP export for {bus_id}")
        await self._unshare_usbip(state, bus_id)
        await asyncio.sleep(0.5)
        return True

    async def resume_usbip_after_plotcut(self, bus_id: str) -> None:
        """Re-export a device via USB/IP after a Plot-Cut job."""
        state = self._states.get(bus_id)
        if not state:
            return
        await self._share_usbip(state, bus_id, allow_plotcut=True)
        if state.usbip_shared:
            state.usbip_busid = await self._resolve_usbip_busid(bus_id)
            state.attach_mode = "usbip"
            logger.info(f"Plot Cut: restored USB/IP export for {bus_id}")

    async def _share_usbip(self, state: ForwardState, bus_id: str, *,
                           allow_plotcut: bool = False):
        if self.is_plotcut_busy(bus_id) and not allow_plotcut:
            raise RuntimeError(
                f"Plot-Cut-Auftrag läuft auf {bus_id} — USB/IP-Bind gerade nicht möglich"
            )
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

    async def _resolve_usbip_busid(self, bus_id: str) -> str | None:
        """Map a pyusb bus_id (bus-address) to the sysfs busid used by usbip.

        Looks up the device's sysfs path by matching busnum/devnum from
        /sys/bus/usb/devices/. Falls back to bus_id if no match found.
        """
        import glob
        try:
            bus, addr = bus_id.split("-", 1)
        except ValueError:
            return bus_id

        for dev_path in glob.glob("/sys/bus/usb/devices/[0-9]*"):
            try:
                busnum = open(f"{dev_path}/busnum").read().strip()
                devnum = open(f"{dev_path}/devnum").read().strip()
            except (OSError, FileNotFoundError):
                continue
            if busnum == bus and devnum == addr:
                sysfs_id = os.path.basename(dev_path)
                logger.debug(f"Resolved {bus_id} → sysfs {sysfs_id}")
                return sysfs_id

        logger.warning(f"Could not resolve sysfs busid for {bus_id}, using as-is")
        return bus_id

    # ── Serial Forwarding ─────────────────────────────────────────────────────

    async def _start_serial_forward(self, state: ForwardState, serial_dev: str):
        """Start a TCP-to-serial bridge using socat."""
        if state.serial_proc and state.serial_proc.returncode is None:
            return  # already running

        port = state.serial_port or self._next_serial_port
        if state.serial_port is None:
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

    async def _stop_serial_forward(self, state: ForwardState, *, keep_port: bool = False):
        if state.serial_proc and state.serial_proc.returncode is None:
            state.serial_proc.terminate()
            try:
                await asyncio.wait_for(state.serial_proc.wait(), timeout=3)
            except TimeoutError:
                state.serial_proc.kill()
        saved_port = state.serial_port if keep_port else None
        state.serial_proc = None
        state.serial_dev = None
        state.serial_port = saved_port

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

    async def _find_cups_uri(self, bus_id: str) -> str | None:
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

    def _find_serial_device(self, bus_id: str) -> str | None:
        """Find /dev/ttyUSB* or /dev/ttyACM* for a given bus_id.

        The bus_id is in pyusb format 'bus-address' (e.g. '1-4').
        We match it to a sysfs USB device by comparing busnum+devnum,
        then look for a tty device in its interface directories.
        """
        import glob
        try:
            bus, addr = bus_id.split("-", 1)
        except ValueError:
            return None

        for dev_path in glob.glob("/sys/bus/usb/devices/[0-9]*"):
            try:
                busnum = open(f"{dev_path}/busnum").read().strip()
                devnum = open(f"{dev_path}/devnum").read().strip()
            except (OSError, FileNotFoundError):
                continue
            if busnum != bus or devnum != addr:
                continue
            sysfs_name = os.path.basename(dev_path)
            # Pattern: <sysfs>/<iface>/<ttyUSB0>/tty/<ttyUSB0>
            for tty_node in glob.glob(f"{dev_path}/{sysfs_name}:*/ttyUSB*/tty/ttyUSB*") + \
                             glob.glob(f"{dev_path}/{sysfs_name}:*/ttyACM*/tty/ttyACM*"):
                tty_name = os.path.basename(tty_node)
                tty_dev = f"/dev/{tty_name}"
                if os.path.exists(tty_dev):
                    logger.info(f"Resolved {bus_id} → {tty_dev} (sysfs: {sysfs_name})")
                    return tty_dev
            break

        return None

    async def _try_bind_usbserial(self, bus_id: str, vendor_id: str,
                                    product_id: str) -> str | None:
        """Bind a native USB device to the generic usbserial driver.

        Some devices (e.g. Mimaki CG-SR) use vendor-specific USB with bulk
        endpoints but no standard serial class.  Writing their VID:PID to
        /sys/bus/usb-serial/drivers/generic/new_id makes Linux create a
        /dev/ttyUSB* node for them.
        """
        try:
            subprocess.run(["modprobe", "usbserial"], capture_output=True, timeout=5)
            vid_int = int(vendor_id, 16)
            pid_int = int(product_id, 16)
            new_id = f"{vid_int:04x} {pid_int:04x}"
            with open("/sys/bus/usb-serial/drivers/generic/new_id", "w") as f:
                f.write(new_id)
            logger.info(f"Bound {vendor_id}:{product_id} to generic usbserial")
            await asyncio.sleep(1)
            dev = self._find_serial_device(bus_id)
            if dev:
                return dev
            logger.warning(f"usbserial bound but no tty appeared for {bus_id}")
        except Exception as e:
            logger.debug(f"Could not bind {vendor_id}:{product_id} to usbserial: {e}")
        return None

    def _is_printer_class(self, vendor_id: str, product_id: str) -> bool:
        """Check if device is a known printer by vendor:product ID."""
        known_printers = {
            "03f0", "04a9", "04b8", "04e8",  # HP, Canon, Epson, Samsung
            "0922", "04f9",  # Dymo, Brother
        }
        return vendor_id.lower() in known_printers
