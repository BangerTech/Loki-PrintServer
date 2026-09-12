"""
Loki-PrintServer Client
Cross-platform GUI to connect to a Loki-PrintServer and attach USB devices.

Usage:
    python loki_client.py
    python loki_client.py --server 192.168.1.100 --port 7576
"""
import argparse
import platform
import threading
import time
import tkinter as tk
from tkinter import messagebox

try:
    import customtkinter as ctk
    HAS_CTK = True
except ImportError:
    HAS_CTK = False

from core.api_client import DeviceInfo, LokiAPIClient, ServerStatus
from core.discovery import DiscoveredServer, LokiDiscovery
from core.usbip_attach import AttachStatus, USBIPAttacher

OS = platform.system()

# ─── Colors ───────────────────────────────────────────────────────────────────
COLORS = {
    "bg":       "#0f0f13",
    "surface":  "#1a1a24",
    "card":     "#1e1e2e",
    "border":   "#2a2a3a",
    "accent":   "#7c5cbf",
    "accent2":  "#a87cdf",
    "text":     "#e8e8f0",
    "muted":    "#888899",
    "success":  "#4caf7d",
    "warning":  "#f0a030",
    "danger":   "#e05050",
}

DEVICE_ICONS = {
    "Printer":          "🖨",
    "Mass Storage":     "💾",
    "Hub":              "⬡",
    "HID":              "⌨",
    "Communications":   "⬡",
    "Image":            "📷",
    "Vendor Specific":  "⚙",
}


