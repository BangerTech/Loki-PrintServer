"""
Loki-PrintServer - USB over IP Server
Main FastAPI application
"""
import asyncio
import json
import logging
import os
import time
from collections import deque
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path

import psutil
from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from .discovery import MDNSAnnouncer
from .forwarder import ForwardingManager
from .models import (
    AttachModeRequest,
    ClientLogRequest,
    ConnectedClient,
    DeviceInfo,
    RenameRequest,
    ServerConfig,
    ServerStatus,
    ShareRequest,
    UnshareRequest,
)
from .plotcut import PlotCutService, create_router
from .usbip import USBIPManager
from .version import APP_VERSION

_LOG_FILE = Path(os.getenv("LOKI_DATA_DIR", "/etc/loki-printserver")) / "loki-server.log"
_LOG_BUFFER: deque[str] = deque(maxlen=500)
_LOG_HANDLERS: list[logging.Handler] = []


class _RingBufferHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            _LOG_BUFFER.append(self.format(record))
        except Exception:
            self.handleError(record)


def _setup_logging() -> None:
    if not _LOG_HANDLERS:
        fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        ring = _RingBufferHandler()
        ring.setFormatter(fmt)
        _LOG_HANDLERS.append(ring)
        try:
            _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                _LOG_FILE, maxBytes=2_000_000, backupCount=3, encoding="utf-8",
            )
            file_handler.setFormatter(fmt)
            _LOG_HANDLERS.append(file_handler)
        except OSError as e:
            logging.getLogger("loki-printserver").warning("File logging unavailable: %s", e)

    logging.basicConfig(level=logging.INFO, handlers=_LOG_HANDLERS, force=True)
    for name in ("loki-printserver", "uvicorn", "uvicorn.error", "uvicorn.access"):
        log = logging.getLogger(name)
        log.setLevel(logging.INFO)
        for handler in _LOG_HANDLERS:
            if handler not in log.handlers:
                log.addHandler(handler)
        log.propagate = False


_setup_logging()
logger = logging.getLogger("loki-printserver")

usbip_manager = USBIPManager()
mdns_announcer = MDNSAnnouncer()
forwarder = ForwardingManager()
connected_clients: list[WebSocket] = []

_NAMES_FILE = Path(os.getenv("LOKI_DATA_DIR", "/etc/loki-printserver")) / "custom_names.json"
_custom_names: dict[str, str] = {}  # "vendor_id:product_id" -> display name


def _load_custom_names():
    global _custom_names
    try:
        if _NAMES_FILE.exists():
            _custom_names = json.loads(_NAMES_FILE.read_text())
    except Exception as e:
        logger.warning(f"Could not load custom names: {e}")


def _save_custom_names():
    try:
        _NAMES_FILE.parent.mkdir(parents=True, exist_ok=True)
        _NAMES_FILE.write_text(json.dumps(_custom_names, indent=2))
    except Exception as e:
        logger.error(f"Could not save custom names: {e}")


def _apply_custom_names(devices: list[DeviceInfo]):
    for dev in devices:
        key = f"{dev.vendor_id}:{dev.product_id}"
        if key in _custom_names:
            dev.custom_name = _custom_names[key]


async def _auto_restore_devices():
    """Re-share devices that were shared before the last restart."""
    saved = forwarder.load_saved_devices()
    if not saved:
        return

    # Give USB subsystem a moment to settle after container start
    await asyncio.sleep(2)
    current = await usbip_manager.list_devices()
    current_ids = {d.bus_id for d in current}

    for entry in saved:
        if entry.bus_id not in current_ids:
            logger.info(f"Auto-restore: device {entry.bus_id} not present yet, skipping")
            continue
        logger.info(f"Auto-restore: re-sharing {entry.bus_id} ({entry.product_name})")
        try:
            await forwarder.share_device(
                bus_id=entry.bus_id,
                device_class=entry.device_class,
                vendor_id=entry.vendor_id,
                product_id=entry.product_id,
                product_name=entry.product_name,
                auto_share=entry.auto_share,
            )
        except Exception as e:
            logger.warning(f"Auto-restore failed for {entry.bus_id}: {e}")


async def _release_idle_usbip_loop():
    """Give plotters back to macOS shortly after the Windows USB/IP client leaves."""
    while True:
        await asyncio.sleep(5)
        try:
            await forwarder.release_idle_usbip()
        except Exception as e:
            logger.warning("idle USB/IP release failed: %s", e)


