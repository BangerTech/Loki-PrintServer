"""
Loki-PrintServer - Onboarding Window
Shows on first launch. Discovers servers, lets user connect.
Modern dark UI via customtkinter.
"""
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


# ── Color palette — matches Loki logo (teal → blue gradient) ──────────────────
BG       = "#0a0f1e"
SURFACE  = "#0d1529"
CARD     = "#111e35"
BORDER   = "#1e3050"
ACCENT   = "#00bfa5"   # teal from logo
ACCENT2  = "#1e90ff"   # blue from logo
GRAD1    = "#00c2a8"
GRAD2    = "#1e73be"
TEXT     = "#e8f4f8"
MUTED    = "#6a8aaa"
SUCCESS  = "#00e5a0"
WARNING  = "#f0a030"


def _hex(color: str, alpha: float) -> str:
    """Blend color with BG for a fake alpha effect."""
    return color


class OnboardingWindow:
    """
    Multi-step onboarding wizard.
    Steps: Welcome → Discover → Connect → Done
    """

    def __init__(self, config: LokiConfig, on_complete: Callable[[LokiConfig], None]):
        self.config = config
        self.on_complete = on_complete
        self._discovery = LokiDiscovery(on_found=self._on_server_found)
        self._discovered: list[DiscoveredServer] = []
        self._selected: Optional[DiscoveredServer] = None
        self._manual_ip = ""
        self._step = 0

        self._build()
        self._discovery.start()
        self.root.mainloop()

    # ── Build ──────────────────────────────────────────────────────────────────

    def _build(self):
        if HAS_CTK:
            self.root = ctk.CTk()
        else:
            self.root = tk.Tk()

        self.root.title("Loki-Client Setup")
        self.root.geometry("560x480")
        self.root.resizable(False, False)
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self._skip)

        # Center on screen
        self.root.update_idletasks()
        x = (self.root.winfo_screenwidth() - 560) // 2
        y = (self.root.winfo_screenheight() - 480) // 2
        self.root.geometry(f"560x480+{x}+{y}")

        # Main container
        self.container = tk.Frame(self.root, bg=BG)
        self.container.pack(fill=tk.BOTH, expand=True)

        self._show_welcome()

    # ── Step 1: Welcome ────────────────────────────────────────────────────────

    def _show_welcome(self):
        self._clear()
        self._step = 0

        # Logo + title
        logo_frame = tk.Frame(self.container, bg=BG)
        logo_frame.pack(pady=(48, 0))

        # Try to load the real logo
        logo_loaded = False
        try:
            from PIL import Image, ImageTk
            import pathlib
            icon_path = pathlib.Path(__file__).parent / "assets" / "icon_128.png"
            if icon_path.exists():
                img = Image.open(icon_path).resize((80, 80), Image.LANCZOS)
                self._logo_img = ImageTk.PhotoImage(img)
                logo = tk.Label(logo_frame, image=self._logo_img, bg=BG)
                logo.pack()
                logo_loaded = True
        except Exception:
            pass
        if not logo_loaded:
            logo = tk.Label(logo_frame, text="L",
                            bg=ACCENT, fg="white",
                            font=("SF Pro Display", 36, "bold"),
                            width=2, height=1)
            logo.pack()

        tk.Label(self.container, text="Loki-Client",
                 bg=BG, fg=TEXT,
                 font=("SF Pro Display", 28, "bold")).pack(pady=(16, 2))

        tk.Label(self.container, text="by BangerTECH",
                 bg=BG, fg=ACCENT,
                 font=("SF Pro Display", 12)).pack()

        tk.Label(self.container,
                 text="Share USB plotters and printers over your network.\nConnect from any Mac, PC or Linux computer.",
                 bg=BG, fg=MUTED,
                 font=("SF Pro Display", 13),
                 justify=tk.CENTER).pack(pady=(24, 0))

        # Feature pills
        pills = tk.Frame(self.container, bg=BG)
        pills.pack(pady=20)
        for icon, label in [("✂", "Plotters"), ("🖨", "Printers"), ("📡", "Auto-Discovery"), ("🔒", "Secure")]:
            p = tk.Frame(pills, bg=CARD, padx=12, pady=7)
            p.pack(side=tk.LEFT, padx=5)
            tk.Label(p, text=f"{icon}  {label}", bg=CARD, fg=ACCENT,
                     font=("SF Pro Display", 11)).pack()

        self._nav_buttons(back=None, next_text="Get Started →", next_cmd=self._show_discover)

    # ── Step 2: Discover ──────────────────────────────────────────────────────

    def _show_discover(self):
        self._clear()
        self._step = 1

        tk.Label(self.container, text="Find your server",
                 bg=BG, fg=TEXT,
                 font=("SF Pro Display", 20, "bold")).pack(pady=(36, 6))

        tk.Label(self.container,
                 text="Searching your network for Loki-PrintServer instances…",
                 bg=BG, fg=MUTED, font=("SF Pro Display", 12)).pack()

        # Spinner / status
        self._scan_status = tk.Label(self.container, text="🔍 Scanning…",
                                      bg=BG, fg=ACCENT2, font=("SF Pro Display", 11))
        self._scan_status.pack(pady=(10, 0))

        # Server list
        list_frame = tk.Frame(self.container, bg=SURFACE,
                               relief=tk.FLAT, bd=0)
        list_frame.pack(fill=tk.X, padx=40, pady=(12, 0))

        self._server_list_frame = list_frame
        self._server_buttons: list[tk.Frame] = []

        # Manual entry
        sep = tk.Frame(self.container, bg=BORDER, height=1)
        sep.pack(fill=tk.X, padx=40, pady=16)

        manual = tk.Frame(self.container, bg=BG)
        manual.pack(padx=40, fill=tk.X)

        tk.Label(manual, text="Or enter IP manually:",
                 bg=BG, fg=MUTED, font=("SF Pro Display", 11)).pack(side=tk.LEFT)

        self._ip_var = tk.StringVar()
        ip_entry = tk.Entry(manual, textvariable=self._ip_var,
                            bg=CARD, fg=TEXT, insertbackground=TEXT,
                            relief=tk.FLAT, font=("SF Pro Display", 12),
                            width=18)
        ip_entry.pack(side=tk.LEFT, padx=8, ipady=5)
        ip_entry.insert(0, "192.168.x.x")
        ip_entry.bind("<FocusIn>", lambda e: ip_entry.delete(0, tk.END) if ip_entry.get() == "192.168.x.x" else None)

        add_btn = tk.Button(manual, text="Add",
                            bg=ACCENT, fg="white", relief=tk.FLAT,
                            font=("SF Pro Display", 11, "bold"),
                            padx=10, pady=4, cursor="hand2",
                            command=lambda: self._add_manual(self._ip_var.get()))
        add_btn.pack(side=tk.LEFT)

        self._next_btn_ref = self._nav_buttons(
            back=self._show_welcome,
            next_text="Connect →",
            next_cmd=self._do_connect,
            next_enabled=False,
        )

        # Start spinner animation
        self._animate_scan(0)

    def _animate_scan(self, tick: int):
        if self._step != 1:
            return
        frames = ["🔍 Scanning…", "🔍 Scanning..", "🔍 Scanning.", "🔍 Scanning…"]
        if hasattr(self, "_scan_status"):
            if self._discovered:
                self._scan_status.configure(
                    text=f"✓ Found {len(self._discovered)} server(s)",
                    fg=SUCCESS
                )
            else:
                self._scan_status.configure(text=frames[tick % len(frames)], fg=ACCENT2)
        self.root.after(600, lambda: self._animate_scan(tick + 1))

    def _on_server_found(self, server: DiscoveredServer):
        if server not in self._discovered:
            self._discovered.append(server)
            self.root.after(0, lambda s=server: self._add_server_card(s))

    def _add_server_card(self, server: DiscoveredServer):
        is_first = len(self._server_buttons) == 0

        card = tk.Frame(self._server_list_frame, bg=CARD,
                        cursor="hand2", padx=16, pady=10)
        card.pack(fill=tk.X, padx=1, pady=1)

        # Radio dot
        dot_color = ACCENT if is_first else BORDER
        dot = tk.Label(card, text="●", bg=CARD if not is_first else "#0d2a22", fg=dot_color,
                       font=("SF Pro Display", 14))
        dot.pack(side=tk.LEFT, padx=(0, 10))

        info = tk.Frame(card, bg=CARD)
        info.pack(side=tk.LEFT, fill=tk.X, expand=True)

        tk.Label(info, text=server.name, bg=CARD, fg=TEXT,
                 font=("SF Pro Display", 13, "bold"),
                 anchor=tk.W).pack(fill=tk.X)

        tk.Label(info, text=f"{server.ip}:{server.api_port}  ·  v{server.version}",
                 bg=CARD, fg=MUTED, font=("SF Pro Display", 10),
                 anchor=tk.W).pack(fill=tk.X)

        ping = tk.Label(card, text="● Online", bg=CARD, fg=SUCCESS,
                        font=("SF Pro Display", 10))
        ping.pack(side=tk.RIGHT)

        def select(s=server, c=card, d=dot):
            self._selected = s
            # Reset all cards
            for btn_card, btn_dot in self._server_buttons:
                btn_card.configure(bg=CARD)
                btn_dot.configure(fg=BORDER, bg=CARD)
                for w in btn_card.winfo_children():
                    try:
                        w.configure(bg=CARD)
                    except Exception:
                        pass
            # Highlight selected
            c.configure(bg="#0d2a22")
            d.configure(fg=ACCENT, bg="#0d2a22")
            for w in c.winfo_children():
                try:
                    w.configure(bg="#0d2a22")
                except Exception:
                    pass
            # Enable next button
            if self._next_btn_ref:
                self._next_btn_ref.configure(state=tk.NORMAL, bg=ACCENT)

        card.bind("<Button-1>", lambda e, s=server: select(s))
        for w in card.winfo_children():
            w.bind("<Button-1>", lambda e, s=server: select(s))

        self._server_buttons.append((card, dot))

        if is_first:
            select()

    def _add_manual(self, ip: str):
        ip = ip.strip()
        if not ip or ip == "192.168.x.x":
            return

        def _check():
            client = LokiAPIClient(ip, 7576)
            if client.check_health():
                fake = DiscoveredServer(
                    name=ip, host=f"{ip}.", ip=ip,
                    port=7575, api_port=7576, version="?"
                )
                self.root.after(0, lambda: self._on_server_found(fake))
            else:
                self.root.after(0, lambda: self._scan_status.configure(
                    text=f"✗ Cannot reach {ip}:7576", fg=WARNING))
        threading.Thread(target=_check, daemon=True).start()

    def _do_connect(self):
        if not self._selected and self._ip_var.get() not in ("", "192.168.x.x"):
            self._add_manual(self._ip_var.get())
            self.root.after(2000, self._do_connect)
            return
        if self._selected:
            self._show_connecting(self._selected)

    # ── Step 3: Connecting ─────────────────────────────────────────────────────

    def _show_connecting(self, server: DiscoveredServer):
        self._clear()
        self._step = 2

        tk.Label(self.container, text="Connecting…",
                 bg=BG, fg=TEXT, font=("SF Pro Display", 20, "bold")).pack(pady=(60, 12))

        tk.Label(self.container, text=f"{server.ip}:{server.api_port}",
                 bg=BG, fg=ACCENT2, font=("SF Pro Display", 14)).pack()

        self._conn_status = tk.Label(self.container, text="⏳ Checking connection…",
                                      bg=BG, fg=MUTED, font=("SF Pro Display", 12))
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
                    fg=WARNING
                ))
                self.root.after(1500, self._show_discover)

        threading.Thread(target=_do, daemon=True).start()

    # ── Step 4: Done ──────────────────────────────────────────────────────────

    def _show_done(self, server: DiscoveredServer, client: LokiAPIClient):
        self._clear()
        self._step = 3

        tk.Label(self.container, text="✓",
                 bg=BG, fg=SUCCESS,
                 font=("SF Pro Display", 52)).pack(pady=(40, 8))

        tk.Label(self.container, text="You're connected!",
                 bg=BG, fg=TEXT, font=("SF Pro Display", 22, "bold")).pack()

        tk.Label(self.container, text=f"{server.name}  ({server.ip})",
                 bg=BG, fg=ACCENT2, font=("SF Pro Display", 13)).pack(pady=6)

        # Device count
        devices = client.list_devices()
        shared = sum(1 for d in devices if d.is_shared)

        info = tk.Frame(self.container, bg=CARD, padx=20, pady=14)
        info.pack(padx=60, pady=20, fill=tk.X)

        for label, value in [
            ("USB Devices found", str(len(devices))),
            ("Currently shared", str(shared)),
            ("Server version", "v1.0.0"),
        ]:
            row = tk.Frame(info, bg=CARD)
            row.pack(fill=tk.X, pady=2)
            tk.Label(row, text=label, bg=CARD, fg=MUTED,
                     font=("SF Pro Display", 11), anchor=tk.W).pack(side=tk.LEFT)
            tk.Label(row, text=value, bg=CARD, fg=TEXT,
                     font=("SF Pro Display", 11, "bold"), anchor=tk.E).pack(side=tk.RIGHT)

        tk.Label(self.container,
                 text="Loki lives in your menu bar — click the L icon to manage devices.",
                 bg=BG, fg=MUTED, font=("SF Pro Display", 11),
                 wraplength=400, justify=tk.CENTER).pack(pady=(0, 8))

        self._nav_buttons(back=None, next_text="Open Loki →", next_cmd=self._finish)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _nav_buttons(self, back, next_text: str, next_cmd,
                     next_enabled: bool = True) -> Optional[tk.Button]:
        nav = tk.Frame(self.container, bg=BG)
        nav.pack(side=tk.BOTTOM, fill=tk.X, padx=40, pady=20)

        if back:
            tk.Button(nav, text="← Back", command=back,
                      bg=SURFACE, fg=MUTED, relief=tk.FLAT,
                      font=("SF Pro Display", 12), padx=14, pady=6,
                      cursor="hand2").pack(side=tk.LEFT)

        state = tk.NORMAL if next_enabled else tk.DISABLED
        bg_col = ACCENT if next_enabled else BORDER
        fg_col = "#0a0f1e" if next_enabled else MUTED  # dark text on teal
        btn = tk.Button(nav, text=next_text, command=next_cmd,
                        bg=bg_col, fg=fg_col, relief=tk.FLAT,
                        font=("SF Pro Display", 12, "bold"),
                        padx=18, pady=6, cursor="hand2",
                        state=state)
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
