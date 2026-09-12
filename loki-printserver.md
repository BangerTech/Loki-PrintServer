# Loki-PrintServer — Projektdokumentation

## Übersicht

**Loki-PrintServer** teilt USB-Geräte (Schneideplotter, Drucker, Scanner) vom Raspberry Pi über das Netzwerk. Clients unter Windows, macOS und Linux sehen das Gerät als lokal angeschlossen.

**GitHub:** https://github.com/BangerTech/Loki-PrintServer  
**Version:** 1.4.13  
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
- **Windows:** `LokiClient-Setup.exe` als Administrator. Der Microsoft-signierte **USBip**-Treiber wird nach „Fertig“ automatisch still installiert (kein zweites Setup-Fenster). Danach **einmal neu starten**.
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

| Datei | Beschreibung |
|-------|-------------|
| `ruff.toml` | CI-Lint-Regeln (`E4`/`E7`/`E9`, `F`, `UP`) — verhindert Default-Ausweitung neuer Ruff-Versionen |

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
| `build/fetch_usbip_win.ps1` | Lädt den usbip-win2-Installer (USBip 0.9.7.7, nicht im Git) |
| `build/installer.iss` | Windows Installer-Skript (Inno Setup, Admin + usbip.exe install) |

---

## API-Endpunkte

| Method | Endpoint | Beschreibung |
|--------|----------|-------------|
| GET | `/health` | Health-Check (`version` aus `VERSION`) |
| GET | `/api/status` | CPU, RAM (`used`/`total`), Uptime, unique Shared-Zahl, `version` |
| GET | `/api/logs?lines=80` | Letzte Server-Logzeilen (1–500), zuerst Live-Puffer |
| POST | `/api/client-log` | Client-Meldung ins Dashboard-Log (`level`, `message`, `bus_id`, `host`) |
| GET | `/api/devices` | Alle USB-Geräte mit `forward_info` und `is_infrastructure` |
| GET | `/api/devices/shared` | Nur freigegebene Geräte |
| POST | `/api/devices/share` | Gerät freigeben (USB/IP + Serial + CUPS) |
| POST | `/api/devices/unshare` | Freigabe beenden |
| POST | `/api/devices/{bus_id}/auto-share?enabled=true\|false` | Auto-Share-Flag setzen |
| POST | `/api/devices/{bus_id}/attach-mode` | Mimaki: `"usbip"` (Windows) oder `"bridge"` (macOS FineCut) |
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
/etc/loki-printserver/loki-server.log
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

Infrastruktur-Geräte werden im Dashboard in einem **ausgeklappten Bereich** am unteren Ende angezeigt (gedimmt, kein Share-Button). Der Gerätezähler oben zeigt nur echte Peripheriegeräte. Die Kachel **Shared** zählt eindeutige Peripherie nach VID:PID (keine toten bus_ids).

Im Header: Versions-Badge aus `/api/status`, Uptime neben dem Status-Punkt, Umschalter **DE/EN** (Browser-Sprache, sonst EN; Wahl in `localStorage`). Unter der Geräteliste: **Live-Protokoll** (`GET /api/logs`, Auto-Refresh, Pause).

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
| Virtual USB (CDC-ACM / Vendor) | ✅ `/dev/cu.usbmodem*` + FineCut Vendor-USB (AMFI disabled) | — | — |
| USB/IP | Lima VM (optional) | ✅ usbip-win (Mimaki CG-SR) | nativ |
| Serial (PTY / COM) | ✅ `/tmp/tty.loki-*` (Fallback) | com0com + com2tcp | ✅ `/dev/ttyLOKI*` |
| IPP/CUPS | ✅ Netzwerkdrucker | ✅ Windows IPP | ✅ lpadmin |

### macOS Virtual USB (loki-usb-helper)

Auf macOS erstellt der Loki-Client ein **echtes virtuelles USB CDC-ACM Gerät** über `IOUSBHostControllerInterface`. Dieses Gerät:

- Erscheint als `/dev/cu.usbmodem*` in IOKit
- Übernimmt die **echte VID/PID** wenn kein kollidierender macOS-Treiber existiert (Mimaki, etc.) → Vendor-Software erkennt das Gerät
- Nutzt generische VID/PID für Geräte mit konfliktbehafteten Treibern (CH340, FTDI, etc.)
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

