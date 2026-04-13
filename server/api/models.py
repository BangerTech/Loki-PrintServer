"""
Loki-PrintServer - Data Models
"""
from typing import Optional
from pydantic import BaseModel


class DeviceInfo(BaseModel):
    bus_id: str
    vendor_id: str
    product_id: str
    manufacturer: Optional[str] = None
    product: Optional[str] = None
    serial: Optional[str] = None
    device_class: Optional[str] = None
    speed: Optional[str] = None
    is_shared: bool = False
    client_ip: Optional[str] = None
    forward_info: Optional[dict] = None


class ShareRequest(BaseModel):
    bus_id: str


class UnshareRequest(BaseModel):
    bus_id: str


class ConnectedClient(BaseModel):
    ip: str
    port: int
    bus_id: str
    connected_at: str
    device_info: Optional[DeviceInfo] = None


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
