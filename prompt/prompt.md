# livewall v0.1 — Project Bootstrap Prompt

## What is livewall

A dynamic wallpaper engine CLI for macOS. It syncs images from remote sources (FTP for v0.1) into a local content store, materializes an active wallpaper snapshot, and sets it as macOS auto-rotating wallpaper via native folder rotation.

Think of it as: **Spotify for wallpapers** — a source registry, an index of what's available, a local content store of what's downloaded, and a player (macOS desktop).

## Architecture Overview

```
Source(s) ──metadata──▶ Index (SQLite) ──eager fetch──▶ Store ──snapshot apply──▶ Active folder ──macOS plist──▶ Desktop
```

### Key layers (each is a separate module):

1. **Source** — abstraction over where images come from. v0.1 implements `LocalSource` and `FtpSource`. See "Source Protocol" section below for the full contract.
2. **Index** — SQLite db tracking all known images and their storage state. Central brain. Stores only sync/image state, never connection config.
3. **Cache** — split into two directories under `~/Library/Application Support/livewall/`:
   - `store/` holds downloaded content-addressed files as `{sha256}.{ext}`
   - `active/` is a hard-linked snapshot exposed to macOS for wallpaper rotation
   This separation allows partial pulls, safe apply while a pull is still running, and atomic snapshot switches.
4. **Desktop** — macOS wallpaper control via plist manipulation. Split into pure functions (build plist dict) and a thin side-effect layer (write file, restart agent). v0.1 only `MacOSDesktop`.

### What v0.1 does NOT do (but structure should not block):

- Per-image on-demand fetching (window/stream strategy) — index has `cached_path` nullable for this
- API sources (unsplash, wallhaven, custom) — Source protocol allows adding
- Linux/GNOME support — Desktop protocol allows adding
- GUI / menu bar app
- Daemon mode — uses launchd for scheduling if needed
- Cache GC beyond `reset --purge` (v0.1 is eager-only, no partial eviction needed)

## Source Protocol

Every source must return `ImageRef` objects from `list_images()` so the index can do incremental sync without downloading file contents:

```python
@dataclass
class ImageRef:
    remote_path: str    # unique path within the source
    filename: str       # original filename
    size: int | None    # bytes, used for change detection + progress bars
    mtime: float | None # modification time, used for incremental sync
    # future: etag, hash_hint

class Source(Protocol):
    name: str
    def list_images(self) -> list[ImageRef]
    def fetch(self, ref: ImageRef, dest: Path) -> Path
```

- `LocalSource`: given a root path, recursively scan for supported image extensions (jpg/jpeg/png). No hardcoded folder structure.
- `FtpSource`: connect via `ftplib` (stdlib only, no lftp), recursive listing, incremental fetch based on size+mtime with conservative fallback rules:
  - `mtime` + `size` both available: skip only if both still match
  - `mtime` missing: fall back to `remote_path + size`
  - `size` also missing: always re-fetch (safe, but less efficient)

## CLI Commands (click)

```
livewall init                              # create config/data/log dirs + config.toml template + empty index.db
livewall source add <name>                 # interactively register a source (ftp or local)
livewall source list                       # show registered sources
livewall source remove <name>              # remove a source
livewall pull [<name>] [--detach]          # sync index + fetch images to store (eager); optionally run in background
livewall apply [--interval 5m]             # build an active snapshot from completed downloads and set it as macOS rotating wallpaper
livewall status                            # show config state, actual plist state, and pull progress/state
livewall reset                             # restore pre-livewall wallpaper from backup, stop rotation
livewall reset --purge                     # also delete all stored files and active snapshots
```

### Responsibility boundaries:

- `pull` = talk to source → update index → download complete files into `store/`. It may run in the foreground or detached. It does NOT touch wallpaper settings.
- `apply` = read fully downloaded files already present in `store/`, build a new `active/` snapshot, atomically switch to it, then write plist + restart agent. It does NOT download anything, and it is valid to run while `pull` is still in progress.
- `reset` = restore original plist backup → restart agent. Does NOT delete `store/` or `active/` unless `--purge`.

## Tech Stack

- Python 3.10+, packaged with `uv`
- `click` for CLI subcommands
- `rich` for terminal output (tables, progress bars, logging)
- `plistlib` (stdlib) for macOS wallpaper plist manipulation
- `ftplib` (stdlib) for FTP source
- `sqlite3` (stdlib) for index db
- No external heavy dependencies in v0.1

## Project Structure

```
livewall/
├── pyproject.toml
├── src/
│   └── livewall/
│       ├── __init__.py
│       ├── cli.py              # click group + all commands
│       ├── config.py           # config.toml read/write, all path constants (config/data/log dirs)
│       ├── index.py            # SQLite index operations
│       ├── cache.py            # store downloads + build active snapshots via hard links
│       ├── desktop.py          # macOS wallpaper: pure plist builders + thin side-effect layer
│       └── sources/
│           ├── __init__.py     # Source protocol + ImageRef definition
│           ├── local.py        # LocalSource: recursive scan from root path
│           └── ftp.py          # FtpSource: ftplib-based sync
└── tests/
    └── ...
```

