#!/bin/bash
# Loki-Client - Linux Installer
set -e

echo "=========================================="
echo "  Loki-Client - Linux Setup"
echo "=========================================="

# Dependencies
echo "[+] Installing system dependencies..."
sudo apt-get update -q
sudo apt-get install -y \
    python3 python3-pip python3-tk \
    usbip \
    linux-tools-generic 2>/dev/null || \
sudo apt-get install -y python3 python3-pip python3-tk usbip

# Load kernel modules
echo "[+] Loading vhci-hcd kernel module..."
sudo modprobe vhci-hcd 2>/dev/null || echo "    Note: vhci-hcd not available (needed for USB attach)"

# Persist module
if ! grep -q "vhci-hcd" /etc/modules 2>/dev/null; then
    echo "vhci-hcd" | sudo tee -a /etc/modules > /dev/null
fi

# Python dependencies
echo "[+] Installing Python dependencies..."
pip3 install --user -r "$(dirname "$0")/../requirements.txt"

# Create desktop launcher
if command -v xdg-open &>/dev/null; then
    SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
    DESKTOP_FILE="$HOME/.local/share/applications/loki-printserver.desktop"
    cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Name=Loki-PrintServer
Comment=USB over IP Client
Exec=python3 $SCRIPT_DIR/loki_client.py
Icon=network-wired
Terminal=false
Type=Application
Categories=Network;Utility;
EOF
    echo "[+] Desktop launcher created"
fi

echo ""
echo "[✓] Installation complete!"
echo ""
echo "Run the client:"
echo "    python3 loki_client.py"
echo ""
echo "Or with a specific server:"
echo "    python3 loki_client.py --server 192.168.1.100"
