// loki-usb-helper — Virtual USB device for Loki-PrintServer
// Creates either a CDC-ACM serial port (/dev/cu.usbmodem*) or a vendor-specific
// USB device, bridging data to the Loki server via TCP.
// Requires AMFI disabled + ad-hoc signing with IOUSBHost entitlement.
//
// Usage: loki-usb-helper <server-ip> <tcp-port> [vid pid manufacturer product device-name [vendor|cdc]]

import Foundation

func printUsage() {
    fputs("""
    loki-usb-helper — Virtual USB Bridge for Loki-PrintServer

    Usage: loki-usb-helper <server-ip> <tcp-port> [vid pid manufacturer product device-name [vendor|cdc]]

    Modes:
      cdc     (default) CDC-ACM serial device → /dev/cu.usbmodem*
      vendor  Vendor-specific USB device → visible to IOKit USB matching

    The 'vendor' mode is required for software that communicates via USB
    directly (e.g. Mimaki FineCut) rather than through a serial port.

    Arguments:
      server-ip     IP address of the Loki server (Raspberry Pi)
      tcp-port      TCP port of the serial forwarder (e.g. 7580)
      vid           USB Vendor ID in hex (e.g. 0a50) [optional]
      pid           USB Product ID in hex (e.g. 0001) [optional]
      manufacturer  USB manufacturer string [optional]
      product       USB product string [optional]
      device-name   Sanitized name for /dev/cu.usbmodem<name> (CDC mode) [optional]
      vendor|cdc    Device mode [optional, default: cdc]

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
var deviceName: String = "LOKI\(portStr)"
var mode: String = "cdc"

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
if CommandLine.arguments.count >= 8 {
    deviceName = CommandLine.arguments[7]
}
if CommandLine.arguments.count >= 9 {
    mode = CommandLine.arguments[8].lowercased()
}

print("[loki-usb] Starting virtual USB device (mode: \(mode))")
print("[loki-usb] Server: \(serverIP):\(port)")
if let v = vid, let p = pid {
    print("[loki-usb] Requested VID/PID: 0x\(String(v, radix:16))/0x\(String(p, radix:16))")
}
print("[loki-usb] Identity: \(manufacturer) / \(product)")

let device: VirtualUSBDevice

if mode == "vendor" {
    print("[loki-usb] Mode: vendor-specific USB device (for IOKit matching)")
    let vendorDev = VendorDevice(
        serverHost: serverIP, serverPort: port,
        vendorID: vid ?? 0, productID: pid ?? 0,
        manufacturerName: manufacturer, productName: product
    )
    device = vendorDev

    signal(SIGINT) { _ in
        print("\n[loki-usb] Shutting down...")
        vendorDev.stop()
        exit(0)
    }
    signal(SIGTERM) { _ in
        vendorDev.stop()
        exit(0)
    }

    do {
        try vendorDev.connectAndRun()
    } catch {
        fputs("[loki-usb] FATAL: \(error)\n", stderr)
        fputs("[loki-usb] Make sure AMFI is disabled and the binary is ad-hoc signed.\n", stderr)
        exit(1)
    }
} else {
    print("[loki-usb] Mode: CDC-ACM serial → /dev/cu.usbmodem\(deviceName)*")
    let cdcDev = CDCACMDevice(
        serverHost: serverIP, serverPort: port, serialSuffix: deviceName,
        vendorID: vid, productID: pid,
        manufacturerName: manufacturer, productName: product
    )
    device = cdcDev

    signal(SIGINT) { _ in
        print("\n[loki-usb] Shutting down...")
        cdcDev.stop()
        exit(0)
    }
    signal(SIGTERM) { _ in
        cdcDev.stop()
        exit(0)
    }

    do {
        try cdcDev.connectAndRun()
    } catch {
        fputs("[loki-usb] FATAL: \(error)\n", stderr)
        fputs("[loki-usb] Make sure AMFI is disabled and the binary is ad-hoc signed.\n", stderr)
        exit(1)
    }
}

RunLoop.current.run()
