"""Read the project version from the VERSION file."""
import os
from pathlib import Path


def read_version() -> str:
    env = os.getenv("LOKI_VERSION", "").strip()
    if env:
        return env

    here = Path(__file__).resolve()
    candidates = [
        Path(os.getenv("LOKI_VERSION_FILE", "")),
        Path("/app/VERSION"),
        here.parents[2] / "VERSION",  # repo root
        here.parents[1] / "VERSION",  # server/VERSION
    ]
    for path in candidates:
        if not path:
            continue
        try:
            if path.is_file():
                value = path.read_text(encoding="utf-8").strip()
                if value:
                    return value
        except OSError:
            continue
    return "0.0.0"


APP_VERSION = read_version()
