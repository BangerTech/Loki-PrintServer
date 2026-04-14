// VirtualUSBDevice.swift
// Generic synthetic USB device via IOUSBHostControllerInterface.
// Subclass and override data-delivery methods for specific device classes.
//
// Based on JJTech0130's SyntheticIOUSBDevice approach.
// Entitlement required: com.apple.developer.usb.host-controller-interface

import Foundation
import IOUSBHost

// MARK: - Descriptor bundle

public struct USBDescriptors {
    public let device: [UInt8]
    public let configuration: [UInt8]
    public let manufacturer: String
    public let product: String
    public let serialNumber: String

    public init(device: [UInt8], configuration: [UInt8],
                manufacturer: String, product: String, serialNumber: String) {
        self.device = device
        self.configuration = configuration
        self.manufacturer = manufacturer
        self.product = product
        self.serialNumber = serialNumber
    }
}

// MARK: - USB constants

private let kMsgTypeMask: UInt32 = 0x3F
private let kMsgValid: UInt32 = (1 << 15)

private let kUSBReqGetDescriptor: UInt8 = 0x06
private let kUSBReqGetConfig: UInt8 = 0x08
private let kUSBReqSetConfig: UInt8 = 0x09
private let kUSBReqGetInterface: UInt8 = 0x0A
private let kUSBDescDevice: UInt8 = 0x01
private let kUSBDescConfig: UInt8 = 0x02
private let kUSBDescString: UInt8 = 0x03

// MARK: - VirtualUSBDevice

open class VirtualUSBDevice: NSObject {

    public let descriptors: USBDescriptors

    private var controller: IOUSBHostControllerInterface?
    private var portSM: IOUSBHostCIPortStateMachine?
    private var deviceSMs: [Int: IOUSBHostCIDeviceStateMachine] = [:]
    private var endpointSMs: [Int: IOUSBHostCIEndpointStateMachine] = [:]
    private var deviceConnected = false
    private var pendingResponse: Data?
    private var pendingSetupIsOUT = false

    private let string0: [UInt8] = [4, 0x03, 0x09, 0x04]
    private let stringManuf: [UInt8]
    private let stringProd: [UInt8]
    private let stringSerial: [UInt8]

    // MARK: Init

    public init(descriptors: USBDescriptors) {
        self.descriptors = descriptors
        self.stringManuf = Self.makeStringDescriptor(descriptors.manufacturer)
        self.stringProd = Self.makeStringDescriptor(descriptors.product)
        self.stringSerial = Self.makeStringDescriptor(descriptors.serialNumber)
        super.init()
    }

    // MARK: Override points

    /// Handle a class-specific SETUP request. Return response data for IN, empty Data for ACK-only, nil to stall.
    open func handleClassSetup(bmRequestType: UInt8, bRequest: UInt8,
                               wValue: UInt16, wIndex: UInt16, wLength: UInt16) -> Data? {
        return Data()
    }

    /// Called when a class-specific OUT data phase completes (e.g. SET_LINE_CODING payload).
    open func handleClassDataOUT(data: [UInt8]) {}

    /// Called when the host sends data on a non-control OUT endpoint.
    open func handleBulkOUT(endpointAddress: Int, data: [UInt8]) {}

    /// Called when the host requests data from a non-control IN endpoint.
    open func handleBulkIN(endpointAddress: Int, maxLength: Int) -> [UInt8] { [] }

    /// Called when the host requests data from an interrupt IN endpoint.
    open func handleInterruptIN(endpointAddress: Int, maxLength: Int) -> [UInt8] { [] }

    // MARK: Start / Stop

    public func start() throws {
        var err: NSError?
        let ci = IOUSBHostControllerInterface(
            __capabilities: buildCapabilities(),
            queue: nil,
            interruptRateHz: 0,
            error: &err,
            commandHandler: { [weak self] ci, cmd in self?.handleCommand(ci, cmd) },
            doorbellHandler: { [weak self] ci, db, n in self?.handleDoorbells(ci, db, n) },
            interestHandler: nil)

        if let e = err, e.code != 0 { throw e }
        guard let ci else {
            throw NSError(domain: "VirtualUSBDevice", code: -1,
                          userInfo: [NSLocalizedDescriptionKey:
                                        "Failed to create IOUSBHostControllerInterface"])
        }
        controller = ci
        log("Controller created — UUID: \(ci.uuid.uuidString)")
    }

    public func stop() {
        controller?.destroy()
        controller = nil
        log("Stopped.")
    }

    // MARK: Capabilities

