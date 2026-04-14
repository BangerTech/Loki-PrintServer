#!/bin/bash
# Loki-Client — macOS .app + .dmg Builder
# Run from repo root: bash client/build/build_mac.sh
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/../.."
ROOT="$(pwd)"
CLIENT="$ROOT/client"

APPNAME="Loki-Client"
BUNDLE_ID="com.bangertech.loki-client"
VERSION="1.0.0"

echo "=========================================="
echo "  Loki-Client v${VERSION} — macOS Build"
echo "  by BangerTECH"
echo "=========================================="

cd "$CLIENT"

# ── 1. Dependencies ────────────────────────────────────────────────────────────
echo "[1/5] Installing dependencies..."
pip3 install --quiet pyinstaller pillow rumps zeroconf httpx websockets \
    customtkinter pystray packaging pyobjc-framework-Cocoa 2>&1 | grep -E "Successfully|already|ERROR" || true
brew install create-dmg 2>/dev/null || true
brew install socat 2>/dev/null || true

# ── 2. Convert icon → .icns ────────────────────────────────────────────────────
echo "[2/5] Creating .icns icon..."
mkdir -p build/LokiClient.iconset
for SIZE in 16 32 64 128 256 512; do
    cp "assets/icon_${SIZE}.png" "build/LokiClient.iconset/icon_${SIZE}x${SIZE}.png"
    cp "assets/icon_${SIZE}.png" "build/LokiClient.iconset/icon_${SIZE}x${SIZE}@2x.png" 2>/dev/null || true
done
iconutil -c icns "build/LokiClient.iconset" -o "build/LokiClient.icns" 2>/dev/null \
    && echo "    .icns created" \
    || { echo "    iconutil failed, using PNG"; cp "assets/icon_512.png" "build/LokiClient.icns"; }
rm -rf build/LokiClient.iconset

# ── 3. Generate PyInstaller .spec with custom Info.plist keys ─────────────────
echo "[3/5] Generating spec file..."
cat > LokiClient.spec << 'SPECEOF'
# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_all

block_cipher = None
client_dir = SPECPATH

# collect_all must run before Analysis so results can be passed as
# constructor parameters (2-tuple datas format), not appended to the
# internal TOC (which uses 3-tuples and causes normalize_toc to fail).
datas = [
    (os.path.join(client_dir, 'core'), 'core'),
    (os.path.join(client_dir, 'assets'), 'assets'),
    (os.path.join(client_dir, 'onboarding.py'), '.'),
]
binaries = []
hiddenimports = [
    'onboarding',
    'core.api_client',
    'core.config',
    'core.device_db',
    'core.discovery',
    'core.usbip_attach',
    'zeroconf._utils.ipaddress',
    'zeroconf._handlers.answers',
    'zeroconf._handlers.record_manager',
    'zeroconf._services.browser',
]

for pkg in ['rumps', 'zeroconf', 'customtkinter']:
    tmp_datas, tmp_binaries, tmp_hiddenimports = collect_all(pkg)
    datas += tmp_datas
    binaries += tmp_binaries
    hiddenimports += tmp_hiddenimports

a = Analysis(
    [os.path.join(client_dir, 'tray_app.py')],
    pathex=[client_dir],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Loki-Client',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='Loki-Client',
)

app = BUNDLE(
    coll,
    name='Loki-Client.app',
    icon=os.path.join(client_dir, 'build', 'LokiClient.icns'),
    bundle_identifier='com.bangertech.loki-client',
    info_plist={
        'CFBundleName': 'Loki-Client',
        'CFBundleDisplayName': 'Loki-Client',
        'CFBundleVersion': '1.0.0',
        'CFBundleShortVersionString': '1.0.0',
        'NSHighResolutionCapable': True,
        'LSUIElement': True,
        'NSPrincipalClass': 'NSApplication',
        'LSMinimumSystemVersion': '11.0',
    },
)
SPECEOF

# ── 4. PyInstaller ─────────────────────────────────────────────────────────────
echo "[4/5] Building ${APPNAME}.app with PyInstaller..."
rm -rf dist

pyinstaller \
    --noconfirm \
    --clean \
    --log-level INFO \
    LokiClient.spec

echo "    Verifying binary architecture..."
file "dist/${APPNAME}.app/Contents/MacOS/${APPNAME}"

echo "    ${APPNAME}.app ready."

# ── 5. DMG ─────────────────────────────────────────────────────────────────────
echo "[5/5] Creating ${APPNAME}.dmg..."
rm -f "dist/${APPNAME}.dmg"

if command -v create-dmg &>/dev/null; then
    create-dmg \
        --volname "Loki-Client" \
        --volicon "build/LokiClient.icns" \
        --window-pos 200 120 \
        --window-size 540 380 \
        --icon-size 120 \
        --icon "${APPNAME}.app" 140 190 \
        --hide-extension "${APPNAME}.app" \
        --app-drop-link 400 190 \
        --no-internet-enable \
        "dist/${APPNAME}.dmg" \
        "dist/${APPNAME}.app" 2>/dev/null \
    || hdiutil create -volname "Loki-Client" \
        -srcfolder "dist/${APPNAME}.app" \
        -ov -format UDZO "dist/${APPNAME}.dmg"
else
    hdiutil create \
        -volname "Loki-Client" \
        -srcfolder "dist/${APPNAME}.app" \
        -ov -format UDZO \
        "dist/${APPNAME}.dmg"
fi

echo ""
echo "=========================================="
echo "  Build complete!"
echo ""
echo "  client/dist/${APPNAME}.dmg"
echo ""
echo "  Installation:"
echo "  1. Open the .dmg"
echo "  2. Drag Loki-Client to Applications"
echo "  3. Right-click the app -> Open (first time only)"
echo "  4. Loki appears in the menu bar"
echo "=========================================="
