"""livewall CLI — click group + all commands."""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from livewall import config as cfg
from livewall import index, cache, desktop
from livewall.config import (
    ACTIVE_DIR,
    CONFIG_FILE,
    DATA_DIR,
    INDEX_FILE,
    INTERVAL_MAP,
    LOG_DIR,
    LOG_FILE,
    ORIGINAL_PLIST,
    STORE_DIR,
    WALLPAPER_PLIST,
    get_interval,
    get_sources,
    find_source,
    add_source,
    remove_source,
    load_config,
    save_config,
)

console = Console()
log = logging.getLogger("livewall")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _source_factory(src_cfg: dict):
    """Build a Source object from a config dict."""
    src_type = src_cfg.get("type", "")
    name = src_cfg["name"]
    if src_type == "ftp":
        from livewall.sources.ftp import FtpSource
        return FtpSource(
            name=name,
            host=src_cfg["host"],
            path=src_cfg.get("path", "/"),
            username=src_cfg.get("username", "anonymous"),
            password=src_cfg.get("password", ""),
            port=int(src_cfg.get("port", 21)),
        )
    else:
        raise click.ClickException(f"Unknown source type: {src_type!r}")


# ---------------------------------------------------------------------------
# Root CLI group
# ---------------------------------------------------------------------------

@click.group()
@click.option("--verbose", "-v", is_flag=True, default=False, help="Enable debug logging")
def cli(verbose: bool) -> None:
    """livewall — dynamic wallpaper engine for macOS."""
    cfg.setup_logging(verbose=verbose)


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

@cli.command()
def init() -> None:
    """Create config/data/log directories, config.toml template, and empty index.db."""
    # Create all directories
    cfg.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    ACTIVE_DIR.mkdir(parents=True, exist_ok=True)

    # Config template (only if not already present)
    cfg.init_config()

    # Init DB
    index.init_db()

    table = Table(title="livewall initialized", show_header=False)
    table.add_column("Key", style="bold cyan")
    table.add_column("Path")
    table.add_row("Config", str(CONFIG_FILE))
    table.add_row("Index", str(INDEX_FILE))
    table.add_row("Store", str(STORE_DIR))
    table.add_row("Active", str(ACTIVE_DIR))
    table.add_row("Logs", str(LOG_FILE))
    console.print(table)


# ---------------------------------------------------------------------------
# source subgroup
# ---------------------------------------------------------------------------

@cli.group()
def source() -> None:
    """Manage image sources."""


@source.command("add")
def source_add() -> None:
    """Interactively register a new FTP image source."""
    name = click.prompt("Source name")

    conf = load_config()
    if find_source(conf, name):
        raise click.ClickException(f"Source {name!r} already exists.")

    host = click.prompt("FTP host")
    path = click.prompt("Remote path", default="/")
    username = click.prompt("Username", default="anonymous")
    password = click.prompt("Password", hide_input=True, default="")
    port = click.prompt("Port", default=21, type=int)
    src_cfg = {
        "name": name,
        "type": "ftp",
        "host": host,
        "path": path,
        "username": username,
        "password": password,
        "port": port,
    }

    add_source(conf, src_cfg)
    save_config(conf)
    console.print(f"[green]Source [bold]{name}[/bold] added.[/green]")


@source.command("list")
def source_list() -> None:
    """Show registered sources."""
    conf = load_config()
    sources = get_sources(conf)

    if not sources:
        console.print("[dim]No sources configured. Run [bold]livewall source add[/bold] to add one.[/dim]")
        return

    table = Table(title="Registered Sources")
    table.add_column("Name", style="bold")
    table.add_column("Type", style="cyan")
    table.add_column("Details")
    for src in sources:
        detail = f"{src.get('username', '')}@{src.get('host', '')}:{src.get('port', 21)}{src.get('path', '/')}"
        table.add_row(src["name"], src["type"], detail)
    console.print(table)


@source.command("remove")
@click.argument("name")
def source_remove(name: str) -> None:
    """Remove a source by NAME."""
    conf = load_config()
    if not remove_source(conf, name):
        raise click.ClickException(f"Source {name!r} not found.")
    save_config(conf)
    console.print(f"[green]Source [bold]{name}[/bold] removed.[/green]")