async def _auto_share_all():
    """Share every detected USB device (AUTO_SHARE_ALL=true mode)."""
    await asyncio.sleep(2)
    devices = await usbip_manager.list_devices()
    for dev in devices:
        if not forwarder.get_state(dev.bus_id):
            logger.info(f"AUTO_SHARE_ALL: sharing {dev.bus_id} ({dev.product})")
            try:
                await forwarder.share_device(
                    bus_id=dev.bus_id,
                    device_class=dev.device_class or "",
                    vendor_id=dev.vendor_id,
                    product_id=dev.product_id,
                    product_name=dev.product or f"Loki-{dev.bus_id}",
                    auto_share=True,
                )
            except Exception as e:
                logger.warning(f"AUTO_SHARE_ALL failed for {dev.bus_id}: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _setup_logging()
    logger.info("Starting Loki-PrintServer v%s...", APP_VERSION)
    _load_custom_names()
    await usbip_manager.start()
    await mdns_announcer.start(
        service_name="LokiPrint",
        port=int(os.getenv("LOKI_PORT", 7575)),
    )

    # Auto-share on startup
    if os.getenv("AUTO_SHARE_ALL", "false").lower() == "true":
        asyncio.create_task(_auto_share_all())
    else:
        asyncio.create_task(_auto_restore_devices())

    idle_task = asyncio.create_task(_release_idle_usbip_loop())

    yield
    idle_task.cancel()
    logger.info("Shutting down Loki-PrintServer...")
    # Stop forwarding processes but keep saved state for auto-restore
    for state in forwarder.get_all_shared():
        await forwarder.unshare_device(state.bus_id, persist=False)
    await mdns_announcer.stop()
    await usbip_manager.stop()


app = FastAPI(
    title="Loki-PrintServer",
    description="USB over IP Server - Share USB devices over the network",
    version=APP_VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok", "version": APP_VERSION, "service": "loki-printserver"}


@app.get("/api/status", response_model=ServerStatus)
async def get_status():
    """Get server status and system info."""
    cpu = psutil.cpu_percent(interval=0.1)
    mem = psutil.virtual_memory()
    await forwarder.prune_duplicate_vidpid()
    return ServerStatus(
        running=True,
        version=APP_VERSION,
        cpu_percent=cpu,
        memory_used_mb=mem.used // 1024 // 1024,
        memory_total_mb=mem.total // 1024 // 1024,
        shared_device_count=forwarder.unique_shared_count(),
        uptime_seconds=int(time.time() - psutil.boot_time()),
    )


@app.get("/api/logs")
async def get_logs(lines: int = Query(80, ge=1, le=500)):
    """Last log lines: live buffer first (this process), then the rotating file."""
    if _LOG_BUFFER:
        return {"lines": list(_LOG_BUFFER)[-lines:]}
    text_lines: list[str] = []
    if _LOG_FILE.exists():
        try:
            text_lines = _LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as e:
            logger.warning("Could not read log file: %s", e)
    return {"lines": text_lines[-lines:]}


@app.post("/api/client-log")
async def client_log(req: ClientLogRequest, request: Request):
    """Windows/macOS clients report attach errors so they show in the dashboard log."""
    ip = request.client.host if request.client else "?"
    host = (req.host or ip)[:80]
    msg = (req.message or "").replace("\n", " ").strip()[:500]
    bus = (req.bus_id or "")[:32]
    prefix = f"Client {host}"
    if bus:
        prefix += f" [{bus}]"
    line = f"{prefix}: {msg}"
    level = (req.level or "info").strip().lower()
    if level == "error":
        logger.error(line)
    elif level == "warning":
        logger.warning(line)
    else:
        logger.info(line)
    return {"ok": True}


async def _merge_shared_devices(devices: list[DeviceInfo]) -> list[DeviceInfo]:
    """Keep shared plotters visible when pyusb/usbip temporarily hides them."""
    visible_ids = {d.bus_id for d in devices}
    await forwarder.prune_duplicate_vidpid(visible_ids)
    visible_vidpid = {(d.vendor_id, d.product_id) for d in devices}
    for dev in devices:
        state = forwarder.find_state(dev.bus_id, dev.vendor_id, dev.product_id)
        if state:
            dev.is_shared = True
            dev.forward_info = forwarder.get_forward_info(state.bus_id)
    for state in forwarder.get_all_shared():
        if state.bus_id in visible_ids:
            continue
        if (state.vendor_id, state.product_id) in visible_vidpid:
            continue
        devices.append(DeviceInfo(
            bus_id=state.bus_id,
            vendor_id=state.vendor_id,
            product_id=state.product_id,
            manufacturer=None,
            product=state.product_name or None,
            serial=None,
            device_class=state.device_class or None,
            speed=None,
            is_shared=True,
            is_infrastructure=False,
            forward_info=forwarder.get_forward_info(state.bus_id),
        ))
    return devices


@app.get("/api/devices", response_model=list[DeviceInfo])
async def list_devices():
    """List all USB devices with forwarding info."""
    devices = await _merge_shared_devices(await usbip_manager.list_devices())
    _apply_custom_names(devices)
    return devices


@app.get("/api/devices/shared", response_model=list[DeviceInfo])
async def list_shared_devices():
    """List only shared devices."""
    devices = await _merge_shared_devices(await usbip_manager.list_devices())
    result = [d for d in devices if d.is_shared]
    _apply_custom_names(result)
    return result


@app.post("/api/devices/share")
async def share_device(req: ShareRequest):
    """Share a USB device via all available methods."""
    devices = await usbip_manager.list_devices()
    target = next((d for d in devices if d.bus_id == req.bus_id), None)
    if not target:
        raise HTTPException(status_code=404, detail=f"Device {req.bus_id} not found")

    try:
        await forwarder.share_device(
            bus_id=req.bus_id,
            device_class=target.device_class or "",
            vendor_id=target.vendor_id,
            product_id=target.product_id,
            product_name=target.product or f"Loki-{req.bus_id}",
        )
        info = forwarder.get_forward_info(req.bus_id)
        await broadcast_event("device_shared", {
            "bus_id": req.bus_id,
            "forward_info": info,
        })
        return {
            "success": True,
            "message": f"Device {req.bus_id} is now shared",
            "bus_id": req.bus_id,
            "forward_info": info,
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/devices/unshare")
async def unshare_device(req: UnshareRequest):
    """Stop sharing a USB device."""
    try:
        await forwarder.unshare_device(req.bus_id)
        await broadcast_event("device_unshared", {"bus_id": req.bus_id})
        return {"success": True, "message": f"Device {req.bus_id} is no longer shared"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/devices/{bus_id}/auto-share")
async def toggle_auto_share(bus_id: str, enabled: bool = True):
    """Toggle auto-share (persist across restarts) for a device."""
    state = forwarder.get_state(bus_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"Device {bus_id} is not currently shared")
    forwarder.mark_auto_share(bus_id, enabled)
    return {"success": True, "bus_id": bus_id, "auto_share": enabled}


@app.put("/api/devices/{bus_id}/name")
async def rename_device(bus_id: str, req: RenameRequest):
    """Set or clear a custom display name for a device."""
    devices = await usbip_manager.list_devices()
    target = next((d for d in devices if d.bus_id == bus_id), None)
    if not target:
        raise HTTPException(status_code=404, detail=f"Device {bus_id} not found")

    key = f"{target.vendor_id}:{target.product_id}"
    name = req.name.strip()
    if name:
        _custom_names[key] = name
    else:
        _custom_names.pop(key, None)
    _save_custom_names()
    await broadcast_event("device_renamed", {"bus_id": bus_id, "custom_name": name or None})
    return {"success": True, "bus_id": bus_id, "custom_name": name or None}


@app.get("/api/devices/{bus_id}/forward")
async def get_forward_info(bus_id: str):
    """Get forwarding details for a specific device."""
    info = forwarder.get_forward_info(bus_id)
    if not info.get("shared"):
        raise HTTPException(status_code=404, detail="Device not shared")
    return info


@app.post("/api/devices/{bus_id}/attach-mode")
async def set_attach_mode(bus_id: str, req: AttachModeRequest):
    """Switch Mimaki/raw-USB devices between Mac USB-bridge and Windows USB/IP."""
    try:
        mode = req.mode.strip().lower()
        logger.info("attach-mode %s → %s (Windows USB/IP or Mac bridge)", bus_id, mode)
        info = await forwarder.set_attach_mode(bus_id, mode)
        logger.info("attach-mode %s done usbip=%s busid=%s",
                    bus_id, info.get("usbip"), info.get("usbip_busid"))
        await broadcast_event("device_shared", {"bus_id": bus_id, "forward_info": info})
        return {"success": True, "bus_id": bus_id, "forward_info": info}
    except ValueError as e:
        logger.warning("attach-mode %s rejected: %s", bus_id, e)
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        logger.error("attach-mode %s failed: %s", bus_id, e)
        raise HTTPException(status_code=400, detail=str(e)) from e


def _plotcut_custom_name(vid: str, pid: str) -> str | None:
    from .plotcut import _norm_hex

    for key, name in _custom_names.items():
        parts = str(key).split(":")
        if len(parts) == 2 and _norm_hex(parts[0]) == vid and _norm_hex(parts[1]) == pid:
            return name
    return None


async def _plotcut_list_usb():
    devices = await _merge_shared_devices(await usbip_manager.list_devices())
    _apply_custom_names(devices)
    return devices


plotcut_service = PlotCutService(
    forwarder=forwarder,
    list_usb=_plotcut_list_usb,
    usbip_port=int(os.getenv("LOKI_PORT", 7575)),
    get_custom_name=_plotcut_custom_name,
)
app.include_router(create_router(plotcut_service))


@app.get("/api/clients", response_model=list[ConnectedClient])
async def list_clients():
    """List connected clients."""
    return await usbip_manager.get_connected_clients()


@app.get("/api/config", response_model=ServerConfig)
async def get_config():
    return ServerConfig(
        port=int(os.getenv("LOKI_PORT", 7575)),
        api_port=int(os.getenv("LOKI_API_PORT", 7576)),
        allow_all=os.getenv("LOKI_ALLOW_ALL", "true").lower() == "true",
        secret_configured=bool(os.getenv("LOKI_SECRET")),
    )


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    connected_clients.append(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        connected_clients.remove(websocket)


async def broadcast_event(event: str, data: dict):
    message = {"event": event, "data": data}
    for client in connected_clients[:]:
        try:
            await client.send_json(message)
        except Exception:
            connected_clients.remove(client)


app.mount("/assets", StaticFiles(directory="/app/web"), name="assets")


@app.get("/", response_class=HTMLResponse)
async def web_ui():
    try:
        with open("/app/web/index.html") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(content="<h1>Loki-PrintServer running</h1>")
