// loki-usb-helper — Virtual USB CDC-ACM device for Loki-PrintServer
// Creates a /dev/cu.usbmodem* that bridges to the Loki server via TCP.
// Requires SIP disabled + ad-hoc signing with IOUSBHost entitlement.
//
// Usage: loki-usb-helper <server-ip> <tcp-port>
// Example: loki-usb-helper 192.168.1.100 7580

import Foundation

func printUsage() {
    fputs("""
    loki-usb-helper — Virtual USB Serial Bridge for Loki-PrintServer

    Usage: loki-usb-helper <server-ip> <tcp-port>

    Creates a virtual USB CDC-ACM device (/dev/cu.usbmodem*) that bridges
    serial data to the Loki server's TCP serial forwarder.

    Arguments:
      server-ip   IP address of the Loki server (Raspberry Pi)
      tcp-port    TCP port of the serial forwarder (e.g. 7580)

    The virtual device appears in System Information under USB and is
    recognized by cutting software like FineCut, xfcut, Inkcut, etc.

    Requirements:
      - macOS 10.15+
      - System Integrity Protection (SIP) must have kext signing disabled
      - Binary must be ad-hoc signed with IOUSBHost entitlement

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

print("[loki-usb] Starting virtual USB CDC-ACM device")
print("[loki-usb] Server: \(serverIP):\(port)")

let device = CDCACMDevice(serverHost: serverIP, serverPort: port, serialSuffix: portStr)

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
    fputs("[loki-usb] Make sure SIP is disabled (csrutil disable) and the binary is signed.\n", stderr)
    exit(1)
}

RunLoop.current.run()
