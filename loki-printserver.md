# Loki-PrintServer — Projektdokumentation

## Projektübersicht

**Loki-PrintServer** ist ein Open-Source USB-over-IP-System, das es ermöglicht, USB-Geräte (Schneideplotter, Drucker, Scanner) von einem Raspberry Pi über das Netzwerk an Windows-, macOS- und Linux-Rechner weiterzuleiten.

Das Gerät erscheint auf dem Client-Rechner als lokal angeschlossenes USB-Gerät.

---

## Architektur

```
┌───────────────────────────────────────┐
│         Raspberry Pi (Server)          │
│                                        │
│  ┌────────────┐  ┌──────────────────┐  │
│  │ USB-Gerät  │  │   Docker         │  │
│  │ (Plotter)  │  │  ┌────────────┐  │  │
│  └─────┬──────┘  │  │  usbipd    │  │  │
│        │         │  │  :7575     │  │  │
│        └─────────►  ├────────────┤  │  │
│                  │  │ FastAPI    │  │  │
│                  │  │  :7576     │  │  │
│                  │  ├────────────┤  │  │
│                  │  │ Web UI     │  │  │
│                  │  └────────────┘  │  │
│                  └──────────────────┘  │
└───────────────────────────────────────┘
              │ TCP/IP
              ▼
┌───────────────────────────────────────┐
│         Client-Rechner                 │
│                                        │
│  ┌────────────────────────────────┐   │
│  │     Loki-Client GUI            │   │
│  │  (Python + tkinter/customtkinter│   │
│  └─────────────┬──────────────────┘   │
│                │                       │
│         usbip attach                  │
│                │                       │
│         /dev/ttyUSB0                  │
│         oder COM3                     │
│         (Gerät erscheint lokal!)      │
└───────────────────────────────────────┘
```

---

## Komponenten

### Server (`/server/`)

| Datei | Beschreibung |
|-------|-------------|
| `docker-compose.yml` | Docker Compose Konfiguration |
| `Dockerfile` | Container-Image (Python 3.12 + usbip tools) |
| `api/main.py` | FastAPI Hauptanwendung, REST-Endpunkte, WebSocket |
| `api/models.py` | Pydantic Datenmodelle |
| `api/usbip.py` | USB/IP Gerätemanager (usbipd wrapper) |
| `api/discovery.py` | mDNS/Bonjour Server-Ankündigung (Zeroconf) |
| `scripts/start.sh` | Container-Startskript |
| `scripts/setup-host.sh` | Einmalige Host-Vorbereitung (Kernel-Module) |
| `web/index.html` | Web-Dashboard (HTML/CSS/JS) |

### Client (`/client/`)

| Datei | Beschreibung |
|-------|-------------|
| `loki_client.py` | Haupt-GUI-Anwendung (tkinter/customtkinter) |
| `core/api_client.py` | HTTP-Client für Server-API |
| `core/discovery.py` | mDNS-Serversuche (Zeroconf) |
| `core/usbip_attach.py` | Platform-spezifischer USB/IP-Attach (Linux/Win/Mac) |
| `linux/install.sh` | Linux-Installer |
| `windows/install.ps1` | Windows-Installer (PowerShell) |
| `mac/install.sh` | macOS-Installer |

---

## API-Endpunkte

### REST API (Port 7576)

| Method | Endpoint | Beschreibung |
|--------|----------|-------------|
| GET | `/health` | Health-Check |
| GET | `/api/status` | Server-Status (CPU, RAM, etc.) |
| GET | `/api/devices` | Alle USB-Geräte auflisten |
| GET | `/api/devices/shared` | Nur freigegebene Geräte |
| POST | `/api/devices/share` | Gerät freigeben |
| POST | `/api/devices/unshare` | Freigabe beenden |
| GET | `/api/clients` | Verbundene Clients |
| GET | `/api/config` | Server-Konfiguration |
| GET | `/` | Web-Dashboard |

### WebSocket

| Endpoint | Beschreibung |
|----------|-------------|
| `ws://<host>:7576/ws` | Echtzeit-Updates (device_shared, device_unshared) |

