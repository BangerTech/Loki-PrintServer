#!/bin/bash
set -e

echo "=========================================="
echo "  Loki-PrintServer v1.0.0"
echo "  USB over IP Server — by BangerTECH"
echo "=========================================="

LOKI_PORT=${LOKI_PORT:-7575}
LOKI_API_PORT=${LOKI_API_PORT:-7576}

echo "[+] Loading USB/IP kernel modules..."
modprobe usbip_core 2>/dev/null || echo "    usbip_core: already loaded or unavailable"
modprobe usbip_host 2>/dev/null || echo "    usbip_host: already loaded or unavailable"

echo "[+] Starting mDNS (avahi)..."
if command -v avahi-daemon &>/dev/null; then
    avahi-daemon --daemonize --no-chroot 2>/dev/null || true
fi

echo "[+] Starting CUPS (IPP printer sharing)..."
if command -v cupsd &>/dev/null; then
    # Allow remote access to CUPS
    if [ -f /etc/cups/cupsd.conf ]; then
        sed -i 's/Listen localhost:631/Listen 0.0.0.0:631/' /etc/cups/cupsd.conf
        sed -i 's/Browsing Off/Browsing On/' /etc/cups/cupsd.conf
        # Allow remote admin
        sed -i '/<Location \/>/,/<\/Location>/s/Order allow,deny/Order allow,deny\n  Allow all/' /etc/cups/cupsd.conf 2>/dev/null || true
    fi
    cupsd 2>/dev/null || true
    cupsctl --share-printers 2>/dev/null || true
    echo "    CUPS running on port 631"
fi

echo "[+] Starting Loki-PrintServer API on port ${LOKI_API_PORT}..."
echo "[+] USB/IP daemon on port ${LOKI_PORT}"
echo "[+] Serial forwarding on ports 7580+"
echo "[+] CUPS/IPP printing on port 631"
echo ""

cd /app
exec python3 -m uvicorn api.main:app \
    --host 0.0.0.0 \
    --port "${LOKI_API_PORT}" \
    --log-level info \
    --workers 1
