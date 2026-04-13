#!/bin/bash
# Loki-Client — macOS .app + .dmg Builder
# Run this on your Mac: cd ~/Loki-Client && bash build/build_mac.sh
set -e

cd "$(dirname "$0")/.."
APPNAME="Loki-Client"
BUNDLE_ID="com.bangertech.loki-client"

echo "=========================================="
echo "  Loki-Client — macOS Build"
echo "  by BangerTECH"
echo "=========================================="

# ── 1. Dependencies ────────────────────────────────────────────────────────────
echo "[1/5] Installing Python dependencies..."
pip3 install --quiet pyinstaller pillow rumps zeroconf httpx websockets \
    customtkinter pystray packaging

echo "[1/5] Installing socat (serial forwarding)..."
brew install socat 2>/dev/null || true

# ── 2. Convert PNG icon → .icns (required for macOS apps) ────────────────────
echo "[2/5] Creating .icns icon..."
ICONSET="build/LokiClient.iconset"
mkdir -p "$ICONSET"
for SIZE in 16 32 64 128 256 512; do
    cp "assets/icon_${SIZE}.png" "${ICONSET}/icon_${SIZE}x${SIZE}.png"
    cp "assets/icon_${SIZE}.png" "${ICONSET}/icon_${SIZE}x${SIZE}@2x.png" 2>/dev/null || true
done
iconutil -c icns "$ICONSET" -o "build/LokiClient.icns" 2>/dev/null || \
    cp "assets/icon_512.png" "build/LokiClient.icns"
rm -rf "$ICONSET"
echo "    Icon created."

# ── 3. PyInstaller build ───────────────────────────────────────────────────────
echo "[3/5] Building ${APPNAME}.app..."
rm -rf dist build/__pycache__

pyinstaller \
    --name "$APPNAME" \
    --windowed \
    --onedir \
    --noconfirm \
    --osx-bundle-identifier "$BUNDLE_ID" \
    --icon "build/LokiClient.icns" \
    --add-data "core:core" \
    --add-data "assets:assets" \
    --hidden-import "onboarding" \
    --hidden-import "core.api_client" \
    --hidden-import "core.config" \
    --hidden-import "core.device_db" \
    --hidden-import "core.discovery" \
    --hidden-import "core.usbip_attach" \
    --hidden-import "zeroconf._utils.ipaddress" \
    --hidden-import "zeroconf._handlers.answers" \
    --hidden-import "zeroconf._handlers.browser" \
    --collect-all "rumps" \
    --collect-all "zeroconf" \
    --collect-all "customtkinter" \
    --paths "." \
    tray_app.py

# Copy onboarding module into the app (needed as importable Python file)
cp onboarding.py "dist/${APPNAME}/_internal/" 2>/dev/null || \
    cp onboarding.py "dist/${APPNAME}/" 2>/dev/null || true

echo "    ${APPNAME}.app created."

# ── 4. Code sign (optional, skip if no Apple cert) ────────────────────────────
if security find-identity -p codesigning -v 2>/dev/null | grep -q "Developer ID"; then
    echo "[4/5] Code signing..."
    codesign --force --deep --sign "Developer ID Application" \
        "dist/${APPNAME}.app" 2>/dev/null && echo "    Signed." || echo "    Signing failed, continuing anyway."
else
    echo "[4/5] Skipping code signing (no Developer ID found)"
    echo "    Users may need to right-click → Open on first launch."
fi

# ── 5. Create .dmg ────────────────────────────────────────────────────────────
echo "[5/5] Creating ${APPNAME}.dmg..."
rm -f "dist/${APPNAME}.dmg"

if command -v create-dmg &>/dev/null; then
    create-dmg \
        --volname "$APPNAME" \
        --volicon "build/LokiClient.icns" \
        --window-pos 200 120 \
        --window-size 620 420 \
        --icon-size 120 \
        --icon "${APPNAME}.app" 180 200 \
        --hide-extension "${APPNAME}.app" \
        --app-drop-link 440 200 \
        --background "assets/logo.png" \
        --no-internet-enable \
        "dist/${APPNAME}.dmg" \
        "dist/${APPNAME}/"
else
    # Fallback: hdiutil
    hdiutil create \
        -volname "$APPNAME" \
        -srcfolder "dist/${APPNAME}/" \
        -ov -format UDZO \
        "dist/${APPNAME}.dmg"
fi

echo ""
echo "=========================================="
echo "  ✓ Build complete!"
echo ""
echo "  DMG: dist/${APPNAME}.dmg"
echo ""
echo "  → Double-click the .dmg"
echo "  → Drag Loki-Client to Applications"
echo "  → First launch: right-click → Open"
echo "    (only needed once, no Apple cert)"
echo "=========================================="
