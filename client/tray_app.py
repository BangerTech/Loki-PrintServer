"""
Loki-PrintServer — System Tray / Menu Bar Client
macOS: Native menu bar via rumps
Windows/Linux: System tray via pystray

Features:
  - Multi-server support
  - Auto-discovery via mDNS
  - Onboarding on first launch
  - Persistent server list
"""
from __future__ import annotations

import pathlib
import sys

# ── Early bootstrap for macOS .app bundles ──────────────────────────────────
# In a PyInstaller .app with console=False, stdout/stderr can be None.
# Redirect to a temp file so imports don't crash on print().
if getattr(sys, "frozen", False) and sys.platform == "darwin":
    _boot_log = pathlib.Path.home() / "Library" / "Logs" / "Loki-Client"
    _boot_log.mkdir(parents=True, exist_ok=True)
    _boot_fh = open(_boot_log / "startup.log", "a", encoding="utf-8")  # noqa: SIM115
    if sys.stdout is None:
        sys.stdout = _boot_fh
    if sys.stderr is None:
        sys.stderr = _boot_fh

# ── Now safe to import everything else ──────────────────────────────────────
import argparse  # noqa: E402
import os  # noqa: E402
import platform  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
import traceback  # noqa: E402
import webbrowser  # noqa: E402
from typing import Optional  # noqa: E402

from core.logger import setup_logging  # noqa: E402

log = setup_logging()

OS = platform.system()

try:
    import rumps
    HAS_RUMPS = True
except ImportError:
    HAS_RUMPS = False

from core.api_client import DeviceInfo, LokiAPIClient, ServerStatus  # noqa: E402
from core.config import LokiConfig, ServerEntry  # noqa: E402
from core.device_db import get_display_name  # noqa: E402
from core.discovery import DiscoveredServer, LokiDiscovery  # noqa: E402
from core.usbip_attach import AttachStatus, DeviceAttacher  # noqa: E402

try:
    import pystray
    from PIL import Image
    HAS_PYSTRAY = True
except ImportError:
    HAS_PYSTRAY = False


