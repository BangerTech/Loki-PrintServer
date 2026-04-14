"""
Loki-PrintServer - API Client
Communicates with the server REST API.
Uses only stdlib (urllib) to avoid httpx/anyio issues in PyInstaller bundles.
"""
import json
import logging
import urllib.request
import urllib.error
from typing import Optional
from dataclasses import dataclass

log = logging.getLogger("loki.api")


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
    is_infrastructure: bool = False
    client_ip: Optional[str] = None
    forward_info: Optional[dict] = None
    custom_name: Optional[str] = None

    @property
    def display_name(self) -> str:
        return self.custom_name or self.product or f"USB Device {self.vendor_id}:{self.product_id}"

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
            is_infrastructure=d.get("is_infrastructure", False),
            client_ip=d.get("client_ip"),
            forward_info=d.get("forward_info"),
            custom_name=d.get("custom_name"),
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
        self.timeout = timeout

    def _get(self, path: str) -> dict | list | None:
        url = f"{self.base_url}{path}"
        log.debug("GET %s", url)
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read())
            log.debug("GET %s → %s", path, resp.status)
            return data

    def _post_json(self, path: str, data: dict) -> dict:
        url = f"{self.base_url}{path}"
        body = json.dumps(data).encode()
        log.debug("POST %s  body=%s", url, data)
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            result = json.loads(resp.read())
            log.debug("POST %s → %s", path, resp.status)
            return result

    def check_health(self) -> bool:
        try:
            result = self._get("/health")
            log.info("Health OK: %s:%s", self.host, self.port)
            return result is not None
        except Exception as e:
            log.warning("Health FAIL %s:%s → %s", self.host, self.port, e)
            return False

    def get_status(self) -> Optional[ServerStatus]:
        try:
            data = self._get("/api/status")
            return ServerStatus.from_dict(data)
        except Exception as e:
            log.warning("get_status failed: %s", e)
            return None

    def list_devices(self) -> list[DeviceInfo]:
        try:
            data = self._get("/api/devices")
            devs = [DeviceInfo.from_dict(d) for d in data]
            log.info("Loaded %d devices from %s", len(devs), self.host)
            return devs
        except Exception as e:
            log.warning("list_devices failed: %s", e)
            return []

    def share_device(self, bus_id: str) -> tuple[bool, str]:
        try:
            j = self._post_json("/api/devices/share", {"bus_id": bus_id})
            log.info("Share %s → %s", bus_id, j.get("success"))
            return j.get("success", False), j.get("message", "")
        except Exception as e:
            log.error("share_device %s failed: %s", bus_id, e)
            return False, str(e)

    def get_forward_info(self, bus_id: str) -> Optional[dict]:
        try:
            return self._get(f"/api/devices/{bus_id}/forward")
        except Exception as e:
            log.debug("get_forward_info %s: %s", bus_id, e)
            return None

    def unshare_device(self, bus_id: str) -> tuple[bool, str]:
        try:
            j = self._post_json("/api/devices/unshare", {"bus_id": bus_id})
            log.info("Unshare %s → %s", bus_id, j.get("success"))
            return j.get("success", False), j.get("message", "")
        except Exception as e:
            log.error("unshare_device %s failed: %s", bus_id, e)
            return False, str(e)

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass
