"""
Loki-PrintServer - Client Configuration
Persists server list and settings to ~/.config/loki-printserver/config.json
"""
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


CONFIG_DIR = Path.home() / ".config" / "loki-printserver"
CONFIG_FILE = CONFIG_DIR / "config.json"


@dataclass
class ServerEntry:
    host: str
    port: int = 7576
    name: str = ""
    auto_connect: bool = True
    color: str = "#7c5cbf"  # accent color per server

    def __post_init__(self):
        if not self.name:
            self.name = self.host

    @property
    def api_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def __eq__(self, other):
        return isinstance(other, ServerEntry) and self.host == other.host and self.port == other.port

    def __hash__(self):
        return hash((self.host, self.port))


@dataclass
class LokiConfig:
    servers: list[ServerEntry] = field(default_factory=list)
    first_launch: bool = True
    start_at_login: bool = False
    show_notifications: bool = True

    def add_server(self, host: str, port: int = 7576, name: str = "") -> ServerEntry:
        entry = ServerEntry(host=host, port=port, name=name or host)
        if entry not in self.servers:
            self.servers.append(entry)
            self.save()
        return entry

    def remove_server(self, host: str, port: int = 7576):
        self.servers = [s for s in self.servers if not (s.host == host and s.port == port)]
        self.save()

    def save(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            "servers": [asdict(s) for s in self.servers],
            "first_launch": self.first_launch,
            "start_at_login": self.start_at_login,
            "show_notifications": self.show_notifications,
        }
        CONFIG_FILE.write_text(json.dumps(data, indent=2))

    @classmethod
    def load(cls) -> "LokiConfig":
        if not CONFIG_FILE.exists():
            return cls()
        try:
            data = json.loads(CONFIG_FILE.read_text())
            servers = [ServerEntry(**s) for s in data.get("servers", [])]
            return cls(
                servers=servers,
                first_launch=data.get("first_launch", True),
                start_at_login=data.get("start_at_login", False),
                show_notifications=data.get("show_notifications", True),
            )
        except Exception:
            return cls()
