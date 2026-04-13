"""
Loki-PrintServer - USB over IP Server
Main FastAPI application
"""
import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Optional

import psutil
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from .models import (
    ConnectedClient,
    DeviceInfo,
    ServerConfig,
    ServerStatus,
    ShareRequest,
    UnshareRequest,
)
from .usbip import USBIPManager
from .discovery import MDNSAnnouncer
from .forwarder import ForwardingManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("loki-printserver")

usbip_manager = USBIPManager()
mdns_announcer = MDNSAnnouncer()
forwarder = ForwardingManager()
connected_clients: list[WebSocket] = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Loki-PrintServer...")
    await usbip_manager.start()
    await mdns_announcer.start(
        service_name="LokiPrint",
        port=int(os.getenv("LOKI_PORT", 7575)),
    )
    yield
    logger.info("Shutting down Loki-PrintServer...")
    # Unshare all devices on shutdown
    for state in forwarder.get_all_shared():
        await forwarder.unshare_device(state.bus_id)
    await mdns_announcer.stop()
    await usbip_manager.stop()


app = FastAPI(
    title="Loki-PrintServer",
    description="USB over IP Server - Share USB devices over the network",
    version="1.0.0",
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
    return {"status": "ok", "version": "1.0.0", "service": "loki-printserver"}


@app.get("/api/status", response_model=ServerStatus)
async def get_status():
    """Get server status and system info."""
    cpu = psutil.cpu_percent(interval=0.1)
    mem = psutil.virtual_memory()
    shared = forwarder.get_all_shared()
    return ServerStatus(
        running=True,
        version="1.0.0",
        cpu_percent=cpu,
        memory_used_mb=mem.used // 1024 // 1024,
        memory_total_mb=mem.total // 1024 // 1024,
        shared_device_count=len(shared),
        uptime_seconds=int(time.time() - psutil.boot_time()),
    )


@app.get("/api/devices", response_model=list[DeviceInfo])
async def list_devices():
    """List all USB devices with forwarding info."""
    devices = await usbip_manager.list_devices()
    for dev in devices:
        state = forwarder.get_state(dev.bus_id)
        if state:
            dev.is_shared = True
            dev.forward_info = forwarder.get_forward_info(dev.bus_id)
    return devices


@app.get("/api/devices/shared", response_model=list[DeviceInfo])
async def list_shared_devices():
    """List only shared devices."""
    devices = await usbip_manager.list_devices()
    result = []
    for dev in devices:
        state = forwarder.get_state(dev.bus_id)
        if state:
            dev.is_shared = True
            dev.forward_info = forwarder.get_forward_info(dev.bus_id)
            result.append(dev)
    return result


@app.post("/api/devices/share")
async def share_device(req: ShareRequest):
    """Share a USB device via all available methods."""
    devices = await usbip_manager.list_devices()
    target = next((d for d in devices if d.bus_id == req.bus_id), None)
    if not target:
        raise HTTPException(status_code=404, detail=f"Device {req.bus_id} not found")

    try:
        state = await forwarder.share_device(
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


@app.get("/api/devices/{bus_id}/forward")
async def get_forward_info(bus_id: str):
    """Get forwarding details for a specific device."""
    info = forwarder.get_forward_info(bus_id)
    if not info.get("shared"):
        raise HTTPException(status_code=404, detail="Device not shared")
    return info


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