# ---------------------------------------------------------------------------
# pull
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("name", required=False)
@click.option("--detach", is_flag=True, default=False, help="Run pull in background")
def pull(name: str | None, detach: bool) -> None:
    """Sync index + fetch images to store. Optionally run in background with --detach."""

    if detach:
        # Re-launch this process without --detach
        args = [sys.executable, "-m", "livewall.cli", "pull"]
        if name:
            args.append(name)
        proc = os.fork() if hasattr(os, "fork") else None
        if proc is None:
            # Fallback: subprocess detach on systems without fork
            import subprocess
            subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            console.print("[dim]Pull started in background.[/dim]")
            return
        elif proc == 0:
            # Child: double-fork to detach fully
            if os.fork() > 0:
                os._exit(0)
            os.setsid()
            _run_pull(name)
            os._exit(0)
        else:
            os.waitpid(proc, 0)
            console.print("[dim]Pull started in background.[/dim]")
        return

    _run_pull(name)


def _run_pull(name: str | None) -> None:
    """Execute pull logic synchronously."""
    from datetime import datetime, timezone
    from rich.progress import Progress, BarColumn, TextColumn, DownloadColumn, TimeRemainingColumn

    conf = load_config()
    index.init_db()

    # Determine which sources to pull
    all_sources = get_sources(conf)
    if not all_sources:
        raise click.ClickException("No sources configured. Run 'livewall source add' first.")

    if name:
        src_cfgs = [s for s in all_sources if s["name"] == name]
        if not src_cfgs:
            raise click.ClickException(f"Source {name!r} not found.")
    else:
        src_cfgs = all_sources

    for src_cfg in src_cfgs:
        src_name = src_cfg["name"]
        source = _source_factory(src_cfg)

        run_id = index.start_sync_run(src_name)
        pull_start = datetime.now(tz=timezone.utc).isoformat()
        log.info("pull started source=%s", src_name)

        try:
            # Step 1: List images from source
            with console.status(f"[cyan]Indexing {src_name}...[/cyan]", spinner="dots") as status:
                def _on_found(count: int) -> None:
                    status.update(f"[cyan]Indexing {src_name}... [bold]{count}[/bold] images found[/cyan]")
                refs = source.list_images(on_found=_on_found)
            console.print(f"[cyan]Indexing {src_name}... [bold]{len(refs)}[/bold] images found[/cyan]")

            new_count, unchanged_count = index.upsert_images(src_name, refs, pull_start)
            removed_paths = index.prune_removed_images(src_name, pull_start)
            removed_count = len(removed_paths)
            log.info(
                "index updated: %d new, %d removed, %d unchanged",
                new_count, removed_count, unchanged_count,
            )
            console.print(
                f"  Index updated: [green]{new_count} new[/green], "
                f"[red]{removed_count} removed[/red], "
                f"[dim]{unchanged_count} unchanged[/dim]"
            )

            # Step 2: Download pending images
            pending = index.get_pending_images(src_name)

            # Also check existing rows that need re-fetch due to changed size/mtime
            refetch_rows = _get_refetch_rows(src_name, refs)
            all_download = pending + refetch_rows

            if not all_download:
                console.print(f"  [dim]Nothing to download for {src_name}[/dim]")
                index.finish_sync_run(
                    run_id,
                    status="succeeded",
                    indexed_count=len(refs),
                    cached_count=len(refs) - len(pending),
                )
                log.info("pull completed source=%s (no new downloads)", src_name)
                continue

            # Build a map from remote_path → ImageRef for download
            ref_map = {r.remote_path: r for r in refs}

            # Calculate total bytes across ALL images (cached + pending) for progress
            already_cached_bytes = sum(
                ref_map[row["remote_path"]].size or 0
                for row in index.get_cached_images()
                if row["source"] == src_name and row["remote_path"] in ref_map
            )
            pending_bytes = sum(
                ref_map[row["remote_path"]].size or 0
                for row in all_download
                if row["remote_path"] in ref_map
            )
            total_bytes = already_cached_bytes + pending_bytes

            pending_str = f"{pending_bytes / 1_048_576:.1f} MB" if pending_bytes else "unknown size"
            total_str = f"{total_bytes / 1_048_576:.1f} MB" if total_bytes else "unknown size"
            log.info("downloading %d images (%s remaining, %s total)", len(all_download), pending_str, total_str)
            console.print(
                f"  Downloading [bold]{len(all_download)}[/bold] images ({pending_str} remaining, {total_str} total)"
            )

            cached_count = 0
            error_count = 0
            t0 = time.monotonic()

            with Progress(
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                "[progress.percentage]{task.percentage:>3.0f}%",
                DownloadColumn(),
                TimeRemainingColumn(),
                console=console,
                transient=True,
            ) as progress:
                task = progress.add_task(
                    f"Pulling {src_name}",
                    total=total_bytes or len(refs),
                    completed=already_cached_bytes,
                )
                for row in all_download:
                    remote_path = row["remote_path"]
                    ref = ref_map.get(remote_path)
                    if ref is None:
                        continue
                    try:
                        hash_, cached_path = cache.download_image(source, ref)
                        index.mark_cached(row["id"], hash_, cached_path)
                        cached_count += 1
                    except Exception as exc:
                        log.error("Failed to fetch %s: %s", remote_path, exc)
                        error_count += 1
                    finally:
                        advance = ref.size or 1
                        progress.advance(task, advance)

            duration = time.monotonic() - t0
            log.info(
                "pull completed source=%s duration=%.1fs cached=%d errors=%d",
                src_name, duration, cached_count, error_count,
            )
            console.print(
                f"  [green]Pull complete[/green] — "
                f"{cached_count} fetched, {error_count} errors, {duration:.1f}s"
            )

            status = "failed" if error_count and cached_count == 0 else "succeeded"
            index.finish_sync_run(
                run_id,
                status=status,
                indexed_count=len(refs),
                cached_count=cached_count,
                downloading_count=error_count,
            )

        except KeyboardInterrupt:
            log.info("Pull interrupted by user for source %s", src_name)
            index.finish_sync_run(run_id, status="interrupted", error="aborted by user")
            console.print(f"\n  [yellow]Pull interrupted.[/yellow] Already downloaded files are saved — run [bold]livewall pull[/bold] to resume.")
            return
        except Exception as exc:
            log.error("Pull failed for source %s: %s", src_name, exc)
            index.finish_sync_run(run_id, status="failed", error=str(exc))
            raise


