"""Tests for livewall source protocol, local source, index, cache, and desktop."""

from __future__ import annotations

import os
import plistlib
import shutil
import sqlite3
import tempfile
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# sources/__init__.py — ImageRef and Source protocol
# ---------------------------------------------------------------------------

class TestImageRef:
    def test_fields(self):
        from livewall.sources import ImageRef
        ref = ImageRef(remote_path="a/b.jpg", filename="b.jpg", size=1024, mtime=1000.0)
        assert ref.remote_path == "a/b.jpg"
        assert ref.filename == "b.jpg"
        assert ref.size == 1024
        assert ref.mtime == 1000.0

    def test_optional_fields(self):
        from livewall.sources import ImageRef
        ref = ImageRef(remote_path="x.png", filename="x.png", size=None, mtime=None)
        assert ref.size is None
        assert ref.mtime is None


# ---------------------------------------------------------------------------
# index.py — SQLite operations
# ---------------------------------------------------------------------------

class TestIndex:
    def setup_method(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        # Patch INDEX_FILE to a temp location
        import livewall.index as idx_mod
        import livewall.config as cfg_mod
        self._orig_index = cfg_mod.INDEX_FILE
        self._orig_data_dir = cfg_mod.DATA_DIR
        cfg_mod.INDEX_FILE = self.tmpdir / "index.db"
        cfg_mod.DATA_DIR = self.tmpdir
        # Reload index module constants
        idx_mod.INDEX_FILE = cfg_mod.INDEX_FILE

    def teardown_method(self):
        import livewall.config as cfg_mod
        import livewall.index as idx_mod
        cfg_mod.INDEX_FILE = self._orig_index
        cfg_mod.DATA_DIR = self._orig_data_dir
        idx_mod.INDEX_FILE = self._orig_index
        shutil.rmtree(self.tmpdir)

    def test_init_creates_tables(self):
        from livewall.index import init_db
        import livewall.config as cfg_mod
        # Patch _connect to use temp db
        _patch_connect(cfg_mod.INDEX_FILE)
        init_db()
        conn = sqlite3.connect(str(cfg_mod.INDEX_FILE))
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        conn.close()
        assert "images" in tables
        assert "sync_runs" in tables

    def test_upsert_and_count(self):
        from livewall.sources import ImageRef
        from livewall.index import init_db, upsert_images, count_images
        import livewall.config as cfg_mod
        _patch_connect(cfg_mod.INDEX_FILE)
        init_db()
        refs = [
            ImageRef("a/1.jpg", "1.jpg", 100, 1000.0),
            ImageRef("a/2.jpg", "2.jpg", 200, 2000.0),
        ]
        new, unchanged = upsert_images("src1", refs, "2024-01-01T00:00:00+00:00")
        assert new == 2
        assert unchanged == 0
        counts = count_images("src1")
        assert counts["indexed"] == 2
        assert counts["cached"] == 0

    def test_needs_refetch_rules(self):
        from livewall.index import needs_refetch
        from livewall.sources import ImageRef

        # Both match → no refetch
        row = _make_row(size=100, mtime=1000.0)
        ref = ImageRef("x", "x", size=100, mtime=1000.0)
        assert not needs_refetch(row, ref)

        # Size changed → refetch
        ref2 = ImageRef("x", "x", size=200, mtime=1000.0)
        assert needs_refetch(row, ref2)

        # No size → trust cache (don't refetch)
        ref3 = ImageRef("x", "x", size=None, mtime=None)
        assert not needs_refetch(row, ref3)

        # No mtime: compare size only — match
        row2 = _make_row(size=100, mtime=None)
        ref4 = ImageRef("x", "x", size=100, mtime=None)
        assert not needs_refetch(row2, ref4)


# ---------------------------------------------------------------------------
# desktop.py — pure functions
# ---------------------------------------------------------------------------

class TestDesktopPureFunctions:
    def test_build_folder_plist_sets_folder(self):
        from livewall.desktop import build_folder_plist
        folder = Path("/some/path/active")
        shuffle_id = "shuffle_every_5_minutes"
        result = build_folder_plist(folder, shuffle_id, {})
        # AllSpacesAndDisplays should be set
        asd = result["AllSpacesAndDisplays"]
        assert "Desktop" in asd
        choices = asd["Desktop"]["Content"]["Choices"]
        cfg = plistlib.loads(choices[0]["Configuration"])
        assert cfg["type"] == "imageFolder"
        assert "active" in cfg["url"]["relative"]

    def test_build_folder_plist_sets_idle_to_same_folder(self):
        from livewall.desktop import build_folder_plist
        result = build_folder_plist(Path("/tmp/active"), "shuffle_every_1_minute", {})
        desktop_cfg = plistlib.loads(
            result["AllSpacesAndDisplays"]["Desktop"]["Content"]["Choices"][0]["Configuration"]
        )
        idle_cfg = plistlib.loads(
            result["AllSpacesAndDisplays"]["Idle"]["Content"]["Choices"][0]["Configuration"]
        )
        assert desktop_cfg["type"] == "imageFolder"
        assert idle_cfg["type"] == "imageFolder"
        assert desktop_cfg["url"] == idle_cfg["url"]

    def test_build_reset_plist_returns_backup(self):
        from livewall.desktop import build_reset_plist
        backup = {"key": "value", "AllSpacesAndDisplays": {}}
        assert build_reset_plist(backup) is backup


# ---------------------------------------------------------------------------
# cache.py — hash and download helpers
# ---------------------------------------------------------------------------

class TestCache:
    def setup_method(self):
        self.tmpdir = Path(tempfile.mkdtemp())

    def teardown_method(self):
        shutil.rmtree(self.tmpdir)

    def test_hash_file(self):
        import hashlib
        from livewall.cache import _hash_file
        content = b"hello world"
        p = self.tmpdir / "test.bin"
        p.write_bytes(content)
        expected = hashlib.sha256(content).hexdigest()
        assert _hash_file(p) == expected

    def test_download_image_stores_by_hash(self):
        import livewall.config as cfg_mod
        import livewall.cache as cache_mod
        from livewall.sources import ImageRef

        # Patch STORE_DIR to temp
        orig_store = cfg_mod.STORE_DIR
        orig_active = cfg_mod.ACTIVE_DIR
        cfg_mod.STORE_DIR = self.tmpdir / "store"
        cfg_mod.STORE_DIR.mkdir()
        cfg_mod.ACTIVE_DIR = self.tmpdir / "active"
        cfg_mod.ACTIVE_DIR.mkdir()
        cache_mod.STORE_DIR = cfg_mod.STORE_DIR
        cache_mod.ACTIVE_DIR = cfg_mod.ACTIVE_DIR

        try:
            content = b"\xff\xd8\xff" + b"\xaa" * 512
            import hashlib
            expected_hash = hashlib.sha256(content).hexdigest()

            src_dir = self.tmpdir / "src"
            src_dir.mkdir()
            (src_dir / "photo.jpg").write_bytes(content)

            # Stub source that copies from src_dir
            class _StubSource:
                name = "test"
                def list_images(self, on_found=None):
                    return []
                def fetch(self, ref, dest):
                    import shutil as _shutil
                    _shutil.copy2(src_dir / ref.remote_path, dest)
                    return dest

            source = _StubSource()
            ref = ImageRef("photo.jpg", "photo.jpg", len(content), None)
            hash_, path = cache_mod.download_image(source, ref)

            assert hash_ == expected_hash
            stored = Path(path)
            assert stored.exists()
            assert stored.read_bytes() == content
            assert stored.name == f"{expected_hash}.jpg"
        finally:
            cfg_mod.STORE_DIR = orig_store
            cfg_mod.ACTIVE_DIR = orig_active
            cache_mod.STORE_DIR = orig_store
            cache_mod.ACTIVE_DIR = orig_active


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _patch_connect(index_file: Path):
    """Monkey-patch livewall.index._connect to use index_file."""
    import livewall.index as idx
    from contextlib import contextmanager

    @contextmanager
    def _connect():
        index_file.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(index_file))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    idx._connect = _connect


def _make_row(size, mtime) -> sqlite3.Row:
    """Create a sqlite3.Row-like object for testing needs_refetch."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE t (size INTEGER, mtime REAL)")
    conn.execute("INSERT INTO t VALUES (?, ?)", (size, mtime))
    return conn.execute("SELECT * FROM t").fetchone()
