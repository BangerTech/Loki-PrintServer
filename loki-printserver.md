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

- **macOS:** `Loki-Client.dmg` → in Applications ziehen → Rechtsklick → Öffnen (einmalig, da nicht notarisiert)
- **Windows:** `LokiClient-Setup.exe` → Installer ausführen
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
| `tray_app.py` | Haupt-App: macOS Menu Bar (rumps) + Tray (pystray); Crash-Log nach `~/Library/Logs/Loki-Client/` |
| `onboarding.py` | Onboarding-Wizard (4 Schritte, customtkinter); bringt sich via `NSApp.activateIgnoringOtherApps_` in den Vordergrund |
| `core/api_client.py` | HTTP-Client für Server-API |
| `core/config.py` | Server-Liste persistieren (`~/.config/loki-printserver/`) |
| `core/logger.py` | Zentrales Logging (RotatingFileHandler, 2 MB, 3 Backups) |
| `core/device_db.py` | USB-ID Datenbank (CH340, Plotter, Drucker, etc.) |
| `core/discovery.py` | mDNS-Serversuche (Zeroconf) |
| `core/usbip_attach.py` | Plattform-Attach: USB/IP + Serial + IPP |
| `assets/` | Rundes App-Icon (alle Größen 16–512 + .ico) + rechteckiges Logo |
| `mac/usb-helper/VirtualUSBDevice.swift` | Basis-Klasse: virtuelles USB-Gerät über IOUSBHostControllerInterface |
| `mac/usb-helper/CDCACMDevice.swift` | CDC-ACM Implementation: virtueller USB-Seriell-Port mit TCP-Bridge |
| `mac/usb-helper/main.swift` | Einstiegspunkt für den loki-usb-helper |
| `mac/usb-helper/entitlements.plist` | Entitlement für IOUSBHostControllerInterface |
| `mac/usb-helper/build.sh` | Standalone-Build-Script für den USB-Helper |
| `build/build_mac.sh` | macOS .app + .dmg Builder (PyInstaller `.spec` + `create-dmg` + USB-Helper) |
| `build/build_windows.bat` | Windows .exe Builder (PyInstaller + Inno Setup) |
| `build/installer.iss` | Windows Installer-Skript (Inno Setup) |

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
| PUT | `/api/devices/{bus_id}/name` | Custom-Name setzen (`{"name":"..."}`, leer = Reset) |
| GET | `/api/config` | Server-Konfiguration |
| WS | `/ws` | Echtzeit-Updates (`device_shared`, `device_unshared`, `device_renamed`) |

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
/etc/loki-printserver/custom_names.json
```

`shared_devices.json` (Beispiel):
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

`custom_names.json` (Beispiel):
```json
{
  "1a86:7523": "Mein Schneideplotter"
}
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
| Virtual USB (CDC-ACM) | ✅ `/dev/cu.usbmodem*` (AMFI disabled) | — | — |
| USB/IP | Lima VM (optional) | usbip-win | nativ |
| Serial (PTY) | ✅ `/tmp/tty.loki-*` (Fallback) | com0com / TCP | ✅ `/dev/ttyLOKI*` |
| IPP/CUPS | ✅ Netzwerkdrucker | ✅ Windows IPP | ✅ lpadmin |

### macOS Virtual USB (loki-usb-helper)

Auf macOS erstellt der Loki-Client ein **echtes virtuelles USB CDC-ACM Gerät** über `IOUSBHostControllerInterface`. Dieses Gerät:

- Erscheint als `/dev/cu.usbmodem*` in IOKit
- Übernimmt die **echte VID/PID** des Original-USB-Geräts → Vendor-Software (FineCut, xfcut, etc.) erkennt das Gerät
- Bridget Daten transparent über TCP zum Loki-Server

**Voraussetzungen:**
- macOS 10.15+ (Catalina oder neuer)
- **AMFI (Apple Mobile File Integrity) muss deaktiviert sein** — ohne dies kann der USB-Helper keine Kernel-Verbindung herstellen
- SIP muss (teilweise) deaktiviert sein: Custom Configuration mit deaktiviertem Kext Signing
- Das `loki-usb-helper` Binary wird automatisch ad-hoc signiert mit dem `com.apple.developer.usb.host-controller-interface` Entitlement

**AMFI deaktivieren:**

| Methode | Anleitung |
|---------|-----------|
| **OpenCore Legacy Patcher** | Settings → Kernel Security → "Disable AMFI" aktivieren → Apply → Neustart |
| **OpenCore manuell** | In `config.plist` → `NVRAM` → `boot-args` den Wert `amfi_get_out_of_my_way=1` anhängen → Neustart |
| **Normaler Mac (kein Hackintosh)** | Recovery-Modus → Terminal → `nvram boot-args="amfi_get_out_of_my_way=1"` → Neustart |

