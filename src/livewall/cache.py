"""Cache layer: download images into store/, build active/ snapshots via hard links."""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import sqlite3
from pathlib import Path

from livewall.config import ACTIVE_DIR, ACTIVE_NEXT_DIR, STORE_DIR
from livewall.sources import ImageRef, Source

log = logging.getLogger(__name__)

IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".heic", ".bmp", ".gif", ".tiff"})


# ---------------------------------------------------------------------------
# Store helpers
# ---------------------------------------------------------------------------

def ensure_store() -> None:
    """Create store and active directories if needed, and clean up stale tmp files."""
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    ACTIVE_DIR.mkdir(parents=True, exist_ok=True)
    # Remove leftover partial downloads from interrupted pulls
    for tmp in STORE_DIR.glob("_tmp_*"):
        try:
            tmp.unlink()
            log.debug("Cleaned up stale tmp file: %s", tmp.name)
        except OSError:
            pass


def _hash_file(path: Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with path.open("rb") as fp:
        for chunk in iter(lambda: fp.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def download_image(source: Source, ref: ImageRef) -> tuple[str, str]:
    """Download *ref* from *source* into the store.

    Writes to ``store/{hash}.{ext}.tmp`` then atomically renames to
    ``store/{hash}.{ext}``.

    Returns (sha256_hex, cached_path_str).
    """
    ensure_store()

    suffix = Path(ref.filename).suffix.lower()
    if not suffix or suffix not in IMAGE_EXTENSIONS:
        suffix = ".jpg"

    # Temporary download path (unique via filename to avoid collisions)
    tmp_path = STORE_DIR / f"_tmp_{ref.filename}"
    try:
        source.fetch(ref, tmp_path)

        hash_ = _hash_file(tmp_path)
        final_path = STORE_DIR / f"{hash_}{suffix}"

        if final_path.exists():
            # Content already stored (duplicate from another source)
            tmp_path.unlink()
        else:
            os.rename(str(tmp_path), str(final_path))

        return hash_, str(final_path)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise


# ---------------------------------------------------------------------------
# Active snapshot
# ---------------------------------------------------------------------------

def build_active_snapshot(unique_rows: list[sqlite3.Row]) -> int:
    """Rebuild the active/ snapshot from *unique_rows* using hard links.

    Protocol:
    1. Create active.next/
    2. Hard-link each unique store file into it
    3. Validate
    4. Atomically replace active/

    Returns the number of images in the new snapshot.
    """
    ensure_store()

    # Clean up any leftover active.next from a previous failed apply
    if ACTIVE_NEXT_DIR.exists():
        shutil.rmtree(ACTIVE_NEXT_DIR)
    ACTIVE_NEXT_DIR.mkdir(parents=True, exist_ok=True)

    count = 0
    for row in unique_rows:
        src = Path(row["cached_path"])
        if not src.exists():
            log.warning("Store file missing, skipping: %s", src)
            continue
        dest = ACTIVE_NEXT_DIR / src.name
        # os.link is cheaper than shutil.copy2; works because store and active
        # live under the same ~/Library top-level directory
        try:
            os.link(str(src), str(dest))
        except OSError:
            # Cross-device link or other issue: fall back to copy
            shutil.copy2(src, dest)
        count += 1

    # Atomically replace active/
    if ACTIVE_DIR.exists():
        old_dir = ACTIVE_DIR.with_name("active.old")
        if old_dir.exists():
            shutil.rmtree(old_dir)
        os.rename(str(ACTIVE_DIR), str(old_dir))

    os.rename(str(ACTIVE_NEXT_DIR), str(ACTIVE_DIR))

    # Remove the old snapshot now that the swap is complete
    old_dir = ACTIVE_DIR.with_name("active.old")
    if old_dir.exists():
        shutil.rmtree(old_dir)

    return count


def purge_all() -> None:
    """Delete store/ and active/ directories entirely (used by reset --purge)."""
    for d in (ACTIVE_DIR, ACTIVE_NEXT_DIR, STORE_DIR):
        if d.exists():
            shutil.rmtree(d)
    log.info("Purged store and active directories")