def _get_refetch_rows(src_name: str, refs):
    """Return existing rows whose size/mtime has changed (need re-fetch)."""
    import sqlite3 as _sqlite3
    from livewall.config import INDEX_FILE
    conn = _sqlite3.connect(str(INDEX_FILE))
    conn.row_factory = _sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM images WHERE source=? AND cached_path IS NOT NULL",
        (src_name,),
    ).fetchall()
    conn.close()

    ref_map = {r.remote_path: r for r in refs}
    stale = []
    for row in rows:
        ref = ref_map.get(row["remote_path"])
        if ref and index.needs_refetch(row, ref):
            stale.append(row)
    return stale


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------

@cli.command()
def show() -> None:
    """Open the image store directory in Finder."""
    import subprocess
    if not STORE_DIR.exists():
        raise click.ClickException(f"Store directory does not exist: {STORE_DIR}\nRun 'livewall init' first.")
    subprocess.run(["open", str(STORE_DIR)], check=True)


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------

@cli.command()
@click.option(
    "--interval",
    default=None,
    type=click.Choice(list(INTERVAL_MAP.keys())),
    help="Wallpaper rotation interval (overrides config.toml setting)",
)
def apply(interval: str | None) -> None:
    """Build active snapshot from store and set as macOS rotating wallpaper."""
    index.init_db()

    conf = load_config()
    interval = interval or get_interval(conf)
    shuffle_id = INTERVAL_MAP[interval]

    log.info(
        "apply started store=%s active=%s interval=%s",
        STORE_DIR, ACTIVE_DIR, interval,
    )

    unique_rows = index.get_unique_cached_hashes()
    if not unique_rows:
        raise click.ClickException(
            "No cached images found. Run 'livewall pull' first."
        )

    count = cache.build_active_snapshot(unique_rows)
    log.info("active snapshot rebuilt count=%d", count)
    console.print(f"[green]Snapshot built[/green] — {count} images in active/")

    desktop.apply_wallpaper(ACTIVE_DIR, shuffle_id)
    console.print(f"[green]Wallpaper applied[/green] — rotating every {interval}")


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

