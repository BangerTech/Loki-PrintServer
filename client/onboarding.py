"""
Loki-PrintServer - Onboarding Window
Shows on first launch. Discovers servers, lets user connect.
"""
import sys
import threading
import tkinter as tk
from typing import Callable, Optional

try:
    import customtkinter as ctk
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("dark-blue")
    HAS_CTK = True
except ImportError:
    HAS_CTK = False

from core.api_client import LokiAPIClient
from core.config import LokiConfig
from core.discovery import DiscoveredServer, LokiDiscovery


# ── Color palette ──────────────────────────────────────────────────────────────
BG      = "#0a0f1e"
SURFACE = "#0d1529"
CARD    = "#131f3a"
BORDER  = "#1e3050"
ACCENT  = "#00bfa5"
ACCENT2 = "#1e90ff"
TEXT    = "#e8f4f8"
MUTED   = "#6a8aaa"
SUCCESS = "#00e5a0"
WARNING = "#f0a030"
SEL_BG  = "#0d2a22"   # selected card background

# On macOS, "Helvetica Neue" is a clean system font that renders crisply in
# tkinter/PyInstaller bundles — unlike "SF Pro Display" which isn't a valid
# tkinter font name and falls back to a blurry default.
_FONT = "Helvetica Neue"


def _f(size: int, weight: str = "normal") -> tuple:
    return (_FONT, size, weight)


