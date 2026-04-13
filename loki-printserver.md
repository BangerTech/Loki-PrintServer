# Loki-PrintServer — Projektdokumentation

## Übersicht

**Loki-PrintServer** teilt USB-Geräte (Schneideplotter, Drucker, Scanner) vom Raspberry Pi über das Netzwerk. Clients unter Windows, macOS und Linux sehen das Gerät als lokal angeschlossen.

**GitHub:** https://github.com/BangerTech/Loki-PrintServer  
**Version:** 1.0.0  
**Lizenz:** MIT — © BangerTECH

---

## Für den Endnutzer

### Server starten (Raspberry Pi)

```bash
git clone https://github.com/BangerTech/Loki-PrintServer.git
cd Loki-PrintServer
sudo bash server/scripts/setup-host.sh   # einmalig
cd server && docker compose up -d
```

### Client installieren

Unter **https://github.com/BangerTech/Loki-PrintServer/releases** die neueste Version herunterladen:

- **macOS:** `Loki-Client.dmg` → in Applications ziehen
- **Windows:** `Loki-Client-Setup.exe` → Installer ausführen
- **Linux:** `Loki-Client-linux` → ausführbar machen und starten

---

## Architektur

```
Raspberry Pi
  ├── Docker Container: loki-printserver
  │     ├── FastAPI API          Port 7576  (REST + WebSocket)
  │     ├── Web Dashboard        Port 7576  (/)
  │     ├── usbipd               Port 7575  (USB/IP Protokoll)
  │     ├── socat                Port 7580+ (Serial-over-TCP)
  │     └── CUPS                 Port 631   (IPP Netzwerkdrucker)
  └── Kernel Module: usbip_core, usbip_host

Client (Mac/Win/Linux)
  ├── rumps (macOS Menüleiste) / pystray (Windows/Linux Tray)
  ├── Onboarding Wizard (customtkinter)
  ├── mDNS Discovery (zeroconf) → findet Server automatisch
  ├── Multi-Server Support (JSON config in ~/.config/loki-printserver/)
  └── Attach-Methoden:
        ├── USB/IP    → Linux (nativ), Windows (usbip-win)
        ├── Serial    → socat → virtueller /dev/tty.loki-* oder COM-Port
        └── IPP       → CUPS lpadmin → Netzwerkdrucker
```

---

## Dateien

### Server (`server/`)

| Datei | Beschreibung |
|-------|-------------|
| `docker-compose.yml` | Docker Compose Konfiguration |
| `Dockerfile` | Container-Image |
| `api/main.py` | FastAPI Hauptapp, alle Endpunkte |
| `api/models.py` | Pydantic Datenmodelle |
| `api/usbip.py` | USB-Geräteerkennung (pyusb) + usbipd |
| `api/forwarder.py` | Forwarding Manager (USB/IP, Serial, CUPS) |
| `api/discovery.py` | mDNS Ankündigung (Zeroconf) |
| `scripts/start.sh` | Container-Start (Module, CUPS, avahi, uvicorn) |
| `scripts/setup-host.sh` | Host-Vorbereitung (Kernel-Module, usbip tools) |
| `web/index.html` | Web-Dashboard |
| `web/icon_*.png` | Icons für Dashboard + Favicon |

### Client (`client/`)

| Datei | Beschreibung |
|-------|-------------|
| `tray_app.py` | Haupt-App: macOS Menu Bar (rumps) + Tray (pystray) |
| `onboarding.py` | Onboarding-Wizard (Schritt 1-4, customtkinter) |
| `core/api_client.py` | HTTP-Client für Server-API |
| `core/config.py` | Server-Liste persistieren (~/.config/loki-printserver/) |
| `core/device_db.py` | USB-ID Datenbank (CH340, Plotter, Drucker, etc.) |
| `core/discovery.py` | mDNS-Serversuche (Zeroconf) |
| `core/usbip_attach.py` | Plattform-Attach: USB/IP + Serial + IPP |
| `assets/` | Logo + Icons in allen Größen |
| `build/build_mac.sh` | macOS .app + .dmg Builder (PyInstaller + iconutil) |
| `build/build_windows.bat` | Windows .exe Builder (PyInstaller) |
| `build/installer.iss` | Windows Installer (Inno Setup) |

