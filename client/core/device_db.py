"""
Loki-PrintServer - USB Device Name Database
Maps vendor:product IDs to human-readable names.
Focused on plotters, printers and common USB-serial adapters.
"""
from typing import Optional

# Format: "vendor_id:product_id" -> ("Manufacturer", "Product Name", icon)
KNOWN_DEVICES: dict[str, tuple[str, str, str]] = {
    # ── USB-Serial Adapters (common in plotters) ──────────────────────────
    "1a86:7523": ("QinHeng Electronics", "CH340 USB-Serial", "⚡"),
    "1a86:7522": ("QinHeng Electronics", "CH340K USB-Serial", "⚡"),
    "1a86:5523": ("QinHeng Electronics", "CH341 USB-Serial", "⚡"),
    "0403:6001": ("FTDI", "FT232 USB-Serial", "⚡"),
    "0403:6010": ("FTDI", "FT2232 USB-Serial", "⚡"),
    "0403:6011": ("FTDI", "FT4232 USB-Serial", "⚡"),
    "0403:6014": ("FTDI", "FT232H USB-Serial", "⚡"),
    "10c4:ea60": ("Silicon Labs", "CP2102 USB-Serial", "⚡"),
    "10c4:ea61": ("Silicon Labs", "CP2105 USB-Serial", "⚡"),
    "067b:2303": ("Prolific", "PL2303 USB-Serial", "⚡"),
    "067b:23a3": ("Prolific", "PL2303GC USB-Serial", "⚡"),

    # ── Cutting Plotters ──────────────────────────────────────────────────
    "0b4d:110a": ("Graphtec", "Graphtec Plotter", "✂"),
    "0b4d:110c": ("Graphtec", "FC8600 Cutting Plotter", "✂"),
    "0b4d:1121": ("Graphtec", "CE7000 Cutting Plotter", "✂"),
    "0b4d:113e": ("Graphtec", "CE6000 Cutting Plotter", "✂"),
    "0459:0017": ("Silhouette", "Silhouette Cameo", "✂"),
    "0459:0018": ("Silhouette", "Silhouette Cameo 2", "✂"),
    "0459:0060": ("Silhouette", "Silhouette Cameo 3", "✂"),
    "0459:0069": ("Silhouette", "Silhouette Cameo 4", "✂"),
    "0459:006c": ("Silhouette", "Silhouette Portrait 3", "✂"),
    "0459:004b": ("Silhouette", "Silhouette Curio", "✂"),
    "1949:0044": ("Cricut", "Cricut Explore", "✂"),
    "1949:006a": ("Cricut", "Cricut Maker", "✂"),
    "1949:0073": ("Cricut", "Cricut Joy", "✂"),
    "2166:0000": ("Roland DG", "Roland Vinyl Cutter", "✂"),
    "2166:0001": ("Roland DG", "Roland GX-24 Plotter", "✂"),
    "04b8:0005": ("Roland DG", "Roland Plotter", "✂"),
    "0a39:0003": ("Mimaki", "Mimaki Plotter", "✂"),

    # ── Label Printers ────────────────────────────────────────────────────
    "0922:0020": ("Dymo", "DYMO LabelWriter 400", "🏷"),
    "0922:0028": ("Dymo", "DYMO LabelWriter 450", "🏷"),
    "0922:002a": ("Dymo", "DYMO LabelWriter 450 Turbo", "🏷"),
    "0922:0029": ("Dymo", "DYMO LabelWriter 450 Twin Turbo", "🏷"),
    "04f9:0027": ("Brother", "Brother QL-500 Label Printer", "🏷"),
    "04f9:2042": ("Brother", "Brother QL-820NWB", "🏷"),
    "04f9:205c": ("Brother", "Brother QL-1110NWB", "🏷"),

    # ── Regular Printers ─────────────────────────────────────────────────
    "03f0:0004": ("HP", "HP DeskJet", "🖨"),
    "03f0:4117": ("HP", "HP LaserJet", "🖨"),
    "04a9:0000": ("Canon", "Canon Printer", "🖨"),
    "04b8:0005": ("Epson", "Epson Printer", "🖨"),
    "04e8:3413": ("Samsung", "Samsung Printer", "🖨"),

    # ── Common USB Hubs & Controllers ────────────────────────────────────
    "2109:3431": ("VIA Labs", "USB 2.0 Hub", "⬡"),
    "2109:0817": ("VIA Labs", "USB 3.0 Hub", "⬡"),
    "05e3:0608": ("Genesys Logic", "USB 2.0 Hub", "⬡"),
    "1d6b:0002": ("Linux", "xHCI Host Controller (USB 2.0)", "⬡"),
    "1d6b:0003": ("Linux", "xHCI Host Controller (USB 3.0)", "⬡"),

    # ── Scanners ─────────────────────────────────────────────────────────
    "04b8:0121": ("Epson", "Perfection V39 Scanner", "📷"),
    "04a9:220e": ("Canon", "CanoScan Scanner", "📷"),
    "03f0:1c05": ("HP", "HP ScanJet", "📷"),
}

# USB Device Class → (name, icon)
CLASS_NAMES: dict[int, tuple[str, str]] = {
    0x00: ("Device (class per interface)", "🔌"),
    0x01: ("Audio", "🔊"),
    0x02: ("Communications (CDC)", "📡"),
    0x03: ("HID (Keyboard/Mouse)", "⌨"),
    0x05: ("Physical Interface", "🕹"),
    0x06: ("Image (Camera/Scanner)", "📷"),
    0x07: ("Printer", "🖨"),
    0x08: ("Mass Storage", "💾"),
    0x09: ("Hub", "⬡"),
    0x0A: ("CDC-Data", "📡"),
    0x0B: ("Smart Card", "💳"),
    0x0D: ("Content Security", "🔒"),
    0x0E: ("Video", "📹"),
    0x0F: ("Personal Healthcare", "❤"),
    0xDC: ("Diagnostic Device", "🔧"),
    0xE0: ("Wireless Controller", "📶"),
    0xEF: ("Miscellaneous", "🔌"),
    0xFE: ("Application Specific", "⚙"),
    0xFF: ("Vendor Specific", "⚙"),
}


def lookup_device(vendor_id: str, product_id: str) -> Optional[tuple[str, str, str]]:
    """
    Look up a device by vendor:product ID.
    Returns (manufacturer, product_name, icon) or None if unknown.
    """
    key = f"{vendor_id.lower()}:{product_id.lower()}"
    return KNOWN_DEVICES.get(key)


def get_display_name(vendor_id: str, product_id: str,
                     manufacturer: Optional[str], product: Optional[str],
                     device_class_id: int = 0xFF) -> tuple[str, str, str]:
    """
    Returns (manufacturer_display, product_display, icon) with best available info.
    Priority: known DB > USB descriptor strings > class name fallback.
    """
    db = lookup_device(vendor_id, product_id)
    if db:
        return db  # (manufacturer, product, icon)

    class_info = CLASS_NAMES.get(device_class_id, ("Vendor Specific", "⚙"))
    class_name, class_icon = class_info

    mfr = manufacturer or vendor_id.upper()
    prod = product or f"{class_name} ({vendor_id}:{product_id})"

    return mfr, prod, class_icon


def is_plotter_or_printer(vendor_id: str, product_id: str, device_class_id: int = 0xFF) -> bool:
    """Returns True if the device is likely a plotter, cutter or printer."""
    db = lookup_device(vendor_id, product_id)
    if db:
        icon = db[2]
        return icon in ("✂", "🖨", "🏷")
    return device_class_id == 0x07  # USB Printer class
