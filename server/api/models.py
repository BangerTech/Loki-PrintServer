"""
Loki-PrintServer - Data Models
"""

from pydantic import BaseModel


class DeviceInfo(BaseModel):
    bus_id: str
    vendor_id: str
    product_id: str
    manufacturer: str | None = None
    product: str | None = None
    serial: str | None = None
    device_class: str | None = None
    speed: str | None = None
    is_shared: bool = False
    is_infrastructure: bool = False
    client_ip: str | None = None
    forward_info: dict | None = None
    custom_name: str | None = None


class RenameRequest(BaseModel):
    name: str


class ShareRequest(BaseModel):
    bus_id: str


class UnshareRequest(BaseModel):
    bus_id: str


class AttachModeRequest(BaseModel):
    mode: str  # "usbip" (Windows) or "bridge" (macOS FineCut)


class ClientLogRequest(BaseModel):
    level: str = "info"
    message: str
    bus_id: str = ""
    host: str = ""


class ConnectedClient(BaseModel):
    ip: str
    port: int
    bus_id: str
    connected_at: str
    device_info: DeviceInfo | None = None


class ServerStatus(BaseModel):
    running: bool
    version: str
    cpu_percent: float
    memory_used_mb: int
    memory_total_mb: int
    shared_device_count: int
    uptime_seconds: int


class ServerConfig(BaseModel):
    port: int
    api_port: int
    allow_all: bool
    secret_configured: bool
