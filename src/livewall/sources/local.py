"""LocalSource: recursively scans a filesystem directory for images."""

from __future__ import annotations

import shutil
from pathlib import Path

from livewall.sources import ImageRef

IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".heic", ".bmp", ".gif", ".tiff"})


class LocalSource:
    """Scan a local directory tree for supported image files.

    No hardcoded folder structure — it recursively walks from *root*.
    """

    def __init__(self, name: str, root: str | Path) -> None:
        self.name = name
        self.root = Path(root)

    # ------------------------------------------------------------------
    # Source protocol
    # ------------------------------------------------------------------

    def list_images(self, on_found=None) -> list[ImageRef]:
        """Return an ImageRef for every image found under *self.root*."""
        refs: list[ImageRef] = []
        for entry in self.root.rglob("*"):
            if not entry.is_file():
                continue
            if entry.suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            try:
                stat = entry.stat()
                refs.append(
                    ImageRef(
                        remote_path=str(entry.relative_to(self.root)),
                        filename=entry.name,
                        size=stat.st_size,
                        mtime=stat.st_mtime,
                    )
                )
                if on_found:
                    on_found(len(refs))
            except OSError:
                continue
        return refs

    def fetch(self, ref: ImageRef, dest: Path) -> Path:
        """Copy the local file to *dest*. Returns *dest*."""
        src = self.root / ref.remote_path
        shutil.copy2(src, dest)
        return dest
