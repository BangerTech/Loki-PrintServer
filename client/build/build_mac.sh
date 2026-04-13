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
    customtkinter pystray packaging 2>&1 | grep -E "Successfully|already|ERROR" || true
brew install socat 2>/dev/null || true

# universal2 builds require ALL native extensions to be fat binaries.
# Pillow's arm64 wheel contains _imagingtk.so as arm64-only, which makes
# PyInstaller fail. Reinstall Pillow as universal2 wheel when building in CI.
TARGET_ARCH="universal2"
if [ "${CI:-}" = "true" ] && [ "$(uname -m)" = "arm64" ]; then
    echo "    Reinstalling Pillow as universal2 wheel for fat binary support..."
    pip3 download pillow \
        --platform macosx_11_0_universal2 \
        --only-binary :all: \
        -d /tmp/pillow_u2 --quiet 2>/dev/null \
    && pip3 install /tmp/pillow_u2/Pillow*.whl --force-reinstall --quiet \
    && echo "    Pillow universal2 installed." \
    || {
        echo "    Warning: universal2 Pillow unavailable, falling back to native arch (arm64)."
        TARGET_ARCH="arm64"
    }
fi

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

# ── 3. Write Info.plist (LSUIElement = menu bar only, no dock icon) ────────────
echo "[3/5] Writing Info.plist..."
cat > build/Info.plist << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>Loki-Client</string>
    <key>CFBundleDisplayName</key>
    <string>Loki-Client</string>
    <key>CFBundleIdentifier</key>
    <string>${BUNDLE_ID}</string>
    <key>CFBundleVersion</key>
    <string>${VERSION}</string>
    <key>CFBundleShortVersionString</key>
    <string>${VERSION}</string>
    <key>CFBundleExecutable</key>
    <string>Loki-Client</string>
    <key>CFBundleIconFile</key>
    <string>LokiClient</string>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>LSUIElement</key>
    <true/>
    <key>NSPrincipalClass</key>
    <string>NSApplication</string>
    <key>LSMinimumSystemVersion</key>
    <string>11.0</string>
</dict>
</plist>
EOF

# ── 4. PyInstaller ─────────────────────────────────────────────────────────────
echo "[4/5] Building ${APPNAME}.app with PyInstaller..."
rm -rf dist "${APPNAME}.spec"

pyinstaller \
    --name "$APPNAME" \
    --windowed \
    --onedir \
    --noconfirm \
    --clean \
    --target-arch "$TARGET_ARCH" \
    --osx-bundle-identifier "$BUNDLE_ID" \
    --icon "build/LokiClient.icns" \
    --add-data "core:core" \
    --add-data "assets:assets" \
    --add-data "onboarding.py:." \
    --hidden-import "onboarding" \
    --hidden-import "core.api_client" \
    --hidden-import "core.config" \
    --hidden-import "core.device_db" \
    --hidden-import "core.discovery" \
    --hidden-import "core.usbip_attach" \
    --hidden-import "zeroconf._utils.ipaddress" \
    --hidden-import "zeroconf._handlers.answers" \
    --hidden-import "zeroconf._handlers.browser" \
    --hidden-import "zeroconf._handlers.record_manager" \
    --hidden-import "zeroconf._services.browser" \
    --collect-all "rumps" \
    --collect-all "zeroconf" \
    --collect-all "customtkinter" \
    --paths "." \
    tray_app.py

# Inject correct Info.plist (LSUIElement = true → no dock icon, lives in menu bar)
echo "    Injecting Info.plist..."
cp build/Info.plist "dist/${APPNAME}.app/Contents/Info.plist"

# Make sure onboarding.py is importable inside the bundle
INTERNAL="dist/${APPNAME}.app/Contents/MacOS"
[ ! -f "${INTERNAL}/onboarding.py" ] && cp onboarding.py "${INTERNAL}/" 2>/dev/null || true
INTERNAL2="dist/${APPNAME}/_internal"
[ -d "$INTERNAL2" ] && [ ! -f "${INTERNAL2}/onboarding.py" ] && cp onboarding.py "${INTERNAL2}/" 2>/dev/null || true

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
echo "  ✓ Build complete!"
echo ""
echo "  client/dist/${APPNAME}.dmg"
echo ""
echo "  Installation:"
echo "  1. Öffne die .dmg"
echo "  2. Ziehe Loki-Client in Applications"
echo "  3. Rechtsklick → Öffnen (einmalig)"
echo "  4. Loki erscheint oben in der Menüleiste"
echo "=========================================="
