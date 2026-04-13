"""
Loki-PrintServer - API Client
Communicates with the server REST API.
"""
import httpx
from typing import Optional
from dataclasses import dataclass


@dataclass
class DeviceInfo:
    bus_id: str
    vendor_id: str
    product_id: str
    manufacturer: Optional[str]
    product: Optional[str]
    serial: Optional[str]
    device_class: Optional[str]
    speed: Optional[str]
    is_shared: bool
    client_ip: Optional[str] = None

    @property
    def display_name(self) -> str:
        if self.product:
            return self.product
        return f"USB Device {self.vendor_id}:{self.product_id}"

    @classmethod
    def from_dict(cls, d: dict) -> "DeviceInfo":
        return cls(
            bus_id=d.get("bus_id", ""),
            vendor_id=d.get("vendor_id", ""),
            product_id=d.get("product_id", ""),
            manufacturer=d.get("manufacturer"),
            product=d.get("product"),
            serial=d.get("serial"),
            device_class=d.get("device_class"),
            speed=d.get("speed"),
            is_shared=d.get("is_shared", False),
            client_ip=d.get("client_ip"),
        )


@dataclass
class ServerStatus:
    running: bool
    version: str
    cpu_percent: float
    memory_used_mb: int
    memory_total_mb: int
    shared_device_count: int
    uptime_seconds: int

    @classmethod
    def from_dict(cls, d: dict) -> "ServerStatus":
        return cls(**d)


class LokiAPIClient:
    def __init__(self, host: str, port: int = 7576, timeout: float = 5.0):
        self.host = host
        self.port = port
        self.base_url = f"http://{host}:{port}"
        self._client = httpx.Client(timeout=timeout)

    def check_health(self) -> bool:
        try:
            r = self._client.get(f"{self.base_url}/health")
            return r.status_code == 200
        except Exception:
            return False

    def get_status(self) -> Optional[ServerStatus]:
        try:
            r = self._client.get(f"{self.base_url}/api/status")
            r.raise_for_status()
            return ServerStatus.from_dict(r.json())
        except Exception:
            return None

    def list_devices(self) -> list[DeviceInfo]:
        try:
            r = self._client.get(f"{self.base_url}/api/devices")
            r.raise_for_status()
            return [DeviceInfo.from_dict(d) for d in r.json()]
        except Exception:
            return []

    def share_device(self, bus_id: str) -> tuple[bool, str]:
        try:
            r = self._client.post(
                f"{self.base_url}/api/devices/share",
                json={"bus_id": bus_id},
            )
            j = r.json()
            return j.get("success", False), j.get("message", "")
        except Exception as e:
            return False, str(e)

    def unshare_device(self, bus_id: str) -> tuple[bool, str]:
        try:
            r = self._client.post(
                f"{self.base_url}/api/devices/unshare",
                json={"bus_id": bus_id},
            )
            j = r.json()
            return j.get("success", False), j.get("message", "")
        except Exception as e:
            return False, str(e)

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
