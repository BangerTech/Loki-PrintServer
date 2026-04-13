"""
Loki-PrintServer - USB/IP Manager
Manages USB device sharing via the Linux USB/IP kernel subsystem.
"""
import asyncio
import logging
import re
import subprocess
from typing import Optional

import usb.core
import usb.util

from .models import ConnectedClient, DeviceInfo

logger = logging.getLogger("loki-printserver.usbip")

USB_CLASS_NAMES = {
    0x00: "Device",
    0x01: "Audio",
    0x02: "Communications",
    0x03: "HID",
    0x05: "Physical",
    0x06: "Image",
    0x07: "Printer",
    0x08: "Mass Storage",
    0x09: "Hub",
    0x0A: "CDC-Data",
    0x0B: "Smart Card",
    0x0D: "Content Security",
    0x0E: "Video",
    0x0F: "Personal Healthcare",
    0xDC: "Diagnostic",
    0xE0: "Wireless Controller",
    0xEF: "Miscellaneous",
    0xFE: "Application Specific",
    0xFF: "Vendor Specific",
}

# Known USB device names by vendor:product ID
KNOWN_DEVICES: dict[str, tuple[str, str]] = {
    # CH340 = Vevor, generic Chinese cutters, many vinyl/cutting plotters
    "1a86:7523": ("Cutting Plotter", "CH340 Serial Cutter (Vevor / Generic)"),
    "1a86:7522": ("Cutting Plotter", "CH340K Serial Cutter"),
    "1a86:5523": ("Cutting Plotter", "CH341 Serial Cutter"),
    # FTDI = Roland, Graphtec (older), professional plotters
    "0403:6001": ("Cutting Plotter", "FT232 Serial Cutter (Roland / FTDI)"),
    # CP210x = Liyu, GCC, budget vinyl cutters
    "10c4:ea60": ("Cutting Plotter", "CP2102 Serial Cutter"),
    "067b:2303": ("Cutting Plotter", "PL2303 Serial Cutter"),
    "0b4d:110a": ("Graphtec", "Graphtec Plotter"),
    "0b4d:110c": ("Graphtec", "FC8600 Cutting Plotter"),
    "0b4d:1121": ("Graphtec", "CE7000 Cutting Plotter"),
    "0459:0017": ("Silhouette", "Silhouette Cameo"),
    "0459:0060": ("Silhouette", "Silhouette Cameo 3"),
    "0459:0069": ("Silhouette", "Silhouette Cameo 4"),
    "1949:0044": ("Cricut", "Cricut Explore"),
    "1949:006a": ("Cricut", "Cricut Maker"),
    "2166:0000": ("Roland DG", "Roland Vinyl Cutter"),
    "0922:0028": ("Dymo", "DYMO LabelWriter 450"),
    "04f9:0027": ("Brother", "Brother QL Label Printer"),
}

USB_SPEED_NAMES = {
    1: "Low Speed (1.5 Mbps)",
    2: "Full Speed (12 Mbps)",
    3: "High Speed (480 Mbps)",
    4: "SuperSpeed (5 Gbps)",
    5: "SuperSpeed+ (10 Gbps)",
}

# Vendor IDs that are always USB infrastructure (never shareable peripherals)
INFRA_VENDORS = {
    "1d6b",  # Linux Foundation (root hubs)
    "0000",  # unassigned / placeholder
}

def _is_infrastructure(dev) -> bool:
    """Return True for USB hubs, root controllers and other non-shareable devices."""
    vid = f"{dev.idVendor:04x}"
    # Hub class
    if dev.bDeviceClass == 0x09:
        return True
    # Linux root hubs / unassigned VIDs
    if vid in INFRA_VENDORS:
        return True
    # VID:PID 0000:0000 = placeholder
    if dev.idVendor == 0 and dev.idProduct == 0:
        return True
    return False


