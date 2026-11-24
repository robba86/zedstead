"""zsm container-init — sync config from env for the Docker/systemd bootstrap.

Hidden command. The container's systemd bootstrap unit runs this first so the
config reflects the compose ``.env`` before the server is prepped and started,
while preserving anything set through the web UI or the CLI on earlier boots.
"""

from __future__ import annotations

import click
from rich.console import Console

from app.shared.container import (
    sync_battleye_cfg,
    sync_config_from_env,
    sync_server_cfg,
)

console = Console()


@click.command(name="container-init", hidden=True)
@click.pass_context
def container_init(ctx) -> None:
    """Create-or-merge zsm config from container env vars (Docker bootstrap).

    Idempotent. Only the env-mapped server settings are overwritten; scheduled
    tasks, mod load order, Discord and restart-warning settings configured in
    the web UI survive a container recreate.
    """
    notes: list[str] = []
    config, created, changed = sync_config_from_env(notes=notes)
    verb = "Created" if created else "Updated"
    if changed:
        console.print(f"[green]{verb} config[/] — set: {', '.join(changed)}")
    else:
        console.print(f"[green]{verb} config[/] — no env overrides applied")
    # Never the password itself: this output is the container log.
    for note in notes:
        console.print(note, markup=False, highlight=False, soft_wrap=True)


@click.command(name="sync-server-cfg", hidden=True)
@click.pass_context
def sync_server_cfg_cmd(ctx) -> None:
    """Apply .env-driven values into the server's own config files.

    Writes serverDZ.cfg `hostname`/`steamQueryPort` (from SERVER_NAME /
    STEAM_QUERY_PORT) and beserver_x64.cfg `RConPassword`/`RConPort` (from
    RCON_PASSWORD/RCON_PORT) — values that live in the server files, not just
    zsm's config.json. Called by the container bootstrap after `zsm setup`.
    No-op for anything not yet installed.
    """
    from app.shared.config import DayzConfig

    config = DayzConfig.load_or_none()
    if config is None:
        console.print("[yellow]No config yet — skipping server cfg sync.[/]")
        return
    notes: list[str] = []
    written = sync_server_cfg(config, notes)
    rcon_port = sync_battleye_cfg(config, notes)
    for key, value in written.items():
        console.print(f"[green]serverDZ.cfg {key}[/] → {value!r}")
    if rcon_port is not None:
        console.print(f"[green]beserver_x64.cfg RCon[/] → port {rcon_port}")
    # A value the writers refused; never the value itself (the container log).
    for note in notes:
        console.print(note, markup=False, highlight=False, soft_wrap=True, style="yellow")
    if not written and rcon_port is None and not notes:
        console.print("[dim]server cfg files already in sync (or not installed).[/]")
