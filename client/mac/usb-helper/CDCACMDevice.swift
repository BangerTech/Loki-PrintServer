// CDCACMDevice.swift
// Virtual USB CDC-ACM serial device that bridges to a TCP connection.
// Appears as /dev/cu.usbmodem* on macOS — recognized by FineCut, xfcut, etc.

import Foundation

final class CDCACMDevice: VirtualUSBDevice {

    private let serverHost: String
    private let serverPort: Int
    private var tcpSocket: Int32 = -1
    private var tcpConnected = false

    private let inLock = NSLock()
    private var inBuffer = Data()

    // Default line coding: 9600 8N1
    private var lineCoding: [UInt8] = [
        0x80, 0x25, 0x00, 0x00,  // dwDTERate: 9600 LE
        0x00,                     // bCharFormat: 1 stop bit
        0x00,                     // bParityType: None
        0x08,                     // bDataBits: 8
    ]

    init(serverHost: String, serverPort: Int, serialSuffix: String,
         vendorID: UInt16? = nil, productID: UInt16? = nil,
         manufacturerName: String = "BangerTECH", productName: String = "Loki Virtual Plotter") {
        self.serverHost = serverHost
        self.serverPort = serverPort

        let desc = Self.buildDescriptors(
            serialSuffix: serialSuffix,
            vid: vendorID, pid: productID,
            manufacturer: manufacturerName, product: productName
        )
        super.init(descriptors: desc)
    }

    // MARK: - Lifecycle

    func connectAndRun() throws {
        try start()
        connectTCP()
        log("CDC-ACM device ready — waiting for macOS driver to create /dev/cu.usbmodem*")
    }

    // MARK: - TCP connection

    private func connectTCP() {
        Thread.detachNewThread { [weak self] in
            guard let self = self else { return }
            while true {
                self.doConnect()
                if !self.tcpConnected {
                    self.log("TCP: retrying in 2s...")
                    Thread.sleep(forTimeInterval: 2.0)
                } else {
                    self.tcpReadLoop()
                    self.log("TCP: connection lost")
                    self.tcpConnected = false
                }
            }
        }
    }

