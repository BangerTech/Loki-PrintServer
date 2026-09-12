"""
Raw USB Bridge — Forwards USB control + bulk transfers over TCP.

Used for vendor-specific USB devices (e.g. Mimaki plotters) where the
host application (FineCut) communicates via IOKit USB directly, not serial.

The bridge opens the real USB device via pyusb on the Pi, listens on a
TCP port, and transparently proxies all USB operations to/from the
virtual device on the Mac.

TCP Frame Protocol:
  [TYPE:1][LENGTH:2 big-endian][PAYLOAD:LENGTH]

  0x01  CTRL_REQ    Client→Server  Control transfer request
        Payload: [bmRequestType:1][bRequest:1][wValue:2LE][wIndex:2LE][wLength:2LE][data:wLength if OUT]
  0x02  CTRL_RESP   Server→Client  Control transfer response
        Payload: [status:1][data:*]  (status: 0=OK, 1=STALL, 2=ERROR)
  0x03  BULK_OUT    Client→Server  Bulk OUT data (host→device)
        Payload: [endpoint:1][data:*]
  0x04  BULK_IN     Server→Client  Bulk IN data (device→host, pushed)
        Payload: [endpoint:1][data:*]
  0x05  PING        Client→Server  Keepalive / IN poll request
        Payload: [endpoint:1]
  0x06  PONG        Server→Client  Keepalive / IN poll response (empty = no data)
        Payload: [endpoint:1][data:*]  (may be empty)
"""

import asyncio
import logging
import struct
import threading
import time

logger = logging.getLogger("loki-printserver.usb-bridge")

MSG_CTRL_REQ = 0x01
MSG_CTRL_RESP = 0x02
MSG_BULK_OUT = 0x03
MSG_BULK_IN = 0x04
MSG_PING = 0x05
MSG_PONG = 0x06

STATUS_OK = 0
STATUS_STALL = 1
STATUS_ERROR = 2


def _frame(msg_type: int, payload: bytes) -> bytes:
    return struct.pack(">BH", msg_type, len(payload)) + payload


