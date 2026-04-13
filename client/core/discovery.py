"""
Loki-PrintServer - Client-Side mDNS Discovery
Finds Loki-PrintServer instances on the local network automatically.
"""
import socket
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from zeroconf import ServiceBrowser, ServiceListener, Zeroconf

# RFC 6335: service type labels must be ≤ 15 chars (without underscore)
SERVICE_TYPE = "_lokiprint._tcp.local."


@dataclass
class DiscoveredServer:
    name: str
    host: str
    ip: str
    port: int
    api_port: int
    version: str

    def __eq__(self, other):
        return isinstance(other, DiscoveredServer) and self.ip == other.ip and self.port == other.port

    def __hash__(self):
        return hash((self.ip, self.port))

    def __str__(self):
        return f"{self.name} ({self.ip}:{self.api_port})"


class LokiDiscovery:
    """Discover Loki-PrintServer instances on the local network via mDNS."""

    def __init__(self, on_found: Optional[Callable[[DiscoveredServer], None]] = None,
                 on_lost: Optional[Callable[[DiscoveredServer], None]] = None):
        self._on_found = on_found
        self._on_lost = on_lost
        self._servers: dict[str, DiscoveredServer] = {}
        self._zeroconf: Optional[Zeroconf] = None
        self._browser: Optional[ServiceBrowser] = None
        self._lock = threading.Lock()

    def start(self):
        """Start mDNS discovery."""
        self._zeroconf = Zeroconf()
        listener = _Listener(self)
        self._browser = ServiceBrowser(self._zeroconf, SERVICE_TYPE, listener)

    def stop(self):
        if self._zeroconf:
            self._zeroconf.close()

    def get_servers(self) -> list[DiscoveredServer]:
        with self._lock:
            return list(self._servers.values())

    def _add_server(self, name: str, zeroconf: Zeroconf, info):
        if info is None:
            return
        try:
            ip = socket.inet_ntoa(info.addresses[0]) if info.addresses else None
            if not ip:
                return
            props = {k.decode(): v.decode() for k, v in info.properties.items()} if info.properties else {}
            server = DiscoveredServer(
                name=info.name.replace(f".{SERVICE_TYPE}", "").replace(SERVICE_TYPE, ""),
                host=info.server,
                ip=ip,
                port=info.port,
                api_port=int(props.get("api_port", info.port + 1)),
                version=props.get("version", "?"),
            )
            with self._lock:
                self._servers[name] = server
            if self._on_found:
                self._on_found(server)
        except Exception:
            pass

    def _remove_server(self, name: str):
        with self._lock:
            server = self._servers.pop(name, None)
        if server and self._on_lost:
            self._on_lost(server)


class _Listener(ServiceListener):
    def __init__(self, discovery: "LokiDiscovery"):
        self._d = discovery

    def add_service(self, zeroconf: Zeroconf, type_: str, name: str):
        info = zeroconf.get_service_info(type_, name)
        self._d._add_server(name, zeroconf, info)

    def update_service(self, zeroconf: Zeroconf, type_: str, name: str):
        info = zeroconf.get_service_info(type_, name)
        self._d._add_server(name, zeroconf, info)

    def remove_service(self, zeroconf: Zeroconf, type_: str, name: str):
        self._d._remove_server(name)
