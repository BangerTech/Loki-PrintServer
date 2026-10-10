"""
Plot Cut API — deliver finished plotter bytes unchanged.

Plot Cut / VectorCraft sends raw MGL-IIc or HP-GL. Loki does not generate
or rewrite commands. Existing FineCut / USB/IP / socat paths stay intact.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import socket
import struct
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from .models import PlotCutDevice, PlotCutJobResult

logger = logging.getLogger("loki-printserver.plotcut")

MAX_JOB_BYTES = 50 * 1024 * 1024
USBIP_ST_AVAILABLE = 0
USBIP_ST_USED = 1
PLOTTER_CLASS_RE = re.compile(r"plotter|cutter", re.IGNORECASE)
DATA_DIR = Path(os.getenv("LOKI_DATA_DIR", "/etc/loki-printserver"))
CONFIG_NAME = "plotcut_devices.json"
BUNDLED_CONFIG = Path(__file__).resolve().parents[1] / "config" / CONFIG_NAME

DEFAULT_DEVICES: list[dict[str, Any]] = [
    {
        "id": "mimaki-cg60sr",
        "name": "Mimaki CG-60SR",
        "kind": "usb-vendor",
        "match": {"vendor": "0a50", "product": "0001"},
        "path": "",
        "baud": 9600,
        "flow": "rtscts",
    },
    {
        "id": "vevor",
        "name": "Vevor",
        "kind": "serial",
        "match": {"vendor": "1a86", "product": "7523"},
        "path": "",
        "baud": 9600,
        "flow": "none",
    },
]


class PlotCutError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def _norm_hex(value: str | None) -> str:
    raw = (value or "").strip().lower().replace("0x", "")
    return raw.zfill(4) if raw else ""


def _stable_id(vid: str, pid: str) -> str:
    return f"plotter-{_norm_hex(vid)}-{_norm_hex(pid)}"


def load_plotcut_config(data_dir: Path | None = None) -> list[dict[str, Any]]:
    """Load device config from LOKI_DATA_DIR, seeding a default file if needed."""
    directory = data_dir or DATA_DIR
    path = directory / CONFIG_NAME
    try:
        directory.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            src = BUNDLED_CONFIG if BUNDLED_CONFIG.is_file() else None
            payload = src.read_text(encoding="utf-8") if src else json.dumps(
                {"devices": DEFAULT_DEVICES}, indent=2
            )
            path.write_text(payload, encoding="utf-8")
        raw = json.loads(path.read_text(encoding="utf-8"))
        devices = raw.get("devices", raw) if isinstance(raw, dict) else raw
        if isinstance(devices, list) and devices:
            return devices
    except OSError as e:
        logger.warning("Plot-Cut-Config nicht lesbar (%s) — Defaults", e)
    except json.JSONDecodeError as e:
        logger.warning("Plot-Cut-Config ungültig (%s) — Defaults", e)
    return list(DEFAULT_DEVICES)


def read_usbip_status(usbip_busid: str | None) -> int | None:
    """Return usbip_status (0=available, 1=used) or None if unreadable."""
    if not usbip_busid:
        return None
    candidates = [
        Path(f"/sys/bus/usb/devices/{usbip_busid}/usbip_status"),
        Path(f"/sys/devices/{usbip_busid}/usbip_status"),
    ]
    for path in candidates:
        try:
            if path.is_file():
                return int(path.read_text(encoding="utf-8").strip().split()[0])
        except (OSError, ValueError):
            return None
    return None


def _hex_ipv4(hex_ip: str) -> str:
    n = int(hex_ip, 16)
    return socket.inet_ntoa(struct.pack("<I", n))


def tcp_peers_on_port(port: int, *, local_only_ok: bool = False) -> list[str]:
    """IPv4 peers with an ESTABLISHED connection to a local listening port."""
    peers: list[str] = []
    want = f"{port:04X}"
    try:
        lines = Path("/proc/net/tcp").read_text(encoding="utf-8").splitlines()[1:]
    except OSError:
        return peers
    for line in lines:
        parts = line.split()
        if len(parts) < 4:
            continue
        local_addr, remote_addr, state = parts[1], parts[2], parts[3]
        if state != "01":
            continue
        _lip, lport = local_addr.split(":")
        if lport.upper() != want:
            continue
        rip, _rport = remote_addr.split(":")
        ip = _hex_ipv4(rip)
        if ip in ("0.0.0.0",):
            continue
        if ip.startswith("127.") and not local_only_ok:
            continue
        if ip not in peers:
            peers.append(ip)
    return peers


def usbip_peer_label(usbip_port: int = 7575) -> str:
    peers = tcp_peers_on_port(usbip_port)
    return peers[0] if peers else "USB/IP-Client"


def socat_has_remote_client(port: int | None) -> bool:
    if not port:
        return False
    return bool(tcp_peers_on_port(port))


def find_serial_by_id(vendor_id: str, product_id: str) -> str | None:
    """Resolve /dev/serial/by-id for a VID:PID. Never returns ttyUSB* aliases."""
    by_id = Path("/dev/serial/by-id")
    if not by_id.is_dir():
        return None
    vid, pid = _norm_hex(vendor_id), _norm_hex(product_id)
    try:
        for link in sorted(by_id.iterdir()):
            try:
                target = link.resolve()
                sysdev = Path("/sys/class/tty") / target.name / "device"
                usb = sysdev.resolve()
                for _ in range(6):
                    id_vendor = usb / "idVendor"
                    id_product = usb / "idProduct"
                    if id_vendor.is_file() and id_product.is_file():
                        if _norm_hex(id_vendor.read_text()) == vid and _norm_hex(
                            id_product.read_text()
                        ) == pid:
                            return str(link)
                    usb = usb.parent
            except OSError:
                continue
    except OSError:
        return None
    return None


def write_socat_tcp(host: str, port: int, data: bytes) -> int:
    with socket.create_connection((host, port), timeout=30) as sock:
        sock.settimeout(60)
        sent = 0
        view = memoryview(data)
        while sent < len(data):
            n = sock.send(view[sent:])
            if n == 0:
                raise OSError("socat-Verbindung geschlossen")
            sent += n
    return sent


def write_serial_path(path: str, data: bytes, baud: int, flow: str) -> int:
    import serial  # pyserial

    if path.startswith("/dev/ttyUSB") or path.startswith("/dev/ttyACM"):
        raise OSError(f"Unstabiler Seriell-Pfad verweigert: {path}")
    rtscts = (flow or "").lower() in ("rtscts", "crtscts", "hw")
    with serial.Serial(
        path,
        baudrate=baud or 9600,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        rtscts=rtscts,
        timeout=30,
        write_timeout=60,
    ) as ser:
        return int(ser.write(data))


def write_usblp(path: str, data: bytes) -> int:
    with open(path, "wb", buffering=0) as fh:
        return int(fh.write(data))


def write_pyusb_bulk(vendor_id: str, product_id: str, data: bytes) -> int:
    """Claim the device via pyusb, write Bulk-OUT, then release."""
    import usb.core
    import usb.util

    vid = int(_norm_hex(vendor_id), 16)
    pid = int(_norm_hex(product_id), 16)
    dev = usb.core.find(idVendor=vid, idProduct=pid)
    if dev is None:
        raise FileNotFoundError(f"USB {vendor_id}:{product_id} nicht gefunden")

    detached = False
    claimed = False
    try:
        try:
            if dev.is_kernel_driver_active(0):
                dev.detach_kernel_driver(0)
                detached = True
        except (usb.core.USBError, NotImplementedError):
            pass
        try:
            dev.set_configuration()
        except usb.core.USBError:
            pass
        cfg = dev.get_active_configuration()
        intf = cfg[(0, 0)]
        ep_out = usb.util.find_descriptor(
            intf,
            custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress)
            == usb.util.ENDPOINT_OUT,
        )
        if ep_out is None:
            raise RuntimeError("Kein Bulk-OUT-Endpunkt")
        usb.util.claim_interface(dev, 0)
        claimed = True
        written = 0
        chunk_size = 4096
        for offset in range(0, len(data), chunk_size):
            chunk = data[offset:offset + chunk_size]
            written += int(ep_out.write(chunk, timeout=30000))
        return written
    finally:
        if claimed:
            try:
                usb.util.release_interface(dev, 0)
            except Exception:
                pass
        if detached:
            try:
                dev.attach_kernel_driver(0)
            except Exception:
                pass


@dataclass
class ResolvedDevice:
    id: str
    name: str
    kind: str
    vendor_id: str
    product_id: str
    bus_id: str | None = None
    path: str = ""
    baud: int = 9600
    flow: str = "none"
    present: bool = False
    usbip_exported: bool = False
    usbip_busid: str | None = None
    serial_port: int | None = None
    serial_dev: str | None = None
    attached_to: str | None = None
    available: bool = False
    block_reason: str | None = None
    config: dict[str, Any] = field(default_factory=dict)


ListUsbFn = Callable[[], Awaitable[list[Any]]]
NameFn = Callable[[str, str], str | None]


class PlotCutService:
    def __init__(
        self,
        forwarder,
        list_usb: ListUsbFn,
        *,
        data_dir: Path | None = None,
        usbip_port: int = 7575,
        get_custom_name: NameFn | None = None,
    ):
        self.forwarder = forwarder
        self._list_usb = list_usb
        self._data_dir = data_dir or DATA_DIR
        self.usbip_port = usbip_port
        self._get_custom_name = get_custom_name
        self._meta_lock = asyncio.Lock()
        self._busy: set[str] = set()
        self.write_socat = write_socat_tcp
        self.write_serial = write_serial_path
        self.write_lp = write_usblp
        self.write_pyusb = write_pyusb_bulk

    def _configs(self) -> list[dict[str, Any]]:
        return load_plotcut_config(self._data_dir)

    def _config_by_vidpid(self) -> dict[tuple[str, str], dict[str, Any]]:
        out: dict[tuple[str, str], dict[str, Any]] = {}
        for cfg in self._configs():
            match = cfg.get("match") or {}
            vid = _norm_hex(match.get("vendor") or cfg.get("vendor_id"))
            pid = _norm_hex(match.get("product") or cfg.get("product_id"))
            if vid and pid:
                out[(vid, pid)] = cfg
        return out

    def _display_name(self, vid: str, pid: str, fallback: str) -> str:
        if self._get_custom_name:
            custom = self._get_custom_name(vid, pid)
            if custom:
                return custom
        return fallback

    async def resolve_all(self) -> list[ResolvedDevice]:
        usb_devs = await self._list_usb()
        by_vidpid: dict[tuple[str, str], Any] = {}
        for dev in usb_devs:
            key = (_norm_hex(getattr(dev, "vendor_id", "")),
                   _norm_hex(getattr(dev, "product_id", "")))
            if key[0] and key[1]:
                by_vidpid[key] = dev

        seen_ids: set[str] = set()
        resolved: list[ResolvedDevice] = []

        for cfg in self._configs():
            match = cfg.get("match") or {}
            vid = _norm_hex(match.get("vendor") or cfg.get("vendor_id"))
            pid = _norm_hex(match.get("product") or cfg.get("product_id"))
            usb = by_vidpid.get((vid, pid))
            item = self._build_resolved(cfg, usb, vid, pid)
            seen_ids.add(item.id)
            resolved.append(item)

        for (vid, pid), usb in by_vidpid.items():
            if (vid, pid) in self._config_by_vidpid():
                continue
            cls = getattr(usb, "device_class", "") or ""
            if not PLOTTER_CLASS_RE.search(cls):
                continue
            auto_id = _stable_id(vid, pid)
            if auto_id in seen_ids:
                continue
            name = self._display_name(
                vid, pid,
                getattr(usb, "custom_name", None)
                or getattr(usb, "product", None)
                or f"Plotter {vid}:{pid}",
            )
            cfg = {
                "id": auto_id,
                "name": name,
                "kind": "",
                "match": {"vendor": vid, "product": pid},
                "path": "",
                "baud": 9600,
                "flow": "none",
            }
            resolved.append(self._build_resolved(cfg, usb, vid, pid))
            seen_ids.add(auto_id)

        return resolved

    def _build_resolved(
        self, cfg: dict[str, Any], usb: Any | None, vid: str, pid: str,
    ) -> ResolvedDevice:
        bus_id = getattr(usb, "bus_id", None) if usb else None
        state = None
        if self.forwarder:
            if bus_id:
                state = self.forwarder.find_state(bus_id, vid, pid)
            elif vid and pid:
                state = self.forwarder.find_state("", vid, pid)
        if state:
            bus_id = state.bus_id

        serial_port = state.serial_port if state else cfg.get("socat_port")
        serial_dev = state.serial_dev if state else None
        kind = self._infer_kind(cfg, serial_dev, serial_port)
        name = self._display_name(vid, pid, cfg.get("name") or f"Plotter {vid}:{pid}")
        path = (cfg.get("path") or "") or ""
        if not path and serial_dev and not str(serial_dev).startswith("usb-bridge:"):
            if "/serial/by-id/" in str(serial_dev):
                path = serial_dev

        item = ResolvedDevice(
            id=cfg.get("id") or _stable_id(vid, pid),
            name=name,
            kind=kind,
            vendor_id=vid,
            product_id=pid,
            bus_id=bus_id,
            path=path,
            baud=int(cfg.get("baud") or 9600),
            flow=str(cfg.get("flow") or "none"),
            present=usb is not None,
            usbip_exported=bool(state.usbip_shared) if state else False,
            usbip_busid=state.usbip_busid if state else None,
            serial_port=serial_port,
            serial_dev=serial_dev,
            config=cfg,
        )
        self._apply_occupancy(item)
        return item

    def _infer_kind(
        self, cfg: dict[str, Any], serial_dev: str | None, serial_port: int | None,
    ) -> str:
        configured = (cfg.get("kind") or "").strip().lower()
        if serial_dev and str(serial_dev).startswith("usb-bridge:"):
            return "usb-vendor"
        if serial_port and serial_dev and not str(serial_dev).startswith("usb-bridge:"):
            return "socat"
        path = cfg.get("path") or ""
        if path.startswith("/dev/usb/lp"):
            return "usblp"
        if configured in ("serial", "socat", "usblp", "usb-vendor"):
            return configured
        return "usb-vendor" if _norm_hex((cfg.get("match") or {}).get("vendor")) == "0a50" else "serial"

    def _apply_occupancy(self, item: ResolvedDevice, *, ignore_job: bool = False) -> None:
        if not ignore_job and (
            item.id in self._busy
            or (item.bus_id and self.forwarder and self.forwarder.is_plotcut_busy(item.bus_id))
        ):
            item.available = False
            item.block_reason = "job"
            item.attached_to = item.attached_to or "Plot Cut"
            return

        if not item.present:
            path = item.path
            if path and Path(path).exists():
                item.present = True
            else:
                item.available = False
                item.block_reason = "missing"
                return

        if item.usbip_exported:
            status = read_usbip_status(item.usbip_busid)
            if status == USBIP_ST_USED:
                host = usbip_peer_label(self.usbip_port)
                item.available = False
                item.attached_to = host
                item.block_reason = "usbip-attached"
                return
            if status is None:
                host = usbip_peer_label(self.usbip_port)
                item.available = False
                item.attached_to = host
                item.block_reason = "usbip-unknown"
                return
            # status == AVAILABLE: free to pause-and-write
        elif item.usbip_busid and read_usbip_status(item.usbip_busid) == USBIP_ST_USED:
            host = usbip_peer_label(self.usbip_port)
            item.available = False
            item.attached_to = host
            item.block_reason = "usbip-attached"
            return

        bridge = None
        if item.bus_id and self.forwarder:
            bridge = self.forwarder.get_usb_bridge(item.bus_id)
        if bridge is not None and getattr(bridge, "has_client", False):
            item.available = False
            item.attached_to = "Mac-Client"
            item.block_reason = "bridge-client"
            return

        if item.kind == "socat" and socat_has_remote_client(item.serial_port):
            peers = tcp_peers_on_port(item.serial_port or 0)
            item.available = False
            item.attached_to = peers[0] if peers else "Serial-Client"
            item.block_reason = "socat-client"
            return

        item.available = True
        item.attached_to = None
        item.block_reason = None

    async def list_devices(self) -> list[PlotCutDevice]:
        items = await self.resolve_all()
        return [
            PlotCutDevice(
                id=i.id,
                name=i.name,
                kind=i.kind,
                available=i.available,
                attached_to=i.attached_to,
            )
            for i in items
        ]

    async def submit_job(self, device_id: str, data: bytes) -> PlotCutJobResult:
        if len(data) > MAX_JOB_BYTES:
            raise PlotCutError(413, "Auftrag zu groß (maximal 50 MB).")

        items = await self.resolve_all()
        item = next((i for i in items if i.id == device_id), None)
        if item is None:
            raise PlotCutError(404, f"Unbekanntes Plot-Cut-Gerät „{device_id}“.")

        self._apply_occupancy(item)
        if not item.available:
            raise self._occupancy_error(item)

        async with self._meta_lock:
            if item.id in self._busy:
                raise PlotCutError(
                    409,
                    f"{item.name} ist gerade mit einem anderen Auftrag beschäftigt.",
                )
            self._busy.add(item.id)
        if item.bus_id and self.forwarder:
            self.forwarder.mark_plotcut_busy(item.bus_id, True)

        paused_usbip = False
        try:
            self._apply_occupancy(item, ignore_job=True)
            if not item.available:
                raise self._occupancy_error(item)

            if item.usbip_exported and item.block_reason is None and self.forwarder:
                status = read_usbip_status(item.usbip_busid)
                if status == USBIP_ST_AVAILABLE and item.bus_id:
                    paused_usbip = await self.forwarder.pause_usbip_for_plotcut(item.bus_id)

            written = await asyncio.to_thread(self._write_bytes, item, data)
            logger.info("Plot Cut: %s wrote %s bytes via %s", item.id, written, item.kind)
            return PlotCutJobResult(ok=True, bytes=written)
        except PlotCutError:
            raise
        except FileNotFoundError as e:
            raise PlotCutError(
                503,
                f"{item.name} ist nicht angeschlossen oder die Gerätedatei fehlt ({e}).",
            ) from e
        except Exception as e:
            logger.exception("Plot Cut write failed for %s", item.id)
            raise PlotCutError(
                500, f"Schreibfehler an {item.name}: {e}",
            ) from e
        finally:
            if paused_usbip and item.bus_id and self.forwarder:
                try:
                    await self.forwarder.resume_usbip_after_plotcut(item.bus_id)
                except Exception as e:
                    logger.error("Plot Cut: USB/IP restore failed for %s: %s", item.id, e)
            if item.bus_id and self.forwarder:
                self.forwarder.mark_plotcut_busy(item.bus_id, False)
            async with self._meta_lock:
                self._busy.discard(item.id)

    def _occupancy_error(self, item: ResolvedDevice) -> PlotCutError:
        reason = item.block_reason
        host = item.attached_to or "einem Client"
        if reason == "job":
            return PlotCutError(
                409, f"{item.name} ist gerade mit einem anderen Auftrag beschäftigt.",
            )
        if reason == "usbip-attached":
            return PlotCutError(
                409,
                f"{item.name} ist per USB/IP an {host} angehängt. "
                "Bitte im Loki-Client trennen.",
            )
        if reason == "usbip-unknown":
            return PlotCutError(
                409,
                f"{item.name} ist per USB/IP exportiert; der Belegt-Status ist unklar. "
                "Bitte im Loki-Client trennen.",
            )
        if reason == "bridge-client":
            return PlotCutError(
                409,
                f"{item.name} ist an einen Mac-Client (FineCut) angehängt. "
                "Bitte im Loki-Client trennen.",
            )
        if reason == "socat-client":
            return PlotCutError(
                409,
                f"{item.name} wird gerade über Serial-over-TCP von {host} genutzt.",
            )
        return PlotCutError(
            503, f"{item.name} ist nicht angeschlossen oder die Gerätedatei fehlt.",
        )

    def _write_bytes(self, item: ResolvedDevice, data: bytes) -> int:
        """Synchronous write dispatch (runs in a worker thread)."""
        bridge = None
        if item.bus_id and self.forwarder:
            bridge = self.forwarder.get_usb_bridge(item.bus_id)

        if item.kind == "socat" and item.serial_port:
            return self.write_socat("127.0.0.1", int(item.serial_port), data)

        if bridge is not None:
            return int(bridge.write_raw(data))

        if item.kind == "usb-vendor":
            return self.write_pyusb(item.vendor_id, item.product_id, data)

        if item.kind == "usblp":
            path = item.path or "/dev/usb/lp0"
            if not Path(path).exists():
                raise FileNotFoundError(path)
            return self.write_lp(path, data)

        path = item.path or find_serial_by_id(item.vendor_id, item.product_id) or ""
        if item.serial_port and item.kind in ("serial", "socat"):
            return self.write_socat("127.0.0.1", int(item.serial_port), data)
        if not path:
            raise FileNotFoundError("kein /dev/serial/by-id-Pfad")
        return self.write_serial(path, data, item.baud, item.flow)


def create_router(service: PlotCutService) -> APIRouter:
    router = APIRouter(prefix="/api/plotcut", tags=["plotcut"])

    @router.get("/devices")
    async def list_plotcut_devices():
        devices = await service.list_devices()
        return {"devices": [d.model_dump() for d in devices]}

    @router.post("/devices/{device_id}/job", response_model=PlotCutJobResult)
    async def submit_plotcut_job(device_id: str, request: Request):
        body = await request.body()
        try:
            return await service.submit_job(device_id, body)
        except PlotCutError as e:
            raise HTTPException(status_code=e.status_code, detail=e.detail) from e

    return router