class USBBridge:
    """Bridges a real USB device (via pyusb) to TCP clients."""

    def __init__(self, vid: int, pid: int, tcp_port: int):
        self.vid = vid
        self.pid = pid
        self.tcp_port = tcp_port
        self._dev = None
        self._ep_out = None
        self._ep_in = None
        self._running = False
        self._client_writer: asyncio.StreamWriter | None = None
        self._lock = threading.Lock()

    async def start(self):
        if not self._open_device():
            logger.error(f"USB bridge: device {self.vid:04x}:{self.pid:04x} not found")
            return False

        self._running = True
        server = await asyncio.start_server(
            self._handle_client, "0.0.0.0", self.tcp_port
        )
        logger.info(
            f"USB bridge: {self.vid:04x}:{self.pid:04x} → TCP:{self.tcp_port}"
        )
        asyncio.ensure_future(self._serve(server))
        return True

    async def stop(self):
        self._running = False
        self._close_device()

    def _open_device(self) -> bool:
        try:
            import usb.core
            import usb.util

            self._unbind_usbserial()

            dev = usb.core.find(idVendor=self.vid, idProduct=self.pid)
            if not dev:
                return False

            try:
                if dev.is_kernel_driver_active(0):
                    dev.detach_kernel_driver(0)
                    logger.info("USB bridge: detached kernel driver from interface 0")
            except (usb.core.USBError, NotImplementedError):
                pass

            try:
                dev.set_configuration()
            except usb.core.USBError:
                dev.reset()
                time.sleep(0.5)
                dev = usb.core.find(idVendor=self.vid, idProduct=self.pid)
                if not dev:
                    return False
                try:
                    if dev.is_kernel_driver_active(0):
                        dev.detach_kernel_driver(0)
                except (usb.core.USBError, NotImplementedError):
                    pass
                dev.set_configuration()

            cfg = dev.get_active_configuration()
            intf = cfg[(0, 0)]

            self._ep_out = usb.util.find_descriptor(
                intf,
                custom_match=lambda e: usb.util.endpoint_direction(
                    e.bEndpointAddress
                )
                == usb.util.ENDPOINT_OUT,
            )
            self._ep_in = usb.util.find_descriptor(
                intf,
                custom_match=lambda e: usb.util.endpoint_direction(
                    e.bEndpointAddress
                )
                == usb.util.ENDPOINT_IN,
            )

            usb.util.claim_interface(dev, 0)
            self._dev = dev
            logger.info(
                f"USB bridge: opened {dev.product} "
                f"(EP OUT=0x{self._ep_out.bEndpointAddress:02X}, "
                f"EP IN=0x{self._ep_in.bEndpointAddress:02X})"
            )

            self._self_test()
            return True
        except Exception as e:
            logger.error(f"USB bridge: open failed: {e}")
            return False

    def _self_test(self):
        """Quick self-test: send OH; and check if plotter responds."""
        try:
            r = self._dev.ctrl_transfer(0xC1, 0x0D, 0, 0, 4, timeout=2000)
            logger.info(f"USB bridge self-test: status before = {bytes(r).hex()}")

            self._ep_out.write(b"OH;", timeout=5000)
            time.sleep(0.3)

            r = self._dev.ctrl_transfer(0xC1, 0x0D, 0, 0, 4, timeout=2000)
            status = bytes(r).hex()
            logger.info(f"USB bridge self-test: status after OH; = {status}")

            if status != "00000000":
                data = self._ep_in.read(512, timeout=1000)
                logger.info(
                    f"USB bridge self-test: BULK IN = {bytes(data)!r} "
                    f"*** PLOTTER RESPONDING ***"
                )
            else:
                logger.warning(
                    "USB bridge self-test: plotter NOT responding to OH;"
                )
        except Exception as e:
            logger.warning(f"USB bridge self-test failed: {e}")

    def _unbind_usbserial(self):
        """Remove any usbserial_generic registration for our VID:PID."""
        try:
            remove_path = "/sys/bus/usb-serial/drivers/generic/remove_id"
            remove_id = f"{self.vid:04x} {self.pid:04x}"
            with open(remove_path, "w") as f:
                f.write(remove_id)
            logger.info(f"USB bridge: removed usbserial binding for {remove_id}")
        except FileNotFoundError:
            pass
        except OSError:
            pass

    def _close_device(self):
        if self._dev:
            try:
                import usb.util

                usb.util.release_interface(self._dev, 0)
            except Exception:
                pass
            self._dev = None

    async def _serve(self, server: asyncio.AbstractServer):
        async with server:
            await server.serve_forever()

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        addr = writer.get_extra_info("peername")
        logger.info(f"USB bridge: client connected from {addr}")
        self._client_writer = writer

        try:
            while self._running:
                header = await reader.readexactly(3)
                msg_type, length = struct.unpack(">BH", header)
                payload = await reader.readexactly(length) if length > 0 else b""

                if msg_type == MSG_CTRL_REQ:
                    resp = self._handle_control(payload)
                    writer.write(resp)
                    await writer.drain()

                elif msg_type == MSG_BULK_OUT:
                    self._handle_bulk_out(payload)

                elif msg_type == MSG_PING:
                    resp = self._handle_ping(payload)
                    writer.write(resp)
                    await writer.drain()

        except (asyncio.IncompleteReadError, ConnectionError):
            logger.info("USB bridge: client disconnected")
        except Exception as e:
            logger.error(f"USB bridge: client error: {e}")
        finally:
            self._client_writer = None
            writer.close()

    def _handle_control(self, payload: bytes) -> bytes:
        """Forward a USB control transfer to the real device."""
        if len(payload) < 8:
            return _frame(MSG_CTRL_RESP, bytes([STATUS_ERROR]))

        bmRT, bReq = payload[0], payload[1]
        wVal = struct.unpack_from("<H", payload, 2)[0]
        wIdx = struct.unpack_from("<H", payload, 4)[0]
        wLen = struct.unpack_from("<H", payload, 6)[0]
        data_out = payload[8:] if len(payload) > 8 else b""

        is_status_poll = (bmRT == 0xC1 and bReq == 0x0D)
        if not is_status_poll:
            logger.debug(
                f"USB bridge: CTRL bmRT=0x{bmRT:02X} bReq=0x{bReq:02X} "
                f"wVal=0x{wVal:04X} wIdx=0x{wIdx:04X} wLen={wLen}"
            )

        if not self._dev:
            return _frame(MSG_CTRL_RESP, bytes([STATUS_ERROR]))

        try:
            import usb.core

            if bmRT & 0x80:
                result = self._dev.ctrl_transfer(bmRT, bReq, wVal, wIdx, wLen, timeout=2000)
                if not is_status_poll:
                    logger.debug(f"USB bridge: CTRL IN → {len(result)}B")
                return _frame(MSG_CTRL_RESP, bytes([STATUS_OK]) + bytes(result))
            else:
                self._dev.ctrl_transfer(bmRT, bReq, wVal, wIdx, data_out, timeout=2000)
                logger.debug("USB bridge: CTRL OUT → OK")
                return _frame(MSG_CTRL_RESP, bytes([STATUS_OK]))
        except usb.core.USBError as e:
            if e.errno == 32:  # pipe error (STALL)
                return _frame(MSG_CTRL_RESP, bytes([STATUS_STALL]))
            logger.warning(f"USB bridge: control error: {e}")
            return _frame(MSG_CTRL_RESP, bytes([STATUS_ERROR]))

    def _handle_bulk_out(self, payload: bytes):
        """Forward bulk OUT data to the real device."""
        if len(payload) < 2 or not self._dev:
            return
        ep = payload[0]
        data = payload[1:]
        logger.debug(f"USB bridge: BULK OUT ep=0x{ep:02X} {len(data)}B: {data[:40]!r}")
        try:
            if self._ep_out:
                self._ep_out.write(data, timeout=5000)
        except Exception as e:
            logger.warning(f"USB bridge: bulk OUT error: {e}")

    def _handle_ping(self, payload: bytes) -> bytes:
        """Poll bulk IN endpoint and return any available data."""
        if len(payload) < 1 or not self._dev:
            return _frame(MSG_PONG, payload[:1] if payload else b"\x82")

        ep = payload[0]
        try:
            import usb.core

            if self._ep_in:
                data = self._ep_in.read(64, timeout=50)
                if len(data) > 0:
                    logger.debug(
                        f"USB bridge: BULK IN ep=0x{ep:02X} → {len(data)}B: "
                        f"{bytes(data)[:40]!r}"
                    )
                    return _frame(MSG_PONG, bytes([ep]) + bytes(data))
        except usb.core.USBTimeoutError:
            pass
        except Exception as e:
            logger.warning(f"USB bridge: bulk IN error: {e}")

        return _frame(MSG_PONG, bytes([ep]))


# Vendor-specific VIDs that need raw USB bridge instead of serial
RAW_USB_VIDS = {0x0A50}  # Mimaki


def needs_raw_usb(vendor_id: str) -> bool:
    """Check if a device needs the raw USB bridge instead of socat serial."""
    try:
        return int(vendor_id, 16) in RAW_USB_VIDS
    except (ValueError, TypeError):
        return False
