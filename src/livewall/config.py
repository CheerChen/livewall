"""Configuration management: paths, config.toml read/write, and logging setup."""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Standard macOS paths
# ---------------------------------------------------------------------------

CONFIG_DIR = Path.home() / ".config" / "livewall"
DATA_DIR = Path.home() / "Library" / "Application Support" / "livewall"
LOG_DIR = Path.home() / "Library" / "Logs" / "livewall"

CONFIG_FILE = CONFIG_DIR / "config.toml"
INDEX_FILE = DATA_DIR / "index.db"
STORE_DIR = DATA_DIR / "store"
ACTIVE_DIR = DATA_DIR / "active"
ACTIVE_NEXT_DIR = DATA_DIR / "active.next"
ORIGINAL_PLIST = DATA_DIR / "original.plist"

LOG_FILE = LOG_DIR / "livewall.log"

# macOS wallpaper plist location
WALLPAPER_PLIST = (
    Path.home()
    / "Library"
    / "Application Support"
    / "com.apple.wallpaper"
    / "Store"
    / "Index.plist"
)

DEFAULT_WALLPAPER = Path("/System/Library/Desktop Pictures/Sonoma.heic")

# Rotation interval identifiers recognised by macOS wallpaper agent
INTERVAL_MAP: dict[str, str] = {
    "1m":    "shuffle_every_1_minute",
    "5m":    "shuffle_every_5_minutes",
    "15m":   "shuffle_every_15_minutes",
    "30m":   "shuffle_every_30_minutes",
    "1h":    "shuffle_every_1_hour",
    "12h":   "shuffle_every_12_hours",
    "1d":    "shuffle_every_day",
    "login": "shuffle_on_login",
    "wake":  "shuffle_on_wake",
}

DEFAULT_INTERVAL = "5m"

# ---------------------------------------------------------------------------
# Config.toml template
# ---------------------------------------------------------------------------

CONFIG_TEMPLATE = """\
[settings]
interval = "5m"

# Add FTP sources below, e.g.:
#
# [[sources]]
# name = "nas"
# type = "ftp"
# host = "192.168.1.100"
# path = "/wallpaper"
# username = "user"
# password = "secret"
"""

# ---------------------------------------------------------------------------
# TOML helpers (support Python 3.10 and 3.11+)
# ---------------------------------------------------------------------------

try:
    import tomllib  # type: ignore[import]
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]

import tomli_w


def load_config() -> dict[str, Any]:
    """Load config.toml.  Returns empty dict if the file does not exist yet."""
    if not CONFIG_FILE.exists():
        return {}
    with CONFIG_FILE.open("rb") as fp:
        return tomllib.load(fp)


def save_config(data: dict[str, Any]) -> None:
    """Write *data* to config.toml (creates parent directory if needed)."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with CONFIG_FILE.open("wb") as fp:
        tomli_w.dump(data, fp)


def init_config() -> None:
    """Write the default config.toml template if it does not already exist."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_FILE.exists():
        CONFIG_FILE.write_text(CONFIG_TEMPLATE, encoding="utf-8")


# ---------------------------------------------------------------------------
# Source helpers
# ---------------------------------------------------------------------------

def get_sources(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the [[sources]] list from *config*, or an empty list."""
    return config.get("sources", [])


def find_source(config: dict[str, Any], name: str) -> dict[str, Any] | None:
    for src in get_sources(config):
        if src.get("name") == name:
            return src
    return None


def add_source(config: dict[str, Any], source: dict[str, Any]) -> None:
    """Add *source* to the sources list in *config* (mutates in place)."""
    sources = config.setdefault("sources", [])
    sources.append(source)


def remove_source(config: dict[str, Any], name: str) -> bool:
    """Remove the source named *name*.  Returns True if it was found."""
    sources = config.get("sources", [])
    new_sources = [s for s in sources if s.get("name") != name]
    if len(new_sources) == len(sources):
        return False
    config["sources"] = new_sources
    return True


def get_interval(config: dict[str, Any]) -> str:
    """Return the configured rotation interval string (e.g. '5m')."""
    return config.get("settings", {}).get("interval", DEFAULT_INTERVAL)


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def setup_logging(verbose: bool = False) -> None:
    """Configure root logger: rich stderr handler + rotating file handler."""
    from rich.logging import RichHandler

    level = logging.DEBUG if verbose else logging.INFO
    root = logging.getLogger()

    if root.handlers:
        # Already initialised — only escalate to DEBUG console output, never downgrade
        if verbose:
            for h in root.handlers:
                if isinstance(h, RichHandler):
                    h.setLevel(logging.DEBUG)
        return

    root.setLevel(logging.DEBUG)

    # Rich stderr handler for interactive use
    # Non-verbose: only warnings/errors (user-facing output uses console.print)
    # Verbose: show all log levels on the console
    rich_handler = RichHandler(
        rich_tracebacks=True,
        show_path=False,
        markup=True,
    )
    rich_handler.setLevel(logging.DEBUG if verbose else logging.WARNING)
    root.addHandler(rich_handler)

    # Rotating file handler for post-hoc debugging
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        LOG_FILE,
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root.addHandler(file_handler)
