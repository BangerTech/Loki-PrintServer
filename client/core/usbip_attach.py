"""
Loki-Client — Platform-specific USB Device Attachment
Supports three methods depending on platform and device type:

  1. USB/IP  — Full USB passthrough (Linux native, Windows via usbip-win)
  2. Serial  — TCP-to-virtual-serial bridge (pure Python PTY, no socat needed)
  3. IPP     — Network printer via CUPS/Bonjour (macOS & Linux auto-discover)
"""
import glob
import logging
import os
import platform
import re
import select
import shutil
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional

log = logging.getLogger("loki.attach")

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


class _PtyBridge:
    """
    Pure-Python TCP → PTY bridge for macOS/Linux serial devices.
    No external tools (socat, etc.) required — uses only stdlib.

    Creates a PTY pair, symlinks the slave to a discoverable path,
    and forwards data between the PTY master and a TCP socket in a daemon thread.
    """

    def __init__(self):
        self._master_fd: Optional[int] = None
        self._slave_fd: Optional[int] = None
        self._sock: Optional[socket.socket] = None
        self._running = False
        self.device_path: Optional[str] = None
        self._links: list[str] = []

    def start(self, server_ip: str, tcp_port: int,
              link: str, fallback_link: Optional[str] = None) -> str:
        """
        Connect to server_ip:tcp_port and expose as a PTY.
        Tries `link` first (e.g. /dev/cu.loki-*), falls back to
        `fallback_link` (e.g. /tmp/tty.loki-*).
        Returns the path to use in cutting/printing software.
        """
        import pty as _pty
        self._master_fd, self._slave_fd = _pty.openpty()
        slave_name = os.ttyname(self._slave_fd)

        if OS == "Darwin":
            import termios
            attrs = termios.tcgetattr(self._slave_fd)
            attrs[4] = termios.B9600   # ispeed
            attrs[5] = termios.B9600   # ospeed
            attrs[2] |= termios.CLOCAL  # ignore modem control
            termios.tcsetattr(self._slave_fd, termios.TCSANOW, attrs)

        self.device_path = slave_name
        for candidate in [link, fallback_link]:
            if not candidate:
                continue
            try:
                if os.path.islink(candidate) or os.path.exists(candidate):
                    os.unlink(candidate)
                os.symlink(slave_name, candidate)
                self._links.append(candidate)
                if self.device_path == slave_name:
                    self.device_path = candidate
                log.info("PTY bridge: symlink %s → %s", candidate, slave_name)
            except OSError as e:
                log.debug("Cannot create symlink %s: %s", candidate, e)

        log.info("PTY bridge: slave=%s device=%s tcp=%s:%s",
                 slave_name, self.device_path, server_ip, tcp_port)

        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.settimeout(10)
        self._sock.connect((server_ip, tcp_port))
        self._sock.settimeout(None)
        self._running = True
        threading.Thread(target=self._loop, daemon=True, name="loki-pty-bridge").start()
        return self.device_path

    def _loop(self):
        sock_fd = self._sock.fileno()
        while self._running:
            try:
                r, _, _ = select.select([self._master_fd, sock_fd], [], [], 2.0)
                for fd in r:
                    if fd == self._master_fd:
                        data = os.read(self._master_fd, 4096)
                        self._sock.sendall(data)
                    else:
                        data = self._sock.recv(4096)
                        if not data:
                            log.info("PTY bridge: server closed connection")
                            self._running = False
                            break
                        os.write(self._master_fd, data)
            except Exception as e:
                log.debug("PTY bridge loop ended: %s", e)
                break
        self._cleanup()

    def stop(self):
        self._running = False

    def _cleanup(self):
        for fd in (self._master_fd, self._slave_fd):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
        self._master_fd = self._slave_fd = None
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        for lnk in self._links:
            if os.path.islink(lnk):
                try:
                    os.unlink(lnk)
                except OSError:
                    pass
        log.info("PTY bridge cleaned up: %s", self.device_path)


class DeviceAttacher:
    """Manages local attachment of remote USB devices using the best method."""

    def __init__(self):
        self._attached: dict[str, AttachResult] = {}  # bus_id -> result
        self._bridges: dict[str, _PtyBridge] = {}     # bus_id -> active bridge

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
                result = self._attach_serial_linux(server_ip, serial_info, bus_id)
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
                result = self._attach_serial_macos(server_ip, serial_info, bus_id)
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
                                "USB device attached via USB/IP", local)
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

    def _attach_serial_macos(self, server_ip: str, serial_info: dict,
                             bus_id: str = "") -> AttachResult:
        port = serial_info.get("port")
        if not port:
            return AttachResult(AttachStatus.ERROR, message="No serial port info from server")

        dev_link = f"/dev/cu.loki-plotter-{port}"
        tmp_link = f"/tmp/tty.loki-plotter-{port}"

        bridge = _PtyBridge()
        try:
            device_path = bridge.start(server_ip, int(port), dev_link, tmp_link)
        except Exception as e:
            log.error("PTY bridge start failed: %s", e)
            return AttachResult(AttachStatus.ERROR,
                                message=f"Serial bridge failed: {e}")

        if bus_id:
            self._bridges[bus_id] = bridge

        return AttachResult(
            AttachStatus.ATTACHED, AttachMethod.SERIAL,
            f"Plotter ready!\n\n"
            f"Port: {device_path}\n\n"
            f"Select 'Serial' in your cutting software and\n"
            f"choose this port from the dropdown.\n\n"
            f"Works with: FineCut, xfcut, Inkcut, Silhouette Studio, etc.",
            local_device=device_path,
        )

    def _attach_serial_linux(self, server_ip: str, serial_info: dict,
                             bus_id: str = "") -> AttachResult:
        port = serial_info.get("port")
        if not port:
            return AttachResult(AttachStatus.ERROR, message="No serial port info from server")

        dev_link = f"/dev/ttyLOKI{port}"
        tmp_link = f"/tmp/tty.loki-plotter-{port}"
        log.info("Serial attach (Linux): %s:%s", server_ip, port)

        bridge = _PtyBridge()
        try:
            device_path = bridge.start(server_ip, int(port), dev_link, tmp_link)
        except Exception as e:
            log.error("PTY bridge start failed: %s", e)
            return AttachResult(AttachStatus.ERROR, message=f"Serial bridge failed: {e}")

        if bus_id:
            self._bridges[bus_id] = bridge

        return AttachResult(
            AttachStatus.ATTACHED, AttachMethod.SERIAL,
            f"Virtual serial port: {device_path}",
            local_device=device_path,
        )

    def _attach_serial_windows(self, server_ip: str, serial_info: dict) -> AttachResult:
        port = serial_info.get("port")
        if not port:
            return AttachResult(AttachStatus.ERROR, message="No serial port info from server")

        # Windows: use com2tcp from com0com project, or just direct TCP
        com2tcp = shutil.which("com2tcp.exe")
        if com2tcp:
            proc = subprocess.Popen(
                [com2tcp, "\\\\.\\CNCB0", server_ip, str(port)],
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
        bridge = self._bridges.pop(bus_id, None)
        if bridge:
            bridge.stop()
            log.info("PTY bridge stopped for %s", bus_id)
        # Also kill any residual socat processes
        if OS in ("Linux", "Darwin"):
            _run(["pkill", "-f", "tty.loki"])
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