---

## API-Endpunkte

| Method | Endpoint | Beschreibung |
|--------|----------|-------------|
| GET | `/health` | Health-Check |
| GET | `/api/status` | CPU, RAM, Uptime, shared count |
| GET | `/api/devices` | Alle USB-Geräte mit forward_info |
| GET | `/api/devices/shared` | Nur freigegebene Geräte |
| POST | `/api/devices/share` | Gerät freigeben (USB/IP + Serial + CUPS) |
| POST | `/api/devices/unshare` | Freigabe beenden |
| GET | `/api/devices/{bus_id}/forward` | Forwarding-Details eines Geräts |
| GET | `/api/config` | Server-Konfiguration |
| WS | `/ws` | Echtzeit-Updates (device_shared, device_unshared) |

### Forwarding Info (Response von `/api/devices/share`)

```json
{
  "success": true,
  "bus_id": "1-3",
  "forward_info": {
    "shared": true,
    "usbip": true,
    "serial": {
      "available": true,
      "port": 7580,
      "device": "/dev/ttyUSB0"
    },
    "ipp": {
      "available": false,
      "cups_name": null
    }
  }
}
```

---

## Ports

| Port | Protokoll | Beschreibung |
|------|-----------|-------------|
| 7575 | TCP | USB/IP Protokoll (usbipd) |
| 7576 | HTTP/WS | Management API + Web Dashboard |
| 7580+ | TCP | Serial-over-TCP (je Gerät ein Port) |
| 631 | HTTP | CUPS / IPP Netzwerkdrucker |

---

## Konfiguration (server/.env)

| Variable | Standard | Beschreibung |
|----------|---------|-------------|
| `LOKI_PORT` | `7575` | USB/IP Port |
| `LOKI_API_PORT` | `7576` | API + Dashboard Port |
| `LOKI_SECRET` | leer | Optionales API-Passwort |
| `LOKI_ALLOW_ALL` | `true` | Alle Geräte freigebar |

---

## Kernel-Module

| Modul | Seite | Beschreibung |
|-------|-------|-------------|
| `usbip_core` | Server | USB/IP Basis |
| `usbip_host` | Server | Geräte exportieren |
| `vhci_hcd` | Linux-Client | Geräte importieren |

---

## Forwarding-Methoden je Plattform

| Methode | macOS | Windows | Linux |
|---------|-------|---------|-------|
| USB/IP | Lima VM (optional) | usbip-win | nativ |
| Serial (socat) | ✅ `/dev/tty.loki-*` | com0com / TCP | ✅ `/dev/ttyLOKI*` |
| IPP/CUPS | ✅ Netzwerkdrucker | ✅ Windows IPP | ✅ lpadmin |

---

## Release-Prozess

Neues Release erstellen:

```bash
# Version anpassen
echo "1.1.0" > VERSION

# Commit + Tag
git add -A && git commit -m "Release v1.1.0"
git tag v1.1.0
git push && git push --tags
```

GitHub Actions baut dann automatisch:
- Docker Image (linux/arm64 + linux/amd64) → GitHub Container Registry
- macOS `.dmg` → GitHub Release
- Windows `.exe` → GitHub Release

---

## Abhängigkeiten

### Server
| Paket | Version | Zweck |
|-------|---------|-------|
| fastapi | 0.115.0 | Web-Framework |
| uvicorn | 0.30.6 | ASGI-Server |
| pyusb | 1.2.1 | USB-Erkennung |
| zeroconf | 0.136.2 | mDNS |
| psutil | 6.1.0 | System-Monitoring |

### Client
| Paket | Zweck |
|-------|-------|
| rumps | macOS Menu Bar |
| pystray | Windows/Linux Tray |
| customtkinter | Onboarding UI |
| zeroconf | Server-Discovery |
| httpx | API-Client |
| Pillow | Icon-Rendering |

---

## Changelog

| Datum | Version | Änderungen |
|-------|---------|------------|
| 2026-04 | 1.0.0 | Initialer Release by BangerTECH |
