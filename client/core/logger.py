"""
Loki-Client — Centralized Logging
Writes to ~/Library/Logs/Loki-Client/loki-client.log (macOS)
or ~/.config/loki-printserver/loki-client.log (Linux/Windows).
Log rotates at 2 MB, keeps 3 backups.
"""
import logging
import platform
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_initialized = False


def get_log_path() -> Path:
    if platform.system() == "Darwin":
        d = Path.home() / "Library" / "Logs" / "Loki-Client"
    else:
        d = Path.home() / ".config" / "loki-printserver"
    d.mkdir(parents=True, exist_ok=True)
    return d / "loki-client.log"


def setup_logging() -> logging.Logger:
    global _initialized
    log = logging.getLogger("loki")
    if _initialized:
        return log
    _initialized = True

    log.setLevel(logging.DEBUG)
    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-5s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # File handler (always)
    try:
        fh = RotatingFileHandler(
            get_log_path(), maxBytes=2 * 1024 * 1024, backupCount=3,
            encoding="utf-8",
        )
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        log.addHandler(fh)
    except Exception:
        pass

    # Console handler (only when stdout is real, not in frozen .app)
    if sys.stdout is not None and not getattr(sys, "frozen", False):
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(logging.INFO)
        ch.setFormatter(fmt)
        log.addHandler(ch)

    return log
