"""macOS wallpaper control via plist manipulation.

Architecture
-----------
Pure functions (testable, no side effects):
    build_folder_plist()  — returns a modified plist dict
    build_reset_plist()   — returns the original/fallback plist dict

Thin side-effect layer:
    write_plist()            — write binary plist to disk
    restart_wallpaper_agent() — find PID via launchctl, send SIGTERM
    backup_plist()           — copy plist on first apply

The AppleScript approach does NOT work for folder rotation on modern macOS —
plist manipulation is the only reliable method.
"""

from __future__ import annotations

import datetime
import logging
import plistlib
import shutil
import subprocess
import uuid
from pathlib import Path

from livewall.config import DEFAULT_WALLPAPER, ORIGINAL_PLIST, WALLPAPER_PLIST

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal plist blob builders
# ---------------------------------------------------------------------------

def _build_folder_config(folder: Path) -> bytes:
    """Return binary plist blob for an imageFolder choice."""
    url = "file://" + str(folder).replace(" ", "%20") + "/"
    return plistlib.dumps(
        {"type": "imageFolder", "url": {"relative": url}},
        fmt=plistlib.FMT_BINARY,
    )


def _build_file_config(file_path: Path) -> bytes:
    """Return binary plist blob for a single imageFile choice."""
    url = "file://" + str(file_path).replace(" ", "%20")
    return plistlib.dumps(
        {"type": "imageFile", "url": {"relative": url}},
        fmt=plistlib.FMT_BINARY,
    )


def _build_shuffle_options(shuffle_id: str) -> bytes:
    """Return binary plist blob with shuffle frequency."""
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


def _default_idle() -> dict:
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


def _apply_desktop_to_plist(plist: dict, desktop: dict) -> dict:
    """Return a new plist with *desktop* applied to all sections."""
    plist = dict(plist)  # shallow copy

    idle = plist.get("AllSpacesAndDisplays", {}).get("Idle", _default_idle())

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

    displays = dict(plist.get("Displays", {}))
    for display_id in displays:
        idle_d = displays[display_id].get("Idle", idle)
        displays[display_id] = {
            "Desktop": desktop,
            "Idle": idle_d,
            "Type": "individual",
        }
    plist["Displays"] = displays

    spaces = dict(plist.get("Spaces", {}))
    for space_id in spaces:
        space = dict(spaces[space_id])
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
        spaces[space_id] = space
    plist["Spaces"] = spaces

    return plist


# ---------------------------------------------------------------------------
# Pure functions (no side effects)
# ---------------------------------------------------------------------------

def build_folder_plist(folder: Path, shuffle_id: str, existing: dict) -> dict:
    """Return a modified *existing* plist that uses *folder* for all displays/spaces."""
    config_blob = _build_folder_config(folder)
    options_blob = _build_shuffle_options(shuffle_id)
    desktop = _make_desktop_entry(
        config_blob,
        "com.apple.wallpaper.choice.image",
        options_blob,
    )
    return _apply_desktop_to_plist(existing, desktop)


def build_reset_plist(backup: dict) -> dict:
    """Return the original plist to restore (i.e. the backup unchanged)."""
    return backup


def build_default_plist(existing: dict) -> dict:
    """Build a plist pointing to the system default wallpaper (fallback for reset)."""
    config_blob = _build_file_config(DEFAULT_WALLPAPER)
    desktop = _make_desktop_entry(config_blob, "com.apple.wallpaper.choice.image")
    return _apply_desktop_to_plist(existing, desktop)


# ---------------------------------------------------------------------------
# Side-effect layer
# ---------------------------------------------------------------------------

def read_plist(path: Path) -> dict:
    """Read a binary plist file and return the dict, or {} if missing."""
    if not path.exists():
        return {}
    with path.open("rb") as fp:
        return plistlib.load(fp)


def write_plist(data: dict, path: Path) -> None:
    """Write *data* as a binary plist to *path*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fp:
        plistlib.dump(data, fp, fmt=plistlib.FMT_BINARY)


def backup_plist(src: Path, dest: Path) -> None:
    """Copy *src* plist to *dest* (first-time backup only; caller must check existence)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)


def restart_wallpaper_agent() -> None:
    """Find wallpaper agent PID via launchctl and send SIGTERM so it restarts."""
    result = subprocess.run(
        ["launchctl", "list"],
        capture_output=True,
        text=True,
    )
    for line in result.stdout.splitlines():
        if "com.apple.wallpaper.agent" in line:
            parts = line.split()
            pid_str = parts[0]
            if pid_str.isdigit():
                log.info("Restarting wallpaper agent (PID %s)", pid_str)
                subprocess.run(["kill", pid_str], check=False)
                return
    log.warning("Wallpaper agent PID not found; changes may require logout to take effect")


# ---------------------------------------------------------------------------
# High-level apply / reset
# ---------------------------------------------------------------------------

def apply_wallpaper(active_dir: Path, shuffle_id: str) -> None:
    """Full apply flow: backup plist (first time), write new plist, restart agent."""
    existing = read_plist(WALLPAPER_PLIST)

    # First-time backup
    if not ORIGINAL_PLIST.exists() and WALLPAPER_PLIST.exists():
        backup_plist(WALLPAPER_PLIST, ORIGINAL_PLIST)
        log.info("Backed up original plist (first time)")

    new_plist = build_folder_plist(active_dir, shuffle_id, existing)
    write_plist(new_plist, WALLPAPER_PLIST)
    restart_wallpaper_agent()
    log.info("Wallpaper applied: folder=%s interval=%s", active_dir, shuffle_id)


def reset_wallpaper() -> None:
    """Restore original plist backup, or fall back to system default wallpaper."""
    if ORIGINAL_PLIST.exists():
        backup_data = read_plist(ORIGINAL_PLIST)
        restored = build_reset_plist(backup_data)
        write_plist(restored, WALLPAPER_PLIST)
        log.info("Restored wallpaper from original.plist backup")
    else:
        existing = read_plist(WALLPAPER_PLIST)
        fallback = build_default_plist(existing)
        write_plist(fallback, WALLPAPER_PLIST)
        log.info("No backup found; reset to system default wallpaper")
    restart_wallpaper_agent()


def get_current_wallpaper_folder() -> Path | None:
    """Read the active folder from the current plist (best-effort)."""
    from urllib.parse import unquote
    plist = read_plist(WALLPAPER_PLIST)
    try:
        choices = plist["AllSpacesAndDisplays"]["Desktop"]["Content"]["Choices"]
        cfg_blob = choices[0]["Configuration"]
        cfg = plistlib.loads(cfg_blob)
        rel_url: str = cfg["url"]["relative"]
        # Strip file:// prefix and trailing slash, decode %20 etc.
        path_str = unquote(rel_url.removeprefix("file://").rstrip("/"))
        return Path(path_str)
    except (KeyError, IndexError, ValueError):
        return None