## Directory Layout — Separation of Concerns

Three categories, three standard macOS locations:

| Type | What it holds | Path |
|------|---------------|------|
| Config | User-editable settings, source registration | `~/.config/livewall/` |
| Data | Program-managed state (db, store, active snapshot, plist backup) | `~/Library/Application Support/livewall/` |
| Logs | Runtime logs | `~/Library/Logs/livewall/` |

config.toml owns: connection info, user preferences.
index.db owns: sync state, image metadata, storage state.
They never duplicate each other's data.

```
~/.config/livewall/
└── config.toml              # sources + settings (user-editable)

~/Library/Application Support/livewall/
├── index.db                 # SQLite: sync state (managed by livewall)
├── original.plist           # backup of wallpaper plist before first apply
├── store/                   # actual downloaded image files
│   ├── a3f8e2...4b.jpg      # {sha256}.{ext}
│   └── ...
├── active/                  # hard-linked snapshot currently exposed to macOS
│   ├── a3f8e2...4b.jpg      # hard link to store/{sha256}.{ext}
│   └── ...
└── active.next/             # temporary snapshot dir during apply, then atomically renamed

~/Library/Logs/livewall/
└── livewall.log             # rotating log file
```

`init` is responsible for creating all three directories above, creating `store/` and `active/`, writing a template `config.toml`, and initializing an empty `index.db`.

## Store vs Active Snapshot Model

- `store/` is the durable content store. `pull` writes files there as `{sha256}.{ext}.tmp`, then atomically renames them to `{sha256}.{ext}` once the download completes.
- `active/` is a generated snapshot for desktop consumption. It is rebuilt from the set of fully downloaded files currently present in the index.
- `active/` must use hard links, not symlinks:
  - no extra disk usage
  - safe if a `store/` entry is later unlinked during prune
  - simple, stable folder semantics for macOS wallpaper rotation
- `store/` and `active/` must live on the same filesystem so hard links are possible.
- `apply` must not rebuild `active/` in place. Instead:
  1. create `active.next/`
  2. hard-link the current unique downloaded hash set into it
  3. validate the snapshot
  4. atomically replace `active/`

This model means a large initial `pull` does not block usability: the user may `apply` a partial but fully valid snapshot at any time, then re-`apply` later after more downloads complete.

### config.toml example:

```toml
[settings]
interval = "5m"

[[sources]]
name = "nas"
type = "ftp"
host = "192.168.1.100"
path = "/wallpaper"
username = "user"
password = "xxx"

[[sources]]
name = "local-art"
type = "local"
path = "/Users/cheerchen/Public/Wallpaper"
```

### index.db schema:

```sql
CREATE TABLE images (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,           -- source name (matches config)
    remote_path TEXT NOT NULL,      -- original path within source
    filename TEXT NOT NULL,         -- original filename
    size INTEGER,                   -- file size in bytes
    mtime REAL,                    -- source modification time
    hash TEXT,                      -- sha256, also cache filename stem
    cached_path TEXT,               -- local store path, NULL if not downloaded yet
    last_seen TIMESTAMP NOT NULL,   -- updated each pull for this source
    UNIQUE(source, remote_path)
);

CREATE TABLE sync_runs (
    id INTEGER PRIMARY KEY,
    source TEXT,                    -- NULL means "all sources"
    status TEXT NOT NULL,           -- running / succeeded / failed
    started_at TIMESTAMP NOT NULL,
    finished_at TIMESTAMP,          -- NULL while still running
    indexed_count INTEGER NOT NULL DEFAULT 0,
    cached_count INTEGER NOT NULL DEFAULT 0,
    downloading_count INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
```

### Pruning rule:

When `pull <name>` completes, images with `source = <name> AND last_seen < pull_start_time` are considered removed from that source. This is always scoped to the pulled source — never touches other sources' records.

Removal must happen in two steps:

1. Delete the image row from `images`
2. Delete the physical store file only if no other rows still reference the same `hash`

Before removing `{sha256}.{ext}` from `store/`, check:

```sql
SELECT COUNT(*) FROM images WHERE hash = ? AND id != ?;
```

If the count is non-zero, keep the file and delete only the source-specific row.

If the count is zero, unlink the file from `store/`. A currently applied `active/` snapshot remains valid because it uses hard links to the same inode.

### Apply input set:

`apply` must build `active/` from the unique set of fully downloaded hashes (`cached_path IS NOT NULL`), not from raw `images` rows. This avoids duplicate wallpapers when the same content appears in multiple sources.

## macOS Wallpaper Control

