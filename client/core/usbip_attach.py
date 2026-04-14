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
import sys
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
        import tty as _tty
        import termios as _termios
        self._master_fd, self._slave_fd = _pty.openpty()
        slave_name = os.ttyname(self._slave_fd)

        _tty.setraw(self._master_fd)
        _tty.setraw(self._slave_fd)

        attrs = _termios.tcgetattr(self._slave_fd)
        attrs[4] = _termios.B9600   # ispeed
        attrs[5] = _termios.B9600   # ospeed
        attrs[2] |= _termios.CLOCAL
        _termios.tcsetattr(self._slave_fd, _termios.TCSANOW, attrs)

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


class _USBHelperBridge:
    """
    Launches the loki-usb-helper Swift binary to create a real virtual USB
    CDC-ACM device on macOS. The device appears as /dev/cu.usbmodem* and is
    recognized by IOKit-based applications (FineCut, xfcut, etc.).

    Requires: macOS with SIP kext-signing disabled, ad-hoc signed binary.
    """

    _starting = False  # class-level guard against concurrent starts

    def __init__(self):
        self._proc: Optional[subprocess.Popen] = None
        self.device_path: Optional[str] = None

    @staticmethod
    def find_helper() -> Optional[str]:
        """Locate the loki-usb-helper binary."""
        candidates = []
        if getattr(sys, "frozen", False):
            bundle_dir = os.path.dirname(sys.executable)
            candidates.append(os.path.join(bundle_dir, "loki-usb-helper"))
            candidates.append(os.path.join(bundle_dir, "..", "Frameworks", "loki-usb-helper"))
            candidates.append(os.path.join(bundle_dir, "..", "Resources", "loki-usb-helper"))
        script_dir = os.path.dirname(os.path.abspath(__file__))
        candidates.append(os.path.join(script_dir, "..", "mac", "usb-helper", "loki-usb-helper"))
        if shutil.which("loki-usb-helper"):
            candidates.append(shutil.which("loki-usb-helper"))
        for c in candidates:
            if c and os.path.isfile(c) and os.access(c, os.X_OK):
                return os.path.realpath(c)
        return None

    def _get_log_path(self) -> str:
        log_dir = os.path.expanduser("~/Library/Logs/Loki-Client")
        os.makedirs(log_dir, exist_ok=True)
        return os.path.join(log_dir, "usb-helper.log")

    def start(self, server_ip: str, tcp_port: int,
              manufacturer: str = "", product_name: str = "") -> Optional[str]:
        if _USBHelperBridge._starting:
            log.debug("loki-usb-helper already starting, skipping duplicate")
            return None

        helper = self.find_helper()
        if not helper:
            log.info("loki-usb-helper not found, skipping virtual USB")
            return None

        existing = set(glob.glob("/dev/cu.usbmodem*"))
        _USBHelperBridge._starting = True
        helper_log = self._get_log_path()

        cmd = [helper, server_ip, str(tcp_port)]
        if manufacturer or product_name:
            cmd += [manufacturer or "BangerTECH",
                    product_name or "Loki Virtual Plotter"]
            log.info("USB identity: %s / %s", manufacturer, product_name)

        # Try 1: direct launch (works when AMFI is disabled)
        log.info("Starting loki-usb-helper: %s", " ".join(cmd))
        try:
            log_fh = open(helper_log, "w")
            self._proc = subprocess.Popen(
                cmd, stdout=log_fh, stderr=log_fh,
            )
        except Exception as e:
            log.warning("loki-usb-helper direct launch failed: %s", e)
            self._proc = None

        if self._proc:
            result = self._wait_for_device(existing, timeout=5)
            if result:
                return result
            if self._proc and self._proc.poll() is not None:
                self._read_helper_log(helper_log)
                log.info("Direct launch failed, trying with admin privileges...")
                self._proc = None
            elif self._proc and self._proc.poll() is None:
                result = self._wait_for_device(existing, timeout=10)
                if result:
                    return result

        # Try 2: sudo via osascript (password dialog)
        if not self.device_path:
            if self._proc and self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
                self._proc = None
            log.info("Requesting admin privileges for loki-usb-helper...")
            try:
                escaped = helper.replace('"', '\\\\"')
                extra_args = ""
                if manufacturer or product_name:
                    mfr = (manufacturer or "BangerTECH").replace('"', '\\\\"')
                    prd = (product_name or "Loki Virtual Plotter").replace('"', '\\\\"')
                    extra_args = f' \\"{mfr}\\" \\"{prd}\\"'
                osa_cmd = (
                    f'do shell script "\\"{escaped}\\" {server_ip} {tcp_port}'
                    f'{extra_args} '
                    f'> \\"{helper_log}\\" 2>&1 &" '
                    f'with administrator privileges'
                )
                self._proc = subprocess.Popen(
                    ["osascript", "-e", osa_cmd],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                )
            except Exception as e:
                log.warning("loki-usb-helper sudo launch failed: %s", e)
                _USBHelperBridge._starting = False
                return None

            result = self._wait_for_device(existing, timeout=30)
            if result:
                return result
            self._read_helper_log(helper_log)

        log.warning("loki-usb-helper: no /dev/cu.usbmodem* appeared")
        self.stop()
        _USBHelperBridge._starting = False
        return None

    def _wait_for_device(self, existing: set, timeout: int = 15) -> Optional[str]:
        for _ in range(timeout * 2):
            time.sleep(0.5)
            if self._proc and self._proc.poll() is not None:
                rc = self._proc.returncode
                if rc != 0:
                    return None
            current = set(glob.glob("/dev/cu.usbmodem*"))
            new_devices = current - existing
            if new_devices:
                self.device_path = sorted(new_devices)[0]
                log.info("Virtual USB device appeared: %s", self.device_path)
                _USBHelperBridge._starting = False
                return self.device_path
        return None

    def _read_helper_log(self, path: str):
        try:
            with open(path) as f:
                content = f.read().strip()
            if content:
                log.warning("loki-usb-helper output: %s", content[:500])
        except Exception:
            pass

    def stop(self):
        # Kill helper — try regular signal first, then pkill for root processes
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        try:
            subprocess.run(["pkill", "-f", "loki-usb-helper"],
                           timeout=3, capture_output=True)
        except Exception:
            pass
        self._proc = None
        self.device_path = None


