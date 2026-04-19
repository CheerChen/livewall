"""SQLite index operations for livewall."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator

from livewall.config import INDEX_FILE
from livewall.sources import ImageRef

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS images (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    remote_path TEXT NOT NULL,
    filename TEXT NOT NULL,
    size INTEGER,
    mtime REAL,
    hash TEXT,
    cached_path TEXT,
    last_seen TIMESTAMP NOT NULL,
    UNIQUE(source, remote_path)
);

CREATE TABLE IF NOT EXISTS sync_runs (
    id INTEGER PRIMARY KEY,
    source TEXT,
    status TEXT NOT NULL,
    started_at TIMESTAMP NOT NULL,
    finished_at TIMESTAMP,
    indexed_count INTEGER NOT NULL DEFAULT 0,
    cached_count INTEGER NOT NULL DEFAULT 0,
    downloading_count INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
"""

# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


@contextmanager
def _connect() -> Generator[sqlite3.Connection, None, None]:
    INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(INDEX_FILE))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create tables if they don't exist yet."""
    with _connect() as conn:
        conn.executescript(_SCHEMA)


# ---------------------------------------------------------------------------
# Image index operations
# ---------------------------------------------------------------------------

def upsert_images(source_name: str, refs: list[ImageRef], seen_at: str) -> tuple[int, int]:
    """Insert or update image metadata rows.

    Returns (new_count, unchanged_count).
    """
    new_count = 0
    unchanged_count = 0
    with _connect() as conn:
        for ref in refs:
            existing = conn.execute(
                "SELECT id, size, mtime FROM images WHERE source=? AND remote_path=?",
                (source_name, ref.remote_path),
            ).fetchone()
            if existing is None:
                conn.execute(
                    """INSERT INTO images (source, remote_path, filename, size, mtime, last_seen)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (source_name, ref.remote_path, ref.filename, ref.size, ref.mtime, seen_at),
                )
                new_count += 1
            else:
                conn.execute(
                    """UPDATE images
                       SET filename=?, size=?, mtime=?, last_seen=?
                       WHERE source=? AND remote_path=?""",
                    (ref.filename, ref.size, ref.mtime, seen_at,
                     source_name, ref.remote_path),
                )
                unchanged_count += 1
    return new_count, unchanged_count


def get_pending_images(source_name: str | None = None) -> list[sqlite3.Row]:
    """Return image rows that haven't been downloaded yet (cached_path IS NULL)."""
    with _connect() as conn:
        if source_name:
            return conn.execute(
                "SELECT * FROM images WHERE source=? AND cached_path IS NULL",
                (source_name,),
            ).fetchall()
        return conn.execute(
            "SELECT * FROM images WHERE cached_path IS NULL"
        ).fetchall()


def get_cached_images() -> list[sqlite3.Row]:
    """Return all fully downloaded image rows (cached_path IS NOT NULL)."""
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM images WHERE cached_path IS NOT NULL"
        ).fetchall()


def get_unique_cached_hashes() -> list[sqlite3.Row]:
    """Return one row per unique hash from cached images (for active snapshot)."""
    with _connect() as conn:
        return conn.execute(
            """SELECT DISTINCT hash, cached_path, filename
               FROM images
               WHERE cached_path IS NOT NULL AND hash IS NOT NULL"""
        ).fetchall()


def mark_cached(image_id: int, hash_: str, cached_path: str) -> None:
    """Record that an image has been downloaded and stored."""
    with _connect() as conn:
        conn.execute(
            "UPDATE images SET hash=?, cached_path=? WHERE id=?",
            (hash_, cached_path, image_id),
        )


def needs_refetch(row: sqlite3.Row, ref: ImageRef) -> bool:
    """Decide whether *ref* should be re-downloaded given existing *row*.

    For rows that already have a cached file (hash is set), we only re-fetch
    when we can *positively detect* a change.  When there is no metadata to
    compare (size=None, mtime=None), we trust the existing cache.

    For rows that have never been downloaded (first-time sync), the caller
    uses ``cached_path IS NULL`` instead — this function is only for
    change-detection on already-cached rows.

    Rules:
    - both mtime + size available: skip only if both still match
    - mtime missing, size available: compare size only
    - size missing (no metadata): trust existing cache, skip re-fetch
    """
    if ref.size is None:
        # No metadata from source to compare — cannot detect change,
        # trust what we already have.
        return False
    if ref.mtime is not None:
        return not (row["size"] == ref.size and row["mtime"] == ref.mtime)
    # mtime missing — compare size only
    return row["size"] != ref.size