    private func doConnect() {
        let sock = socket(AF_INET, SOCK_STREAM, 0)
        guard sock >= 0 else { return }

        var addr = sockaddr_in()
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = UInt16(serverPort).bigEndian
        inet_pton(AF_INET, serverHost, &addr.sin_addr)

        let result = withUnsafePointer(to: &addr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.connect(sock, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }

        if result == 0 {
            tcpSocket = sock
            tcpConnected = true
            log("TCP: connected to \(serverHost):\(serverPort)")
        } else {
            close(sock)
        }
    }

    private func tcpReadLoop() {
        var buf = [UInt8](repeating: 0, count: 4096)
        while tcpConnected {
            let n = recv(tcpSocket, &buf, buf.count, 0)
            if n <= 0 {
                break
            }
            inLock.lock()
            inBuffer.append(contentsOf: buf[0..<n])
            inLock.unlock()
        }
        close(tcpSocket)
        tcpSocket = -1
    }

    private func tcpSend(_ data: [UInt8]) {
        guard tcpConnected, tcpSocket >= 0 else { return }
        data.withUnsafeBufferPointer { ptr in
            if let base = ptr.baseAddress {
                _ = send(tcpSocket, base, data.count, 0)
            }
        }
    }

    // MARK: - USB data handling

    override func handleBulkOUT(endpointAddress: Int, data: [UInt8]) {
        tcpSend(data)
    }

    override func handleBulkIN(endpointAddress: Int, maxLength: Int) -> [UInt8] {
        inLock.lock()
        defer { inLock.unlock() }
        if inBuffer.isEmpty { return [] }
        let n = min(inBuffer.count, maxLength)
        let chunk = Array(inBuffer.prefix(n))
        inBuffer.removeFirst(n)
        return chunk
    }

    override func handleInterruptIN(endpointAddress: Int, maxLength: Int) -> [UInt8] {
        // CDC notification endpoint — no pending notifications
        return []
    }

    // MARK: - CDC class requests

    override func handleClassSetup(bmRequestType: UInt8, bRequest: UInt8,
                                   wValue: UInt16, wIndex: UInt16, wLength: UInt16) -> Data? {
        switch bRequest {
        case 0x20: // SET_LINE_CODING — data follows in OUT phase
            return Data()
        case 0x21: // GET_LINE_CODING
            return Data(lineCoding.prefix(Int(wLength)))
        case 0x22: // SET_CONTROL_LINE_STATE
            let dtr = (wValue & 0x01) != 0
            let rts = (wValue & 0x02) != 0
            log("CDC: DTR=\(dtr) RTS=\(rts)")
            return Data()
        default:
            return Data()
        }
    }

    override func handleClassDataOUT(data: [UInt8]) {
        if data.count >= 7 {
            lineCoding = Array(data.prefix(7))
            let baud = UInt32(data[0]) | (UInt32(data[1]) << 8)
                     | (UInt32(data[2]) << 16) | (UInt32(data[3]) << 24)
            log("CDC: SET_LINE_CODING baud=\(baud)")
        }
    }

    // MARK: - USB descriptors for CDC-ACM

    // VIDs with third-party macOS kexts that conflict with CDC-ACM.
    // When the device descriptor contains one of these, macOS loads the
    // vendor-specific driver instead of AppleUSBACM → no /dev/cu.usbmodem*.
    private static let blockedVIDs: Set<UInt16> = [
        0x1A86,  // WCH (CH340/CH341)
        0x0403,  // FTDI (FT232R, FT2232, etc.)
        0x067B,  // Prolific (PL2303)
        0x10C4,  // Silicon Labs (CP210x)
    ]

    private static func buildDescriptors(serialSuffix: String,
                                          vid: UInt16? = nil, pid: UInt16? = nil,
                                          manufacturer: String = "BangerTECH",
                                          product: String = "Loki Virtual Plotter") -> USBDescriptors {
        let actualVID: UInt16
        let actualPID: UInt16

        if let v = vid, let p = pid, !blockedVIDs.contains(v) {
            actualVID = v
            actualPID = p
            log("Using real VID/PID: 0x\(String(v, radix:16))/0x\(String(p, radix:16))")
        } else {
            actualVID = 0x1D50  // OpenMoko — generic CDC-ACM compatible
            actualPID = 0x614E
            if let v = vid, blockedVIDs.contains(v) {
                log("VID 0x\(String(v, radix:16)) has conflicting macOS driver, using generic CDC-ACM VID/PID")
            }
        }
        let device: [UInt8] = [
            18,          // bLength
            0x01,        // bDescriptorType: DEVICE
            0x00, 0x02,  // bcdUSB: 2.00
            0x02,        // bDeviceClass: Communications
            0x00,        // bDeviceSubClass
            0x00,        // bDeviceProtocol
            0x08,        // bMaxPacketSize0
            UInt8(actualVID & 0xFF), UInt8(actualVID >> 8),  // idVendor (LE)
            UInt8(actualPID & 0xFF), UInt8(actualPID >> 8),  // idProduct (LE)
            0x00, 0x01,  // bcdDevice: 1.00
            0x01,        // iManufacturer: string 1
            0x02,        // iProduct: string 2
            0x03,        // iSerialNumber: string 3
            0x01,        // bNumConfigurations
        ]

        let config: [UInt8] = [
            // Configuration descriptor (9 bytes)
            9, 0x02,
            67, 0x00,    // wTotalLength: 67
            0x02,        // bNumInterfaces: 2
            0x01,        // bConfigurationValue
            0x00,        // iConfiguration
            0x80,        // bmAttributes: bus powered
            50,          // bMaxPower: 100 mA

            // Interface 0: CDC Communication (9 bytes)
            9, 0x04,
            0x00,        // bInterfaceNumber: 0
            0x00,        // bAlternateSetting
            0x01,        // bNumEndpoints: 1 (notification)
            0x02,        // bInterfaceClass: Communications
            0x02,        // bInterfaceSubClass: ACM
            0x00,        // bInterfaceProtocol
            0x00,        // iInterface

            // CDC Header Functional Descriptor (5 bytes)
            5, 0x24, 0x00, 0x10, 0x01,

            // CDC Call Management Functional Descriptor (5 bytes)
            5, 0x24, 0x01, 0x00, 0x01,

            // CDC ACM Functional Descriptor (4 bytes)
            4, 0x24, 0x02, 0x02,

            // CDC Union Functional Descriptor (5 bytes)
            5, 0x24, 0x06, 0x00, 0x01,

            // Endpoint: Interrupt IN EP2 (7 bytes)
            7, 0x05,
            0x82,        // bEndpointAddress: IN EP2
            0x03,        // bmAttributes: Interrupt
            0x08, 0x00,  // wMaxPacketSize: 8
            0xFF,        // bInterval: 255 ms

            // Interface 1: CDC Data (9 bytes)
            9, 0x04,
            0x01,        // bInterfaceNumber: 1
            0x00,        // bAlternateSetting
            0x02,        // bNumEndpoints: 2
            0x0A,        // bInterfaceClass: CDC Data
            0x00,        // bInterfaceSubClass
            0x00,        // bInterfaceProtocol
            0x00,        // iInterface

            // Endpoint: Bulk OUT EP1 (7 bytes)
            7, 0x05,
            0x01,        // bEndpointAddress: OUT EP1
            0x02,        // bmAttributes: Bulk
            0x40, 0x00,  // wMaxPacketSize: 64
            0x00,        // bInterval

            // Endpoint: Bulk IN EP1 (7 bytes)
            7, 0x05,
            0x81,        // bEndpointAddress: IN EP1
            0x02,        // bmAttributes: Bulk
            0x40, 0x00,  // wMaxPacketSize: 64
            0x00,        // bInterval
        ]

        return USBDescriptors(
            device: device,
            configuration: config,
            manufacturer: manufacturer,
            product: product,
            serialNumber: "LOKI\(serialSuffix)"
        )
    }
}