class LokiClientApp:
    def __init__(self, initial_server: str | None = None, initial_port: int = 7576):
        self.api: LokiAPIClient | None = None
        self.attacher = USBIPAttacher()
        self.discovery = LokiDiscovery(
            on_found=self._on_server_discovered,
            on_lost=self._on_server_lost,
        )
        self._devices: list[DeviceInfo] = []
        self._status: ServerStatus | None = None
        self._poll_thread: threading.Thread | None = None
        self._running = False

        self._build_ui()

        if initial_server:
            self._connect_to(initial_server, initial_port)
        else:
            self.discovery.start()
            self._update_status_label("Searching for servers on local network...", COLORS["muted"])

        self._running = True
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.mainloop()

    # ── UI Construction ────────────────────────────────────────────────────────

    def _build_ui(self):
        if HAS_CTK:
            ctk.set_appearance_mode("dark")
            ctk.set_default_color_theme("dark-blue")
            self.root = ctk.CTk()
        else:
            self.root = tk.Tk()

        self.root.title("Loki-PrintServer Client")
        self.root.geometry("820x600")
        self.root.minsize(640, 480)
        self.root.configure(bg=COLORS["bg"])

        self._set_icon()
        self._build_header()
        self._build_server_bar()
        self._build_device_panel()
        self._build_footer()

    def _set_icon(self):
        try:
            # Create a simple L icon programmatically
            img = tk.PhotoImage(width=32, height=32)
            self.root.iconphoto(True, img)
        except Exception:
            pass

    def _build_header(self):
        header = tk.Frame(self.root, bg="#1a1a2e", height=56)
        header.pack(fill=tk.X)
        header.pack_propagate(False)

        logo = tk.Label(header, text="L", bg=COLORS["accent"], fg="white",
                        font=("Segoe UI", 18, "bold"), width=2, height=1)
        logo.pack(side=tk.LEFT, padx=16, pady=8)

        title = tk.Label(header, text="Loki-PrintServer", bg="#1a1a2e",
                         fg=COLORS["text"], font=("Segoe UI", 14, "bold"))
        title.pack(side=tk.LEFT, pady=8)

        version = tk.Label(header, text="v1.0.0", bg="#1a1a2e",
                           fg=COLORS["muted"], font=("Segoe UI", 9))
        version.pack(side=tk.LEFT, padx=6, pady=8)

        self.conn_dot = tk.Label(header, text="●", bg="#1a1a2e",
                                  fg=COLORS["danger"], font=("Segoe UI", 12))
        self.conn_dot.pack(side=tk.RIGHT, padx=16)

    def _build_server_bar(self):
        bar = tk.Frame(self.root, bg=COLORS["surface"], pady=10)
        bar.pack(fill=tk.X)

        tk.Label(bar, text="Server:", bg=COLORS["surface"], fg=COLORS["muted"],
                 font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(16, 4))

        self.server_var = tk.StringVar(value="")
        self.server_entry = tk.Entry(bar, textvariable=self.server_var,
                                     bg=COLORS["card"], fg=COLORS["text"],
                                     insertbackground=COLORS["text"],
                                     relief=tk.FLAT, font=("Segoe UI", 10),
                                     width=22)
        self.server_entry.pack(side=tk.LEFT, padx=4, ipady=4)

        tk.Label(bar, text="Port:", bg=COLORS["surface"], fg=COLORS["muted"],
                 font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(8, 4))
        self.port_var = tk.StringVar(value="7576")
        self.port_entry = tk.Entry(bar, textvariable=self.port_var,
                                   bg=COLORS["card"], fg=COLORS["text"],
                                   insertbackground=COLORS["text"],
                                   relief=tk.FLAT, font=("Segoe UI", 10), width=6)
        self.port_entry.pack(side=tk.LEFT, padx=4, ipady=4)

        self.connect_btn = tk.Button(bar, text="Connect", command=self._on_connect,
                                     bg=COLORS["accent"], fg="white",
                                     relief=tk.FLAT, font=("Segoe UI", 9, "bold"),
                                     cursor="hand2", padx=12, pady=4)
        self.connect_btn.pack(side=tk.LEFT, padx=8)

        # Discovered servers dropdown
        self.discovered_var = tk.StringVar(value="Auto-discovered servers")
        self.disc_menu = tk.OptionMenu(bar, self.discovered_var, "Scanning...")
        self.disc_menu.configure(bg=COLORS["card"], fg=COLORS["text"],
                                  relief=tk.FLAT, font=("Segoe UI", 9),
                                  highlightthickness=0)
        self.disc_menu.pack(side=tk.LEFT, padx=8)
        self.discovered_var.trace("w", self._on_server_selected)

        self.status_label = tk.Label(bar, text="", bg=COLORS["surface"],
                                      fg=COLORS["muted"], font=("Segoe UI", 9))
        self.status_label.pack(side=tk.RIGHT, padx=16)

    def _build_device_panel(self):
        self.device_frame = tk.Frame(self.root, bg=COLORS["bg"])
        self.device_frame.pack(fill=tk.BOTH, expand=True, padx=16, pady=12)

        # Section header
        hdr = tk.Frame(self.device_frame, bg=COLORS["bg"])
        hdr.pack(fill=tk.X, pady=(0, 8))

        tk.Label(hdr, text="USB Devices", bg=COLORS["bg"],
                 fg=COLORS["accent2"], font=("Segoe UI", 11, "bold")).pack(side=tk.LEFT)

        self.refresh_btn = tk.Button(hdr, text="↻ Refresh", command=self._refresh_devices,
                                      bg=COLORS["surface"], fg=COLORS["muted"],
                                      relief=tk.FLAT, font=("Segoe UI", 9),
                                      cursor="hand2", padx=8, pady=3)
        self.refresh_btn.pack(side=tk.RIGHT)

        # Scrollable device list
        canvas = tk.Canvas(self.device_frame, bg=COLORS["bg"], highlightthickness=0)
        scrollbar = tk.Scrollbar(self.device_frame, orient=tk.VERTICAL, command=canvas.yview)
        self.scroll_frame = tk.Frame(canvas, bg=COLORS["bg"])
        self.scroll_frame.bind("<Configure>",
                                lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.scroll_frame, anchor=tk.NW)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    def _build_footer(self):
        footer = tk.Frame(self.root, bg=COLORS["surface"], height=28)
        footer.pack(fill=tk.X, side=tk.BOTTOM)
        footer.pack_propagate(False)

        self.footer_label = tk.Label(footer, text=f"OS: {OS}  |  Loki-PrintServer Client",
                                      bg=COLORS["surface"], fg=COLORS["muted"],
                                      font=("Segoe UI", 8))
        self.footer_label.pack(side=tk.LEFT, padx=12, pady=5)

        if OS == "Darwin":
            tk.Label(footer, text="⚠ macOS: Limited USB/IP support",
                     bg=COLORS["surface"], fg=COLORS["warning"],
                     font=("Segoe UI", 8)).pack(side=tk.RIGHT, padx=12, pady=5)

    # ── Device Cards ──────────────────────────────────────────────────────────

    def _render_devices(self):
        for widget in self.scroll_frame.winfo_children():
            widget.destroy()

        if not self._devices:
            msg = "No devices found" if self.api else "Not connected to a server"
            tk.Label(self.scroll_frame, text=msg, bg=COLORS["bg"],
                     fg=COLORS["muted"], font=("Segoe UI", 10),
                     pady=40).pack()
            return

        for dev in self._devices:
            self._make_device_card(dev)

    def _make_device_card(self, dev: DeviceInfo):
        is_attached = dev.bus_id in self.attacher.get_attached()

        card = tk.Frame(self.scroll_frame, bg=COLORS["card"],
                        relief=tk.FLAT, pady=12, padx=14)
        card.pack(fill=tk.X, pady=4, padx=2)

        # Icon
        icon_char = DEVICE_ICONS.get(dev.device_class, "🔌")
        tk.Label(card, text=icon_char, bg=COLORS["card"],
                 font=("Segoe UI", 20), width=2).pack(side=tk.LEFT, padx=(0, 10))

        # Info
        info = tk.Frame(card, bg=COLORS["card"])
        info.pack(side=tk.LEFT, fill=tk.X, expand=True)

        name_color = COLORS["success"] if dev.is_shared else COLORS["text"]
        tk.Label(info, text=dev.display_name, bg=COLORS["card"],
                 fg=name_color, font=("Segoe UI", 10, "bold"),
                 anchor=tk.W).pack(fill=tk.X)

        meta = f"{dev.vendor_id}:{dev.product_id}  ·  Bus {dev.bus_id}"
        if dev.manufacturer:
            meta = f"{dev.manufacturer}  ·  " + meta
        if dev.device_class:
            meta += f"  ·  {dev.device_class}"
        tk.Label(info, text=meta, bg=COLORS["card"],
                 fg=COLORS["muted"], font=("Courier", 8),
                 anchor=tk.W).pack(fill=tk.X)

        # Status badge
        badge_frame = tk.Frame(card, bg=COLORS["card"])
        badge_frame.pack(side=tk.RIGHT, padx=8)

        if dev.is_shared:
            tk.Label(badge_frame, text="● Shared", bg=COLORS["card"],
                     fg=COLORS["success"], font=("Segoe UI", 8, "bold")).pack()
        else:
            tk.Label(badge_frame, text="Idle", bg=COLORS["card"],
                     fg=COLORS["muted"], font=("Segoe UI", 8)).pack()

        # Connect/Disconnect button (only if device is shared on server)
        if dev.is_shared:
            if is_attached:
                btn = tk.Button(badge_frame, text="Detach", cursor="hand2",
                                bg=COLORS["danger"], fg="white",
                                relief=tk.FLAT, font=("Segoe UI", 8, "bold"),
                                padx=8, pady=3,
                                command=lambda d=dev: self._detach_device(d))
            else:
                btn = tk.Button(badge_frame, text="Attach Here", cursor="hand2",
                                bg=COLORS["accent"], fg="white",
                                relief=tk.FLAT, font=("Segoe UI", 8, "bold"),
                                padx=8, pady=3,
                                command=lambda d=dev: self._attach_device(d))
            btn.pack(pady=(4, 0))

    # ── Event Handlers ────────────────────────────────────────────────────────

    def _on_connect(self):
        host = self.server_var.get().strip()
        if not host:
            messagebox.showwarning("No Server", "Please enter a server address.")
            return
        try:
            port = int(self.port_var.get())
        except ValueError:
            messagebox.showerror("Invalid Port", "Port must be a number.")
            return
        self._connect_to(host, port)

    def _connect_to(self, host: str, port: int):
        self._update_status_label(f"Connecting to {host}:{port}...", COLORS["muted"])
        self.server_var.set(host)
        self.port_var.set(str(port))

        def _do():
            if self.api:
                self.api.close()
            self.api = LokiAPIClient(host, port)
            if self.api.check_health():
                self.root.after(0, lambda: self._on_connected(host, port))
            else:
                self.root.after(0, lambda: self._on_connect_failed(host, port))

        threading.Thread(target=_do, daemon=True).start()

    def _on_connected(self, host: str, port: int):
        self.conn_dot.configure(fg=COLORS["success"])
        self._update_status_label(f"Connected: {host}:{port}", COLORS["success"])
        self._refresh_devices()

    def _on_connect_failed(self, host: str, port: int):
        self.conn_dot.configure(fg=COLORS["danger"])
        self._update_status_label(f"Cannot reach {host}:{port}", COLORS["danger"])
        messagebox.showerror("Connection Failed",
                             f"Could not connect to Loki-PrintServer at {host}:{port}.\n\n"
                             "Make sure the server is running and the address is correct.")

    def _on_server_discovered(self, server: DiscoveredServer):
        self.root.after(0, lambda: self._update_discovery_menu())
        if not self.api:
            self.root.after(0, lambda: self._update_status_label(
                f"Found: {server} — click to connect", COLORS["accent2"]))

    def _on_server_lost(self, server: DiscoveredServer):
        self.root.after(0, lambda: self._update_discovery_menu())

    def _update_discovery_menu(self):
        servers = self.discovery.get_servers()
        menu = self.disc_menu["menu"]
        menu.delete(0, tk.END)
        if not servers:
            menu.add_command(label="No servers found", state=tk.DISABLED)
        for s in servers:
            label = str(s)
            menu.add_command(label=label,
                             command=lambda sv=s: self._select_discovered(sv))

    def _select_discovered(self, server: DiscoveredServer):
        self.server_var.set(server.ip)
        self.port_var.set(str(server.api_port))
        self._connect_to(server.ip, server.api_port)

    def _on_server_selected(self, *args):
        pass  # handled by _select_discovered

    def _refresh_devices(self):
        if not self.api:
            return
        def _do():
            devices = self.api.list_devices()
            self.root.after(0, lambda: self._set_devices(devices))
        threading.Thread(target=_do, daemon=True).start()

    def _set_devices(self, devices: list[DeviceInfo]):
        self._devices = devices
        self._render_devices()

    def _attach_device(self, dev: DeviceInfo):
        if not self.api:
            return
        server_host = self.server_var.get().strip()

        def _do():
            # Tell server to share (if not already)
            ok, msg = self.api.share_device(dev.bus_id)
            # Attach locally
            result = self.attacher.attach(server_host, dev.bus_id)
            self.root.after(0, lambda: self._show_attach_result(result, dev))

        threading.Thread(target=_do, daemon=True).start()

    def _show_attach_result(self, result, dev: DeviceInfo):
        if result.status == AttachStatus.ATTACHED:
            msg = f"Device '{dev.display_name}' is now attached!\n"
            if result.local_device:
                msg += f"\nLocal device: {result.local_device}"
            msg += "\n\nYour OS now sees it as a locally connected USB device."
            messagebox.showinfo("Device Attached", msg)
        elif result.status == AttachStatus.UNSUPPORTED:
            messagebox.showwarning("Platform Not Supported", result.message)
        else:
            messagebox.showerror("Attach Failed", result.message)
        self._refresh_devices()

    def _detach_device(self, dev: DeviceInfo):
        def _do():
            self.attacher.detach(dev.bus_id)
            self.root.after(0, lambda: self._refresh_devices())
        threading.Thread(target=_do, daemon=True).start()

    def _update_status_label(self, text: str, color: str):
        self.status_label.configure(text=text, fg=color)

    # ── Background Polling ────────────────────────────────────────────────────

    def _poll_loop(self):
        while self._running:
            if self.api:
                try:
                    devices = self.api.list_devices()
                    status = self.api.get_status()
                    self.root.after(0, lambda d=devices, s=status: self._on_poll(d, s))
                except Exception:
                    self.root.after(0, lambda: self.conn_dot.configure(fg=COLORS["danger"]))
            time.sleep(5)

    def _on_poll(self, devices: list[DeviceInfo], status: ServerStatus | None):
        self._devices = devices
        self._status = status
        self._render_devices()
        if status:
            self.footer_label.configure(
                text=f"OS: {OS}  ·  Server CPU: {status.cpu_percent:.0f}%  ·  "
                     f"RAM: {status.memory_used_mb}/{status.memory_total_mb} MB  ·  "
                     f"Shared: {status.shared_device_count}"
            )

    def _on_close(self):
        self._running = False
        self.discovery.stop()
        if self.api:
            self.api.close()
        self.root.destroy()


def main():
    parser = argparse.ArgumentParser(description="Loki-PrintServer Client")
    parser.add_argument("--server", "-s", help="Server IP or hostname")
    parser.add_argument("--port", "-p", type=int, default=7576, help="API port (default: 7576)")
    args = parser.parse_args()

    LokiClientApp(initial_server=args.server, initial_port=args.port)


if __name__ == "__main__":
    main()