**Smart VID/PID:** Der USB-Helper erhält die echte Vendor-ID, Product-ID, Herstellername und Produktname vom Server. Er prüft eine Blockliste bekannter VIDs mit kollidierenden macOS-Treibern (CH340 `0x1A86`, FTDI `0x0403`, Prolific `0x067B`, CP210x `0x10C4`). Für blockierte VIDs wird eine generische CDC-ACM VID/PID verwendet (OpenMoko `0x1D50:0x614E`). Für alle anderen VIDs (z.B. Mimaki `0x0A50`) wird die echte VID/PID durchgereicht — damit erkennt Vendor-Software wie FineCut das Gerät automatisch.

**Human-Readable Device Names:** Der virtuelle Port heißt `/dev/cu.usbmodem<DeviceName>1` statt einer generischen Nummer. Der Name wird aus `custom_name`, `manufacturer`+`product` oder dem Fallback `LOKI<port>` generiert und als USB Serial Number an macOS übergeben. Beispiel: `/dev/cu.usbmodemMIMAKICGSR1`.

**CDC SERIAL_STATE Notification:** Der macOS AppleUSBACM-Treiber benötigt eine `SERIAL_STATE`-Benachrichtigung (USB CDC 1.1 §6.3.5) auf dem Interrupt-IN-Endpoint mit DCD+DSR-Bits, bevor er den Bulk-Datentransfer über EP 0x01/0x81 startet. Ohne diese Notification erkennt die Software zwar den Port, kann aber keine Daten senden/empfangen. Der Helper sendet diese Notification automatisch beim ersten Interrupt-Poll und bei jeder DTR-Änderung.

**Datenfluss-Logging:** Der USB-Helper loggt den kompletten Datenpfad in `~/Library/Logs/Loki-Client/usb-helper.log`:
- `CDC: sending SERIAL_STATE notification (DCD+DSR)` — Notification an macOS
- `USB→TCP: N bytes` — Daten von der Schneidsoftware zum Plotter
- `TCP recv: N bytes` — Antwort vom Plotter empfangen
- `TCP→USB: N bytes` — Antwort an die Schneidsoftware übergeben

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

# 3. Neues Tag setzen (Semver: Major.Minor.Patch)
git tag v1.2.2

