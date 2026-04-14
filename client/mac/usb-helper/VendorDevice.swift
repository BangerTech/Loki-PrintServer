// VendorDevice.swift
// Virtual USB vendor-specific device that bridges to a TCP connection.
// Presents the exact USB descriptors of the real device (e.g. Mimaki CG-SR)
// so that vendor software (FineCut) discovers it via IOKit USB matching.
// No serial port is created — data flows through bulk endpoints directly.

import Foundation

final class VendorDevice: VirtualUSBDevice {

    private let serverHost: String
    private let serverPort: Int
    private var tcpSocket: Int32 = -1
    private var tcpConnected = false

    private let inLock = NSLock()
    private var inBuffer = Data()

    init(serverHost: String, serverPort: Int,
         vendorID: UInt16, productID: UInt16,
         manufacturerName: String, productName: String) {
        self.serverHost = serverHost
        self.serverPort = serverPort

        let desc = Self.buildDescriptors(
            vid: vendorID, pid: productID,
            manufacturer: manufacturerName, product: productName
        )
        super.init(descriptors: desc)
    }

    // MARK: - Lifecycle

    func connectAndRun() throws {
        try start()
        connectTCP()
        log("Vendor device ready — exposed as USB \(descriptors.manufacturer) / \(descriptors.product)")
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
            if n <= 0 { break }
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

    // MARK: - USB data handling (Bulk endpoints)

    override func handleBulkOUT(endpointAddress: Int, data: [UInt8]) {
        log("USB→TCP: \(data.count)B on EP\(String(format: "0x%02X", endpointAddress))")
        tcpSend(data)
    }

    override func handleBulkIN(endpointAddress: Int, maxLength: Int) -> [UInt8] {
        inLock.lock()
        defer { inLock.unlock() }
        if inBuffer.isEmpty { return [] }
        let n = min(inBuffer.count, maxLength)
        let chunk = Array(inBuffer.prefix(n))
        inBuffer.removeFirst(n)
        log("TCP→USB: \(n)B on EP\(String(format: "0x%02X", endpointAddress))")
        return chunk
    }

    // MARK: - Descriptors matching real Mimaki CG-SR

    private static func buildDescriptors(vid: UInt16, pid: UInt16,
                                          manufacturer: String,
                                          product: String) -> USBDescriptors {
        let device: [UInt8] = [
            18,          // bLength
            0x01,        // bDescriptorType: DEVICE
            0x10, 0x01,  // bcdUSB: 1.10 (matching real device)
            0x00,        // bDeviceClass: 0 (defined at interface level)
            0x00,        // bDeviceSubClass
            0x00,        // bDeviceProtocol
            0x40,        // bMaxPacketSize0: 64
            UInt8(vid & 0xFF), UInt8(vid >> 8),
            UInt8(pid & 0xFF), UInt8(pid >> 8),
            0x00, 0x01,  // bcdDevice: 1.00
            0x01,        // iManufacturer: string 1
            0x02,        // iProduct: string 2
            0x00,        // iSerialNumber: none (matches real Mimaki)
            0x01,        // bNumConfigurations
        ]

        let config: [UInt8] = [
            // Configuration descriptor
            9, 0x02,
            32, 0x00,    // wTotalLength: 32
            0x01,        // bNumInterfaces: 1
            0x01,        // bConfigurationValue
            0x00,        // iConfiguration
            0xC0,        // bmAttributes: Self Powered
            0x01,        // bMaxPower: 2 mA

            // Interface 0: Vendor Specific
            9, 0x04,
            0x00,        // bInterfaceNumber: 0
            0x00,        // bAlternateSetting
            0x02,        // bNumEndpoints: 2
            0xFF,        // bInterfaceClass: Vendor Specific
            0x00,        // bInterfaceSubClass
            0xFF,        // bInterfaceProtocol
            0x00,        // iInterface

            // Endpoint: Bulk OUT EP1
            7, 0x05,
            0x01,        // bEndpointAddress: OUT EP1
            0x02,        // bmAttributes: Bulk
            0x40, 0x00,  // wMaxPacketSize: 64
            0x00,        // bInterval

            // Endpoint: Bulk IN EP2
            7, 0x05,
            0x82,        // bEndpointAddress: IN EP2
            0x02,        // bmAttributes: Bulk
            0x40, 0x00,  // wMaxPacketSize: 64
            0x00,        // bInterval
        ]

        return USBDescriptors(
            device: device,
            configuration: config,
            manufacturer: manufacturer,
            product: product,
            serialNumber: ""
        )
    }
}
