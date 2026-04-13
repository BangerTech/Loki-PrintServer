"""
Loki-Client — Platform-specific USB Device Attachment
Supports three methods depending on platform and device type:

  1. USB/IP  — Full USB passthrough (Linux native, Windows via usbip-win)
  2. Serial  — TCP-to-virtual-serial bridge (macOS socat, Windows com0com)
  3. IPP     — Network printer via CUPS/Bonjour (macOS & Linux auto-discover)
"""
import glob
import logging
import os
import platform
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional

logger = logging.getLogger("loki-client.attach")

OS = platform.system()  # "Linux", "Windows", "Darwin"


class AttachStatus(Enum):
    ATTACHED = "attached"
    DETACHED = "detached"
    ERROR = "error"
    UNSUPPORTED = "unsupported"


class AttachMethod(Enum):
    USBIP = "usbip"
    SERIAL = "serial"
    IPP = "ipp"


@dataclass
class AttachResult:
    status: AttachStatus
    method: Optional[AttachMethod] = None
    message: str = ""
    local_device: Optional[str] = None  # /dev/ttyUSB0, COM3, printer name


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


class DeviceAttacher:
    """Manages local attachment of remote USB devices using the best method."""

    def __init__(self):
        self._attached: dict[str, AttachResult] = {}  # bus_id -> result

    def attach(self, server_ip: str, bus_id: str,
               forward_info: Optional[dict] = None) -> AttachResult:
        """
        Attach a remote USB device. Tries the best method for this platform:
          - Linux/Windows: USB/IP first, then serial fallback
          - macOS: Serial first, then IPP for printers
        """
        if not forward_info:
            forward_info = {}

        serial_info = forward_info.get("serial", {})
        ipp_info = forward_info.get("ipp", {})
        has_usbip = forward_info.get("usbip", False)
        has_serial = serial_info.get("available", False)
        has_ipp = ipp_info.get("available", False)

        result = None

        if OS == "Linux":
            # Linux: prefer USB/IP (full passthrough)
            if has_usbip:
                result = self._attach_usbip_linux(server_ip, bus_id)
            if (not result or result.status != AttachStatus.ATTACHED) and has_serial:
                result = self._attach_serial_linux(server_ip, serial_info)
            if (not result or result.status != AttachStatus.ATTACHED) and has_ipp:
                result = self._attach_ipp_linux(server_ip, ipp_info)

        elif OS == "Windows":
            if has_usbip:
                result = self._attach_usbip_windows(server_ip, bus_id)
            if (not result or result.status != AttachStatus.ATTACHED) and has_serial:
                result = self._attach_serial_windows(server_ip, serial_info)
            if (not result or result.status != AttachStatus.ATTACHED) and has_ipp:
                result = self._attach_ipp_windows(server_ip, ipp_info)

        elif OS == "Darwin":
            # macOS: Serial for plotters/serial, IPP for printers, USB/IP via Lima
            if has_serial:
                result = self._attach_serial_macos(server_ip, serial_info)
            if (not result or result.status != AttachStatus.ATTACHED) and has_ipp:
                result = self._attach_ipp_macos(server_ip, ipp_info)
            if (not result or result.status != AttachStatus.ATTACHED) and has_usbip:
                result = self._attach_usbip_macos(server_ip, bus_id)

        if not result:
            result = AttachResult(
                AttachStatus.ERROR,
                message="No compatible forwarding method available for this device.\n"
                        "Make sure the device is shared on the server first."
            )

        if result.status == AttachStatus.ATTACHED:
            self._attached[bus_id] = result

        return result

    def detach(self, bus_id: str) -> AttachResult:
        prev = self._attached.get(bus_id)
        if not prev:
            return AttachResult(AttachStatus.DETACHED, message="Already detached")

        if prev.method == AttachMethod.USBIP:
            result = self._detach_usbip(bus_id)
        elif prev.method == AttachMethod.SERIAL:
            result = self._detach_serial(bus_id)
        elif prev.method == AttachMethod.IPP:
            result = self._detach_ipp(prev.local_device)
        else:
            result = AttachResult(AttachStatus.DETACHED, message="Cleaned up")

        self._attached.pop(bus_id, None)
        return result

    def get_attached(self) -> list[str]:
        return list(self._attached.keys())

    def get_attach_info(self, bus_id: str) -> Optional[AttachResult]:
        return self._attached.get(bus_id)

    # ══════════════════════════════════════════════════════════════════════════
    #  USB/IP
    # ══════════════════════════════════════════════════════════════════════════

    def _attach_usbip_linux(self, server_ip: str, bus_id: str) -> AttachResult:
        subprocess.run(["modprobe", "vhci-hcd"], capture_output=True)
        if not shutil.which("usbip"):
            return AttachResult(AttachStatus.ERROR,
                                message="usbip not installed.\nInstall: sudo apt install linux-tools-generic")
        r = _run(["usbip", "attach", "-r", server_ip, "-b", bus_id])
        if r.returncode == 0:
            time.sleep(1)
            local = self._find_new_device()
            return AttachResult(AttachStatus.ATTACHED, AttachMethod.USBIP,
                                f"USB device attached via USB/IP", local)
        return AttachResult(AttachStatus.ERROR, message=r.stderr.strip() or r.stdout.strip())

    def _attach_usbip_windows(self, server_ip: str, bus_id: str) -> AttachResult:
        exe = self._find_usbip_win()
        if not exe:
            return AttachResult(
                AttachStatus.ERROR,
                message="usbip-win not found.\n"
                        "Download: https://github.com/cezanne/usbip-win/releases\n"
                        "Install the driver and restart."
            )
        r = _run([exe, "attach", "-r", server_ip, "-b", bus_id])
        if r.returncode == 0:
            return AttachResult(AttachStatus.ATTACHED, AttachMethod.USBIP,
                                "USB device attached via USB/IP")
        return AttachResult(AttachStatus.ERROR, message=r.stderr.strip())

    def _attach_usbip_macos(self, server_ip: str, bus_id: str) -> AttachResult:
        if shutil.which("limactl"):
            cmd = f"sudo modprobe vhci-hcd && sudo usbip attach -r {server_ip} -b {bus_id}"
            r = _run(["limactl", "shell", "default", "sh", "-c", cmd])
            if r.returncode == 0:
                return AttachResult(AttachStatus.ATTACHED, AttachMethod.USBIP,
                                    "USB device attached via Lima VM")
        return AttachResult(
            AttachStatus.UNSUPPORTED,
            message="Full USB/IP on macOS requires Lima VM.\n"
                    "Install: brew install lima && limactl start"
        )

    def _detach_usbip(self, bus_id: str) -> AttachResult:
        if OS == "Linux":
            r = _run(["usbip", "port"])
            for line in r.stdout.splitlines():
                if bus_id in line:
                    m = re.search(r"Port (\d+)", line)
                    if m:
                        _run(["usbip", "detach", "-p", m.group(1)])
        elif OS == "Windows":
            exe = self._find_usbip_win()
            if exe:
                _run([exe, "detach", "-b", bus_id])
        elif OS == "Darwin" and shutil.which("limactl"):
            _run(["limactl", "shell", "default", "sh", "-c",
                  f"sudo usbip detach -b {bus_id}"])
        return AttachResult(AttachStatus.DETACHED, message="USB/IP detached")

    def _find_usbip_win(self) -> Optional[str]:
        for name in ("usbip.exe", "usbip-win.exe"):
            if shutil.which(name):
                return shutil.which(name)
        for p in (r"C:\Program Files\usbip-win\usbip.exe",
                  r"C:\usbip-win\usbip.exe"):
            if os.path.exists(p):
                return p
        return None

    # ══════════════════════════════════════════════════════════════════════════
    #  Serial-over-TCP
    # ══════════════════════════════════════════════════════════════════════════

    def _attach_serial_macos(self, server_ip: str, serial_info: dict) -> AttachResult:
        port = serial_info.get("port")
        if not port:
            return AttachResult(AttachStatus.ERROR, message="No serial port info from server")

        if not shutil.which("socat"):
            return AttachResult(
                AttachStatus.ERROR,
                message="socat not installed.\nInstall: brew install socat"
            )

        pty_path = f"/tmp/tty.loki-plotter-{port}"

        # socat creates a virtual serial port linked to the TCP stream
        proc = subprocess.Popen([
            "socat",
            f"pty,raw,echo=0,link={pty_path},mode=666",
            f"tcp:{server_ip}:{port}",
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        time.sleep(1)
        if proc.poll() is not None:
            err = proc.stderr.read().decode().strip()
            return AttachResult(AttachStatus.ERROR, message=f"socat failed: {err}")

        # Also create a symlink in /dev/ for convenience
        dev_link = f"/dev/tty.loki-{port}"
        try:
            if os.path.exists(dev_link):
                os.remove(dev_link)
            os.symlink(pty_path, dev_link)
        except OSError:
            dev_link = pty_path

        return AttachResult(
            AttachStatus.ATTACHED, AttachMethod.SERIAL,
            f"Virtual serial port created.\n\n"
            f"Use this device in your cutting/printing software:\n"
            f"  {dev_link}\n\n"
            f"Works with: xfcut, Silhouette Studio, Inkcut, etc.",
            local_device=dev_link,
        )

    def _attach_serial_linux(self, server_ip: str, serial_info: dict) -> AttachResult:
        port = serial_info.get("port")
        if not port:
            return AttachResult(AttachStatus.ERROR, message="No serial port info from server")

        if not shutil.which("socat"):
            return AttachResult(AttachStatus.ERROR,
                                message="socat not installed. Install: sudo apt install socat")

        pty_path = f"/dev/ttyLOKI{port}"
        proc = subprocess.Popen([
            "socat",
            f"pty,raw,echo=0,link={pty_path},mode=666",
            f"tcp:{server_ip}:{port}",
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        time.sleep(1)
        if proc.poll() is not None:
            err = proc.stderr.read().decode().strip()
            return AttachResult(AttachStatus.ERROR, message=f"socat failed: {err}")

        return AttachResult(
            AttachStatus.ATTACHED, AttachMethod.SERIAL,
            f"Virtual serial port: {pty_path}",
            local_device=pty_path,
        )

    def _attach_serial_windows(self, server_ip: str, serial_info: dict) -> AttachResult:
        port = serial_info.get("port")
        if not port:
            return AttachResult(AttachStatus.ERROR, message="No serial port info from server")

        # Windows: use com2tcp from com0com project, or just direct TCP
        com2tcp = shutil.which("com2tcp.exe")
        if com2tcp:
            proc = subprocess.Popen(
                [com2tcp, f"\\\\.\\CNCB0", server_ip, str(port)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            time.sleep(1)
            if proc.poll() is None:
                return AttachResult(
                    AttachStatus.ATTACHED, AttachMethod.SERIAL,
                    "Virtual COM port created.\nUse CNCB0 in your software.",
                    local_device="CNCB0",
                )

        return AttachResult(
            AttachStatus.ATTACHED, AttachMethod.SERIAL,
            f"Serial device available at:\n"
            f"  TCP: {server_ip}:{port}\n\n"
            f"Use in software that supports TCP serial connections.\n"
            f"Or install com0com for a virtual COM port:\n"
            f"  https://sourceforge.net/projects/com0com/",
            local_device=f"tcp://{server_ip}:{port}",
        )

    def _detach_serial(self, bus_id: str) -> AttachResult:
        # Kill any socat processes for this device
        if OS in ("Linux", "Darwin"):
            _run(["pkill", "-f", f"loki.*{bus_id}"])
            _run(["pkill", "-f", f"tty.loki"])
        return AttachResult(AttachStatus.DETACHED, message="Serial bridge stopped")

    # ══════════════════════════════════════════════════════════════════════════
    #  IPP / Network Printer
    # ══════════════════════════════════════════════════════════════════════════

    def _attach_ipp_macos(self, server_ip: str, ipp_info: dict) -> AttachResult:
        cups_name = ipp_info.get("cups_name")
        if not cups_name:
            return AttachResult(AttachStatus.ERROR, message="No CUPS printer info from server")

        local_name = f"Loki-{cups_name}"
        uri = f"ipp://{server_ip}:631/printers/{cups_name}"

        r = _run([
            "lpadmin", "-p", local_name, "-v", uri, "-E",
            "-m", "everywhere",
            "-o", "printer-is-shared=false"
        ])

        if r.returncode == 0:
            return AttachResult(
                AttachStatus.ATTACHED, AttachMethod.IPP,
                f"Printer added to macOS!\n\n"
                f"Name: {local_name}\n"
                f"You can now print from any application.\n"
                f"Open System Settings → Printers to verify.",
                local_device=local_name,
            )

        return AttachResult(
            AttachStatus.ERROR,
            message=f"Failed to add printer: {r.stderr.strip()}\n\n"
                    f"Try manually:\n"
                    f"  System Settings → Printers → Add: {uri}"
        )

    def _attach_ipp_linux(self, server_ip: str, ipp_info: dict) -> AttachResult:
        cups_name = ipp_info.get("cups_name")
        if not cups_name:
            return AttachResult(AttachStatus.ERROR, message="No CUPS printer info")

        local_name = f"Loki-{cups_name}"
        uri = f"ipp://{server_ip}:631/printers/{cups_name}"

        r = _run(["lpadmin", "-p", local_name, "-v", uri, "-E", "-m", "everywhere"])
        if r.returncode == 0:
            return AttachResult(AttachStatus.ATTACHED, AttachMethod.IPP,
                                f"Printer added: {local_name}", local_name)

        return AttachResult(AttachStatus.ERROR, message=r.stderr.strip())

    def _attach_ipp_windows(self, server_ip: str, ipp_info: dict) -> AttachResult:
        cups_name = ipp_info.get("cups_name")
        if not cups_name:
            return AttachResult(AttachStatus.ERROR, message="No printer info")

        # Windows: Add network printer via PowerShell
        uri = f"http://{server_ip}:631/printers/{cups_name}"
        local_name = f"Loki-{cups_name}"

        ps_cmd = (
            f'Add-Printer -Name "{local_name}" '
            f'-PortName "{uri}" -DriverName "Microsoft IPP Class Driver"'
        )
        r = _run(["powershell", "-Command", ps_cmd])
        if r.returncode == 0:
            return AttachResult(
                AttachStatus.ATTACHED, AttachMethod.IPP,
                f"Printer added to Windows!\nName: {local_name}",
                local_device=local_name,
            )

        return AttachResult(
            AttachStatus.ATTACHED, AttachMethod.IPP,
            f"Add the printer manually in Windows Settings:\n"
            f"  Settings → Printers → Add Printer → {uri}",
            local_device=uri,
        )

    def _detach_ipp(self, printer_name: Optional[str]) -> AttachResult:
        if printer_name:
            if OS in ("Linux", "Darwin"):
                _run(["lpadmin", "-x", printer_name])
            elif OS == "Windows":
                _run(["powershell", "-Command",
                      f'Remove-Printer -Name "{printer_name}"'])
        return AttachResult(AttachStatus.DETACHED, message="Printer removed")

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _find_new_device(self) -> Optional[str]:
        time.sleep(1)
        for pattern in ("/dev/ttyUSB*", "/dev/ttyACM*", "/dev/usb/lp*"):
            matches = sorted(glob.glob(pattern))
            if matches:
                return matches[-1]
        return None