> **Hinweis:** Auf Hackintosh/OpenCore-Systemen kann `nvram` nicht direkt aus dem laufenden System heraus boot-args setzen — die Änderung muss in der OpenCore `config.plist` erfolgen.

**VID/PID-Spoofing:** Der USB-Helper erhält die echte Vendor-ID, Product-ID, Herstellername und Produktname vom Server. Damit erscheint z.B. ein Mimaki-Plotter als echtes Mimaki-USB-Gerät (`VID=0x0B4D`) in IOKit — FineCut erkennt ihn dadurch automatisch.

**Fallback:** Wenn der USB-Helper nicht verfügbar ist oder fehlschlägt (z.B. AMFI nicht deaktiviert), wird automatisch auf den PTY-Bridge-Modus zurückgefallen (`/tmp/tty.loki-*`). Dieser funktioniert mit Software die manuelle Port-Eingabe erlaubt, wird aber von IOKit-basierten Programmen nicht erkannt.

---

## Release-Prozess

> **Für KI-Agenten:** Nach jeder Änderung an Client- oder Build-Dateien **immer** committen, pushen und das Tag neu setzen. Nur so startet der GitHub-Actions-Workflow und ein neues `.dmg`/`.exe`/Binary wird gebaut. Änderungen ohne Tag-Push erzeugen **kein** Release.

### Normaler Ablauf nach Änderungen

```bash
# 1. Alle Änderungen committen
git add -A
git commit -m "fix: beschreibung der änderung"

# 2. Auf main pushen
git push origin main

# 3. Altes Tag löschen (lokal + remote)
git tag -d v1.0.0
git push origin :refs/tags/v1.0.0

# 4. Neues Tag setzen und pushen → startet GitHub Actions
git tag -a v1.0.0 -m "v1.0.0"
git push origin v1.0.0
```

Der Push des Tags auf `v*.*.*` triggert `.github/workflows/release.yml` und baut alle drei Plattformen.

### Was GitHub Actions automatisch macht

| Job | Runner | Ausgabe |
|-----|--------|---------|
| `build-mac` | `macos-15-intel` (x86_64) | `Loki-Client.dmg` via `create-dmg` |
| `build-windows` | `windows-latest` | `LokiClient-Setup.exe` (Inno Setup) |
| `build-linux` | `ubuntu-latest` | `Loki-Client-linux` (onefile) |
| `release` | `ubuntu-latest` | Löscht alten Release, erstellt neuen mit allen Assets |

Der Release-Job löscht den bestehenden GitHub-Release via `gh release delete` bevor er neu erstellt wird — dadurch wird auch die Release-Beschreibung immer neu geschrieben.

### Checkliste vor jedem Tag-Push

- [ ] Alle geänderten Dateien committed (`git status` zeigt nichts Offenes)
- [ ] `git push origin main` erfolgreich
- [ ] Altes Tag lokal und remote gelöscht
- [ ] Neues annotiertes Tag (`-a`) gesetzt und gepusht
- [ ] GitHub Actions unter https://github.com/BangerTech/Loki-PrintServer/actions prüfen ob der Workflow gestartet ist

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
| urllib (stdlib) | API-Client (kein externes Paket) |
| Pillow | Icon-Rendering |
| pyobjc-framework-Cocoa | `NSApp.activateIgnoringOtherApps_` (macOS Fenster-Fokus) |
| pyinstaller | macOS/Linux Bundle |

---

## macOS Build — bekannte Stolperfallen

| Problem | Ursache | Fix |
|---------|---------|-----|
| `IncompatibleBinaryArchError: not a fat binary` | `PIL/_imagingtk.so` ist arm64-only, kein universal2-Wheel verfügbar | Runner auf `macos-15-intel` (x86_64) umstellen, kein `--target-arch` nötig |
| `macos-13` runner error | `macos-13` seit Dez 2025 abgeschaltet | `macos-15-intel` verwenden |
| App öffnet sich nicht / keine Menüleiste | `LSUIElement=True` → Onboarding-Fenster öffnet hinter anderen Fenstern | `NSApp.activateIgnoringOtherApps_(True)` in `onboarding.py` |
| "App ist beschädigt" | Gatekeeper-Quarantäne durch Browser-Download | `xattr -cr /Applications/Loki-Client.app` |
| `Failed to create IOUSBHostControllerInterface` | AMFI blockiert Kernel-Zugang für USB-Helper | AMFI deaktivieren: `amfi_get_out_of_my_way=1` in boot-args (OpenCore: in config.plist) |
| FineCut erkennt Plotter nicht | Virtuelle USB-Geräte hatten generische VID/PID | Update auf Version mit VID/PID-Spoofing; Helper übernimmt jetzt die Original-VID/PID |
| "Programm wird auf diesem Mac nicht unterstützt" | Falscher Build-Runner (arm64 statt x86_64) | `macos-15-intel` runner bestätigt x86_64 |
| Bundle-Modifikationen nach PyInstaller | Post-build Info.plist/Datei-Kopien brechen Ad-hoc-Signatur | `info_plist={}` im `.spec`-`BUNDLE`-Block verwenden, keine Post-build-Patches |
| `ValueError: not enough values to unpack` | `collect_all()` Ergebnisse per `+=` auf `a.datas` TOC-Objekt | `collect_all()` **vor** `Analysis()` aufrufen, Ergebnisse als Konstruktor-Parameter übergeben |
| `script not found` | Spec-Datei in `client/build/`, `SPECPATH` zeigt dorthin | Spec in `client/` schreiben, `SPECPATH` für alle Pfade nutzen |

