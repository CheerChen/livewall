#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["rich"]
# ///
"""
Wallpaper link manager: sync images into a single symlink folder,
apply it as macOS auto-rotating wallpaper, or reset to default.

Usage:
    uv run update_wallpaper_links.py              # sync links only
    uv run update_wallpaper_links.py --apply      # sync links + set as wallpaper with rotation
    uv run update_wallpaper_links.py --reset      # restore macOS default wallpaper
    uv run update_wallpaper_links.py --interval 5m  # set rotation interval (with --apply)
"""

import argparse
import datetime
import logging
import plistlib
import shutil
import signal
import subprocess
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SOURCE_BASE = Path("/Users/cheerchen/Public/Wallpaper")
TARGET_DIR = Path.home() / "Pictures" / "AllWallpapers"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}
FOLDER_RANGE = range(1, 51)

WALLPAPER_PLIST = (
    Path.home()
    / "Library"
    / "Application Support"
    / "com.apple.wallpaper"
    / "Store"
    / "Index.plist"
)
DEFAULT_WALLPAPER = Path("/System/Library/Desktop Pictures/Sonoma.heic")

# Shuffle frequency identifiers used by macOS wallpaper agent
INTERVAL_MAP: dict[str, str] = {
    "1m": "shuffle_every_1_minute",
    "5m": "shuffle_every_5_minutes",
    "15m": "shuffle_every_15_minutes",
    "30m": "shuffle_every_30_minutes",
    "1h": "shuffle_every_1_hour",
    "12h": "shuffle_every_12_hours",
    "1d": "shuffle_every_day",
    "login": "shuffle_on_login",
    "wake": "shuffle_on_wake",
}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

console = Console()

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[RichHandler(console=Console(stderr=True), rich_tracebacks=True)],
)
log = logging.getLogger("wallpaper")

# ---------------------------------------------------------------------------
# Link sync
# ---------------------------------------------------------------------------


