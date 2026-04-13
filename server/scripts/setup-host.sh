#!/bin/bash
# Run this script ONCE on the Raspberry Pi host to prepare the system.
# This loads the required USB/IP kernel modules at boot.
set -e

echo "=========================================="
echo "  Loki-PrintServer - Host Setup"
echo "=========================================="

# Load modules now
echo "[+] Loading USB/IP kernel modules..."
modprobe usbip_core
modprobe usbip_host

# Persist modules across reboots
echo "[+] Persisting kernel modules..."
if ! grep -q "usbip_core" /etc/modules 2>/dev/null; then
    echo "usbip_core" | sudo tee -a /etc/modules
fi
if ! grep -q "usbip_host" /etc/modules 2>/dev/null; then
    echo "usbip_host" | sudo tee -a /etc/modules
fi

# Install usbip tools on the host for module management
echo "[+] Installing usbip tools..."
apt-get install -y usbip linux-tools-raspi 2>/dev/null || \
    apt-get install -y usbip 2>/dev/null || \
    echo "    Note: install usbip manually if missing"

echo ""
echo "[✓] Host setup complete!"
echo ""
echo "Now start the server with:"
echo "    cd server && docker compose up -d"
