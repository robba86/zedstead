"""zsm info — show config, disk space, mod status."""

import shutil
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from app.shared.config import DayzConfig
from app.modmgr import modlist
from app.shared.steam import is_mod_downloaded

console = Console()


def _dir_size(path: Path) -> float:
    """Return directory size in GB."""
    total = 0
    if path.is_dir():
        for f in path.rglob("*"):
            if f.is_file():
                total += f.stat().st_size
    return total / (1024 ** 3)


@click.command()
@click.pass_context
def info(ctx):
    """Show config, disk space, and mod status."""
    config = DayzConfig.load()

    # Config table
    cfg_table = Table(title="Configuration", show_header=False, expand=False)
    cfg_table.add_column("Key", style="cyan")
    cfg_table.add_column("Value")
    cfg_table.add_row("Config file", str(config.paths.config_file))
    cfg_table.add_row("Server dir", config.server_dir)
    cfg_table.add_row("SteamCMD dir", config.steamcmd_dir)
    cfg_table.add_row("Workshop dir", str(config.workshop_dir))
    cfg_table.add_row("Steam user", config.steam_username)
    cfg_table.add_row("Server port", str(config.server_port))
    cfg_table.add_row("Config path", config.config_path)
    console.print(cfg_table)
    console.print()

    # Disk space
    server_size = _dir_size(config.server_dir_path)
    disk = shutil.disk_usage(config.server_dir_path if config.server_dir_path.exists() else Path.home())
    free_gb = disk.free / (1024 ** 3)
    total_gb = disk.total / (1024 ** 3)

    disk_lines = [
        f"DayZ Server:  {server_size:.1f} GB used",
        f"Partition:    {free_gb:.0f} GB free / {total_gb:.0f} GB total",
        f"Recommended:  60+ GB free (30 server + 20 mods + headroom)",
    ]
    console.print(Panel("\n".join(disk_lines), title="Disk Space", expand=False))
    console.print()

    # Mod status
    mod_list = modlist.installed(config)
    cached_ids = modlist.workshop_ids(config)

    mod_table = Table(title=f"Mods ({len(mod_list)} total)", expand=False)
    mod_table.add_column("Mod", style="cyan")
    mod_table.add_column("Workshop ID")
    mod_table.add_column("Downloaded")

    for mod in mod_list:
        mod_id = cached_ids.get(mod.lstrip("@"), cached_ids.get(mod, ""))
        if mod_id:
            downloaded = is_mod_downloaded(config, mod_id)
            status = "[green]Yes[/green]" if downloaded else "[red]No[/red]"
        else:
            status = "[yellow]No ID[/yellow]"
        mod_table.add_row(mod, mod_id or "-", status)

    console.print(mod_table)