---

## Changelog

| Datum | Version | Änderungen |
|-------|---------|------------|
| 2026-04 | 1.0.0 | Initialer Release by BangerTECH |
| 2026-04 | — | Auto-Share: Geräte werden nach Neustart automatisch wiederhergestellt. `AUTO_SHARE_ALL` Env-Var. Dashboard Auto-Share Toggle pro Gerät. |
| 2026-04 | — | Geräteliste: Trennung in Peripheriegeräte und USB-Infrastruktur (Hubs/Controller). Infrastruktur ausgegraut ohne Share-Funktion. |
| 2026-04 | — | Dashboard-Icons: ✂️ Plotter, 🔊 Audio, 🎥 Video, 📶 Wireless. |
| 2026-04 | — | macOS Build: universal2 → `macos-15-intel` (x86_64); `.spec`-Datei statt CLI-Flags; `create-dmg` für korrektes DMG. |
| 2026-04 | — | macOS Onboarding: `NSApp.activateIgnoringOtherApps_` damit Fenster bei `LSUIElement=True` im Vordergrund erscheint. |
| 2026-04 | — | Brand-Assets: neues rundes App-Icon + rechteckiges Logo in allen Verzeichnissen. |
| 2026-04 | — | Crash-Log: Startup-Fehler werden nach `~/Library/Logs/Loki-Client/` geschrieben. |
| 2026-04 | — | Fix: `USBIPAttacher` → `DeviceAttacher` Import behoben (App startete nicht auf macOS). Startup-Logging + stdout/stderr-Redirect fuer `.app`-Bundles. |
|| 2026-04 | — | Fix: Tray zeigte permanent "Offline" — `_connect_all` crashte durch `self.root`-Guard. 1s Delay für rumps-Event-Loop. |
|| 2026-04 | — | Fix: `forward_info` wurde nicht an Attacher übergeben — Geräte konnten nie attached werden. Wird jetzt nach Share vom Server geholt. |
|| 2026-04 | — | Tray filtert Infrastruktur-Geräte (Hubs, Controller) — nur Peripheriegeräte werden angezeigt. |
|| 2026-04 | — | macOS: Dock-Icon wird nach Onboarding via `NSApplicationActivationPolicyAccessory` versteckt. |
|| 2026-04 | — | Custom Device Names: Geräte können im Dashboard umbenannt werden (✏️). Name wird per `vendor_id:product_id` in `custom_names.json` persistiert und an Clients propagiert. |
|| 2026-04 | — | Onboarding zeigt nur echte Peripheriegeräte (Infrastructure-Filter). |
|| 2026-04 | — | Logging: `core/logger.py` — zentrales RotatingFileHandler-Logging (2 MB, 3 Backups). macOS: `~/Library/Logs/Loki-Client/loki-client.log`, Linux/Windows: `~/.config/loki-printserver/loki-client.log`. Alle Verbindungen, API-Calls, Discovery-Events, Attach/Detach und Fehler werden geloggt. |
|| 2026-04 | — | macOS Virtual USB: `loki-usb-helper` (Swift) erstellt echte virtuelle USB CDC-ACM Geräte über `IOUSBHostControllerInterface`. Erscheint als `/dev/cu.usbmodem*` — erkannt von FineCut, xfcut, etc. Benötigt AMFI disabled. Automatischer Fallback auf PTY-Bridge wenn nicht verfügbar. |
|| 2026-04 | — | VID/PID-Spoofing: Virtuelle USB-Geräte übernehmen die echte Vendor-ID/Product-ID des Original-USB-Geräts vom Server. Vendor-Software (FineCut für Mimaki, etc.) erkennt die Geräte dadurch automatisch. |