    private func buildCapabilities() -> Data {
        var ctlCap = IOUSBHostCIMessage()
        ctlCap.control =
            UInt32(IOUSBHostCIMessageTypeControllerCapabilities.rawValue)
            | (1 << 14) | (1 << 15) | (1 << 16)
        ctlCap.data0 = (1 << 0) | (2 << 4)

        var portCap = IOUSBHostCIMessage()
        portCap.control =
            UInt32(IOUSBHostCIMessageTypePortCapabilities.rawValue)
            | (1 << 14) | (1 << 15) | (1 << 16) | (0 << 24)
        portCap.data0 = UInt32(500 / 8)

        var data = Data(bytes: &ctlCap, count: MemoryLayout<IOUSBHostCIMessage>.size)
        data.append(Data(bytes: &portCap, count: MemoryLayout<IOUSBHostCIMessage>.size))
        return data
    }

    // MARK: Command handler

    private func handleCommand(_ ci: IOUSBHostControllerInterface, _ cmdIn: IOUSBHostCIMessage) {
        var cmd = cmdIn
        let rawType = cmd.control & kMsgTypeMask
        let msgType = IOUSBHostCIMessageType(rawValue: rawType)

        do {
            switch msgType {
            case IOUSBHostCIMessageTypeControllerPowerOn,
                 IOUSBHostCIMessageTypeControllerPowerOff,
                 IOUSBHostCIMessageTypeControllerStart,
                 IOUSBHostCIMessageTypeControllerPause:
                try ci.controllerStateMachine.respond(
                    toCommand: &cmd, status: IOUSBHostCIMessageStatusSuccess)

            case IOUSBHostCIMessageTypePortPowerOn,
                 IOUSBHostCIMessageTypePortPowerOff,
                 IOUSBHostCIMessageTypePortResume,
                 IOUSBHostCIMessageTypePortSuspend,
                 IOUSBHostCIMessageTypePortReset,
                 IOUSBHostCIMessageTypePortDisable,
                 IOUSBHostCIMessageTypePortStatus:
                var portErr: NSError?
                let psm = ci.getPortStateMachine(forCommand: &cmd, error: &portErr)
                if portErr == nil || portErr!.code == 0 {
                    portSM = psm
                    try psm.respond(toCommand: &cmd, status: IOUSBHostCIMessageStatusSuccess)
                    if msgType == IOUSBHostCIMessageTypePortPowerOn {
                        psm.powered = true
                        if !deviceConnected {
                            deviceConnected = true
                            psm.connected = true
                            try psm.updateLinkState(
                                IOUSBHostCILinkStateU0,
                                speed: IOUSBHostCIDeviceSpeedFull,
                                inhibitLinkStateChange: false)
                            log("Port 1: device connected (full-speed)")
                        }
                    } else if msgType == IOUSBHostCIMessageTypePortReset {
                        try psm.updateLinkState(
                            IOUSBHostCILinkStateU0,
                            speed: IOUSBHostCIDeviceSpeedFull,
                            inhibitLinkStateChange: false)
                    }
                }

            case IOUSBHostCIMessageTypeDeviceCreate:
                let dsm = try IOUSBHostCIDeviceStateMachine(__interface: ci, command: &cmd)
                let addr = 1
                try dsm.respond(toCommand: &cmd, status: IOUSBHostCIMessageStatusSuccess,
                                deviceAddress: addr)
                deviceSMs[addr] = dsm

            case IOUSBHostCIMessageTypeDeviceDestroy,
                 IOUSBHostCIMessageTypeDeviceStart,
                 IOUSBHostCIMessageTypeDevicePause,
                 IOUSBHostCIMessageTypeDeviceUpdate:
                let devAddr = Int(cmd.data0 & 0xFF)
                if let dsm = deviceSMs[devAddr] {
                    try dsm.respond(toCommand: &cmd, status: IOUSBHostCIMessageStatusSuccess)
                    if msgType == IOUSBHostCIMessageTypeDeviceDestroy {
                        deviceSMs.removeValue(forKey: devAddr)
                    }
                }

            case IOUSBHostCIMessageTypeEndpointCreate:
                let esm = try IOUSBHostCIEndpointStateMachine(__interface: ci, command: &cmd)
                try esm.respond(toCommand: &cmd, status: IOUSBHostCIMessageStatusSuccess)
                let key = (esm.deviceAddress << 8) | esm.endpointAddress
                endpointSMs[key] = esm
                log("Endpoint dev=\(esm.deviceAddress) ep=0x\(String(format: "%02X", esm.endpointAddress))")

            case IOUSBHostCIMessageTypeEndpointDestroy,
                 IOUSBHostCIMessageTypeEndpointPause,
                 IOUSBHostCIMessageTypeEndpointUpdate,
                 IOUSBHostCIMessageTypeEndpointReset,
                 IOUSBHostCIMessageTypeEndpointSetNextTransfer:
                let devAddr = Int(cmd.data0 & 0xFF)
                let epAddr = Int((cmd.data0 >> 8) & 0xFF)
                let key = (devAddr << 8) | epAddr
                if let esm = endpointSMs[key] {
                    try esm.respond(toCommand: &cmd, status: IOUSBHostCIMessageStatusSuccess)
                    if msgType == IOUSBHostCIMessageTypeEndpointDestroy {
                        endpointSMs.removeValue(forKey: key)
                    }
                }

            default:
                break
            }
        } catch {
            log("handleCommand error: \(error)")
        }
    }

