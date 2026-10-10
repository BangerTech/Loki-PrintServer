<p align="center">
  <img src="assets/logo.png" alt="Loki-PrintServer" width="480">
</p>

<p align="center">
  <strong>Share USB devices (plotters, printers, scanners) over your network</strong><br>
  <sub>by BangerTECH</sub>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/version-1.5.3-blue?style=flat-square" alt="Version">
  <img src="https://img.shields.io/badge/server-Raspberry%20Pi-red?style=flat-square" alt="Platform">
  <img src="https://img.shields.io/badge/clients-macOS%20%7C%20Windows%20%7C%20Linux-blueviolet?style=flat-square" alt="Clients">
  <img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" alt="License">
</p>

---

## What is Loki-PrintServer?

Loki-PrintServer lets you plug a USB device (cutting plotter, printer, scanner, label printer…) into a **Raspberry Pi** and use it from **any computer on your network** — as if it were plugged in locally.

Works with **Illustrator + FineCut**, xfcut, Silhouette Studio, Inkcut, and **VectorCraft / Plot Cut** (raw plotter bytes over HTTP).

---

## Server — Raspberry Pi Setup

> **Requires:** Raspberry Pi with Docker installed

```bash
git clone https://github.com/BangerTech/Loki-PrintServer.git
cd Loki-PrintServer/server
docker compose up -d
```

That's it. The server is now running.

**Web Dashboard:** open `http://<raspberry-pi-ip>:7576` in your browser. Language DE/EN, live log, and a **Plot Cut** panel (device status + 10 mm test job).

### Plot Cut API (VectorCraft)

Plot Cut sends **finished** plotter bytes (MGL-IIc / HP-GL). Loki writes them unchanged — it does not generate commands. Illustrator + FineCut keep their own paths.

```bash
curl http://<pi>:7576/api/plotcut/devices

curl --data-binary "IN;PU0,0;PD400,0;PU0,0;" \
  -H "Content-Type: application/octet-stream" \
  http://<pi>:7576/api/plotcut/devices/mimaki-cg60sr/job
```

Stable ids: `mimaki-cg60sr` (Mimaki `0a50:0001`), `vevor` (CH340 `1a86:7523`). Config: `/etc/loki-printserver/plotcut_devices.json`. If a plotter is attached via USB/IP to a PC, the job returns `409` and Loki will not steal the device. One job per device at a time.

---

## Client — Download & Install

Download the latest client from **[GitHub Releases](https://github.com/BangerTech/Loki-PrintServer/releases/latest)**:

| Platform | File | Installation |
|----------|------|--------------|
| **macOS (Apple Silicon + Intel)** | `Loki-Client.dmg` | Open → drag to Applications. First launch: right-click → Open |
| **Windows** | `LokiClient-Setup.exe` | Run as administrator. The signed **USBip** driver installs silently after Finish (no second wizard). **Restart Windows once.** |
| **Linux** | `Loki-Client-linux` | `chmod +x` and run |

### First Launch

1. Open **Loki-Client** — menu bar (macOS) or system tray (Windows/Linux). The tray light turns green when a plotter is actually attached.
2. The server on your network is found automatically.
3. Click your server → select your device → **Attach**.
4. The device appears locally — open your software and use it normally.

> **macOS:** If you get "App is damaged", run `xattr -cr /Applications/Loki-Client.app`

---

## How it works

```
┌─────────────────────────┐        ┌──────────────────────────┐
│   Raspberry Pi (Server) │        │  Your Computer (Client)  │
│                         │        │                          │
│  USB Plotter            │        │  Loki-Client             │
│    └─► usbipd  :7575   │◄──TCP──►│    Windows: USBip        │
│        FastAPI :7576    │        │    macOS: virtual USB    │
│        CUPS    :631     │        │    Linux: usbip / tty    │
│        socat   :7580+   │        │                          │
└─────────────────────────┘        └──────────────────────────┘
```

| Plotter | macOS | Windows |
|---|---|---|
| **Mimaki CG-60SR** (FineCut) | Raw USB bridge — FineCut sees the real USB device | USB/IP via the bundled USBip driver |
| **Vevor / CH340** and other serial cutters | socat → virtual USB serial port | USB/IP → a real COM port (no com0com) |
| USB printer | CUPS / IPP network printer | CUPS / IPP network printer |

A plotter can only be on one path at a time. While Windows holds it over USB/IP, the Mac does not see it. About **20 seconds** after the Windows client is gone — including a shutdown, without clicking Detach — the Pi puts the Mac path back by itself (Vevor → socat, Mimaki → USB bridge). Then attach from the Mac as usual.

---

## Supported devices

- ✂ Cutting plotters: **Mimaki CG-60SR**, **Vevor / CH340**, Silhouette Cameo, Cricut, Graphtec, Roland
- 🖨 Printers: HP, Canon, Epson, Brother, Samsung
- 🏷 Label printers: DYMO LabelWriter, Brother QL
- 📷 Scanners: Epson Perfection, Canon CanoScan, HP ScanJet
- 🔌 Any other USB device (via USB/IP on Windows & Linux)

---

## Requirements

**Server:**
- Raspberry Pi running Raspberry Pi OS or any Debian-based Linux
- Docker

**Client:**
- macOS 11+, Windows 10/11, or Linux
- macOS cutting plotters: **AMFI must be disabled** for virtual USB (see below)
- Windows: the **USBip** driver (usbip-win2) is installed by Loki Setup. Do not run a second USBip wizard if it is already installed.

---

## macOS — Virtual USB for Cutting Plotters

On macOS, Loki creates a **real virtual USB device** (`/dev/cu.usbmodem*`) that **FineCut**, **xfcut**, and **Inkcut** recognize. The virtual device keeps the original Vendor ID and Product ID, so vendor software detects the plotter on its own.

**This requires AMFI (Apple Mobile File Integrity) to be disabled:**

| Setup | How to disable AMFI |
|-------|---------------------|
| **OpenCore / Hackintosh** | Add `amfi_get_out_of_my_way=1` to `boot-args` in your OpenCore `config.plist`, then reboot |
| **OpenCore Legacy Patcher** | Settings → Kernel Security → Enable "Disable AMFI" → Apply → Reboot |
| **Stock Mac** | Boot into Recovery → Terminal → `nvram boot-args="amfi_get_out_of_my_way=1"` → Reboot |

> Without AMFI disabled, Loki falls back to a PTY bridge (`/tmp/tty.loki-*`). That works for software that lets you pick a port, but IOKit apps such as FineCut will not see the device.

---

## Build clients from source

```bash
git clone https://github.com/BangerTech/Loki-PrintServer.git
cd Loki-PrintServer

# macOS
bash client/build/build_mac.sh

# Windows (run on Windows; needs Inno Setup)
client\build\build_windows.bat
```

---

## License

MIT License — © BangerTECH