# 4. Tag pushen → startet GitHub Actions
git push origin v1.2.2
```

Der Push eines Tags im Format `v*.*.*` triggert `.github/workflows/release.yml` und baut alle drei Plattformen.

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
- [ ] Neues Tag mit inkrementierter Version gesetzt (z.B. `v1.2.1` → `v1.2.2`)
- [ ] Tag gepusht (`git push origin v1.2.2`)
- [ ] GitHub Actions unter https://github.com/BangerTech/Loki-PrintServer/actions prüfen ob der Workflow gestartet ist
- [ ] Optional lokal: `ruff check server/api/ client/` (entspricht den CI-Lint-Jobs)

### CI / Lint (`.github/workflows/ci.yml`)

Die Jobs **Lint & Test Server** und **Lint & Test Client** führen u. a. `ruff check server/api/` bzw. `ruff check client/` aus. Schlägt Ruff fehl, endet der Job **sofort** (oft nach wenigen Sekunden) — das ist dann ein **Lint-Problem im Code**, nicht automatisch ein „Minuten-Budget“-Thema.

Die Regelmenge steht in `ruff.toml` (`E4`/`E7`/`E9`, `F`, `UP`). Ohne diese Datei übernimmt ein aktuelles Ruff (0.16+) sehr viele Extra-Regeln (BLE, S, ASYNC, …) und CI scheitert an Dutzenden Hinweisen.

Wenn GitHub stattdessen meldet, dass der Job **gar nicht gestartet** wurde (*recent account payments have failed* / *spending limit*), liegt es an **Billing** (Zahlungsmittel, offene Rechnungen, Actions-Spending-Limit): **Settings → Billing and plans** (bzw. Organisation → Billing).

Lokal prüfen (Python 3.12 empfohlen, wie im Workflow):

```bash
pip install ruff
ruff check server/api/ client/
```

Viele Ruff-Hinweise sind mit `ruff check --fix` automatisch behebbar.

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

## Windows Build — bekannte Stolperfallen

| Problem | Ursache | Fix |
|---------|---------|-----|
| `IPersistFile::Save failed; Code 0x80070005. Zugriff verweigert` beim Anlegen von `C:\Users\Public\Desktop\Loki-Client.lnk` | Installer läuft per-user (`PrivilegesRequired=lowest`), die Desktop-Verknüpfung zielte auf `{commondesktop}` (öffentlicher Desktop, braucht Admin) | `{autodesktop}` nutzen — ohne Elevation landet die `.lnk` auf dem Benutzer-Desktop |
| `Failed to execute script 'tray_app'` / `ValueError: <function …_open_dashboard>` | pystray erlaubt nur 0–2 Positionsargumente; `def _open_dashboard(_, h=host, p=port)` hat 3 → Crash beim Tray-Start | Callbacks über `_pystray_action()` wrappen (nur `icon, item`) |
| Dashboard „Shared“, FineCut/Plotter nicht grün, kein Schneiden | Windows hat kein virtuelles USB. Client meldete fälschlich `tcp://server:7580` als Attach — FineCut braucht das echte USB-Gerät | Server `attach-mode=usbip`; Setup installiert usbip-win (UAC). Ohne Treiber Dialog statt Fake-Attach |
| `usbip: error: attacher.exe not found` / UAC „Zulassen“ nicht klickbar | `usbip.exe` sucht `attacher.exe` im Arbeitsverzeichnis (Loki-App), nicht neben sich; Fehlerdialog lag über der UAC | Attach mit `cwd={app}\usbip-win`; kein blockierender Auto-Attach-Dialog |
| PowerShell-/Konsolenfenster poppt im Sekundentakt auf | `subprocess.run`/`Popen` ohne `CREATE_NO_WINDOW` in einer `--noconsole`-App; Auto-Attach ohne Retry-Bremse | `CREATE_NO_WINDOW` überall; 60s-Backoff pro `bus_id` in `_auto_attach_shared` |
| Fehlerdialog „Plotter not attached“ nicht wegklickbar / stapelt sich | `_notify` erzeugte pro Aufruf ein neues `tk.Tk()` aus einem Hintergrund-Thread | Native Win32 `MessageBoxW` (thread-sicher, immer klickbar) + Dedup |
| `usbip: error: vhci driver is not loaded` | Altes usbip-win 0.3.5 (2021, Debug) lädt auf aktuellem Windows 11 nicht, auch mit Test-Signing | Ab 1.4.12: offizieller **USBip/usbip-win2**-Installer (attestiert). Nach Loki-Setup USBip durchklicken, neu starten. Bei weiterem Fehler: Speicherintegrität (Core Isolation) aus |
| Setup: `bcdedit.exe` CreateProcess Code 2 (Datei nicht gefunden) | 32-Bit-Inno-Setup sieht `{sys}` als SysWOW64; `bcdedit` existiert nur in System32 (64-Bit) | `ArchitecturesInstallIn64BitMode=x64`; `bcdedit` per Pascal `Exec` (kein Abbruch wenn es fehlschlägt) |
| CI: Inno `Identifier expected` in `installer.iss` | `{...}` im `[Code]`-Block ist keine Pascal-Kommentar-Syntax, Inno parsed `{sys}` als Konstante | Nur `//`-Kommentare im `[Code]`-Block |

---

## macOS Build — bekannte Stolperfallen