    // MARK: Doorbell handler

    private func handleDoorbells(
        _ ci: IOUSBHostControllerInterface,
        _ doorbells: UnsafePointer<IOUSBHostCIDoorbell>,
        _ count: UInt32
    ) {
        for i in 0..<Int(count) {
            let db = doorbells[i]
            let devAddr = Int(db & 0xFF)
            let epAddr = Int((db >> 8) & 0xFF)
            let key = (devAddr << 8) | epAddr
            guard let esm = endpointSMs[key] else { continue }
            do {
                try esm.processDoorbell(db)
                try processTransfers(for: esm)
            } catch {
                log("Doorbell ep=0x\(String(format: "%02X", epAddr)) error: \(error)")
            }
        }
    }

    // MARK: Transfer processing

    private func processTransfers(for esm: IOUSBHostCIEndpointStateMachine) throws {
        while esm.endpointState == IOUSBHostCIEndpointStateActive {
            let xfer = esm.currentTransferMessage
            guard (xfer.pointee.control & kMsgValid) != 0 else { break }

            switch IOUSBHostCIMessageType(rawValue: xfer.pointee.control & kMsgTypeMask) {
            case IOUSBHostCIMessageTypeSetupTransfer:
                handleSetupTransfer(esm: esm, xfer: xfer)
            case IOUSBHostCIMessageTypeNormalTransfer:
                try handleNormalTransfer(esm: esm, xfer: xfer)
            case IOUSBHostCIMessageTypeStatusTransfer:
                try esm.enqueueTransferCompletion(
                    for: xfer, status: IOUSBHostCIMessageStatusSuccess, transferLength: 0)
            default:
                return
            }
        }
    }

    private func handleSetupTransfer(
        esm: IOUSBHostCIEndpointStateMachine,
        xfer: UnsafePointer<IOUSBHostCIMessage>
    ) {
        let d1 = xfer.pointee.data1
        let bmRequestType = UInt8((d1 >> 0) & 0xFF)
        let bRequest = UInt8((d1 >> 8) & 0xFF)
        let wValue = UInt16((d1 >> 16) & 0xFFFF)
        let wIndex = UInt16((d1 >> 32) & 0xFFFF)
        let wLength = UInt16((d1 >> 48) & 0xFFFF)

        pendingSetupIsOUT = (bmRequestType & 0x80) == 0 && wLength > 0

        if pendingSetupIsOUT {
            pendingResponse = nil
        } else {
            pendingResponse = resolveRequest(
                bmRequestType: bmRequestType, bRequest: bRequest,
                wValue: wValue, wIndex: wIndex, wLength: wLength)
        }

        do {
            try esm.enqueueTransferCompletion(
                for: xfer, status: IOUSBHostCIMessageStatusSuccess, transferLength: 0)
        } catch {
            log("Setup ACK error: \(error)")
        }
    }

