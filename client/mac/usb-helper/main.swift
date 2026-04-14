// loki-usb-helper — Virtual USB CDC-ACM device for Loki-PrintServer
// Creates a /dev/cu.usbmodem* that bridges to the Loki server via TCP.
// Requires AMFI disabled + ad-hoc signing with IOUSBHost entitlement.
//
// Usage: loki-usb-helper <server-ip> <tcp-port> [manufacturer product]
// Example: loki-usb-helper 192.168.1.100 7580 "MIMAKI" "CG-SRIII"

import Foundation

func printUsage() {
    fputs("""
    loki-usb-helper — Virtual USB Serial Bridge for Loki-PrintServer

    Usage: loki-usb-helper <server-ip> <tcp-port> [manufacturer product]

    Creates a virtual USB CDC-ACM device (/dev/cu.usbmodem*) that bridges
    serial data to the Loki server's TCP serial forwarder.

    Arguments:
      server-ip     IP address of the Loki server (Raspberry Pi)
      tcp-port      TCP port of the serial forwarder (e.g. 7580)
      manufacturer  USB manufacturer string [optional]
      product       USB product string [optional]

    The device always uses a generic CDC-ACM VID/PID so macOS loads
    the correct AppleUSBACM driver. The manufacturer and product strings
    are shown in the system's USB device list.

    """, stderr)
}

guard CommandLine.arguments.count >= 3 else {
    printUsage()
    exit(1)
}

let serverIP = CommandLine.arguments[1]
let portStr = CommandLine.arguments[2]

guard let port = Int(portStr), port > 0, port < 65536 else {
    fputs("Error: invalid port '\(portStr)'\n", stderr)
    exit(1)
}

var manufacturer: String = "BangerTECH"
var product: String = "Loki Virtual Plotter"

if CommandLine.arguments.count >= 4 {
    manufacturer = CommandLine.arguments[3]
}
if CommandLine.arguments.count >= 5 {
    product = CommandLine.arguments[4]
}

print("[loki-usb] Starting virtual USB CDC-ACM device")
print("[loki-usb] Server: \(serverIP):\(port)")
print("[loki-usb] Identity: \(manufacturer) / \(product)")

let device = CDCACMDevice(
    serverHost: serverIP, serverPort: port, serialSuffix: portStr,
    manufacturerName: manufacturer, productName: product
)

signal(SIGINT) { _ in
    print("\n[loki-usb] Shutting down...")
    device.stop()
    exit(0)
}

signal(SIGTERM) { _ in
    device.stop()
    exit(0)
}

do {
    try device.connectAndRun()
} catch {
    fputs("[loki-usb] FATAL: \(error)\n", stderr)
    fputs("[loki-usb] Make sure AMFI is disabled and the binary is ad-hoc signed with entitlements.\n", stderr)
    exit(1)
}

RunLoop.current.run()