class DeviceAttacher:
    """Manages local attachment of remote USB devices using the best method."""

    def __init__(self):
        self._attached: dict[str, AttachResult] = {}  # bus_id -> result
        self._bridges: dict[str, _PtyBridge] = {}     # bus_id -> active bridge
        self._usb_helpers: dict[str, _USBHelperBridge] = {}  # bus_id -> USB helper

    def attach(self, server_ip: str, bus_id: str,
               forward_info: Optional[dict] = None,
               usb_info: Optional[dict] = None) -> AttachResult:
        """
        Attach a remote USB device. Tries the best method for this platform:
          - Linux/Windows: USB/IP first, then serial fallback
          - macOS: Serial first, then IPP for printers

        usb_info: optional dict with vendor_id, product_id, manufacturer,
                  product from the original USB device (used for VID/PID spoofing).
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
            if has_serial:
                result = self._attach_serial_macos(server_ip, serial_info, bus_id,
                                                   usb_info=usb_info)
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

    def is_attaching(self) -> bool:
        """True if a USB helper is currently waiting for user input (password dialog)."""
        return _USBHelperBridge._starting

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
                             bus_id: str = "",
                             usb_info: Optional[dict] = None) -> AttachResult:
        port = serial_info.get("port")
        if not port:
            return AttachResult(AttachStatus.ERROR, message="No serial port info from server")

        info = usb_info or {}
        # Try virtual USB device first (appears as real /dev/cu.usbmodem*)
        usb_helper = _USBHelperBridge()
        device_path = usb_helper.start(
            server_ip, int(port),
            manufacturer=info.get("manufacturer", ""),
            product_name=info.get("product", ""),
        )
        if device_path:
            if bus_id:
                self._usb_helpers[bus_id] = usb_helper
            return AttachResult(
                AttachStatus.ATTACHED, AttachMethod.SERIAL,
                f"Plotter ready! (Virtual USB)\n\n"
                f"Port: {device_path}\n\n"
                f"The device appears as a real USB serial port.\n"
                f"Select it in your cutting software's port list.\n\n"
                f"Works with: FineCut, xfcut, Inkcut, Silhouette Studio, etc.",
                local_device=device_path,
            )

        log.info("Virtual USB not available, falling back to PTY bridge")

        # Fallback: PTY bridge (works but not visible in IOKit)
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
        usb_helper = self._usb_helpers.pop(bus_id, None)
        if usb_helper:
            usb_helper.stop()
            log.info("USB helper stopped for %s", bus_id)
        bridge = self._bridges.pop(bus_id, None)
        if bridge:
            bridge.stop()
            log.info("PTY bridge stopped for %s", bus_id)
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
