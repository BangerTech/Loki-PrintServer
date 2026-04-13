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
import argparse
import platform
import sys
import threading
import time
import webbrowser
from typing import Optional

OS = platform.system()

try:
    import rumps
    HAS_RUMPS = True
except ImportError:
    HAS_RUMPS = False

try:
    import pystray
    from PIL import Image, ImageDraw
    HAS_PYSTRAY = True
except ImportError:
    HAS_PYSTRAY = False

from core.api_client import DeviceInfo, LokiAPIClient, ServerStatus
from core.config import LokiConfig, ServerEntry
from core.device_db import get_display_name
from core.discovery import DiscoveredServer, LokiDiscovery
from core.usbip_attach import AttachStatus, USBIPAttacher


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

    def try_connect(self) -> bool:
        try:
            c = LokiAPIClient(self.entry.host, self.entry.port)
            if c.check_health():
                self.client = c
                self.connected = True
                self.error = None
                return True
        except Exception as e:
            self.error = str(e)
        self.connected = False
        return False

    def refresh(self):
        if not self.connected or not self.client:
            return
        try:
            self.devices = self.client.list_devices()
            self.status = self.client.get_status()
        except Exception:
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
            self.attacher = USBIPAttacher()
            self.connections: dict[str, ServerConnection] = {}
            self.discovery = LokiDiscovery(on_found=self._on_server_discovered)

            # Build connections for all saved servers
            for entry in config.servers:
                self.connections[entry.host] = ServerConnection(entry)

            self.discovery.start()
            self._rebuild_menu()

            # Start background polling
            self._poll_timer = rumps.Timer(self._poll, 5)
            self._poll_timer.start()

            # Immediate first connect
            threading.Thread(target=self._connect_all, daemon=True).start()

        def _save_icon(self, connected: bool) -> str:
            img = make_tray_icon(connected)
            path = _os.path.join(_tempfile.gettempdir(),
                                 f"loki_icon_{'on' if connected else 'off'}.png")
            img.save(path)
            return path

        # ── Connection ────────────────────────────────────────────────────────

        def _connect_all(self):
            for conn in list(self.connections.values()):
                if not conn.connected:
                    conn.try_connect()
                    if conn.connected:
                        conn.refresh()
            self.root and self._schedule_rebuild()

        def _connect_server(self, entry: ServerEntry):
            def _do():
                conn = self.connections.setdefault(entry.host, ServerConnection(entry))
                ok = conn.try_connect()
                if ok:
                    conn.refresh()
                    rumps.notification(
                        "Loki-PrintServer",
                        f"Connected: {entry.name}",
                        f"{len(conn.devices)} device(s) found"
                    )
                else:
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
            if key in self.connections and self.connections[key].connected:
                return  # already connected

            # Auto-connect if in saved servers
            for entry in self.config.servers:
                if entry.host == server.ip:
                    self._connect_server(entry)
                    return

            # New server found — notify user
            if self.config.show_notifications:
                rumps.notification(
                    "Server found",
                    f"{server.name}",
                    f"Tap to add {server.ip}",
                )

        # ── Menu ─────────────────────────────────────────────────────────────

        def _schedule_rebuild(self):
            # rumps menu updates must happen on main thread via timer trick
            t = rumps.Timer(lambda _: (self._rebuild_menu(), t.stop()), 0.1)
            t.start()

        def _rebuild_menu(self):
            any_connected = any(c.connected for c in self.connections.values())
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
                        if conn.devices:
                            for dev in conn.devices:
                                mfr, prod, icon = get_display_name(
                                    dev.vendor_id, dev.product_id,
                                    dev.manufacturer, dev.product
                                )
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
            items.append(rumps.MenuItem("Quit Loki", callback=lambda _: self._quit()))

            self.menu = items

        # ── Actions ───────────────────────────────────────────────────────────

        def _toggle_device(self, dev: DeviceInfo, conn: ServerConnection):
            if dev.bus_id in self.attacher.get_attached():
                result = self.attacher.detach(dev.bus_id)
                rumps.notification("Detached", dev.display_name, "")
            else:
                self._share_and_attach(dev, conn)

        def _share_and_attach(self, dev: DeviceInfo, conn: ServerConnection):
            def _do():
                if conn.client:
                    conn.client.share_device(dev.bus_id)
                result = self.attacher.attach(conn.entry.host, dev.bus_id)
                if result.status == AttachStatus.ATTACHED:
                    msg = dev.display_name
                    if result.local_device:
                        msg += f" → {result.local_device}"
                    rumps.notification("Device Attached!", msg,
                                       "Now available as a local USB device.")
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
            if r.clicked == 1 and r.text.strip():
                ip = r.text.strip()
                entry = self.config.add_server(ip, 7576)
                self._connect_server(entry)

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
                changed = False
                for conn in list(self.connections.values()):
                    was = conn.connected
                    if not conn.connected:
                        conn.try_connect()
                    if conn.connected:
                        conn.refresh()
                    if conn.connected != was:
                        changed = True
                if changed:
                    self._schedule_rebuild()
                else:
                    self._schedule_rebuild()  # always refresh device list
            threading.Thread(target=_do, daemon=True).start()

        def _quit(self):
            self.discovery.stop()
            rumps.quit_application()


# ══════════════════════════════════════════════════════════════════════════════
#  pystray fallback (Windows + Linux)
# ══════════════════════════════════════════════════════════════════════════════

class LokiPystrayApp:
    def __init__(self, config: LokiConfig):
        self.config = config
        self.attacher = USBIPAttacher()
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
        for conn in self.connections.values():
            conn.try_connect()
            if conn.connected:
                conn.refresh()
        self._refresh_icon()

    def _on_server_discovered(self, server: DiscoveredServer):
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
                    for dev in conn.devices:
                        _, prod, icon = get_display_name(
                            dev.vendor_id, dev.product_id,
                            dev.manufacturer, dev.product
                        )
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
            if conn.client:
                conn.client.share_device(dev.bus_id)
            self.attacher.attach(conn.entry.host, dev.bus_id)
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

def main():
    parser = argparse.ArgumentParser(description="Loki-PrintServer Client")
    parser.add_argument("--server", "-s", help="Server IP (skips discovery)")
    parser.add_argument("--port", "-p", type=int, default=7576)
    parser.add_argument("--no-onboarding", action="store_true")
    args = parser.parse_args()

    config = LokiConfig.load()

    # Add CLI server if given
    if args.server:
        config.add_server(args.server, args.port)
        config.first_launch = False

    # Show onboarding on first launch
    if config.first_launch and not args.no_onboarding:
        from onboarding import OnboardingWindow

        def after_onboarding(updated_config: LokiConfig):
            _launch_tray(updated_config)

        OnboardingWindow(config=config, on_complete=after_onboarding)
    else:
        _launch_tray(config)


def _launch_tray(config: LokiConfig):
    if HAS_RUMPS and OS == "Darwin":
        app = LokiMenuBarApp(config)
        app.run()
    elif HAS_PYSTRAY:
        LokiPystrayApp(config)
    else:
        print("Install dependencies: pip install rumps pillow pystray")
        sys.exit(1)


if __name__ == "__main__":
    main()
