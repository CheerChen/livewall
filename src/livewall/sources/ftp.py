"""FtpSource: fetch images from an FTP server using stdlib ftplib."""

from __future__ import annotations

import ftplib
import logging
from pathlib import Path, PurePosixPath

from livewall.sources import ImageRef

log = logging.getLogger(__name__)

IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".heic", ".bmp", ".gif", ".tiff"})


class FtpSource:
    """List and download images from an FTP server.

    Uses ftplib (stdlib only, no lftp dependency).  Incremental fetch uses
    size + mtime with conservative fallback:
    - both available: skip if both still match
    - mtime missing: compare remote_path + size only
    - size also missing: always re-fetch
    """

    def __init__(
        self,
        name: str,
        host: str,
        path: str,
        username: str = "anonymous",
        password: str = "",
        port: int = 21,
    ) -> None:
        self.name = name
        self.host = host
        self.path = path
        self.username = username
        self.password = password
        self.port = port

    # ------------------------------------------------------------------
    # Source protocol
    # ------------------------------------------------------------------

    def list_images(self, on_found=None) -> list[ImageRef]:
        """Connect to FTP and recursively list all image files.

        *on_found* is an optional callback invoked with the running count
        each time a new image is discovered (for live progress display).
        """
        try:
            ftp = self._connect()
        except ftplib.all_errors as exc:
            raise ConnectionError(
                f"FTP connection failed: {exc} ({self.name}, {self.host}:{self.port})"
            ) from exc

        refs: list[ImageRef] = []
        try:
            self._walk(ftp, PurePosixPath(self.path), refs, on_found=on_found)
        finally:
            try:
                ftp.quit()
            except Exception:
                pass
        return refs

    def fetch(self, ref: ImageRef, dest: Path) -> Path:
        """Download *ref* from FTP and write to *dest*."""
        remote = str(PurePosixPath(self.path) / ref.remote_path)
        try:
            ftp = self._connect()
        except ftplib.all_errors as exc:
            raise ConnectionError(
                f"FTP connection failed: {exc} ({self.name}, {self.host}:{self.port})"
            ) from exc
        try:
            with dest.open("wb") as fp:
                ftp.retrbinary(f"RETR {remote}", fp.write)
        finally:
            try:
                ftp.quit()
            except Exception:
                pass
        return dest

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _connect(self) -> ftplib.FTP:
        ftp = ftplib.FTP()
        ftp.connect(self.host, self.port, timeout=30)
        ftp.login(self.username, self.password)
        return ftp

    def _walk(
        self,
        ftp: ftplib.FTP,
        remote_dir: PurePosixPath,
        out: list[ImageRef],
        on_found=None,
    ) -> None:
        """Recursively walk *remote_dir*, appending ImageRef objects to *out*."""
        log.debug("Listing FTP directory: %s", remote_dir)
        entries: list[tuple[str, dict]] = []
        try:
            entries = list(ftp.mlsd(str(remote_dir), facts=["type", "size", "modify"]))
            log.debug("MLSD returned %d entries for %s", len(entries), remote_dir)
        except ftplib.error_perm as exc:
            log.debug("MLSD failed for %s: %s, falling back to NLST", remote_dir, exc)
            # Fall back to NLST-based listing when MLSD is unavailable
            self._walk_nlst(ftp, remote_dir, out, on_found=on_found)
            return

        for name, facts in entries:
            if name in (".", ".."):
                continue
            entry_type = facts.get("type", "").lower()
            if entry_type == "dir":
                self._walk(ftp, remote_dir / name, out, on_found=on_found)
            elif entry_type in ("file", ""):
                size_str = facts.get("size")
                size = int(size_str) if size_str else None
                modify_str = facts.get("modify")
                mtime: float | None = None
                if modify_str:
                    # MLSD modify format: YYYYMMDDHHmmss[.sss]
                    try:
                        from datetime import datetime, timezone
                        dt = datetime.strptime(modify_str[:14], "%Y%m%d%H%M%S")
                        mtime = dt.replace(tzinfo=timezone.utc).timestamp()
                    except ValueError:
                        pass
                self._try_add_file(ftp, remote_dir, name, size, mtime, out)
                if on_found:
                    on_found(len(out))

    def _walk_nlst(
        self,
        ftp: ftplib.FTP,
        remote_dir: PurePosixPath,
        out: list[ImageRef],
        on_found=None,
    ) -> None:
        """NLST fallback: list entries, probe each to determine file vs dir, recurse."""
        try:
            names = ftp.nlst(str(remote_dir))
        except ftplib.all_errors as exc:
            log.warning("Cannot list %s: %s", remote_dir, exc)
            return

        log.debug("NLST returned %d entries for %s", len(names), remote_dir)
        for raw_name in names:
            # NLST may return full paths or bare names depending on server
            entry_name = PurePosixPath(raw_name).name
            if entry_name in (".", ".."):
                continue
            full_path = str(remote_dir / entry_name)

            # Try CWD to check if it's a directory
            try:
                ftp.cwd(full_path)
                # It's a directory — recurse, then go back
                ftp.cwd(str(remote_dir))
                log.debug("  %s is a directory, recursing", entry_name)
                self._walk_nlst(ftp, remote_dir / entry_name, out, on_found=on_found)
            except ftplib.error_perm:
                # Not a directory — try to get size and treat as file
                size: int | None = None
                try:
                    size = ftp.size(full_path)
                except ftplib.all_errors:
                    pass
                self._try_add_file(ftp, remote_dir, entry_name, size, None, out)
                if on_found:
                    on_found(len(out))

    def _try_add_file(
        self,
        ftp: ftplib.FTP,
        remote_dir: PurePosixPath,
        name: str,
        size: int | None,
        mtime: float | None,
        out: list[ImageRef],
    ) -> None:
        suffix = PurePosixPath(name).suffix.lower()
        if suffix not in IMAGE_EXTENSIONS:
            return
        relative = str(PurePosixPath(remote_dir.relative_to(self.path)) / name)
        # Normalise leading slash
        relative = relative.lstrip("/")
        out.append(ImageRef(remote_path=relative, filename=name, size=size, mtime=mtime))
