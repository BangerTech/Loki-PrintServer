<p align="center">
  <img src="assets/logo.png" alt="Loki-PrintServer" width="500">
</p>

<h1 align="center">Loki-PrintServer</h1>
<p align="center"><strong>by BangerTECH — Share USB devices (plotters, printers, scanners) over your network</strong></p>
<p align="center">
  <img src="https://img.shields.io/badge/version-1.0.0-blue" alt="Version">
  <img src="https://img.shields.io/badge/platform-Raspberry%20Pi-red" alt="Platform">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
  <img src="https://img.shields.io/badge/clients-macOS%20%7C%20Windows%20%7C%20Linux-purple" alt="Clients">
</p>

Loki-PrintServer lets you share any USB device connected to a Raspberry Pi (or any Linux server) with computers on your network — Windows, macOS and Linux. The remote computer sees the device as if it were physically plugged in locally.

> Perfect for cutting plotters, label printers, scanners, USB dongles, and more.

---

## ✨ Features

- **USB over IP** — shares any USB device transparently over TCP/IP
- **Auto-discovery** — clients find servers automatically via mDNS (no IP needed)
- **Web Dashboard** — manage shared devices via browser at `http://pi:7576`
- **Cross-platform client** — GUI client for Linux, Windows, macOS
- **Docker Compose** — one-command server setup on Raspberry Pi
- **Plotter-ready** — tested with cutting plotters (CH340 USB-serial adapters)
- **Open Source** — MIT licensed, no device limits, no license fees

---

## 🚀 Quick Start

### Server (Raspberry Pi)

```bash
# 1. Clone the repo
git clone https://github.com/YOUR_USERNAME/loki-printserver.git
cd loki-printserver/server

# 2. Prepare the host (loads kernel modules, one time)
sudo bash scripts/setup-host.sh

# 3. Start the server
docker compose up -d

# 4. Open the web dashboard
# http://<raspberry-pi-ip>:7576
```

### Client (your computer)

**Linux:**
```bash
cd client
bash linux/install.sh
python3 loki_client.py
```

**macOS:**
```bash
cd client
bash mac/install.sh
python3 loki_client.py
```

**Windows:**
```powershell
# Run as Administrator
cd client
powershell -ExecutionPolicy Bypass -File windows\install.ps1
python loki_client.py
```

---

## 🔧 How It Works

```
┌─────────────────────────────────┐         ┌──────────────────────────────┐
│       Raspberry Pi (Server)     │         │    Your Computer (Client)    │
│                                 │         │                              │
│  USB Plotter ──► usbipd daemon  │◄──TCP──►│  Loki Client GUI             │
│                  port 7575      │         │    ↓ usbip attach            │
│              FastAPI API        │         │  /dev/ttyUSB0  or  COM3      │
│                  port 7576      │         │  (device appears local!)     │
│              Web Dashboard      │         │                              │
└─────────────────────────────────┘         └──────────────────────────────┘
```

1. The Raspberry Pi runs `usbipd` — the Linux USB/IP daemon
2. The server API exposes USB devices and controls sharing
3. Clients use `usbip attach` to mount the remote device locally
4. The OS sees it as a physically connected USB device

---

## 📦 Project Structure

```
loki-printserver/
├── server/                     # Raspberry Pi server
│   ├── docker-compose.yml      # Docker Compose config
│   ├── Dockerfile
│   ├── api/                    # FastAPI server
│   │   ├── main.py             # REST API & WebSocket
│   │   ├── models.py           # Pydantic models
│   │   ├── usbip.py            # USB/IP device manager
│   │   └── discovery.py       # mDNS announcer
│   ├── scripts/
│   │   ├── start.sh            # Container entrypoint
│   │   └── setup-host.sh       # One-time host setup
│   └── web/
│       └── index.html          # Web dashboard
│
├── client/                     # Cross-platform client
│   ├── loki_client.py          # Main GUI application
│   ├── requirements.txt
│   ├── core/
│   │   ├── api_client.py       # Server API client
│   │   ├── discovery.py        # mDNS server discovery
│   │   └── usbip_attach.py     # Platform USB attachment
│   ├── linux/
│   │   └── install.sh
│   ├── windows/
│   │   └── install.ps1
│   └── mac/
│       └── install.sh
│
├── docs/                       # Documentation
├── .github/workflows/          # CI/CD pipelines
└── VERSION
```

