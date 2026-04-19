"""Source protocol and ImageRef definition for livewall."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol, runtime_checkable

# Callback type for reporting progress during list_images
ProgressCallback = Callable[[int], None]  # called with current found count


@dataclass
class ImageRef:
    """Lightweight reference to an image in a source.

    Does not contain file content — only enough metadata for the index to
    detect changes and decide whether to re-fetch.
    """

    remote_path: str    # unique path within the source
    filename: str       # original filename
    size: int | None    # bytes, used for change detection + progress bars
    mtime: float | None # modification time, used for incremental sync


@runtime_checkable
class Source(Protocol):
    """Protocol that every image source must satisfy."""

    name: str

    def list_images(self) -> list[ImageRef]:
        """Return metadata for all images available in this source."""
        ...

    def fetch(self, ref: ImageRef, dest: Path) -> Path:
        """Download the image described by *ref* to *dest* and return dest."""
        ...