def _run(cmd: list[str], check=True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def _get_string_safe(dev, index) -> Optional[str]:
    if index == 0:
        return None
    try:
        return usb.util.get_string(dev, index)
    except Exception:
        return None


class USBIPManager:
    def __init__(self):
        self._shared_bus_ids: set[str] = set()
        self._daemon_proc: Optional[asyncio.subprocess.Process] = None

    async def start(self):
        """Load kernel modules and start usbipd daemon."""
        await self._load_modules()
        await self._start_daemon()

    async def stop(self):
        """Stop the usbipd daemon."""
        if self._daemon_proc and self._daemon_proc.returncode is None:
            self._daemon_proc.terminate()
            await self._daemon_proc.wait()

    async def _load_modules(self):
        for mod in ("usbip_core", "usbip_host"):
            try:
                proc = await asyncio.create_subprocess_exec(
                    "modprobe", mod,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await proc.communicate()
                if proc.returncode == 0:
                    logger.info(f"Loaded kernel module: {mod}")
                else:
                    logger.warning(f"Could not load kernel module {mod} (may already be loaded)")
            except Exception as e:
                logger.warning(f"modprobe {mod}: {e}")

    async def _start_daemon(self):
        """Start usbipd in the background."""
        try:
            self._daemon_proc = await asyncio.create_subprocess_exec(
                "usbipd",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            logger.info("usbipd daemon started")
        except FileNotFoundError:
            logger.error("usbipd not found - USB/IP sharing will not work")

    async def list_devices(self) -> list[DeviceInfo]:
        """Enumerate all USB devices using pyusb."""
        devices = []
        shared = self._shared_bus_ids.copy()

        try:
            usb_devices = list(usb.core.find(find_all=True))
        except Exception as e:
            logger.error(f"Failed to enumerate USB devices: {e}")
            return []

        for dev in usb_devices:
            bus_id = f"{dev.bus}-{dev.address}"
            try:
                manufacturer = _get_string_safe(dev, dev.iManufacturer)
                product = _get_string_safe(dev, dev.iProduct)
                serial = _get_string_safe(dev, dev.iSerialNumber)
            except Exception:
                manufacturer = product = serial = None

            device_class = USB_CLASS_NAMES.get(dev.bDeviceClass, f"0x{dev.bDeviceClass:02X}")
            speed = USB_SPEED_NAMES.get(getattr(dev, "speed", 0), "Unknown")
            vid = f"{dev.idVendor:04x}"
            pid = f"{dev.idProduct:04x}"

            # Enrich with known device names when USB descriptor strings are missing
            known = KNOWN_DEVICES.get(f"{vid}:{pid}")
            if known:
                if not manufacturer:
                    manufacturer = known[0]
                if not product:
                    product = known[1]
                # Override generic class label for serial-based cutters
                if device_class in ("Vendor Specific", "Device"):
                    device_class = "Cutting Plotter / Serial"

            devices.append(DeviceInfo(
                bus_id=bus_id,
                vendor_id=vid,
                product_id=pid,
                manufacturer=manufacturer,
                product=product,
                serial=serial,
                device_class=device_class,
                speed=speed,
                is_shared=bus_id in shared,
                is_infrastructure=_is_infrastructure(dev),
            ))

        return devices

    async def get_shared_devices(self) -> list[DeviceInfo]:
        """Return only shared devices."""
        all_devices = await self.list_devices()
        return [d for d in all_devices if d.is_shared]

    async def share_device(self, bus_id: str):
        """Export a USB device via usbip."""
        # usbip uses format like "1-1.2" not "1-3" (bus-port)
        # We need to find the usbip-compatible bus id
        usbip_bus_id = await self._resolve_usbip_busid(bus_id)
        if not usbip_bus_id:
            raise ValueError(f"Cannot resolve bus_id {bus_id} to usbip format")

        result = _run(["usbip", "bind", "--busid", usbip_bus_id], check=False)
        if result.returncode != 0 and "already bound" not in result.stderr:
            raise RuntimeError(f"usbip bind failed: {result.stderr.strip()}")

        self._shared_bus_ids.add(bus_id)
        logger.info(f"Sharing device {bus_id} (usbip: {usbip_bus_id})")

    async def unshare_device(self, bus_id: str):
        """Stop exporting a USB device."""
        usbip_bus_id = await self._resolve_usbip_busid(bus_id)
        if usbip_bus_id:
            result = _run(["usbip", "unbind", "--busid", usbip_bus_id], check=False)
            if result.returncode != 0:
                logger.warning(f"usbip unbind: {result.stderr.strip()}")

        self._shared_bus_ids.discard(bus_id)
        logger.info(f"Stopped sharing device {bus_id}")

    async def _resolve_usbip_busid(self, bus_id: str) -> Optional[str]:
        """Convert 'bus-address' format to usbip 'bus-port' format."""
        try:
            result = _run(["usbip", "list", "--local"], check=False)
            # Parse output like "- busid 1-1.2 (1a86:7523)"
            for line in result.stdout.splitlines():
                m = re.search(r"busid\s+(\S+)", line)
                if m:
                    return m.group(1)
        except Exception as e:
            logger.error(f"Failed to resolve bus_id: {e}")
        return bus_id

    async def get_connected_clients(self) -> list[ConnectedClient]:
        """Get list of clients connected via usbip."""
        clients = []
        try:
            _run(["usbip", "list", "--remote", "localhost"], check=False)
        except Exception:
            pass
        return clients
