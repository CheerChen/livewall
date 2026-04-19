# livewall

A dynamic wallpaper engine CLI for macOS. Syncs images from FTP sources into a local content store, and sets them as auto-rotating desktop wallpaper.

**Spotify for wallpapers** — a source registry, an index of what's available, a local store of what's downloaded, and macOS desktop as the player.

[中文文档](README_zh.md)

## Features

- **FTP sources** — sync images from FTP servers. Extensible source protocol for future backends.
- **Content-addressed store** — files stored as `{sha256}.{ext}`, automatic deduplication across sources.
- **Incremental sync** — only downloads new/changed files. Resumable after interruption.
- **Atomic snapshots** — `active/` folder rebuilt via hard links, zero extra disk usage.
- **Native macOS integration** — plist-based wallpaper control with configurable rotation intervals.
- **Rich CLI** — progress bars, status tables, spinner during indexing.

## Requirements

- macOS (wallpaper control uses plist manipulation)
- Python 3.10+
- [uv](https://docs.astral.sh/uv/) package manager

## Installation

```bash
# Install globally
uv tool install .

# Or run from source
uv sync
uv run livewall --help
```

## Quick Start

```bash
# 1. Initialize directories and config
livewall init

# 2. Add an image source
livewall source add
# Follow the interactive prompts (local or ftp)

# 3. Pull images from all sources
livewall pull

# 4. Apply as rotating wallpaper (every 5 minutes)
livewall apply

# 5. Check status
livewall status
```

## Commands

| Command | Description |
|---------|-------------|
| `livewall init` | Create config/data/log directories, config template, and empty index |
| `livewall source add` | Interactively register a new FTP source |
| `livewall source list` | Show registered sources |
| `livewall source remove <name>` | Remove a source |
| `livewall pull [<name>]` | Sync index + download images (resumable) |
| `livewall pull --detach` | Run pull in the background |
| `livewall apply [--interval 5m]` | Build active snapshot and set as wallpaper |
| `livewall show` | Open the image store in Finder |
| `livewall status` | Show config state, plist state, and pull progress |
| `livewall reset` | Restore original wallpaper |
| `livewall reset --purge` | Also delete all stored files and index |

### Rotation Intervals

`1m` `5m` `15m` `30m` `1h` `12h` `1d` `login` `wake`

## Architecture

```
Source(s) ──metadata──▶ Index (SQLite) ──eager fetch──▶ Store ──snapshot──▶ Active ──plist──▶ Desktop
```

| Layer | Module | Responsibility |
|-------|--------|----------------|
| Source | `sources/` | List and fetch images from FTP servers |
| Index | `index.py` | SQLite tracking of all known images and sync state |
| Cache | `cache.py` | Content-addressed store + hard-linked active snapshots |
| Desktop | `desktop.py` | macOS plist manipulation (pure functions + thin side effects) |
| CLI | `cli.py` | Click commands wiring everything together |
| Config | `config.py` | Paths, TOML read/write, logging setup |

### Directory Layout

| Path | Contents |
|------|----------|
| `~/.config/livewall/config.toml` | Source registration and settings |
| `~/Library/Application Support/livewall/index.db` | Sync state (SQLite) |
| `~/Library/Application Support/livewall/store/` | Downloaded images (`{sha256}.{ext}`) |
| `~/Library/Application Support/livewall/active/` | Hard-linked snapshot for macOS |
| `~/Library/Logs/livewall/livewall.log` | Rotating log file |

## Config Example

```toml
[settings]
interval = "5m"

[[sources]]
name = "nas"
type = "ftp"
host = "192.168.1.100"
path = "/wallpaper"
username = "user"
password = "secret"
```

## License

MIT