The attached `update_wallpaper_links.py` contains working, tested code for plist-based wallpaper control. The AppleScript approach does NOT work for folder rotation on modern macOS — plist manipulation is the only reliable method.

### desktop.py architecture:

**Pure functions (testable, no side effects):**
- `build_folder_plist(folder: Path, shuffle_id: str, existing: dict) -> dict` — takes current plist, returns modified plist with imageFolder + shuffle config applied to all displays/spaces
- `build_reset_plist(backup: dict) -> dict` — returns the original plist to restore

**Thin side-effect layer:**
- `write_plist(data: dict, path: Path) -> None`
- `restart_wallpaper_agent() -> None` — find PID via `launchctl list`, kill it
- `backup_plist(path: Path, backup_path: Path) -> None` — copy on first apply only

### apply flow:
1. If `original.plist` doesn't exist yet, back up current plist (first-time only)
2. Build a new `active.next/` snapshot from the current unique downloaded hash set
3. Atomically replace `active/` with `active.next/`
4. Build new plist via pure function, pointing to `active/`
5. Write + restart agent

### reset flow:
1. If `original.plist` exists, restore it
2. If not, fall back to building a plist pointing to system default wallpaper
3. Write + restart agent

### Shuffle frequency identifiers macOS recognizes:
`shuffle_every_1_minute`, `shuffle_every_5_minutes`, `shuffle_every_15_minutes`, `shuffle_every_30_minutes`, `shuffle_every_1_hour`, `shuffle_every_12_hours`, `shuffle_every_day`, `shuffle_on_login`, `shuffle_on_wake`

### status truth sources:
- **Config state**: read from config.toml (what the user configured)
- **Actual state**: read from current wallpaper plist (what macOS is actually doing)
- **Pull state**: read from `sync_runs` in `index.db`
- If config and actual state differ, `status` should flag `(out of sync)` so the user knows to re-apply
- `status` should also show: `indexed`, `cached`, `downloading`, `last_pull_started_at`, `last_pull_finished_at`, `in_progress`

## Logging

Two outputs, always both active:

- **stderr (rich)** — real-time CLI feedback via `RichHandler`. For the human running the command.
- **file** — append to `~/Library/Logs/livewall/livewall.log`. For post-hoc debugging, launchd runs, etc. Also visible in macOS Console.app.

### What to log (INFO level):

```
pull:   pull started source=nas
        index updated: 12 new, 3 removed, 2980 unchanged
        downloading 12 images (34.2 MB)
        pull completed source=nas duration=8.3s

apply:  apply started store=.../store active=.../active interval=5m
        active snapshot rebuilt count=2989
        backed up original plist (first time)
        wallpaper agent restarted pid=1234

reset:  restored wallpaper from original.plist backup
        wallpaper agent restarted pid=1235
```

### What to log (ERROR level):

```
FTP connection failed: Connection refused (nas, 192.168.1.100:21)
plist write failed: Permission denied
```

### What NOT to log:

- Individual filenames during pull (3000+ lines of noise — use rich progress bar instead)
- Plist binary content
- Routine "no changes" ticks

### Rotation:

Single file, size-based rotation via stdlib `RotatingFileHandler`: 5 MB max, keep 3 backups (`livewall.log.1`, `.2`, `.3`).

## Implementation Instructions

1. Initialize the project with `uv init --lib livewall` and set up `pyproject.toml` with click/rich dependencies
2. Implement bottom-up: config (paths + toml r/w + logging setup) → index → sources (protocol + ImageRef first, then local, then ftp) → cache → desktop → cli
3. All logs and comments in English
4. Use `rich` for all user-facing output (tables, progress, status)
5. Use `click.group()` with subgroups for `source` commands
6. `source add` is interactive:
   - local source: prompt for filesystem path
   - ftp source: prompt for `host`, `path`, `username`, `password`
   - use `click.prompt(..., hide_input=True)` for password
   - store credentials in `config.toml` for v0.1 (plaintext is acceptable; keyring is future work)
7. `pull` should show a rich progress bar for downloads in foreground mode, and support `--detach` for background execution without becoming a persistent daemon
8. `status` should show config state vs actual plist state, plus pull progress/state from `sync_runs`
9. Store downloads: write to `store/{hash}.{ext}.tmp` first, then `os.rename` to final name (atomic on same filesystem)
10. `apply` should rebuild `active/` from scratch via `active.next/` + atomic rename, using hard links to the unique downloaded hash set in `store/`
11. Do NOT implement: daemon mode, tick command, non-eager fetch strategies, non-macOS desktop support, cache eviction/GC beyond `reset --purge` (but keep the Protocol boundaries so these are addable)

## Reference

The attached `update_wallpaper_links.py` is a working prototype. It contains:
- Image collection logic (→ basis for `LocalSource`, but replace hardcoded numbered folders with recursive scan)
- Full macOS plist wallpaper manipulation (→ extract into `desktop.py`, split into pure functions + side effects)
