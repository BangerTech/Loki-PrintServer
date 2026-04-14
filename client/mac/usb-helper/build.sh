#!/bin/bash
# Build loki-usb-helper — Virtual USB CDC-ACM device for macOS
# Run from repo root: bash client/mac/usb-helper/build.sh
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OUT="$SCRIPT_DIR/loki-usb-helper"

echo "=== Building loki-usb-helper ==="

swiftc \
    -O \
    -framework IOUSBHost \
    -o "$OUT" \
    "$SCRIPT_DIR/main.swift" \
    "$SCRIPT_DIR/VirtualUSBDevice.swift" \
    "$SCRIPT_DIR/CDCACMDevice.swift" \
    "$SCRIPT_DIR/VendorDevice.swift"

echo "=== Signing with IOUSBHost entitlement ==="

codesign --force --sign - \
    --entitlements "$SCRIPT_DIR/entitlements.plist" \
    "$OUT"

echo "=== Verifying ==="
codesign -dvvv "$OUT" 2>&1 | grep -E "Signature|Authority|Entitlements"
echo ""
file "$OUT"
echo ""
echo "=== Done: $OUT ==="
echo ""
echo "Usage: $OUT <server-ip> <tcp-port>"
echo "Example: $OUT 192.168.1.100 7580"