| Problem | Ursache | Fix |
|---------|---------|-----|
| `IncompatibleBinaryArchError: not a fat binary` | `PIL/_imagingtk.so` ist arm64-only, kein universal2-Wheel verfügbar | Runner auf `macos-15-intel` (x86_64) umstellen, kein `--target-arch` nötig |
| `macos-13` runner error | `macos-13` seit Dez 2025 abgeschaltet | `macos-15-intel` verwenden |
| App öffnet sich nicht / keine Menüleiste | `LSUIElement=True` → Onboarding-Fenster öffnet hinter anderen Fenstern | `NSApp.activateIgnoringOtherApps_(True)` in `onboarding.py` |
| "App ist beschädigt" | Gatekeeper-Quarantäne durch Browser-Download | `xattr -cr /Applications/Loki-Client.app` |
| `Failed to create IOUSBHostControllerInterface` | AMFI blockiert Kernel-Zugang für USB-Helper | AMFI deaktivieren: `amfi_get_out_of_my_way=1` in boot-args (OpenCore: in config.plist) |
| Kein `/dev/cu.usbmodem*` erscheint | VID/PID eines Geräts mit eigenem macOS-Treiber (CH340/FTDI) im Descriptor | Blockliste im Helper: CH340/FTDI/PL2303/CP210x → automatisch generische CDC-ACM VID/PID |
| Port erkannt, aber Verbindungstest schlägt fehl | Fehlende CDC SERIAL_STATE Notification (DCD+DSR) auf Interrupt EP | Ab v1.2.1: Notification wird automatisch gesendet; `wMaxPacketSize` 10B, `bInterval` 16ms |
| Vevor-Plotter zeigt generischen Portnamen | `bMaxPacketSize0` war 8 → String-Descriptors wurden abgeschnitten → macOS nutzte Location-ID statt Serial Number | Ab v1.2.4: `bMaxPacketSize0`=64, Serial Number wird korrekt übertragen. `custom_name` wird als Device Name genutzt → `/dev/cu.usbmodemVevor135Plotter1` |
| Mimaki nicht erreichbar / kein `/dev/ttyUSB*` auf Server | `usbip-host` und `usbserial` sind mutual exclusive — usbip-host stahl das Gerät | Ab v1.2.4: Serial-Forwarding wird VOR `usbip bind` versucht. `start.sh` entlädt stale `usbip_host`, bindet Plotter zuerst an `usbserial`, dann erst `usbip_host` neu laden |
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
|| 2026-04 | — | Smart VID/PID: Blockliste für VIDs mit kollidierenden macOS-Treibern (CH340 0x1A86, FTDI 0x0403, Prolific 0x067B, CP210x 0x10C4) — für diese generische CDC-ACM VID/PID. Andere VIDs (z.B. Mimaki 0x0A50) werden durchgereicht → FineCut erkennt den Plotter automatisch. |
|| 2026-04 | 1.2.0 | Human-Readable Device Names: `/dev/cu.usbmodemMIMAKICGSR1` statt `/dev/cu.usbmodemLOKI7581`. Name aus custom_name, manufacturer+product oder Fallback. Fix: Swift Kompilierfehler (`log()` in statischer Methode). |
|| 2026-04 | 1.2.1 | Fix: CDC SERIAL_STATE Notification — FineCut erkannte den Port, aber Verbindungstest schlug fehl weil macOS nie DCD+DSR-Signal auf Interrupt-EP 0x82 bekam → Bulk-Datentransfer startete nicht. Interrupt-EP `wMaxPacketSize` 8→10 Bytes, `bInterval` 255→16ms. Data-Flow-Logging in usb-helper.log. |
|| 2026-04 | 1.2.4 | Fix: `usbip-host` und `usbserial` Treiberkonflikt — serial forwarding jetzt VOR `usbip bind` (mutual exclusive). `start.sh`: stale `usbip_host` entladen, Plotter zuerst an `usbserial` binden, dann `usbip_host` mit sauberem `match_busid` laden. `_find_serial_device`: korrekte sysfs-Auflösung per `busnum`/`devnum` statt erstes `ttyUSB*`. `_try_bind_usbserial`: native USB-Plotter (Mimaki etc.) automatisch an `usbserial generic` binden. `docker-compose.yml`: `/sys` und `/dev` Mounts für Container-Gerätesichtbarkeit. `AUTO_SHARE_ALL` Default auf `true`. Shutdown bewahrt saved state für auto-restore. Client: `bMaxPacketSize0` 8→64 verhindert abgeschnittene String-Descriptors → korrekte `/dev/cu.usbmodem*`-Namen. `SERIAL_STATE` beim ersten Interrupt-Poll statt nur nach DTR → FineCut-Verbindungstest funktioniert. udev-Regel auf Host für automatische Plotter-Bindung. |
|| 2026-04 | 1.3.0 | **Vendor-Specific USB Device Mode**: Neues `VendorDevice.swift` erstellt virtuelle USB-Geräte mit den **echten Hersteller-Deskriptoren** (Klasse 0xFF, Vendor-Specific). Für Geräte wie Mimaki, deren Software (FineCut) per IOKit direkt auf das USB-Device zugreift statt über Serial. Automatische Modus-Erkennung per VID (`0x0A50` = vendor, alle anderen = CDC-ACM). `VirtualUSBDevice.swift`: Endpoint-Typ-Erkennung aus Konfigurationsdeskriptor statt hardcoded. Stale-Helper-Cleanup beim App-Start verhindert Phantom-Devices. Per-Port Log-Dateien. |
|| 2026-04 | 1.4.0 | **Raw USB Bridge**: FineCut-Binary reverse-engineered — nutzt `IOServiceMatching("IOUSBDevice")` + `IOUSBDeviceInterface942` + Bulk ReadPipe/WritePipe. Vendor-Control-Request `0xC0/0x01` für Plotter-Identifikation (`CG-SR-00`). Neues `usb_bridge.py` auf dem Server: pyusb-basierte Raw-USB-Bridge ersetzt socat+usbserial für Mimaki. Framed TCP-Protokoll (Control+Bulk) leitet alle USB-Transaktionen transparent vom Mac-VendorDevice zum echten Plotter weiter. `VendorDevice.swift`: Control-Request-Forwarding via TCP-Protokoll. `VirtualUSBDevice.swift`: Vendor-spezifische Requests (0x40/0xC0) werden an Subklasse delegiert. `forwarder.py`: automatische Erkennung vendor-spezifischer Geräte per VID → Raw USB Bridge statt socat. |
|| 2026-04 | 1.4.1 | **Kritische Fixes für Raw USB Bridge**: (1) `_resolve_usbip_busid` gab immer den ersten busid zurück → usbip band den Mimaki für ALLE Geräte um und stahl ihn von pyusb. Fix: korrekte sysfs-Auflösung per `busnum`/`devnum`. (2) `start.sh` registrierte Mimaki bei `usbserial_generic` → Fix: Mimaki aus usbserial-Registration entfernt, stattdessen Kernel-Treiber aktiv entbunden. (3) `share_device` erstellt bei Re-Share keine zweite Bridge-Instanz mehr (erkennt aktive Bridge via `serial_dev` Prefix). (4) Bridge Self-Test: sendet `OH;` nach Device-Open und prüft Plotter-Antwort. **Ergebnis: FineCut schneidet erfolgreich über Loki-PrintServer.** |
|| 2026-04 | 1.4.2 | **Fix: CDC-ACM Device Name**: macOS `AppleUSBACMData` Treiber nutzt die USB-Seriennummer für den `/dev/cu.usbmodem<name>` Pfad nur wenn sie ≤ 8 ASCII-Zeichen hat (Quellcode-Analyse des Apple CDC-Treibers). Längere Namen verursachen Fallback auf Location-ID-Naming (z.B. `usbmodem89101`). Fix: `CDCACMDevice.swift` und `usbip_attach.py` kürzen den Device-Name auf max 8 Zeichen. Beispiel: `Vevor135Plotter` → `Vevor135` → `/dev/cu.usbmodemVevor1351`. |
|| 2026-04 | — | **CI / Doku:** Ruff-Fixes (`F541` überflüssige `f`-Strings in `usbip_attach.py`; `F401`/`F811`/`F541` in `usb_bridge.py`). `loki-printserver.md`: Abschnitt CI/Lint + Billing-Hinweis. README: Lizenzblock ohne Third-Party-Disclaimer-Zeile. |
|| 2026-09 | 1.4.4 | **Fix: Windows Installer + Tray:** Desktop-Shortcut `{commondesktop}` → `{autodesktop}` (kein `0x80070005` mehr auf `C:\Users\Public\Desktop`). pystray-Callbacks über `_pystray_action()` gewrappt — Tray startet wieder (`ValueError` durch >2 Positionsargumente). |
|| 2026-09 | — | **CI / Ruff:** `ruff.toml` mit fester Regelmenge (E/F/UP). `Optional[X]` → `X \| None` in Server- und Client-Code. Verhindert den 76-Fehler-Break durch Ruff 0.16-Defaults (UP045, BLE001, S110, …). |
|| 2026-09 | — | **Fix: Windows Mimaki-Plot:** Client attached CG-SR nur als `tcp://…:7580` (Raw-USB-Bridge) — FineCut sieht kein USB-Gerät. Neu: `POST /api/devices/{bus_id}/attach-mode` schaltet Pi auf USB/IP; Windows hängt per usbip-win an. Ohne usbip-win Dialog statt Fake-Attach. macOS-Bridge unverändert. |
|| 2026-09 | 1.4.5 | **Windows Setup bündelt usbip-win 0.3.5** (signierter VHCI-Treiber). `usbip.exe install` läuft im Setup (Admin/UAC). Anwender muss den Treiber nicht mehr separat holen. macOS unverändert. |
|| 2026-09 | 1.4.6 | **Fix: `attacher.exe not found`:** usbip.exe wird mit `cwd` + PATH im `usbip-win`-Ordner gestartet. Auto-Attach-Dialog nicht mehr topmost (hat UAC „Zulassen“ überdeckt). **Dashboard-Plotter flackert nicht mehr:** Libusb-Reset max. alle 15s; gesharete Geräte bleiben sichtbar wenn usbip/pyusb sie kurz versteckt; VID:PID-Match nach USB-Re-Enumeration. macOS-Client unverändert. |
|| 2026-09 | 1.4.7 | **Dashboard:** Version kommt aus `VERSION` (`/health`, `/api/status`, Header-Badge). Shared-Zahl = eindeutige VID:PID, tote bus_ids werden aufgeräumt. Memory `used / total`, Uptime neben Status, Karten umbrechen. Live-Protokoll (`GET /api/logs`) mit Auto-Refresh und Pause. Sprache DE/EN (`localStorage`, Browser-Default). macOS-Client unverändert. |
|| 2026-09 | 1.4.8 | **Fix: Windows Konsolenfenster-Flash:** `CREATE_NO_WINDOW` zu allen `subprocess.run`/`Popen`-Calls in `usbip_attach.py` (inkl. `_run_usbip_win`, `com2tcp`). **Fix: usbip-Retry-Loop:** Auto-Attach-Fehler werden mit 60s-Backoff gedrosselt — kein ständiges Aufpoppen mehr wenn `usbip.exe attach` fehlschlägt. Backoff wird zurückgesetzt wenn das Gerät nicht mehr sichtbar ist oder manuell umgeschaltet wird. macOS-Client unverändert. |
|| 2026-09 | 1.4.9 | **Fix: `vhci driver is not loaded`:** Setup aktiviert Test-Signing (`bcdedit /set testsigning on`) und fordert Reboot — usbip-win 0.3.5 ist test-signiert. Client prüft vor `attach` per `usbip port` ob der VHCI-Treiber geladen ist, versucht sonst 1× elevated `usbip install` (UAC) und zeigt eine klare Anleitung (Test-Signing/Secure-Boot). **Fix: Fehlerdialog:** native Win32 `MessageBoxW` statt tkinter aus Hintergrund-Thread — immer wegklickbar, keine gestapelten Dialoge (Dedup). macOS-Client unverändert. |
|| 2026-09 | 1.4.10 | **Fix: Setup `bcdedit.exe` nicht gefunden (CreateProcess 2):** 32-Bit-Inno-Setup hat `{sys}` nach SysWOW64 umgeleitet. Setup ist jetzt 64-Bit; Test-Signing läuft per Pascal `Exec` und bricht die Installation nicht mehr ab. |
|| 2026-09 | 1.4.11 | **Fix: Inno Setup Compile `Identifier expected`:** `{...}`-Kommentare im `[Code]`-Block werden als Konstanten gelesen (`{sys}`). Jetzt `//`-Kommentare. `build_windows.bat` bricht bei ISCC-Fehler wirklich ab. |
|| 2026-09 | 1.4.12 | **Windows-Treiber:** usbip-win 0.3.5 durch **usbip-win2 0.9.7.7 (USBip)** ersetzt. Loki-Setup installiert USBip nach Finish **still** (kein zweites Wizard). Client sucht `C:\Program Files\USBip\usbip.exe` zuerst. **Dashboard-Log:** Live-Puffer statt leerer Datei; Windows-Client schickt Attach-Fehler per `POST /api/client-log`. macOS-Client-Pfad unverändert. |
|| 2026-09 | 1.4.13 | **Windows:** USBip legt kein Desktop-Icon mehr an (`/TASKS=""` + Löschen bekannter `.lnk`). Tray-Icon wird **grün** wenn mindestens ein Gerät wirklich attached ist (nicht nur Server erreichbar); Refresh nach Attach-Wechsel. |