def make_tray_icon(connected: bool = False, size: int = 64) -> "Image.Image":
    """Load the real logo icon, fall back to generated if not found."""
    from PIL import Image, ImageDraw
    import pathlib

    # Try to load the real logo
    base = pathlib.Path(__file__).parent
    for candidate in [
        base / ".." / "assets" / f"icon_{size}.png",
        base / ".." / "assets" / "icon_64.png",
        base / "assets" / f"icon_{size}.png",
        base / "assets" / "icon_64.png",
    ]:
        try:
            img = Image.open(candidate.resolve()).resize((size, size), Image.LANCZOS)
            if not connected:
                img = img.convert("LA").convert("RGBA")  # greyscale when disconnected
            return img
        except Exception:
            continue

    # Fallback: generated icon
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    bg = (124, 92, 191, 255) if connected else (70, 70, 90, 220)
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=size // 5, fill=bg)
    pad = size // 5
    lw = max(size // 8, 4)
    draw.rectangle([pad, pad, pad + lw, size - pad], fill=(255, 255, 255, 255))
    draw.rectangle([pad, size - pad - lw, size - pad, size - pad], fill=(255, 255, 255, 255))
    return img


# ── Per-server connection state ────────────────────────────────────────────────

class ServerConnection:
    def __init__(self, entry: ServerEntry):
        self.entry = entry
        self.client: Optional[LokiAPIClient] = None
        self.devices: list[DeviceInfo] = []
        self.status: Optional[ServerStatus] = None
        self.connected = False
        self.error: Optional[str] = None

    def try_connect(self, retries: int = 3, delay: float = 2.0) -> bool:
        log.info("Connecting to %s:%s (retries=%d, delay=%.1fs)",
                 self.entry.host, self.entry.port, retries, delay)
        for attempt in range(retries):
            try:
                c = LokiAPIClient(self.entry.host, self.entry.port)
                if c.check_health():
                    self.client = c
                    self.connected = True
                    self.error = None
                    log.info("Connected to %s:%s (attempt %d/%d)",
                             self.entry.host, self.entry.port, attempt + 1, retries)
                    return True
                self.error = "Health check returned non-200"
                log.warning("Health check failed for %s:%s (attempt %d/%d)",
                            self.entry.host, self.entry.port, attempt + 1, retries)
            except Exception as e:
                self.error = str(e)
                log.warning("Connect attempt %d/%d to %s:%s failed: %s",
                            attempt + 1, retries, self.entry.host, self.entry.port, e)
            if attempt < retries - 1:
                time.sleep(delay)
        self.connected = False
        log.error("All %d connection attempts to %s:%s failed: %s",
                  retries, self.entry.host, self.entry.port, self.error)
        return False

    def refresh(self):
        if not self.connected or not self.client:
            return
        try:
            self.devices = self.client.list_devices()
            self.status = self.client.get_status()
            log.debug("Refreshed %s: %d devices", self.entry.host, len(self.devices))
        except Exception as e:
            log.error("Refresh failed for %s: %s", self.entry.host, e)
            self.connected = False
            self.client = None

    @property
    def label(self) -> str:
        if self.connected:
            shared = sum(1 for d in self.devices if d.is_shared)
            return f"{'🟢' if self.connected else '🔴'} {self.entry.name}  ({shared} shared)"
        return f"🔴 {self.entry.name}  (offline)"


# ══════════════════════════════════════════════════════════════════════════════
#  macOS Menu Bar App (rumps)
# ══════════════════════════════════════════════════════════════════════════════

if HAS_RUMPS:
    import os as _os
    import tempfile as _tempfile

    class LokiMenuBarApp(rumps.App):

        def __init__(self, config: LokiConfig):
            super().__init__("Loki-Client", quit_button=None)
            self.icon = self._save_icon(connected=False)
            self.config = config
            self.attacher = DeviceAttacher()
            self.connections: dict[str, ServerConnection] = {}
            self.discovery = LokiDiscovery(on_found=self._on_server_discovered)

            for entry in config.servers:
                self.connections[entry.host] = ServerConnection(entry)

            self.discovery.start()
            self._needs_rebuild = False
            self._rebuild_menu()

            self._poll_timer = rumps.Timer(self._poll, 5)
            self._poll_timer.start()

            # Dedicated main-thread timer that flushes rebuild requests from
            # background threads. rumps.Timer must fire on the main thread —
            # creating new timers from background threads (old _schedule_rebuild)
            # results in NSTimers that never fire, keeping the menu stuck on
            # the initial "Offline" state even after a successful connection.
            self._rebuild_timer = rumps.Timer(self._flush_rebuild, 0.5)
            self._rebuild_timer.start()

            threading.Thread(target=self._connect_all, daemon=True).start()

        def _save_icon(self, connected: bool) -> str:
            img = make_tray_icon(connected)
            path = _os.path.join(_tempfile.gettempdir(),
                                 f"loki_icon_{'on' if connected else 'off'}.png")
            img.save(path)
            return path

        # ── Connection ────────────────────────────────────────────────────────

        def _connect_all(self):
            log.info("Initial connection pass for %d server(s)", len(self.connections))
            time.sleep(1)
            for conn in list(self.connections.values()):
                if not conn.connected:
                    conn.try_connect()
                    if conn.connected:
                        conn.refresh()
            any_ok = any(c.connected for c in self.connections.values())
            log.info("Initial connection pass done — any_connected=%s", any_ok)
            self._schedule_rebuild()

        def _connect_server(self, entry: ServerEntry):
            def _do():
                log.info("Connecting to server %s (%s:%s)", entry.name, entry.host, entry.port)
                conn = self.connections.setdefault(entry.host, ServerConnection(entry))
                ok = conn.try_connect()
                if ok:
                    conn.refresh()
                    log.info("Server %s connected, %d device(s)", entry.name, len(conn.devices))
                    rumps.notification(
                        "Loki-PrintServer",
                        f"Connected: {entry.name}",
                        f"{len(conn.devices)} device(s) found"
                    )
                else:
                    log.warning("Server %s unreachable: %s", entry.name, conn.error)
                    rumps.notification(
                        "Loki-PrintServer",
                        f"Cannot reach {entry.name}",
                        f"{entry.host}:{entry.port}",
                        sound=False
                    )
                self._schedule_rebuild()
            threading.Thread(target=_do, daemon=True).start()

        def _on_server_discovered(self, server: DiscoveredServer):
            key = server.ip
            log.info("mDNS discovered server: %s (%s)", server.name, server.ip)
            if key in self.connections and self.connections[key].connected:
                log.debug("Already connected to %s, skipping", key)
                return

            for entry in self.config.servers:
                if entry.host == server.ip:
                    log.info("Auto-connecting to saved server %s", server.ip)
                    self._connect_server(entry)
                    return

            if self.config.show_notifications:
                rumps.notification(
                    "Server found",
                    f"{server.name}",
                    f"Tap to add {server.ip}",
                )

        # ── Menu ─────────────────────────────────────────────────────────────

        def _schedule_rebuild(self):
            # Set a flag — picked up by _flush_rebuild on the main thread.
            # Do NOT create new rumps.Timer here: timers created from background
            # threads are NSTimers not scheduled on the main run loop and never fire.
            self._needs_rebuild = True

        def _flush_rebuild(self, _=None):
            if self._needs_rebuild:
                self._needs_rebuild = False
                self._rebuild_menu()

        def _rebuild_menu(self):
            any_connected = any(c.connected for c in self.connections.values())
            log.debug("Rebuilding menu — %d server(s), any_connected=%s",
                      len(self.connections), any_connected)
            self.icon = self._save_icon(any_connected)
            self.title = ""

            items = []

            # ── Header ────────────────────────────────────────────────────────
            items.append(rumps.MenuItem("Loki-PrintServer  by BangerTECH",
                                        callback=None))
            items.append(None)

            if not self.connections:
                items.append(rumps.MenuItem("No servers configured", callback=None))
                items.append(rumps.MenuItem(
                    "  ＋ Add server…", callback=lambda _: self._add_server_dialog()
                ))
            else:
                # ── Per-server sections ───────────────────────────────────────
                for host, conn in self.connections.items():
                    status_icon = "🟢" if conn.connected else "🔴"
                    server_item = rumps.MenuItem(
                        f"{status_icon}  {conn.entry.name}  —  {host}:{conn.entry.port}"
                    )

                    if conn.connected:
                        peripherals = [d for d in conn.devices
                                       if not d.is_infrastructure]
                        if peripherals:
                            for dev in peripherals:
                                mfr, prod, icon = get_display_name(
                                    dev.vendor_id, dev.product_id,
                                    dev.manufacturer, dev.product
                                )
                                if dev.custom_name:
                                    prod = dev.custom_name
                                is_attached = dev.bus_id in self.attacher.get_attached()
                                shared_mark = " ✓" if is_attached else (" [shared]" if dev.is_shared else "")
                                label = f"  {icon}  {prod}{shared_mark}"
                                if dev.is_shared or is_attached:
                                    server_item.add(rumps.MenuItem(
                                        label,
                                        callback=lambda _, d=dev, c=conn: self._toggle_device(d, c)
                                    ))
                                else:
                                    server_item.add(rumps.MenuItem(
                                        label + "  →  Share & Attach",
                                        callback=lambda _, d=dev, c=conn: self._share_and_attach(d, c)
                                    ))
                        else:
                            server_item.add(rumps.MenuItem("  No USB devices", callback=None))

                        server_item.add(None)
                        server_item.add(rumps.MenuItem(
                            "  🌐 Open Dashboard",
                            callback=lambda _, h=host, p=conn.entry.port:
                                webbrowser.open(f"http://{h}:{p}")
                        ))
                        server_item.add(rumps.MenuItem(
                            "  ✕ Remove server",
                            callback=lambda _, h=host: self._remove_server(h)
                        ))
                    else:
                        server_item.add(rumps.MenuItem(
                            "  Offline — click to retry",
                            callback=lambda _, e=conn.entry: self._connect_server(e)
                        ))
                        server_item.add(rumps.MenuItem(
                            "  ✕ Remove",
                            callback=lambda _, h=host: self._remove_server(h)
                        ))

                    items.append(server_item)

                items.append(None)
                items.append(rumps.MenuItem(
                    "  ＋ Add another server…",
                    callback=lambda _: self._add_server_dialog()
                ))

            # ── Discovered but not saved ───────────────────────────────────
            new_found = [
                s for s in self.discovery.get_servers()
                if s.ip not in self.connections
            ]
            if new_found:
                items.append(None)
                items.append(rumps.MenuItem("Nearby servers:", callback=None))
                for s in new_found:
                    items.append(rumps.MenuItem(
                        f"  📡  {s.name}  ({s.ip})",
                        callback=lambda _, sv=s: self._quick_add(sv)
                    ))

            items.append(None)
            items.append(rumps.MenuItem("Quit Loki",
                                        callback=lambda _: _os._exit(0)))

            self.menu = items

        # ── Actions ───────────────────────────────────────────────────────────

        def _toggle_device(self, dev: DeviceInfo, conn: ServerConnection):
            log.info("Toggle device %s (bus=%s)", dev.display_name, dev.bus_id)
            if dev.bus_id in self.attacher.get_attached():
                self.attacher.detach(dev.bus_id)
                log.info("Detached %s", dev.bus_id)
                rumps.notification("Detached", dev.display_name, "")
            else:
                self._share_and_attach(dev, conn)

        def _share_and_attach(self, dev: DeviceInfo, conn: ServerConnection):
            def _do():
                log.info("Share & attach %s (bus=%s, host=%s)",
                         dev.display_name, dev.bus_id, conn.entry.host)
                fwd_info = dev.forward_info
                if conn.client:
                    conn.client.share_device(dev.bus_id)
                    fwd_info = conn.client.get_forward_info(dev.bus_id) or fwd_info
                log.debug("forward_info for %s: %s", dev.bus_id, fwd_info)
                result = self.attacher.attach(conn.entry.host, dev.bus_id,
                                              forward_info=fwd_info)
                log.info("Attach result: status=%s msg=%s dev=%s",
                         result.status, result.message, result.local_device)
                if result.status == AttachStatus.ATTACHED:
                    msg = dev.display_name
                    if result.local_device:
                        msg += f" → {result.local_device}"
                    rumps.notification("Device Attached!", msg,
                                       "Now available as a local device.")
                elif result.status == AttachStatus.UNSUPPORTED:
                    rumps.notification("macOS: USB/IP not available natively",
                                       "Install Lima for full support.",
                                       "brew install lima", sound=False)
                else:
                    rumps.notification("Attach failed", result.message[:80], "", sound=False)
                conn.refresh()
                self._schedule_rebuild()
            threading.Thread(target=_do, daemon=True).start()

        def _add_server_dialog(self):
            w = rumps.Window(
                message="Enter the IP address of your Loki-PrintServer:",
                title="Add Server",
                default_text="192.168.x.x",
                ok="Add", cancel="Cancel",
            )
            r = w.run()
            if r.clicked and r.text.strip():
                ip = r.text.strip()
                if ip != "192.168.x.x":
                    entry = self.config.add_server(ip, 7576)
                    self.connections[ip] = ServerConnection(entry)
                    self._connect_server(entry)
                    self._schedule_rebuild()

        def _quick_add(self, server: DiscoveredServer):
            entry = self.config.add_server(server.ip, server.api_port, server.name)
            self._connect_server(entry)

        def _remove_server(self, host: str):
            self.config.remove_server(host)
            self.connections.pop(host, None)
            self._rebuild_menu()

        # ── Poll ─────────────────────────────────────────────────────────────

        def _poll(self, _=None):
            def _do():
                for conn in list(self.connections.values()):
                    was_connected = conn.connected
                    if not conn.connected:
                        conn.try_connect(retries=1)
                    if conn.connected:
                        conn.refresh()
                    if conn.connected != was_connected:
                        log.info("Connection state changed: %s → %s (host=%s)",
                                 was_connected, conn.connected, conn.entry.host)
                self._schedule_rebuild()
            threading.Thread(target=_do, daemon=True).start()



# ══════════════════════════════════════════════════════════════════════════════
#  pystray fallback (Windows + Linux)
# ══════════════════════════════════════════════════════════════════════════════

class LokiPystrayApp:
    def __init__(self, config: LokiConfig):
        log.info("LokiPystrayApp init with %d server(s)", len(config.servers))
        self.config = config
        self.attacher = DeviceAttacher()
        self.connections: dict[str, ServerConnection] = {}
        self.discovery = LokiDiscovery(on_found=self._on_server_discovered)

        for entry in config.servers:
            self.connections[entry.host] = ServerConnection(entry)

        self.discovery.start()
        threading.Thread(target=self._connect_all, daemon=True).start()

        self._running = True
        self._icon = pystray.Icon(
            "loki-client",
            make_tray_icon(False),
            "Loki-PrintServer",
            menu=self._build_menu(),
        )
        threading.Thread(target=self._poll_loop, daemon=True).start()
        self._icon.run()

    def _connect_all(self):
        log.info("pystray: initial connection pass")
        for conn in self.connections.values():
            conn.try_connect()
            if conn.connected:
                conn.refresh()
        self._refresh_icon()

    def _on_server_discovered(self, server: DiscoveredServer):
        log.info("pystray: mDNS discovered %s (%s)", server.name, server.ip)
        for entry in self.config.servers:
            if entry.host == server.ip:
                if server.ip not in self.connections:
                    self.connections[server.ip] = ServerConnection(entry)
                conn = self.connections[server.ip]
                if not conn.connected:
                    conn.try_connect()
                    if conn.connected:
                        conn.refresh()
                        self._refresh_icon()
                return

    def _build_menu(self):
        items = [
            pystray.MenuItem("Loki-PrintServer  by BangerTECH", None, enabled=False),
            pystray.Menu.SEPARATOR,
        ]

        if not self.connections:
            items.append(pystray.MenuItem("No servers configured", None, enabled=False))
        else:
            for host, conn in self.connections.items():
                status = "🟢" if conn.connected else "🔴"
                label = f"{status} {conn.entry.name} ({host})"

                sub_items = []
                if conn.connected and conn.devices:
                    peripherals = [d for d in conn.devices
                                   if not d.is_infrastructure]
                    for dev in peripherals:
                        _, prod, icon = get_display_name(
                            dev.vendor_id, dev.product_id,
                            dev.manufacturer, dev.product
                        )
                        if dev.custom_name:
                            prod = dev.custom_name
                        is_att = dev.bus_id in self.attacher.get_attached()
                        mark = " ✓" if is_att else ""
                        sub_items.append(pystray.MenuItem(
                            f"{icon} {prod}{mark}",
                            lambda _, d=dev, c=conn: self._toggle_device(d, c)
                        ))

                sub_items.append(pystray.Menu.SEPARATOR)
                sub_items.append(pystray.MenuItem(
                    "Open Dashboard",
                    lambda _, h=host, p=conn.entry.port:
                        webbrowser.open(f"http://{h}:{p}")
                ))
                items.append(pystray.MenuItem(label, pystray.Menu(*sub_items)))

        items.append(pystray.Menu.SEPARATOR)
        items.append(pystray.MenuItem("Add server…", self._add_server_dialog))
        items.append(pystray.Menu.SEPARATOR)
        items.append(pystray.MenuItem("Quit", self._quit))

        return pystray.Menu(*items)

    def _refresh_icon(self):
        connected = any(c.connected for c in self.connections.values())
        self._icon.icon = make_tray_icon(connected)
        self._icon.menu = self._build_menu()

    def _toggle_device(self, dev: DeviceInfo, conn: ServerConnection):
        if dev.bus_id in self.attacher.get_attached():
            self.attacher.detach(dev.bus_id)
        else:
            fwd_info = dev.forward_info
            if conn.client:
                conn.client.share_device(dev.bus_id)
                fwd_info = conn.client.get_forward_info(dev.bus_id) or fwd_info
            self.attacher.attach(conn.entry.host, dev.bus_id,
                                 forward_info=fwd_info)
        conn.refresh()
        self._refresh_icon()

    def _add_server_dialog(self):
        import tkinter as tk
        from tkinter import simpledialog
        root = tk.Tk()
        root.withdraw()
        ip = simpledialog.askstring("Add Server", "Server IP:", initialvalue="192.168.x.x")
        root.destroy()
        if ip:
            entry = self.config.add_server(ip.strip(), 7576)
            conn = ServerConnection(entry)
            self.connections[ip.strip()] = conn
            threading.Thread(target=lambda: (conn.try_connect(), conn.refresh(),
                                              self._refresh_icon()), daemon=True).start()

    def _poll_loop(self):
        while self._running:
            for conn in list(self.connections.values()):
                if not conn.connected:
                    conn.try_connect()
                if conn.connected:
                    conn.refresh()
            self._refresh_icon()
            time.sleep(5)

    def _quit(self):
        self._running = False
        self.discovery.stop()
        self._icon.stop()


# ══════════════════════════════════════════════════════════════════════════════
#  Entry Point
# ══════════════════════════════════════════════════════════════════════════════

def _acquire_lock() -> bool:
    """Prevent multiple instances via lock file. Returns True if lock acquired."""
    try:
        import fcntl
    except ImportError:
        return True  # Windows — skip lock
    lock_path = pathlib.Path.home() / ".config" / "loki-printserver" / ".lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        global _lock_fd  # noqa: PLW0603
        _lock_fd = open(lock_path, "w")  # noqa: SIM115
        fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_fd.write(str(os.getpid()))
        _lock_fd.flush()
        return True
    except (OSError, IOError):
        return False


_lock_fd = None


def main():
    parser = argparse.ArgumentParser(description="Loki-PrintServer Client")
    parser.add_argument("--server", "-s", help="Server IP (skips discovery)")
    parser.add_argument("--port", "-p", type=int, default=7576)
    parser.add_argument("--no-onboarding", action="store_true")
    args = parser.parse_args()

    from core.logger import get_log_path
    log.info("========== Loki-Client starting ==========")
    log.info("Platform: %s  Python: %s  Frozen: %s",
             OS, sys.version.split()[0], getattr(sys, "frozen", False))
    log.info("Log file: %s", get_log_path())

    if not _acquire_lock():
        log.warning("Another instance is already running, exiting.")
        sys.exit(0)

    config = LokiConfig.load()
    log.info("Config loaded: first_launch=%s  servers=%s",
             config.first_launch, [s.host for s in config.servers])

    if args.server:
        config.add_server(args.server, args.port)
        config.first_launch = False
        log.info("CLI server added: %s:%s", args.server, args.port)

    need_onboarding = (config.first_launch or not config.servers) and not args.no_onboarding
    log.info("Onboarding needed: %s", need_onboarding)

    if need_onboarding:
        from onboarding import OnboardingWindow

        def after_onboarding(updated_config: LokiConfig):
            log.info("Onboarding completed, launching tray with %d server(s)",
                     len(updated_config.servers))
            _launch_tray(updated_config)

        OnboardingWindow(config=config, on_complete=after_onboarding)
    else:
        _launch_tray(config)


def _launch_tray(config: LokiConfig):
    import signal
    signal.signal(signal.SIGTERM, lambda *_: os._exit(0))

    log.info("Launching tray app (OS=%s, HAS_RUMPS=%s, HAS_PYSTRAY=%s)",
             OS, HAS_RUMPS, HAS_PYSTRAY)

    if HAS_RUMPS and OS == "Darwin":
        try:
            from AppKit import NSApp, NSApplicationActivationPolicyAccessory
            NSApp.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
            log.debug("macOS dock icon hidden")
        except Exception:
            pass
        app = LokiMenuBarApp(config)
        app.run()
    elif HAS_PYSTRAY:
        LokiPystrayApp(config)
    else:
        log.error("No tray framework available")
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log.critical("CRASH: %s", traceback.format_exc())
        raise