---

## 🖥️ Platform Support

| Feature              | Linux ✅ | Windows ✅ | macOS ⚠️ |
|----------------------|----------|-----------|----------|
| Server               | ✅       | ✅        | ✅       |
| Device Discovery     | ✅       | ✅        | ✅       |
| USB Device Attach    | ✅ native| ✅ usbip-win | ⚠️ Lima VM |
| Auto-discovery (mDNS)| ✅       | ✅        | ✅       |
| Web Dashboard        | ✅       | ✅        | ✅       |

### macOS Notes

macOS does not have native USB/IP kernel support. Options:
1. **Lima VM (Recommended):** `brew install lima && limactl start` — provides a Linux VM that handles USB/IP attachment
2. **Serial Forwarding:** For plotters/printers using USB-serial, enable serial forwarding mode

### Windows Notes

Windows requires [usbip-win](https://github.com/cezanne/usbip-win/releases) for USB device attachment. Download and install the driver before using the client.

---

## ⚙️ Configuration

### Server (`server/.env`)

```env
LOKI_PORT=7575          # USB/IP protocol port
LOKI_API_PORT=7576      # Management API & web UI port
LOKI_SECRET=            # Optional: set a password for API access
LOKI_ALLOW_ALL=true     # Allow all devices to be shared
```

### Connecting to a specific server

```bash
python3 loki_client.py --server 192.168.1.100 --port 7576
```

---

## 🔌 Cutting Plotter Setup

This project was built for sharing cutting plotters (like Cricut, Silhouette, Roland, and generic CH340-based plotters) over the network.

### Server Setup

1. Plug your plotter into the Raspberry Pi via USB
2. Open the web dashboard at `http://<pi-ip>:7576`
3. Find your plotter in the device list (look for "CH340" or your plotter brand)
4. Click **Share**

### Client Setup

1. Open Loki-Client on your computer
2. Select your server from the auto-discovered list
3. Click **Attach Here** next to your plotter
4. The plotter appears as a local USB device (e.g. `/dev/ttyUSB0` or `COM3`)
5. Open your cutting software and select the device normally

---

## 🛠️ Development

### Run server locally (without Docker)

```bash
cd server
pip install -r api/requirements.txt
sudo modprobe usbip_core usbip_host
python3 -m uvicorn api.main:app --host 0.0.0.0 --port 7576 --reload
```

### Run client

```bash
cd client
pip install -r requirements.txt
python3 loki_client.py
```

### Run tests

```bash
pip install pytest
pytest tests/ -v
```

---

## 🤝 Contributing

Contributions are welcome! Please open an issue or pull request.

1. Fork the repo
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Commit your changes: `git commit -m 'Add my feature'`
4. Push: `git push origin feature/my-feature`
5. Open a Pull Request

---

## 📄 License

MIT License — see [LICENSE](LICENSE) for details.

© BangerTECH — This project is **not affiliated with** VirtualHere or any commercial USB over IP product.

---

## 🙏 Acknowledgments

- [USB/IP Project](http://usbip.sourceforge.net/) — Linux kernel USB/IP subsystem
- [usbip-win](https://github.com/cezanne/usbip-win) — Windows USB/IP driver
- [FastAPI](https://fastapi.tiangolo.com/) — Python API framework
- [customtkinter](https://github.com/TomSchimansky/CustomTkinter) — Modern Tkinter GUI
