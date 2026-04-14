#!/bin/bash
set -e

echo "=========================================="
echo "  Loki-PrintServer v1.0.0"
echo "  USB over IP Server — by BangerTECH"
echo "=========================================="

LOKI_PORT=${LOKI_PORT:-7575}
LOKI_API_PORT=${LOKI_API_PORT:-7576}

echo "[+] Preparing USB drivers..."
# usbip_host and usbserial are MUTUALLY EXCLUSIVE per device.
# Stale match_busid entries from a previous run make usbip_host auto-claim
# devices on re-probe, blocking usbserial.  Unload and reload to get a
# clean match_busid table.
rmmod usbip_host 2>/dev/null || true
rmmod usbip_core 2>/dev/null || true

# Step 1: Bind native USB plotters to generic serial driver FIRST
#   (before usbip_host is loaded, so nothing steals them)
echo "[+] Binding native USB plotters to generic serial driver..."
modprobe usbserial 2>/dev/null || true
for VID_PID in "0a50 0001"; do  # Mimaki CG-SR
    echo "$VID_PID" > /sys/bus/usb-serial/drivers/generic/new_id 2>/dev/null || true
    echo "    Registered ${VID_PID} with usbserial"
done

# Re-probe plotter devices so usbserial claims them
for dev in /sys/bus/usb/devices/[0-9]*-*; do
    [ -f "$dev/idVendor" ] || continue
    vid=$(cat "$dev/idVendor" 2>/dev/null)
    case "$vid" in
        0a50|0b4d|0459|1949|2166)  # Mimaki, Graphtec, Silhouette, Cricut, Roland
            echo 0 > "$dev/authorized" 2>/dev/null
            echo 1 > "$dev/authorized" 2>/dev/null
            echo "    Re-probed $(basename $dev) (VID $vid)"
            ;;
    esac
done
sleep 1

echo "    Serial devices: $(ls /dev/ttyUSB* /dev/ttyACM* 2>/dev/null || echo 'none')"

# Step 2: NOW load usbip_host (clean match_busid — won't steal from usbserial)
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
