"""
Loki-PrintServer - Client Configuration
Persists server list and settings to ~/.config/loki-printserver/config.json
"""
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


CONFIG_DIR = Path.home() / ".config" / "loki-printserver"
CONFIG_FILE = CONFIG_DIR / "config.json"


@dataclass
class ServerEntry:
    host: str
    port: int = 7576
    name: str = ""
    auto_connect: bool = True
    color: str = "#00bfa5"

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
    # VID:PID keys the user attached by clicking. Auto-attach only these.
    auto_attach: list[str] = field(default_factory=list)

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
            "auto_attach": list(self.auto_attach),
        }
        CONFIG_FILE.write_text(json.dumps(data, indent=2))

    @staticmethod
    def device_key(vendor_id: str | None, product_id: str | None) -> str:
        return f"{(vendor_id or '').lower()}:{(product_id or '').lower()}"

    def wants_auto_attach(self, vendor_id: str | None, product_id: str | None) -> bool:
        key = self.device_key(vendor_id, product_id)
        return bool(key != ":") and key in self.auto_attach

    def remember_attach(self, vendor_id: str | None, product_id: str | None) -> None:
        key = self.device_key(vendor_id, product_id)
        if not key or key == ":" or key in self.auto_attach:
            return
        self.auto_attach.append(key)
        self.save()

    def forget_attach(self, vendor_id: str | None, product_id: str | None) -> None:
        key = self.device_key(vendor_id, product_id)
        if key not in self.auto_attach:
            return
        self.auto_attach = [k for k in self.auto_attach if k != key]
        self.save()

    @classmethod
    def load(cls) -> "LokiConfig":
        if not CONFIG_FILE.exists():
            return cls()
        try:
            data = json.loads(CONFIG_FILE.read_text())
            servers = [ServerEntry(**s) for s in data.get("servers", [])]
            raw_keys = data.get("auto_attach") or []
            auto_attach = [str(k).lower() for k in raw_keys if k]
            return cls(
                servers=servers,
                first_launch=data.get("first_launch", True),
                start_at_login=data.get("start_at_login", False),
                show_notifications=data.get("show_notifications", True),
                auto_attach=auto_attach,
            )
        except Exception:
            return cls()