### Ports

| Port | Protokoll | Beschreibung |
|------|-----------|-------------|
| 7575 | TCP | USB/IP Protokoll (usbipd) |
| 7576 | HTTP/WS | Management API + Web UI |

---

## Konfiguration (Umgebungsvariablen)

| Variable | Standard | Beschreibung |
|----------|---------|-------------|
| `LOKI_PORT` | `7575` | USB/IP Protokollport |
| `LOKI_API_PORT` | `7576` | API & Web UI Port |
| `LOKI_SECRET` | leer | Optionales Passwort für API-Zugriff |
| `LOKI_ALLOW_ALL` | `true` | Alle Geräte freigebar |

---

## Kernel-Module

Der Server benötigt diese Linux-Kernel-Module:

| Modul | Seite | Beschreibung |
|-------|-------|-------------|
| `usbip_core` | Server | USB/IP Basismodul |
| `usbip_host` | Server | USB/IP Host-Treiber (Geräte exportieren) |
| `vhci_hcd` | Client | USB/IP Client-Treiber (Geräte importieren) |

---

## Plattform-Unterstützung

### Linux (Server & Client)
- Volle native USB/IP-Unterstützung über Kernel-Module
- Automatische mDNS-Erkennung
- Installer für Debian/Ubuntu/Raspberry Pi OS

### Windows (Client)
- Benötigt [usbip-win](https://github.com/cezanne/usbip-win/releases) Driver
- PowerShell Installer
- Gerät erscheint als COM-Port oder USB-Gerät

### macOS (Client)
- macOS hat keinen nativen USB/IP Kernel-Support
- Option 1: Lima VM (`brew install lima`) — empfohlen
- Option 2: Serial-Forwarding für Plotter (via TCP-zu-seriell)
- Option 3: Manuelle Linux-VM (UTM, Parallels, VMware)

---

## Entwicklungs-Roadmap

### v1.0.0 (aktuell)
- [x] Server: Docker Compose + FastAPI + usbipd
- [x] Web-Dashboard
- [x] mDNS Auto-Discovery
- [x] Cross-Platform Client (Linux/Win/Mac)
- [x] USB-Gerät Attach/Detach
- [x] GitHub Actions CI/CD

### v1.1.0 (geplant)
- [ ] Authentifizierung (API-Keys, Passwort)
- [ ] Gerätezugriffslisten (Whitelist/Blacklist)
- [ ] Serial-Forwarding-Modus für macOS
- [ ] Standalone-Binaries (PyInstaller) für Windows/Mac
- [ ] Systemd-Service-Mode (ohne Docker)

### v1.2.0 (geplant)
- [ ] macOS nativer Kernel-Treiber (Network Extension)
- [ ] Mobile App (iOS/Android) für Monitoring
- [ ] Mehrere Server gleichzeitig
- [ ] Geräteprofil-Speicherung (Auto-Reconnect)

---

## Abhängigkeiten

### Server
| Paket | Version | Beschreibung |
|-------|---------|-------------|
| fastapi | 0.115.0 | Web-Framework |
| uvicorn | 0.30.6 | ASGI-Server |
| pydantic | 2.9.2 | Datenvalidierung |
| pyusb | 1.2.1 | USB-Geräteerkennung |
| zeroconf | 0.136.2 | mDNS/Bonjour |
| psutil | 6.1.0 | Systeminfo |

### Client
| Paket | Version | Beschreibung |
|-------|---------|-------------|
| httpx | 0.27.2 | HTTP-Client |
| zeroconf | 0.136.2 | mDNS-Serversuche |
| websockets | 13.1 | WebSocket-Client |
| customtkinter | 5.2.2 | Moderne GUI |
| Pillow | 10.4.0 | Bildverarbeitung |

---

## Projekthistorie

| Datum | Version | Änderungen |
|-------|---------|------------|
| 2025-04 | 1.0.0 | Initialer Release by BangerTECH: Server + Client, Docker, CI/CD |
