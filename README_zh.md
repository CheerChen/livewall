# livewall

macOS 动态壁纸引擎 CLI。从 FTP 源同步图片到本地存储，并设置为自动轮换桌面壁纸。

**壁纸版 Spotify** — 源注册表、可用索引、本地存储、macOS 桌面作为播放器。

[English](README.md)

## 功能特性

- **FTP 源支持** — 从 FTP 服务器同步图片，可扩展的源协议支持未来新增后端
- **内容寻址存储** — 文件以 `{sha256}.{ext}` 存储，跨源自动去重
- **增量同步** — 仅下载新增/变更文件，中断后可断点续传
- **原子快照** — `active/` 目录通过硬链接重建，零额外磁盘占用
- **原生 macOS 集成** — 基于 plist 的壁纸控制，可配置轮换间隔
- **Rich CLI** — 进度条、状态表格、索引 spinner

## 系统要求

- macOS（壁纸控制使用 plist 操作）
- Python 3.10+
- [uv](https://docs.astral.sh/uv/) 包管理器

## 安装

```bash
# 全局安装
uv tool install .

# 或从源码运行
uv sync
uv run livewall --help
```

## 快速开始

```bash
# 1. 初始化目录和配置
livewall init

# 2. 添加图片源
livewall source add
# 按交互提示选择 local 或 ftp

# 3. 拉取所有源的图片
livewall pull

# 4. 设置为轮换壁纸（每 5 分钟切换）
livewall apply

# 5. 查看状态
livewall status
```

## 命令一览

| 命令 | 说明 |
|------|------|
| `livewall init` | 创建配置/数据/日志目录、配置模板和空索引 |
| `livewall source add` | 交互式注册新 FTP 源 |
| `livewall source list` | 查看已注册的源 |
| `livewall source remove <name>` | 删除指定源 |
| `livewall pull [<name>]` | 同步索引 + 下载图片（支持断点续传） |
| `livewall pull --detach` | 在后台运行 pull |
| `livewall apply [--interval 5m]` | 构建 active 快照并设置壁纸 |
| `livewall show` | 在 Finder 中打开图片存储目录 |
| `livewall status` | 显示配置状态、plist 状态和 pull 进度 |
| `livewall reset` | 恢复原始壁纸 |
| `livewall reset --purge` | 同时删除所有存储文件和索引 |

### 轮换间隔

`1m` `5m` `15m` `30m` `1h` `12h` `1d` `login` `wake`

## 架构

```
Source(s) ──metadata──▶ Index (SQLite) ──eager fetch──▶ Store ──snapshot──▶ Active ──plist──▶ Desktop
```

| 层级 | 模块 | 职责 |
|------|------|------|
| 源 | `sources/` | 从 FTP 服务器列出和获取图片 |
| 索引 | `index.py` | SQLite 跟踪所有已知图片和同步状态 |
| 缓存 | `cache.py` | 内容寻址存储 + 硬链接 active 快照 |
| 桌面 | `desktop.py` | macOS plist 操作（纯函数 + 副作用薄层） |
| CLI | `cli.py` | Click 命令串联所有模块 |
| 配置 | `config.py` | 路径、TOML 读写、日志设置 |

### 目录布局

| 路径 | 内容 |
|------|------|
| `~/.config/livewall/config.toml` | 源注册和设置 |
| `~/Library/Application Support/livewall/index.db` | 同步状态（SQLite） |
| `~/Library/Application Support/livewall/store/` | 已下载图片（`{sha256}.{ext}`） |
| `~/Library/Application Support/livewall/active/` | 硬链接快照（供 macOS 使用） |
| `~/Library/Logs/livewall/livewall.log` | 轮转日志文件 |

## 配置示例

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

## 许可证

MIT
