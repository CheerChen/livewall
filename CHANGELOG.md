# Changelog

All notable changes to this project will be documented in this file.

## [0.2.0] - 2026-04-19

### Added

- **Screensaver sync** — `apply` now sets both Desktop and Idle (screensaver) to the same image folder; `reset` restores both

### Changed

- **Cleaner CLI output** — default mode shows only concise `console.print` messages; `log.info` details hidden unless `-v` is passed

## [0.1.0] - 2026-04-19

### Added

- **CLI** — `init`, `source add/list/remove`, `pull`, `apply`, `show`, `status`, `reset` commands via Click
- **FTP source** — recursive listing with MLSD, automatic NLST fallback for servers without MLSD support
- **Content-addressed store** — images stored as `{sha256}.{ext}` with automatic deduplication
- **Atomic downloads** — write to `.tmp` then `os.rename`; no incomplete files in store
- **Resumable pull** — Ctrl+C safe; re-run `pull` to continue from where it left off
- **Progress reporting** — Rich progress bar with full-total denominator (cached + pending), live spinner during indexing
- **Active snapshots** — hard-linked `active/` directory rebuilt atomically via `active.next/` swap
- **macOS wallpaper control** — plist manipulation with configurable rotation intervals (`1m` to `wake`)
- **First-time plist backup** — `original.plist` saved on first `apply`, restored by `reset`
- **Incremental sync** — change detection via size + mtime; pruning of removed files scoped per source
- **Multi-source deduplication** — same content from different sources shares one store file
- **Dual logging** — Rich stderr for interactive use + rotating file log (`5 MB × 3`)
- **`show` command** — open store directory in Finder
- **`--detach` mode** — run pull in background via fork
- **`reset --purge`** — delete all stored files, active snapshots, and index