def is_image(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_EXTENSIONS


def collect_images(source_base: Path) -> dict[str, Path]:
    """Walk numbered subfolders and return {filename: source_path} for all images."""
    images: dict[str, Path] = {}
    for i in FOLDER_RANGE:
        folder = source_base / f"{i:02d}"
        if not folder.is_dir():
            continue
        for entry in folder.rglob("*"):
            if entry.is_file() and is_image(entry):
                name = entry.name
                if name in images:
                    log.warning("Duplicate filename %r, keeping first occurrence", name)
                else:
                    images[name] = entry
    return images


def sync_links(images: dict[str, Path], target_dir: Path) -> tuple[int, int, int]:
    """Create missing symlinks and remove stale ones. Returns (created, removed, kept)."""
    target_dir.mkdir(parents=True, exist_ok=True)

    created = 0
    kept = 0

    for name, source in images.items():
        link = target_dir / name
        if link.is_symlink():
            if link.resolve() == source.resolve():
                kept += 1
                continue
            link.unlink()
        elif link.exists():
            log.warning("Non-symlink file exists at %s, skipping", link)
            continue
        link.symlink_to(source)
        created += 1

    removed = 0
    for entry in target_dir.iterdir():
        if not entry.is_symlink():
            continue
        if entry.name not in images:
            log.info("Removing stale link: %s", entry.name)
            entry.unlink()
            removed += 1
        elif not entry.exists():
            log.info("Removing broken link: %s", entry.name)
            entry.unlink()
            removed += 1

    return created, removed, kept


# ---------------------------------------------------------------------------
# macOS wallpaper plist manipulation
# ---------------------------------------------------------------------------


def _build_folder_config(folder_url: str) -> bytes:
    """Build the bplist Configuration blob for an imageFolder choice."""
    return plistlib.dumps(
        {"type": "imageFolder", "url": {"relative": folder_url}},
        fmt=plistlib.FMT_BINARY,
    )


def _build_file_config(file_url: str) -> bytes:
    """Build the bplist Configuration blob for an imageFile choice."""
    return plistlib.dumps(
        {"type": "imageFile", "url": {"relative": file_url}},
        fmt=plistlib.FMT_BINARY,
    )


def _build_shuffle_options(shuffle_id: str) -> bytes:
    """Build the bplist EncodedOptionValues blob with shuffle frequency."""
    return plistlib.dumps(
        {
            "values": {
                "shuffleFrequency": {"picker": {"_0": {"id": shuffle_id}}},
                "aerialShuffleFrequency": {
                    "picker": {"_0": {"id": "shuffle_every_12_hours"}}
                },
            }
        },
        fmt=plistlib.FMT_BINARY,
    )


def _make_desktop_entry(
    config_blob: bytes,
    provider: str,
    options_blob: bytes | str = "$null",
    shuffle: str = "$null",
) -> dict:
    """Build a Desktop/Idle content dict for the plist."""
    now = datetime.datetime.now(tz=datetime.timezone.utc).replace(tzinfo=None)
    return {
        "Content": {
            "Choices": [
                {
                    "Configuration": config_blob,
                    "Files": [],
                    "Provider": provider,
                }
            ],
            "EncodedOptionValues": options_blob,
            "Shuffle": shuffle,
        },
        "LastSet": now,
        "LastUse": now,
    }


def _restart_wallpaper_agent() -> None:
    """Kill the wallpaper agent so it restarts and picks up the new plist."""
    result = subprocess.run(
        ["launchctl", "list"],
        capture_output=True,
        text=True,
    )
    for line in result.stdout.splitlines():
        if "com.apple.wallpaper.agent" in line:
            parts = line.split()
            pid = parts[0]
            if pid.isdigit():
                log.info("Restarting wallpaper agent (PID %s)", pid)
                subprocess.run(["kill", pid])
                return
    log.warning("Wallpaper agent PID not found, changes may require logout")


def apply_wallpaper(folder: Path, shuffle_id: str) -> None:
    """Write the wallpaper plist to use a folder with auto-rotation."""
    folder_url = folder.as_uri() + "/"
    config_blob = _build_folder_config(
        "file:///" + str(folder).replace(" ", "%20") + "/"
    )
    options_blob = _build_shuffle_options(shuffle_id)

    desktop = _make_desktop_entry(
        config_blob, "com.apple.wallpaper.choice.image", options_blob
    )

    # Read existing plist to preserve Idle (screensaver) and display IDs
    if WALLPAPER_PLIST.exists():
        with open(WALLPAPER_PLIST, "rb") as f:
            plist = plistlib.load(f)
    else:
        plist = {}

    idle = plist.get("AllSpacesAndDisplays", {}).get("Idle", _default_idle())

    # Apply to AllSpacesAndDisplays
    plist["AllSpacesAndDisplays"] = {
        "Desktop": desktop,
        "Idle": idle,
        "Type": "individual",
    }

    # Apply to SystemDefault
    plist["SystemDefault"] = {
        "Desktop": desktop,
        "Idle": idle,
        "Type": "individual",
    }

    # Apply to each known display
    displays = plist.get("Displays", {})
    for display_id in displays:
        idle_d = displays[display_id].get("Idle", idle)
        displays[display_id] = {
            "Desktop": desktop,
            "Idle": idle_d,
            "Type": "individual",
        }
    plist["Displays"] = displays

    # Apply to each known space
    spaces = plist.get("Spaces", {})
    for space_id in spaces:
        space = spaces[space_id]
        if "Default" in space:
            idle_s = space["Default"].get("Idle", idle)
            space["Default"] = {
                "Desktop": desktop,
                "Idle": idle_s,
                "Type": "individual",
            }
        if "Displays" in space:
            for disp_id in space["Displays"]:
                idle_sd = space["Displays"][disp_id].get("Idle", idle)
                space["Displays"][disp_id] = {
                    "Desktop": desktop,
                    "Idle": idle_sd,
                    "Type": "individual",
                }
    plist["Spaces"] = spaces

    # Backup and write
    if WALLPAPER_PLIST.exists():
        backup = WALLPAPER_PLIST.with_suffix(".plist.bak")
        shutil.copy2(WALLPAPER_PLIST, backup)
        log.info("Backed up plist to %s", backup.name)

    with open(WALLPAPER_PLIST, "wb") as f:
        plistlib.dump(plist, f, fmt=plistlib.FMT_BINARY)

    _restart_wallpaper_agent()
    log.info(
        "Applied wallpaper: folder=%s, shuffle=%s", folder, shuffle_id
    )


def reset_wallpaper() -> None:
    """Reset all desktops to the default macOS wallpaper by rewriting the plist."""
    if not DEFAULT_WALLPAPER.exists():
        log.error("Default wallpaper not found: %s", DEFAULT_WALLPAPER)
        raise SystemExit(1)

    file_url = "file:///" + str(DEFAULT_WALLPAPER).replace(" ", "%20")
    config_blob = _build_file_config(file_url)
    desktop = _make_desktop_entry(
        config_blob, "com.apple.wallpaper.choice.image"
    )
    idle = _default_idle()

    if WALLPAPER_PLIST.exists():
        with open(WALLPAPER_PLIST, "rb") as f:
            plist = plistlib.load(f)
    else:
        plist = {}

    # Reset AllSpacesAndDisplays
    plist["AllSpacesAndDisplays"] = {
        "Desktop": desktop,
        "Idle": idle,
        "Type": "individual",
    }
    plist["SystemDefault"] = {
        "Desktop": desktop,
        "Idle": idle,
        "Type": "individual",
    }

    # Reset all displays
    displays = plist.get("Displays", {})
    for display_id in displays:
        displays[display_id] = {
            "Desktop": desktop,
            "Idle": idle,
            "Type": "individual",
        }
    plist["Displays"] = displays

    # Reset all spaces
    spaces = plist.get("Spaces", {})
    for space_id in spaces:
        space = spaces[space_id]
        if "Default" in space:
            space["Default"] = {
                "Desktop": desktop,
                "Idle": idle,
                "Type": "individual",
            }
        if "Displays" in space:
            for disp_id in space["Displays"]:
                space["Displays"][disp_id] = {
                    "Desktop": desktop,
                    "Idle": idle,
                    "Type": "individual",
                }
    plist["Spaces"] = spaces

    with open(WALLPAPER_PLIST, "wb") as f:
        plistlib.dump(plist, f, fmt=plistlib.FMT_BINARY)

    _restart_wallpaper_agent()
    log.info("Reset wallpaper to %s", DEFAULT_WALLPAPER.name)


def _default_idle() -> dict:
    """Fallback Idle (screensaver) config."""
    now = datetime.datetime.now(tz=datetime.timezone.utc).replace(tzinfo=None)
    return {
        "Content": {
            "Choices": [
                {
                    "Configuration": plistlib.dumps(
                        {"assetID": "C3C48B18-E4AE-4A62-877D-0B0D74CDC9E0"},
                        fmt=plistlib.FMT_BINARY,
                    ),
                    "Files": [],
                    "Provider": "com.apple.wallpaper.choice.aerials",
                }
            ],
            "EncodedOptionValues": plistlib.dumps(
                {
                    "values": {
                        "aerialShuffleFrequency": {
                            "picker": {"_0": {"id": "shuffle_every_12_hours"}}
                        }
                    }
                },
                fmt=plistlib.FMT_BINARY,
            ),
            "Shuffle": "$null",
        },
        "LastSet": now,
        "LastUse": now,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Wallpaper link manager with macOS rotation support.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--apply",
        action="store_true",
        help="Sync links and set the folder as auto-rotating wallpaper",
    )
    group.add_argument(
        "--reset",
        action="store_true",
        help="Reset wallpaper to macOS default (Sonoma.heic)",
    )
    parser.add_argument(
        "--interval",
        default="1m",
        choices=list(INTERVAL_MAP.keys()),
        help="Rotation interval (default: 1m). Only used with --apply",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.reset:
        reset_wallpaper()
        console.print("[bold green]Wallpaper reset to default.[/bold green]")
        return

    # Sync links
    if not SOURCE_BASE.is_dir():
        log.error("Source directory does not exist: %s", SOURCE_BASE)
        raise SystemExit(1)

    log.info("Scanning subfolders in %s", SOURCE_BASE)
    images = collect_images(SOURCE_BASE)

    if not images:
        log.error("No images found - aborting to avoid deleting all links")
        raise SystemExit(1)

    log.info("Found %d images across numbered folders", len(images))
    created, removed, kept = sync_links(images, TARGET_DIR)

    # Summary table
    table = Table(title="Wallpaper Links Summary")
    table.add_column("Metric", style="bold")
    table.add_column("Count", justify="right")
    table.add_row("Images found", str(len(images)))
    table.add_row("[green]Links created[/green]", str(created))
    table.add_row("[yellow]Links unchanged[/yellow]", str(kept))
    table.add_row("[red]Stale links removed[/red]", str(removed))
    table.add_row("Total links now", str(created + kept))
    console.print(table)

    if args.apply:
        shuffle_id = INTERVAL_MAP[args.interval]
        apply_wallpaper(TARGET_DIR, shuffle_id)
        console.print(
            f"\n[bold green]Wallpaper set to rotate every {args.interval} "
            f"from {TARGET_DIR}[/bold green]"
        )
    else:
        console.print(
            f"\n[bold]Target directory:[/bold] {TARGET_DIR}\n"
            "Run with [cyan]--apply[/cyan] to set as wallpaper, "
            "or [cyan]--reset[/cyan] to restore default."
        )


if __name__ == "__main__":
    main()