    private func handleNormalTransfer(
        esm: IOUSBHostCIEndpointStateMachine,
        xfer: UnsafePointer<IOUSBHostCIMessage>
    ) throws {
        let ep = esm.endpointAddress
        let maxLen = Int(xfer.pointee.data0 & 0x0FFF_FFFF)

        if ep == 0 {
            // EP0 data phase
            if pendingSetupIsOUT {
                // Host→Device data (e.g. SET_LINE_CODING)
                if let buf = UnsafeRawPointer(bitPattern: UInt(xfer.pointee.data1)), maxLen > 0 {
                    let data = Array(UnsafeBufferPointer(
                        start: buf.assumingMemoryBound(to: UInt8.self), count: maxLen))
                    handleClassDataOUT(data: data)
                }
                try esm.enqueueTransferCompletion(
                    for: xfer, status: IOUSBHostCIMessageStatusSuccess, transferLength: maxLen)
            } else {
                // Device→Host data (descriptors, GET_LINE_CODING)
                var written = 0
                if let resp = pendingResponse, !resp.isEmpty,
                   let buf = UnsafeMutableRawPointer(bitPattern: UInt(xfer.pointee.data1)) {
                    let n = min(resp.count, maxLen)
                    resp.withUnsafeBytes { buf.copyMemory(from: $0.baseAddress!, byteCount: n) }
                    written = n
                    pendingResponse = nil
                }
                try esm.enqueueTransferCompletion(
                    for: xfer, status: IOUSBHostCIMessageStatusSuccess, transferLength: written)
            }
            pendingSetupIsOUT = false
        } else if (ep & 0x80) != 0 {
            // IN endpoint (device → host)
            let bytes: [UInt8]
            // Check if it's bulk or interrupt based on descriptor (simplified: use subclass method)
            if ep == 0x82 {
                bytes = handleInterruptIN(endpointAddress: Int(ep), maxLength: maxLen)
            } else {
                bytes = handleBulkIN(endpointAddress: Int(ep), maxLength: maxLen)
            }
            let n = min(bytes.count, maxLen)
            if n > 0, let buf = UnsafeMutableRawPointer(bitPattern: UInt(xfer.pointee.data1)) {
                bytes.withUnsafeBufferPointer { buf.copyMemory(from: $0.baseAddress!, byteCount: n) }
            }
            try esm.enqueueTransferCompletion(
                for: xfer, status: IOUSBHostCIMessageStatusSuccess, transferLength: n)
        } else {
            // OUT endpoint (host → device)
            if let buf = UnsafeRawPointer(bitPattern: UInt(xfer.pointee.data1)), maxLen > 0 {
                let data = Array(UnsafeBufferPointer(
                    start: buf.assumingMemoryBound(to: UInt8.self), count: maxLen))
                handleBulkOUT(endpointAddress: Int(ep), data: data)
            }
            try esm.enqueueTransferCompletion(
                for: xfer, status: IOUSBHostCIMessageStatusSuccess, transferLength: maxLen)
        }
    }

    // MARK: Request dispatch

    private func resolveRequest(bmRequestType: UInt8, bRequest: UInt8,
                                wValue: UInt16, wIndex: UInt16, wLength: UInt16) -> Data? {
        let descType = UInt8((wValue >> 8) & 0xFF)
        let descIndex = UInt8(wValue & 0xFF)

        switch bmRequestType {
        case 0x80:
            switch bRequest {
            case kUSBReqGetDescriptor:
                switch descType {
                case kUSBDescDevice: return prefix(descriptors.device, wLength)
                case kUSBDescConfig: return prefix(descriptors.configuration, wLength)
                case kUSBDescString:
                    switch descIndex {
                    case 0: return prefix(string0, wLength)
                    case 1: return prefix(stringManuf, wLength)
                    case 2: return prefix(stringProd, wLength)
                    case 3: return prefix(stringSerial, wLength)
                    default: return nil
                    }
                default: return nil
                }
            case kUSBReqGetConfig: return Data([1])
            default: return Data()
            }

        case 0x81:
            if bRequest == kUSBReqGetInterface { return Data([0]) }
            return handleClassSetup(bmRequestType: bmRequestType, bRequest: bRequest,
                                    wValue: wValue, wIndex: wIndex, wLength: wLength)

        case 0x00:
            if bRequest == kUSBReqSetConfig { return Data() }
            return Data()

        case 0x01:
            return Data()

        case 0x21, 0xA1:
            return handleClassSetup(bmRequestType: bmRequestType, bRequest: bRequest,
                                    wValue: wValue, wIndex: wIndex, wLength: wLength)

        default:
            return Data()
        }
    }

    // MARK: Helpers

    func log(_ msg: String) {
        print("[loki-usb] \(msg)")
        fflush(stdout)
    }

    private func prefix(_ bytes: [UInt8], _ max: UInt16) -> Data {
        Data(bytes.prefix(Int(max)))
    }

    private static func makeStringDescriptor(_ text: String) -> [UInt8] {
        let utf16 = Array(text.utf16)
        var result: [UInt8] = [UInt8(2 + utf16.count * 2), 0x03]
        for cp in utf16 {
            result.append(UInt8(cp & 0xFF))
            result.append(UInt8(cp >> 8))
        }
        return result
    }
}
