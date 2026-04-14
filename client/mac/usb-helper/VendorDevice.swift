// VendorDevice.swift
// Virtual USB vendor-specific device that bridges to a TCP connection.
// Presents the exact USB descriptors of the real device (e.g. Mimaki CG-SR)
// so that vendor software (FineCut) discovers it via IOKit USB matching.
// No serial port is created — all USB operations (control + bulk) are forwarded
// to the real device on the server via a framed TCP protocol.
//
// TCP Frame Protocol:
//   [TYPE:1][LENGTH:2 big-endian][PAYLOAD:LENGTH]
//   0x01  CTRL_REQ    Client→Server
//   0x02  CTRL_RESP   Server→Client
//   0x03  BULK_OUT    Client→Server
//   0x04  BULK_IN     Server→Client (pushed)
//   0x05  PING        Client→Server (poll IN endpoint)
//   0x06  PONG        Server→Client (poll response)

import Foundation

final class VendorDevice: VirtualUSBDevice {

    private let serverHost: String
    private let serverPort: Int
    private var tcpSocket: Int32 = -1
    private var tcpConnected = false

    private let sendLock = NSLock()
    private let inLock = NSLock()
    private var inBuffer = Data()
    private let ctrlLock = NSLock()
    private let ctrlSema = DispatchSemaphore(value: 0)
    private var ctrlResponse: Data?

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

    // MARK: - TCP framed protocol

    private func sendFrame(type: UInt8, payload: [UInt8]) {
        guard tcpConnected, tcpSocket >= 0 else { return }
        var frame = [type,
                     UInt8((payload.count >> 8) & 0xFF),
                     UInt8(payload.count & 0xFF)]
        frame.append(contentsOf: payload)
        sendLock.lock()
        frame.withUnsafeBufferPointer { ptr in
            if let base = ptr.baseAddress {
                _ = send(tcpSocket, base, frame.count, 0)
            }
        }
        sendLock.unlock()
    }

    private func readExact(_ sock: Int32, count: Int) -> Data? {
        var buf = Data(count: count)
        var offset = 0
        while offset < count {
            let n = buf.withUnsafeMutableBytes { ptr -> Int in
                recv(sock, ptr.baseAddress! + offset, count - offset, 0)
            }
            if n <= 0 { return nil }
            offset += n
        }
        return buf
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
        while tcpConnected {
            guard let header = readExact(tcpSocket, count: 3) else { break }
            let msgType = header[0]
            let length = (Int(header[1]) << 8) | Int(header[2])
            var payload = Data()
            if length > 0 {
                guard let p = readExact(tcpSocket, count: length) else { break }
                payload = p
            }

            switch msgType {
            case 0x02: // CTRL_RESP
                ctrlLock.lock()
                ctrlResponse = payload
                ctrlLock.unlock()
                ctrlSema.signal()

            case 0x04: // BULK_IN (pushed from server)
                if payload.count > 1 {
                    inLock.lock()
                    inBuffer.append(payload.suffix(from: 1))
                    inLock.unlock()
                }

            case 0x06: // PONG (poll response)
                if payload.count > 1 {
                    inLock.lock()
                    inBuffer.append(payload.suffix(from: 1))
                    inLock.unlock()
                }

            default:
                break
            }
        }
        close(tcpSocket)
        tcpSocket = -1
    }

    // MARK: - Vendor-specific control request forwarding

    override func handleClassSetup(bmRequestType: UInt8, bRequest: UInt8,
                                   wValue: UInt16, wIndex: UInt16, wLength: UInt16) -> Data? {
        let isVendor = (bmRequestType & 0x60) == 0x40
        if !isVendor {
            return super.handleClassSetup(bmRequestType: bmRequestType, bRequest: bRequest,
                                          wValue: wValue, wIndex: wIndex, wLength: wLength)
        }

        log("Forwarding vendor control: bmRT=0x\(String(format: "%02X", bmRequestType)) bReq=\(bRequest) wVal=\(wValue) wIdx=\(wIndex) wLen=\(wLength)")

        var payload: [UInt8] = [bmRequestType, bRequest]
        payload.append(UInt8(wValue & 0xFF)); payload.append(UInt8(wValue >> 8))
        payload.append(UInt8(wIndex & 0xFF)); payload.append(UInt8(wIndex >> 8))
        payload.append(UInt8(wLength & 0xFF)); payload.append(UInt8(wLength >> 8))

        ctrlLock.lock()
        ctrlResponse = nil
        ctrlLock.unlock()

        sendFrame(type: 0x01, payload: payload)

        let timeout = ctrlSema.wait(timeout: .now() + 3.0)
        if timeout == .timedOut {
            log("Vendor control: timeout waiting for server response")
            return nil
        }

        ctrlLock.lock()
        let resp = ctrlResponse
        ctrlResponse = nil
        ctrlLock.unlock()

        guard let resp = resp, !resp.isEmpty else { return nil }
        let status = resp[0]
        if status != 0 {
            log("Vendor control: server returned status \(status)")
            return nil
        }
        if resp.count > 1 {
            return resp.suffix(from: 1)
        }
        return Data()
    }

    // MARK: - USB data handling (Bulk endpoints)

    override func handleBulkOUT(endpointAddress: Int, data: [UInt8]) {
        var payload: [UInt8] = [UInt8(endpointAddress & 0xFF)]
        payload.append(contentsOf: data)
        sendFrame(type: 0x03, payload: payload)
    }

    override func handleBulkIN(endpointAddress: Int, maxLength: Int) -> [UInt8] {
        inLock.lock()
        if inBuffer.isEmpty {
            inLock.unlock()
            sendFrame(type: 0x05, payload: [UInt8(endpointAddress & 0xFF)])
            Thread.sleep(forTimeInterval: 0.01)
            inLock.lock()
        }
        defer { inLock.unlock() }
        if inBuffer.isEmpty { return [] }
        let n = min(inBuffer.count, maxLength)
        let chunk = Array(inBuffer.prefix(n))
        inBuffer.removeFirst(n)
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