def prune_removed_images(source_name: str, pull_start_time: str) -> list[str]:
    """Remove image rows for *source_name* not seen since *pull_start_time*.

    Also unlinks store files where no other row references the same hash.
    Returns list of store paths that were unlinked.
    """
    unlinked: list[str] = []
    with _connect() as conn:
        stale = conn.execute(
            "SELECT id, hash, cached_path FROM images WHERE source=? AND last_seen < ?",
            (source_name, pull_start_time),
        ).fetchall()
        for row in stale:
            if row["hash"]:
                # Check if another row references the same hash
                count = conn.execute(
                    "SELECT COUNT(*) FROM images WHERE hash=? AND id != ?",
                    (row["hash"], row["id"]),
                ).fetchone()[0]
                if count == 0 and row["cached_path"]:
                    path = Path(row["cached_path"])
                    if path.exists():
                        path.unlink()
                    unlinked.append(row["cached_path"])
            conn.execute("DELETE FROM images WHERE id=?", (row["id"],))
    return unlinked


def count_images(source_name: str | None = None) -> dict[str, int]:
    """Return counts: indexed, cached, downloading."""
    with _connect() as conn:
        where = "WHERE source=?" if source_name else ""
        params: tuple = (source_name,) if source_name else ()
        total = conn.execute(f"SELECT COUNT(*) FROM images {where}", params).fetchone()[0]
        cached = conn.execute(
            f"SELECT COUNT(*) FROM images {where}{'AND' if source_name else 'WHERE'} cached_path IS NOT NULL",
            params,
        ).fetchone()[0]
        downloading = conn.execute(
            f"SELECT COUNT(*) FROM images {where}{'AND' if source_name else 'WHERE'} cached_path IS NULL",
            params,
        ).fetchone()[0]
    return {"indexed": total, "cached": cached, "downloading": downloading}


# ---------------------------------------------------------------------------
# Sync run tracking
# ---------------------------------------------------------------------------

def start_sync_run(source_name: str | None) -> int:
    """Insert a new sync_run row with status 'running'. Returns the run id."""
    # First, clean up any stale 'running' rows from previous interrupted runs
    cleanup_stale_runs()
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO sync_runs (source, status, started_at)
               VALUES (?, 'running', ?)""",
            (source_name, _now_iso()),
        )
        return cur.lastrowid  # type: ignore[return-value]


def finish_sync_run(
    run_id: int,
    status: str,
    indexed_count: int = 0,
    cached_count: int = 0,
    downloading_count: int = 0,
    error: str | None = None,
) -> None:
    with _connect() as conn:
        conn.execute(
            """UPDATE sync_runs
               SET status=?, finished_at=?, indexed_count=?, cached_count=?,
                   downloading_count=?, error=?
               WHERE id=?""",
            (status, _now_iso(), indexed_count, cached_count, downloading_count, error, run_id),
        )


def get_last_sync_run(source_name: str | None = None) -> sqlite3.Row | None:
    """Return the most recent sync_run for *source_name* (or all sources)."""
    with _connect() as conn:
        if source_name:
            return conn.execute(
                "SELECT * FROM sync_runs WHERE source=? ORDER BY id DESC LIMIT 1",
                (source_name,),
            ).fetchone()
        return conn.execute(
            "SELECT * FROM sync_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()


def get_running_sync_run(source_name: str | None = None) -> sqlite3.Row | None:
    """Return the currently running sync_run if any."""
    with _connect() as conn:
        if source_name:
            return conn.execute(
                "SELECT * FROM sync_runs WHERE source=? AND status='running' ORDER BY id DESC LIMIT 1",
                (source_name,),
            ).fetchone()
        return conn.execute(
            "SELECT * FROM sync_runs WHERE status='running' ORDER BY id DESC LIMIT 1"
        ).fetchone()


def cleanup_stale_runs() -> int:
    """Mark any 'running' sync_runs as 'interrupted'.  Returns count fixed."""
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE sync_runs SET status='interrupted', finished_at=? WHERE status='running'",
            (_now_iso(),),
        )
        return cur.rowcount