class OnboardingWindow:
    """4-step onboarding wizard: Welcome → Discover → Connecting → Done."""

    def __init__(self, config: LokiConfig, on_complete: Callable[[LokiConfig], None]):
        self.config = config
        self.on_complete = on_complete
        self._discovery = LokiDiscovery(on_found=self._on_server_found)
        self._discovered: list[DiscoveredServer] = []
        self._selected: Optional[DiscoveredServer] = None
        self._step = 0
        self._drag_x = 0
        self._drag_y = 0

        self._build()
        self._discovery.start()
        self.root.mainloop()

    # ── Window construction ────────────────────────────────────────────────────

    def _build(self):
        self.root = ctk.CTk() if HAS_CTK else tk.Tk()
        self.root.title("Loki-Client Setup")
        self.root.resizable(False, False)
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self._skip)

        W, H = 560, 540

        # Remove native window chrome — we draw our own title bar
        self.root.overrideredirect(True)

        # Center
        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.root.geometry(f"{W}x{H}+{(sw - W) // 2}+{(sh - H) // 2}")

        # On macOS (LSUIElement app) force the window to the front
        if sys.platform == "darwin":
            try:
                from AppKit import NSApp
                NSApp.activateIgnoringOtherApps_(True)
            except Exception:
                pass
            self.root.lift()
            self.root.attributes("-topmost", True)
            self.root.after(400, lambda: self.root.attributes("-topmost", False))

        # ── Custom title bar ─────────────────────────────────────────────────
        titlebar = tk.Frame(self.root, bg=SURFACE, height=38)
        titlebar.pack(fill=tk.X, side=tk.TOP)
        titlebar.pack_propagate(False)

        # macOS-style traffic-light buttons
        lights = tk.Frame(titlebar, bg=SURFACE)
        lights.pack(side=tk.LEFT, padx=14, pady=0)
        lights.place(relx=0, rely=0.5, anchor=tk.W, x=14)

        def _circle(parent, color, cmd=None):
            c = tk.Label(parent, bg=color, width=2, height=1, cursor="hand2" if cmd else "")
            c.pack(side=tk.LEFT, padx=3)
            if cmd:
                c.bind("<Button-1>", lambda e: cmd())
            return c

        _circle(lights, "#ff5f57", self._skip)   # red  → close
        _circle(lights, "#febc2e")                # yellow (no-op)
        _circle(lights, "#28c840")                # green  (no-op)

        tk.Label(titlebar, text="Loki-Client Setup",
                 bg=SURFACE, fg=MUTED, font=_f(11)).place(relx=0.5, rely=0.5, anchor=tk.CENTER)

        # Dragging
        titlebar.bind("<Button-1>",   self._drag_start)
        titlebar.bind("<B1-Motion>",  self._drag_move)
        lights.bind("<Button-1>",     self._drag_start)
        lights.bind("<B1-Motion>",    self._drag_move)

        # Thin accent border at the very top
        tk.Frame(self.root, bg=ACCENT, height=2).pack(fill=tk.X, side=tk.TOP)

        # ── Content container ────────────────────────────────────────────────
        self.container = tk.Frame(self.root, bg=BG)
        self.container.pack(fill=tk.BOTH, expand=True)

        self._show_welcome()

    def _drag_start(self, event):
        self._drag_x = event.x_root - self.root.winfo_x()
        self._drag_y = event.y_root - self.root.winfo_y()

    def _drag_move(self, event):
        self.root.geometry(f"+{event.x_root - self._drag_x}+{event.y_root - self._drag_y}")

    # ── Step 1: Welcome ────────────────────────────────────────────────────────

    def _show_welcome(self):
        self._clear()
        self._step = 0

        logo_frame = tk.Frame(self.container, bg=BG)
        logo_frame.pack(pady=(40, 0))

        logo_loaded = False
        try:
            from PIL import Image, ImageTk
            import pathlib
            icon_path = pathlib.Path(__file__).parent / "assets" / "icon_128.png"
            if icon_path.exists():
                img = Image.open(icon_path).resize((80, 80), Image.LANCZOS)
                self._logo_img = ImageTk.PhotoImage(img)
                tk.Label(logo_frame, image=self._logo_img, bg=BG).pack()
                logo_loaded = True
        except Exception:
            pass
        if not logo_loaded:
            tk.Label(logo_frame, text="L", bg=ACCENT, fg="white",
                     font=_f(36, "bold"), width=2, height=1).pack()

        tk.Label(self.container, text="Loki-Client",
                 bg=BG, fg=TEXT, font=_f(26, "bold")).pack(pady=(14, 2))
        tk.Label(self.container, text="by BangerTECH",
                 bg=BG, fg=ACCENT, font=_f(12)).pack()
        tk.Label(self.container,
                 text="Share USB plotters and printers over your network.\n"
                      "Connect from any Mac, PC or Linux computer.",
                 bg=BG, fg=MUTED, font=_f(13), justify=tk.CENTER).pack(pady=(20, 0))

        pills = tk.Frame(self.container, bg=BG)
        pills.pack(pady=18)
        for icon, label in [("✂", "Plotters"), ("🖨", "Printers"),
                             ("📡", "Auto-Discovery"), ("🔒", "Secure")]:
            p = tk.Frame(pills, bg=CARD, padx=12, pady=7)
            p.pack(side=tk.LEFT, padx=5)
            tk.Label(p, text=f"{icon}  {label}", bg=CARD, fg=ACCENT,
                     font=_f(11)).pack()

        self._nav_buttons(back=None, next_text="Get Started →",
                          next_cmd=self._show_discover)

    # ── Step 2: Discover ──────────────────────────────────────────────────────

    def _show_discover(self):
        self._clear()
        self._step = 1

        tk.Label(self.container, text="Find your server",
                 bg=BG, fg=TEXT, font=_f(20, "bold")).pack(pady=(32, 6))
        tk.Label(self.container,
                 text="Searching your network for Loki-PrintServer instances…",
                 bg=BG, fg=MUTED, font=_f(12)).pack()

        self._scan_status = tk.Label(self.container, text="🔍 Scanning…",
                                     bg=BG, fg=ACCENT2, font=_f(11))
        self._scan_status.pack(pady=(8, 0))

        # Server list container
        list_frame = tk.Frame(self.container, bg=SURFACE)
        list_frame.pack(fill=tk.X, padx=40, pady=(10, 0))
        self._server_list_frame = list_frame
        self._server_buttons: list[tuple[tk.Frame, tk.Label]] = []

        # ── Populate servers already found before this step was shown ────────
        # Discovery starts in __init__ (before the user reaches this step).
        # Any servers found while the user was on the Welcome screen are in
        # _discovered but their cards were never rendered because
        # _server_list_frame didn't exist yet.  Render them now.
        for server in list(self._discovered):
            self._add_server_card(server)

        # Separator
        tk.Frame(self.container, bg=BORDER, height=1).pack(
            fill=tk.X, padx=40, pady=14)

        # Manual IP row
        manual = tk.Frame(self.container, bg=BG)
        manual.pack(padx=40, fill=tk.X)

        tk.Label(manual, text="Or enter IP manually:",
                 bg=BG, fg=MUTED, font=_f(11)).pack(side=tk.LEFT)

        self._ip_var = tk.StringVar()
        ip_entry = tk.Entry(manual, textvariable=self._ip_var,
                            bg=CARD, fg=TEXT, insertbackground=TEXT,
                            relief=tk.FLAT, font=_f(12), width=18)
        ip_entry.pack(side=tk.LEFT, padx=8, ipady=6)
        ip_entry.insert(0, "192.168.x.x")
        ip_entry.bind("<FocusIn>",
                      lambda e: ip_entry.delete(0, tk.END)
                      if ip_entry.get() == "192.168.x.x" else None)
        ip_entry.bind("<Return>", lambda e: self._add_manual(self._ip_var.get()))

        tk.Button(manual, text="Add",
                  bg=ACCENT2, fg="white", relief=tk.FLAT,
                  font=_f(11, "bold"), padx=12, pady=5, cursor="hand2",
                  command=lambda: self._add_manual(self._ip_var.get())).pack(side=tk.LEFT)

        self._next_btn_ref = self._nav_buttons(
            back=self._show_welcome,
            next_text="Connect →",
            next_cmd=self._do_connect,
            next_enabled=bool(self._selected),   # enabled if already selected
        )

        self._animate_scan(0)

    def _animate_scan(self, tick: int):
        if self._step != 1:
            return
        if hasattr(self, "_scan_status"):
            if self._discovered:
                self._scan_status.configure(
                    text=f"✓ Found {len(self._discovered)} server(s)",
                    fg=SUCCESS,
                )
            else:
                frames = ["🔍 Scanning…", "🔍 Scanning..", "🔍 Scanning.", "🔍 Scanning…"]
                self._scan_status.configure(text=frames[tick % 4], fg=ACCENT2)
        self.root.after(600, lambda: self._animate_scan(tick + 1))

    def _on_server_found(self, server: DiscoveredServer):
        if server not in self._discovered:
            self._discovered.append(server)
            # Only render the card if the discover step is currently shown.
            # If we're still on Welcome, the card will be rendered when
            # _show_discover() is called (see the populate loop above).
            if hasattr(self, "_server_list_frame"):
                self.root.after(0, lambda s=server: self._add_server_card(s))

    def _add_server_card(self, server: DiscoveredServer):
        if not hasattr(self, "_server_list_frame"):
            return

        is_first = len(self._server_buttons) == 0

        card = tk.Frame(self._server_list_frame, bg=CARD, cursor="hand2",
                        padx=14, pady=10)
        card.pack(fill=tk.X, padx=1, pady=1)

        # Selection indicator dot
        dot = tk.Label(card, text="●", bg=CARD, fg=BORDER, font=_f(14))
        dot.pack(side=tk.LEFT, padx=(0, 10))

        info = tk.Frame(card, bg=CARD)
        info.pack(side=tk.LEFT, fill=tk.X, expand=True)

        name_lbl = tk.Label(info, text=server.name, bg=CARD, fg=TEXT,
                            font=_f(13, "bold"), anchor=tk.W)
        name_lbl.pack(fill=tk.X)

        detail_lbl = tk.Label(info,
                              text=f"{server.ip}:{server.api_port}  ·  v{server.version}",
                              bg=CARD, fg=MUTED, font=_f(10), anchor=tk.W)
        detail_lbl.pack(fill=tk.X)

        ping_lbl = tk.Label(card, text="● Online", bg=CARD, fg=SUCCESS, font=_f(10))
        ping_lbl.pack(side=tk.RIGHT)

        all_widgets = [card, dot, info, name_lbl, detail_lbl, ping_lbl]

        def select(s=server):
            self._selected = s
            # Reset all cards to unselected style
            for btn_card, btn_dot in self._server_buttons:
                btn_card.configure(bg=CARD)
                btn_dot.configure(fg=BORDER, bg=CARD)
                for w in btn_card.winfo_children():
                    try:
                        w.configure(bg=CARD)
                        for ww in w.winfo_children():
                            ww.configure(bg=CARD)
                    except Exception:
                        pass
            # Highlight this card
            for w in all_widgets:
                try:
                    w.configure(bg=SEL_BG)
                except Exception:
                    pass
            dot.configure(fg=ACCENT, bg=SEL_BG)
            # Enable Connect button
            if self._next_btn_ref:
                self._next_btn_ref.configure(state=tk.NORMAL, bg=ACCENT,
                                             fg="#0a0f1e")

        for w in all_widgets:
            w.bind("<Button-1>", lambda e, s=server: select(s))

        self._server_buttons.append((card, dot))

        if is_first:
            select()

    def _add_manual(self, ip: str):
        ip = ip.strip()
        if not ip or ip == "192.168.x.x":
            return
        if hasattr(self, "_scan_status"):
            self._scan_status.configure(text=f"⏳ Checking {ip}…", fg=MUTED)

        def _check():
            try:
                client = LokiAPIClient(ip, 7576)
                ok = client.check_health()
            except Exception:
                ok = False
            if ok:
                fake = DiscoveredServer(
                    name=ip, host=f"{ip}.", ip=ip,
                    port=7575, api_port=7576, version="?",
                )
                self.root.after(0, lambda: self._on_server_found(fake))
            else:
                self.root.after(0, lambda: self._scan_status.configure(
                    text=f"✗ Cannot reach {ip}:7576", fg=WARNING,
                ) if hasattr(self, "_scan_status") else None)

        threading.Thread(target=_check, daemon=True).start()

    def _do_connect(self):
        if self._selected:
            self._show_connecting(self._selected)
        elif hasattr(self, "_ip_var"):
            ip = self._ip_var.get().strip()
            if ip and ip != "192.168.x.x":
                self._add_manual(ip)
                self.root.after(2500, self._do_connect)

    # ── Step 3: Connecting ────────────────────────────────────────────────────

    def _show_connecting(self, server: DiscoveredServer):
        self._clear()
        self._step = 2

        tk.Label(self.container, text="Connecting…",
                 bg=BG, fg=TEXT, font=_f(20, "bold")).pack(pady=(60, 10))
        tk.Label(self.container, text=f"{server.ip}:{server.api_port}",
                 bg=BG, fg=ACCENT2, font=_f(14)).pack()

        self._conn_status = tk.Label(self.container, text="⏳ Checking connection…",
                                     bg=BG, fg=MUTED, font=_f(12))
        self._conn_status.pack(pady=20)

        def _do():
            client = LokiAPIClient(server.ip, server.api_port)
            ok = client.check_health()
            if ok:
                self.config.add_server(server.ip, server.api_port, server.name)
                self.config.first_launch = False
                self.config.save()
                self.root.after(0, lambda: self._show_done(server, client))
            else:
                self.root.after(0, lambda: self._conn_status.configure(
                    text="✗ Connection failed. Check IP and try again.",
                    fg=WARNING,
                ))
                self.root.after(1500, self._show_discover)

        threading.Thread(target=_do, daemon=True).start()

    # ── Step 4: Done ──────────────────────────────────────────────────────────

    def _show_done(self, server: DiscoveredServer, client: LokiAPIClient):
        self._clear()
        self._step = 3

        tk.Label(self.container, text="✓",
                 bg=BG, fg=SUCCESS, font=_f(52)).pack(pady=(36, 6))
        tk.Label(self.container, text="You're connected!",
                 bg=BG, fg=TEXT, font=_f(22, "bold")).pack()
        tk.Label(self.container, text=f"{server.name}  ({server.ip})",
                 bg=BG, fg=ACCENT2, font=_f(13)).pack(pady=6)

        devices = client.list_devices()
        shared = sum(1 for d in devices if d.is_shared)

        info = tk.Frame(self.container, bg=CARD, padx=20, pady=14)
        info.pack(padx=60, pady=18, fill=tk.X)

        for label, value in [
            ("USB Devices found", str(len(devices))),
            ("Currently shared",  str(shared)),
            ("Server",            f"{server.ip}:{server.api_port}"),
        ]:
            row = tk.Frame(info, bg=CARD)
            row.pack(fill=tk.X, pady=2)
            tk.Label(row, text=label, bg=CARD, fg=MUTED,
                     font=_f(11), anchor=tk.W).pack(side=tk.LEFT)
            tk.Label(row, text=value, bg=CARD, fg=TEXT,
                     font=_f(11, "bold"), anchor=tk.E).pack(side=tk.RIGHT)

        tk.Label(self.container,
                 text="Loki lives in your menu bar — click the icon to manage devices.",
                 bg=BG, fg=MUTED, font=_f(11), wraplength=400,
                 justify=tk.CENTER).pack(pady=(0, 6))

        self._nav_buttons(back=None, next_text="Open Loki →", next_cmd=self._finish)

    # ── Shared helpers ────────────────────────────────────────────────────────

    def _nav_buttons(self, back, next_text: str, next_cmd,
                     next_enabled: bool = True) -> Optional[tk.Button]:
        nav = tk.Frame(self.container, bg=BG)
        nav.pack(side=tk.BOTTOM, fill=tk.X, padx=40, pady=18)

        if back:
            tk.Button(nav, text="← Back", command=back,
                      bg=SURFACE, fg=MUTED, relief=tk.FLAT,
                      font=_f(12), padx=14, pady=7,
                      cursor="hand2").pack(side=tk.LEFT)

        state  = tk.NORMAL if next_enabled else tk.DISABLED
        bg_col = ACCENT  if next_enabled else BORDER
        fg_col = "#0a0f1e" if next_enabled else MUTED
        btn = tk.Button(nav, text=next_text, command=next_cmd,
                        bg=bg_col, fg=fg_col, relief=tk.FLAT,
                        font=_f(12, "bold"), padx=18, pady=7,
                        cursor="hand2", state=state)
        btn.pack(side=tk.RIGHT)
        return btn

    def _clear(self):
        for w in self.container.winfo_children():
            w.destroy()

    def _skip(self):
        self._finish()

    def _finish(self):
        self._discovery.stop()
        self.root.destroy()
        self.on_complete(self.config)
