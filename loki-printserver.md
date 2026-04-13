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
cd Loki-PrintServer/server
docker compose up -d
```

### Client installieren

Unter **https://github.com/BangerTech/Loki-PrintServer/releases** die neueste Version herunterladen:

- **macOS (Apple Silicon + Intel):** `Loki-Client.dmg` → in Applications ziehen
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
| `api/main.py` | FastAPI Hauptapp, alle Endpunkte, Auto-Share Startup-Logik |
| `api/models.py` | Pydantic Datenmodelle (inkl. `is_infrastructure`) |
| `api/usbip.py` | USB-Geräteerkennung (pyusb) + KNOWN_DEVICES Datenbank |
| `api/forwarder.py` | Forwarding Manager (USB/IP, Serial, CUPS) + State-Persistenz |
| `api/discovery.py` | mDNS Ankündigung (Zeroconf, Service: `_lokiprint._tcp.local.`) |
| `scripts/start.sh` | Container-Start (Module, CUPS, avahi, uvicorn) |
| `web/index.html` | Web-Dashboard |
| `web/icon_*.png` | Icons für Dashboard + Favicon |

### Client (`client/`)

| Datei | Beschreibung |
|-------|-------------|
| `tray_app.py` | Haupt-App: macOS Menu Bar (rumps) + Tray (pystray) |
| `onboarding.py` | Onboarding-Wizard (Schritt 1-4, customtkinter) |
| `core/api_client.py` | HTTP-Client für Server-API |
| `core/config.py` | Server-Liste persistieren (`~/.config/loki-printserver/`) |
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
| GET | `/api/devices` | Alle USB-Geräte mit `forward_info` und `is_infrastructure` |
| GET | `/api/devices/shared` | Nur freigegebene Geräte |
| POST | `/api/devices/share` | Gerät freigeben (USB/IP + Serial + CUPS) |
| POST | `/api/devices/unshare` | Freigabe beenden |
| POST | `/api/devices/{bus_id}/auto-share?enabled=true\|false` | Auto-Share-Flag setzen |
| GET | `/api/devices/{bus_id}/forward` | Forwarding-Details eines Geräts |
| GET | `/api/config` | Server-Konfiguration |
| WS | `/ws` | Echtzeit-Updates (`device_shared`, `device_unshared`) |

### Forwarding Info (Response von `/api/devices/share`)

```json
{
  "success": true,
  "bus_id": "1-3",
  "forward_info": {
    "shared": true,
    "auto_share": false,
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

## Konfiguration (Umgebungsvariablen)

| Variable | Standard | Beschreibung |
|----------|---------|-------------|
| `LOKI_PORT` | `7575` | USB/IP Port |
| `LOKI_API_PORT` | `7576` | API + Dashboard Port |
| `LOKI_SECRET` | leer | Optionales API-Passwort |
| `LOKI_ALLOW_ALL` | `true` | Alle Geräte freigebar |
| `AUTO_SHARE_ALL` | `false` | Alle erkannten Peripheriegeräte beim Start automatisch sharen |
| `LOKI_DATA_DIR` | `/etc/loki-printserver` | Verzeichnis für persistente State-Datei |

In der `docker-compose.yml` können diese direkt gesetzt werden:

```yaml
environment:
  - AUTO_SHARE_ALL=true   # jeden USB-Plotter/Drucker sofort sharen
```

---

## Auto-Share — Gerät dauerhaft teilen

Damit ein Gerät **nach jedem Server-Neustart automatisch geshared wird**, gibt es zwei Wege:

### 1. Pro Gerät im Dashboard
Im Web-Dashboard neben dem freigegebenen Gerät die Checkbox **"Auto-Share"** aktivieren. Der Status wird in `/etc/loki-printserver/shared_devices.json` persistiert und beim nächsten Start automatisch wiederhergestellt.

### 2. Alle Geräte automatisch (empfohlen für feste Setups)
In `server/docker-compose.yml`:

```yaml
environment:
  - AUTO_SHARE_ALL=true
```

Dann werden beim Start **alle erkannten Peripheriegeräte** sofort geshared — kein manueller Klick nötig.

### Persistenz-Datei

```
/etc/loki-printserver/shared_devices.json
```

Inhalt (Beispiel):
```json
[
  {
    "bus_id": "1-3",
    "device_class": "Cutting Plotter / Serial",
    "vendor_id": "1a86",
    "product_id": "7523",
    "product_name": "CH340 Serial Cutter (Vevor / Generic)",
    "auto_share": true
  }
]
```

---

## Geräteerkennung & Kategorisierung

### USB-Infrastruktur vs. Peripheriegeräte

Das Dashboard und die API unterscheiden zwei Kategorien:

| Kategorie | `is_infrastructure` | Beschreibung | Sharebar? |
|-----------|--------------------|-|-----------|
| Peripheriegerät | `false` | Plotter, Drucker, Storage, HID etc. | ✅ Ja |
| Infrastruktur | `true` | USB-Hubs, Root-Controller, Linux Foundation | ❌ Nein |

Infrastruktur-Geräte werden im Dashboard in einem **ausgeklappten Bereich** am unteren Ende angezeigt (gedimmt, kein Share-Button). Der Gerätezähler oben zeigt nur echte Peripheriegeräte.

**Als Infrastruktur erkannt:**
- `bDeviceClass == 0x09` (USB Hub)
- Vendor ID `1d6b` (Linux Foundation — Root Hubs)
- Vendor ID `0000` / `0000:0000` (Platzhalter)

### KNOWN_DEVICES Datenbank (`server/api/usbip.py`)

Bekannte Geräte werden anhand ihrer `vendor_id:product_id` angereichert, wenn der USB-Descriptor keine Strings enthält:

| VID:PID | Hersteller | Produkt |
|---------|-----------|---------|
| `1a86:7523` | Cutting Plotter | CH340 Serial Cutter (Vevor / Generic) |
| `1a86:7522` | Cutting Plotter | CH340K Serial Cutter |
| `0403:6001` | Cutting Plotter | FT232 Serial Cutter (Roland / FTDI) |
| `10c4:ea60` | Cutting Plotter | CP2102 Serial Cutter |
| `0b4d:110c` | Graphtec | FC8600 Cutting Plotter |
| `0459:0069` | Silhouette | Silhouette Cameo 4 |
| `1949:006a` | Cricut | Cricut Maker |
| `0922:0028` | Dymo | DYMO LabelWriter 450 |
| `04f9:0027` | Brother | Brother QL Label Printer |

Geräte der Klasse `Vendor Specific` oder `Device` mit bekannter VID:PID werden automatisch als `Cutting Plotter / Serial` eingestuft.

### Dashboard-Icons je Geräteklasse

| Device Class | Icon |
|---|---|
| Cutting Plotter / Serial | ✂️ |
| Printer | 🖨️ |
| Mass Storage | 💾 |
| Hub / Infrastructure | 🔗 |
| HID | ⌨️ |
| Image | 📷 |
| Audio | 🔊 |
| Video | 🎥 |
| Wireless Controller | 📶 |

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
# Version anpassen & taggen
git add -A && git commit -m "Release v1.1.0"
git tag v1.1.0
git push && git push --tags
```

GitHub Actions baut dann automatisch:
- macOS `.dmg` → GitHub Release
- Windows `.exe` → GitHub Release
- Linux Binary → GitHub Release

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
| 2026-04 | 1.0.1 | Auto-Share: Geräte werden nach Neustart automatisch wiederhergestellt. `AUTO_SHARE_ALL` Env-Var. Dashboard Auto-Share Toggle pro Gerät. |
| 2026-04 | 1.0.2 | Geräteliste: Trennung in Peripheriegeräte und USB-Infrastruktur (Hubs/Controller). Infrastruktur ausgegraut ohne Share-Funktion. |
| 2026-04 | 1.0.3 | Dashboard-Icons: ✂️ für Schneideplotter, 🔊 Audio, 🎥 Video, 📶 Wireless. |
| 2026-04 | 1.0.4 | Ruff Lint-Fixes: alle F541/F401/F841/E701/E402 Warnings im Client-Code behoben. |
