#!/bin/bash
# Loki-Client - macOS Installer
set -e

echo "=========================================="
echo "  Loki-Client - macOS Setup"
echo "=========================================="

# Check Homebrew
if ! command -v brew &>/dev/null; then
    echo "[!] Homebrew not found. Installing..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
fi

# Python
if ! command -v python3 &>/dev/null; then
    echo "[+] Installing Python..."
    brew install python@3.12
fi

# tkinter (macOS needs python-tk)
echo "[+] Installing python-tk for GUI..."
brew install python-tk@3.12 2>/dev/null || brew install python-tk 2>/dev/null || true

# Lima for USB/IP support
echo ""
echo "[i] macOS does not natively support USB/IP."
echo "    For full USB device attachment, Lima VM is recommended."
read -r -p "    Install Lima (Linux VM for USB/IP support)? [Y/n] " answer
if [[ "$answer" != "n" && "$answer" != "N" ]]; then
    brew install lima
    echo "[+] Lima installed."
    echo "    After installation, run: limactl start template://default"
    echo "    This creates a Linux VM that enables USB/IP attachment."
fi

# Python deps
echo ""
echo "[+] Installing Python dependencies..."
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
pip3 install -r "$SCRIPT_DIR/requirements.txt"

# Create .app launcher
APP_DIR="$HOME/Applications/LokiClient.app"
mkdir -p "$APP_DIR/Contents/MacOS"

cat > "$APP_DIR/Contents/MacOS/LokiClient" <<EOF
#!/bin/bash
cd "$SCRIPT_DIR"
python3 loki_client.py
EOF
chmod +x "$APP_DIR/Contents/MacOS/LokiClient"

cat > "$APP_DIR/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Loki-PrintServer</string>
  <key>CFBundleIdentifier</key><string>com.loki-printserver.client</string>
  <key>CFBundleVersion</key><string>1.0.0</string>
  <key>CFBundleExecutable</key><string>LokiClient</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
EOF

echo ""
echo "[✓] Installation complete!"
echo ""
echo "Run the client:"
echo "    python3 $SCRIPT_DIR/loki_client.py"
echo ""
echo "Or double-click: ~/Applications/LokiClient.app"
echo ""
echo "Note: For full USB attachment on macOS, run:"
echo "    limactl start template://default"