@cli.command()
def status() -> None:
    """Show config state, actual plist state, and pull progress."""
    index.init_db()

    conf = load_config()
    sources = get_sources(conf)
    configured_interval = get_interval(conf)

    # Actual plist state
    actual_folder = desktop.get_current_wallpaper_folder()

    # Index counts
    counts = index.count_images()
    last_run = index.get_last_sync_run()
    running_run = index.get_running_sync_run()

    # Config vs actual comparison
    plist_matches = actual_folder is not None and str(actual_folder) == str(ACTIVE_DIR)

    # ------ Config state table ------
    config_table = Table(title="Configuration")
    config_table.add_column("Setting", style="bold cyan")
    config_table.add_column("Value")
    config_table.add_row("Config file", str(CONFIG_FILE))
    config_table.add_row("Interval", configured_interval)
    config_table.add_row("Sources", str(len(sources)))
    console.print(config_table)

    # ------ Sources table ------
    if sources:
        src_table = Table(title="Sources")
        src_table.add_column("Name", style="bold")
        src_table.add_column("Type")
        src_table.add_column("Details")
        for src in sources:
            detail = f"{src.get('host', '')}:{src.get('port', 21)}{src.get('path', '/')}"
            src_table.add_row(src["name"], src["type"], detail)
        console.print(src_table)

    # ------ Wallpaper state table ------
    wp_table = Table(title="Wallpaper State")
    wp_table.add_column("Key", style="bold cyan")
    wp_table.add_column("Value", no_wrap=False)
    wp_table.add_row("Active folder (config)", str(ACTIVE_DIR))
    wp_table.add_row(
        "Active folder (actual plist)",
        str(actual_folder) if actual_folder else "[dim]unknown[/dim]",
    )
    sync_status = "[green]in sync[/green]" if plist_matches else "[yellow](out of sync — run apply)[/yellow]"
    wp_table.add_row("Sync status", sync_status)
    console.print(wp_table)

    # ------ Index state table ------
    idx_table = Table(title="Index State")
    idx_table.add_column("Key", style="bold cyan")
    idx_table.add_column("Value")
    idx_table.add_row("Indexed", str(counts["indexed"]))
    idx_table.add_row("Cached", str(counts["cached"]))
    idx_table.add_row("Pending download", str(counts["downloading"]))

    if last_run:
        idx_table.add_row("Last pull source", last_run["source"] or "all")
        idx_table.add_row("Last pull status", last_run["status"])
        idx_table.add_row("Last pull started", last_run["started_at"])
        idx_table.add_row(
            "Last pull finished",
            last_run["finished_at"] or "[dim]—[/dim]",
        )
    if running_run:
        idx_table.add_row(
            "In progress",
            f"[bold yellow]YES[/bold yellow] (source={running_run['source'] or 'all'})",
        )

    console.print(idx_table)


# ---------------------------------------------------------------------------
# reset
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--purge", is_flag=True, default=False, help="Also delete all stored files and active snapshots")
def reset(purge: bool) -> None:
    """Restore pre-livewall wallpaper from backup and stop rotation."""
    desktop.reset_wallpaper()
    console.print("[green]Wallpaper reset.[/green]")

    if purge:
        cache.purge_all()
        # Also wipe the index
        if INDEX_FILE.exists():
            INDEX_FILE.unlink()
        if ORIGINAL_PLIST.exists():
            ORIGINAL_PLIST.unlink()
        console.print("[red]Store and index purged.[/red]")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    cli()


if __name__ == "__main__":
    main()
