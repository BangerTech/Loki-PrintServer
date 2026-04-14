// loki-usb-helper — Virtual USB CDC-ACM device for Loki-PrintServer
// Creates a /dev/cu.usbmodem* that bridges to the Loki server via TCP.
// Requires AMFI disabled + ad-hoc signing with IOUSBHost entitlement.
//
// Usage: loki-usb-helper <server-ip> <tcp-port> [vid pid manufacturer product]
// Example: loki-usb-helper 192.168.1.100 7580 0b4d 6525 "MIMAKI" "CG-SRIII"

import Foundation

func printUsage() {
    fputs("""
    loki-usb-helper — Virtual USB Serial Bridge for Loki-PrintServer

    Usage: loki-usb-helper <server-ip> <tcp-port> [vid pid manufacturer product]

    Creates a virtual USB CDC-ACM device (/dev/cu.usbmodem*) that bridges
    serial data to the Loki server's TCP serial forwarder.

    Arguments:
      server-ip     IP address of the Loki server (Raspberry Pi)
      tcp-port      TCP port of the serial forwarder (e.g. 7580)
      vid           USB Vendor ID in hex (e.g. 0b4d for Mimaki) [optional]
      pid           USB Product ID in hex (e.g. 6525) [optional]
      manufacturer  USB manufacturer string [optional]
      product       USB product string [optional]

    When VID/PID are provided, the virtual device mimics the real USB
    device so that vendor-specific software (FineCut, etc.) recognizes it.

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

var vid: UInt16? = nil
var pid: UInt16? = nil
var manufacturer: String = "BangerTECH"
var product: String = "Loki Virtual Plotter"

if CommandLine.arguments.count >= 5 {
    vid = UInt16(CommandLine.arguments[3], radix: 16)
    pid = UInt16(CommandLine.arguments[4], radix: 16)
}
if CommandLine.arguments.count >= 6 {
    manufacturer = CommandLine.arguments[5]
}
if CommandLine.arguments.count >= 7 {
    product = CommandLine.arguments[6]
}

if let v = vid, let p = pid {
    print("[loki-usb] Spoofing USB device: VID=0x\(String(v, radix:16)) PID=0x\(String(p, radix:16)) \(manufacturer) \(product)")
} else {
    print("[loki-usb] Using generic USB device identity")
}

print("[loki-usb] Starting virtual USB CDC-ACM device")
print("[loki-usb] Server: \(serverIP):\(port)")

let device = CDCACMDevice(
    serverHost: serverIP, serverPort: port, serialSuffix: portStr,
    vendorID: vid, productID: pid,
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
    fputs("[loki-usb] Make sure SIP is disabled (csrutil disable) and the binary is signed.\n", stderr)
    exit(1)
}

RunLoop.current.run()
