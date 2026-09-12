"""
Loki-PrintServer - mDNS/Bonjour Discovery
Announces the server on the local network so clients can find it automatically.
"""
import logging
import socket

from zeroconf import ServiceInfo
from zeroconf.asyncio import AsyncZeroconf

logger = logging.getLogger("loki-printserver.discovery")

# RFC 6335: service type labels must be ≤ 15 chars (without underscore)
SERVICE_TYPE = "_lokiprint._tcp.local."


class MDNSAnnouncer:
    def __init__(self):
        self._zeroconf: AsyncZeroconf | None = None
        self._service_info: ServiceInfo | None = None

    async def start(self, service_name: str, port: int):
        """Register the service via mDNS so clients can auto-discover it."""
        try:
            self._zeroconf = AsyncZeroconf()
            hostname = socket.gethostname()
            local_ip = self._get_local_ip()

            self._service_info = ServiceInfo(
                SERVICE_TYPE,
                f"{service_name}.{SERVICE_TYPE}",
                addresses=[socket.inet_aton(local_ip)],
                port=port,
                properties={
                    "version": "1.0.0",
                    "host": hostname,
                    "api_port": str(port + 1),
                },
                server=f"{hostname}.local.",
            )

            await self._zeroconf.async_register_service(self._service_info)
            logger.info(f"mDNS: Announced '{service_name}' on {local_ip}:{port}")
        except Exception as e:
            logger.warning(f"mDNS announcement failed: {e}")

    async def stop(self):
        if self._zeroconf:
            if self._service_info:
                await self._zeroconf.async_unregister_service(self._service_info)
            await self._zeroconf.async_close()

    def _get_local_ip(self) -> str:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                return s.getsockname()[0]
        except Exception:
            return "127.0.0.1"
