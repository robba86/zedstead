"""zsm backup — backup & restore server persistence data."""

import json
from pathlib import Path

import click
from rich.console import Console

from app.ops.backup_ops import (
    BackupContents,
    BackupInfo,
    BackupMetadata,
    FullBackupInfo,
    FullBackupMetadata,
    FullBackupOptions,
    create_backup,
    create_full_backup,
    format_backup_label,
    format_full_backup_label,
    get_backup_dir,
    list_backups,
    list_full_backups,
    restore_backup,
    restore_full_backup,
)
from app.shared.config import DayzConfig
from app.shared.cli_ui import print_error

console = Console()


def _auto_push(config: DayzConfig, zip_path: Path) -> None:
    """Copy a just-made backup offsite when a target is configured.

    Never raises — see ``offsite_ops.auto_push``: the backup on disk succeeded
    and a remote being down must not report otherwise. The error still prints.
    """
    from app.ops import offsite_ops

    result = offsite_ops.auto_push(
        config, zip_path, log=lambda line: console.print(line, markup=False)
    )
    if result["error"]:
        print_error(f"Offsite upload failed: {result['error']}")
    elif result["uploaded"]:
        console.print(f"[green]✓ Copied offsite: {result['uploaded'][0]}[/green]")


def _print_full_backup_listing(config: DayzConfig) -> None:
    backups = list_full_backups(config)
    if not backups:
        console.print("[dim]No full backups found.[/dim]")
        return
    console.print(f"[bold]Full server backups ({len(backups)}):[/bold]")
    console.print()
    for b in backups:
        console.print(f"  {format_full_backup_label(b)}")
        console.print(f"    [dim]{b.zip_path}[/dim]")


def _print_quick_backup_listing(config: DayzConfig) -> None:
    backups = list_backups(config)
    if not backups:
        console.print("[dim]No quick backups found.[/dim]")
        return
    console.print(f"[bold]Quick backups ({len(backups)}):[/bold]")
    console.print()
    for b in backups:
        console.print(f"  {format_backup_label(b)}")
        console.print(f"    [dim]{b.zip_path}[/dim]")


@click.group(name="backup")
@click.pass_context
def backup_group(ctx):  # noqa: ARG001
    """Backup and restore server persistence data."""
    pass


@backup_group.command(name="full-create")
@click.option("--include-mods", is_flag=True, default=False, help="Include @ModName/ directories")
@click.option("--include-logs", is_flag=True, default=False, help="Include log files")
@click.option("--note", default="", help="Optional note for the backup")
@click.pass_context
def full_backup_create_cmd(ctx, include_mods, include_logs, note):  # noqa: ARG001
    """Create a full server directory backup."""
    config = DayzConfig.load()
    options = FullBackupOptions(include_mods=include_mods, include_logs=include_logs, note=note)
    zip_path = create_full_backup(
        config, options,
        log=lambda msg: console.print(msg),
    )
    if zip_path:
        console.print()
        console.print(f"[green]✓ Full backup created: {zip_path.name}[/green]")
        _auto_push(config, zip_path)
    else:
        console.print("[yellow]No backup created.[/yellow]")


@backup_group.command(name="full-restore")
@click.argument("backup_file", required=False)
@click.pass_context
def full_backup_restore_cmd(ctx, backup_file):
    """Restore from a full server backup. BACKUP_FILE is the path to the .zip."""
    config = DayzConfig.load()

    if not backup_file:
        console.print("[yellow]No backup specified. Available full backups:[/yellow]")
        console.print()
        _print_full_backup_listing(config)
        ctx.exit(1)

    zip_path = Path(backup_file)
    if not zip_path.is_file():
        print_error(f"File not found: {backup_file}")
        ctx.exit(1)

    meta_path = Path(str(zip_path).replace(".zip", ".meta.json"))
    if meta_path.is_file():
        meta_data = json.loads(meta_path.read_text(encoding="utf-8"))
        metadata = FullBackupMetadata(
            timestamp=meta_data["timestamp"],
            server_dir=meta_data["server_dir"],
            include_mods=meta_data.get("include_mods", False),
            include_logs=meta_data.get("include_logs", False),
            file_count=meta_data.get("file_count", 0),
            total_size=meta_data.get("total_size", 0),
            compressed_size=meta_data.get("compressed_size", 0),
            note=meta_data.get("note", ""),
        )
    else:
        metadata = FullBackupMetadata(
            timestamp="", server_dir="", include_mods=False, include_logs=False,
            file_count=0, total_size=0, compressed_size=0,
        )

    backup = FullBackupInfo(zip_path=zip_path, meta_path=meta_path, metadata=metadata)
    count = restore_full_backup(
        config, backup,
        log=lambda msg: console.print(msg),
    )
    console.print()
    console.print(f"[green]✓ Restored {count} file(s).[/green]")


@backup_group.command(name="create")
@click.option("--players/--no-players", default=True, help="Include players.db")
@click.option("--vehicles/--no-vehicles", default=True, help="Include vehicles.bin")
@click.option("--world/--no-world", default=True, help="Include world data")
@click.option(
    "--mod-data/--no-mod-data", default=True,
    help="Include mod data (Expansion, CF, TraderPlus bank, garage and stock)",
)
@click.option("--all", "include_all", is_flag=True, help="Include everything")
@click.option("--note", default="", help="Optional note for the backup")
@click.pass_context
def backup_create_cmd(ctx, players, vehicles, world, mod_data, include_all, note):  # noqa: ARG001
    """Create a backup of server persistence data."""
    config = DayzConfig.load()

    if include_all:
        players = vehicles = world = mod_data = True

    contents = BackupContents(
        players=players, vehicles=vehicles, world=world, mod_data=mod_data
    )
    zip_path = create_backup(
        config, contents, note=note,
        log=lambda msg: console.print(msg),
    )
    if zip_path:
        console.print()
        console.print(f"[green]✓ Backup created: {zip_path.name}[/green]")
        _auto_push(config, zip_path)
    else:
        console.print("[yellow]No backup created.[/yellow]")


@backup_group.command(name="list")
@click.pass_context
def backup_list_cmd(ctx):  # noqa: ARG001
    """List available backups."""
    config = DayzConfig.load()
    backup_dir = get_backup_dir(config)
    console.print(f"[dim]Backup dir: {backup_dir}[/dim]")
    console.print()
    _print_full_backup_listing(config)
    console.print()
    _print_quick_backup_listing(config)


@backup_group.command(name="restore")
@click.argument("backup_file", required=False)
@click.option("--players/--no-players", default=True, help="Restore players.db")
@click.option("--vehicles/--no-vehicles", default=True, help="Restore vehicles.bin")
@click.option("--world/--no-world", default=True, help="Restore world data")
@click.option(
    "--mod-data/--no-mod-data", default=True,
    help="Restore mod data (Expansion, CF, TraderPlus bank, garage and stock)",
)
@click.pass_context
def backup_restore_cmd(ctx, backup_file, players, vehicles, world, mod_data):
    """Restore from a quick backup. BACKUP_FILE is the path to the .zip."""
    config = DayzConfig.load()

    if not backup_file:
        console.print("[yellow]No backup specified. Available quick backups:[/yellow]")
        console.print()
        _print_quick_backup_listing(config)
        ctx.exit(1)

    zip_path = Path(backup_file)
    if not zip_path.is_file():
        print_error(f"File not found: {backup_file}")
        ctx.exit(1)

    meta_path = zip_path.with_suffix("").with_suffix(".meta.json")
    if not meta_path.is_file():
        meta_path = Path(str(zip_path).replace(".zip", ".meta.json"))

    if meta_path.is_file():
        meta_data = json.loads(meta_path.read_text(encoding="utf-8"))
        metadata = BackupMetadata(
            timestamp=meta_data["timestamp"],
            mission=meta_data["mission"],
            map_name=meta_data["map_name"],
            contents=meta_data["contents"],
            file_sizes=meta_data.get("file_sizes", {}),
            total_size=meta_data.get("total_size", 0),
            note=meta_data.get("note", ""),
        )
    else:
        metadata = BackupMetadata(
            timestamp="", mission="unknown", map_name="unknown",
            contents={"players": True, "vehicles": True, "world": True},
            file_sizes={}, total_size=0,
        )

    # The same refusal the web route makes: a running server writes its
    # persistence back on shutdown, over whatever was just restored.
    from app.server import service_control

    if service_control.is_server_running(config):
        print_error("Server is running. Stop it first.")
        ctx.exit(1)

    backup = BackupInfo(zip_path=zip_path, meta_path=meta_path, metadata=metadata)
    contents = BackupContents(
        players=players, vehicles=vehicles, world=world, mod_data=mod_data
    )

    count = restore_backup(
        config, backup, contents,
        log=lambda msg: console.print(msg),
    )
    console.print()
    console.print(f"[green]✓ Restored {count} file(s).[/green]")


# ---------------------------------------------------------------------------
# Offsite target (TODO C1) — the transport lives in app/ops/offsite_ops.py
# ---------------------------------------------------------------------------


@backup_group.group(name="remote")
@click.pass_context
def backup_remote_group(ctx):  # noqa: ARG001
    """Configure where backups are copied offsite."""
    pass


@backup_remote_group.command(name="status")
@click.pass_context
def backup_remote_status_cmd(ctx):  # noqa: ARG001
    """Show the configured offsite target."""
    from app.ops import offsite_ops

    config = DayzConfig.load()
    status = offsite_ops.remote_status(config)
    if not status["configured"]:
        console.print("[yellow]No offsite target configured.[/yellow]")
        console.print("[dim]Set one up with `zsm backup remote set --help`.[/dim]")
    else:
        console.print(f"[bold]Kind:[/bold] {status['kind']}")
        console.print(f"[bold]Target:[/bold] {offsite_ops.remote_target(config)}")
        console.print(
            f"[bold]Auto-upload:[/bold] {'on' if status['auto_upload'] else 'off'}"
        )
        host_key = {
            "confirmed": "confirmed",
            "unconfirmed": "not confirmed yet, so nothing is uploaded "
            "(run `zsm backup remote test`)",
            "unchecked": "not checked, set up before Zedstead checked host keys "
            "(run `zsm backup remote test`)",
        }.get(status["host_key"])
        if host_key:
            console.print(f"[bold]Host key:[/bold] {host_key}")
    binary =status["binary"] or "not installed (downloaded on first use)"
    console.print(f"[dim]rclone: {binary}[/dim]")
    console.print(f"[dim]Credentials: {status['config_file']}[/dim]")


@backup_remote_group.command(name="set")
@click.option(
    "--kind",
    type=click.Choice(["sftp", "s3", "b2"]),
    required=True,
    help="Remote type.",
)
@click.option("--path", default="", help="Directory (or bucket/prefix) to upload into.")
@click.option("--host", default="", help="SFTP host, or the S3 endpoint.")
@click.option("--user", default="", help="SFTP user.")
@click.option("--port", default="", help="SFTP port (default 22).")
@click.option(
    "--key-file",
    default="",
    help="SFTP private key. With none stored, omitting it means ssh-agent/~/.ssh.",
)
@click.option(
    "--password",
    default="",
    envvar="ZSM_REMOTE_PASSWORD",
    help="SFTP password. Prefer a key — a flag value lands in your shell history. "
    "Reads ZSM_REMOTE_PASSWORD when not given.",
)
@click.option("--access-key-id", default="", envvar="ZSM_REMOTE_ACCESS_KEY", help="S3 access key id.")
@click.option(
    "--secret-access-key",
    default="",
    envvar="ZSM_REMOTE_SECRET_KEY",
    help="S3 secret access key.",
)
@click.option("--region", default="", help="S3 region.")
@click.option(
    "--provider",
    default="",
    help="S3 provider name (rclone's own list). Other when the target is new.",
)
@click.option("--account", default="", envvar="ZSM_REMOTE_ACCOUNT", help="B2 account id.")
@click.option("--key", default="", envvar="ZSM_REMOTE_KEY", help="B2 application key.")
@click.option("--auto/--no-auto", default=True, help="Upload after every backup.")
@click.option(
    "--clear",
    "clear",
    multiple=True,
    metavar="FIELD",
    help="Remove a stored field (rclone's key name, e.g. key_file or pass). Repeatable.",
)
@click.pass_context
def backup_remote_set_cmd(ctx, kind, path, auto, clear, **fields):  # noqa: ARG001
    """Write the offsite target.

    Credentials go to rclone's config file (mode 0600), never to config.json.
    A field left out keeps its stored value while the kind stays the same;
    a different kind replaces the target.
    """
    from app.ops import offsite_ops
    from app.ops.offsite_ops import RcloneError

    config = DayzConfig.load()
    payload = {
        "host": fields["host"],
        "user": fields["user"],
        "port": fields["port"],
        "key_file": fields["key_file"],
        "pass": fields["password"],
        "access_key_id": fields["access_key_id"],
        "secret_access_key": fields["secret_access_key"],
        "endpoint": fields["host"],
        "region": fields["region"],
        # "Other" only for a new S3 target, so a re-run keeps a stored one.
        "provider": fields["provider"]
        or ("" if offsite_ops.read_remote_kind() == kind else "Other"),
        "account": fields["account"],
        "key": fields["key"],
    }
    try:
        conf_path = offsite_ops.write_remote(config, kind, payload, clear=clear)
    except RcloneError as e:
        print_error(str(e))
        raise SystemExit(1)

    config.backup_remote_kind = kind
    config.backup_remote_path = path
    config.backup_remote_auto = auto
    config.save()

    console.print(f"[green]✓ Offsite target written to {conf_path}[/green]")
    console.print(f"[dim]Target: {offsite_ops.remote_target(config)}[/dim]")
    warning = offsite_ops.endpoint_warning()
    if warning:
        console.print(warning, style="yellow", markup=False)
    if kind == "sftp":
        console.print(
            "[dim]Run `zsm backup remote test` next: it shows the host's key "
            "fingerprint to confirm, and nothing is uploaded until you do.[/dim]"
        )
    else:
        console.print("[dim]Check it with `zsm backup remote test`.[/dim]")


@backup_remote_group.command(name="clear")
@click.pass_context
def backup_remote_clear_cmd(ctx):  # noqa: ARG001
    """Remove the offsite target and its stored credentials."""
    from app.ops import offsite_ops

    config = DayzConfig.load()
    removed = offsite_ops.clear_remote()
    config.backup_remote_kind = ""
    config.save()
    if removed:
        console.print("[green]✓ Offsite target removed.[/green]")
    else:
        console.print("[yellow]No offsite target was configured.[/yellow]")


@backup_remote_group.command(name="test")
@click.option(
    "--accept-host-key",
    "accept",
    multiple=True,
    metavar="SHA256:...",
    help="Confirm the SFTP host key with this fingerprint without a prompt. "
    "Repeat it for every key the host offers. Compare it with the server's own first.",
)
@click.pass_context
def backup_remote_test_cmd(ctx, accept):
    """Connect to the offsite target and list it.

    On the first connection to an SFTP host, shows the host's key fingerprint
    and saves it only once you confirm it. A key that later differs is refused.
    """
    from rich.markup import escape

    from app.ops import offsite_ops
    from app.ops.offsite_ops import RcloneError
    from app.shared.cli_ui import confirm

    config = DayzConfig.load()
    non_interactive = bool(ctx.obj and ctx.obj.get("non_interactive", False))

    def log(line: str) -> None:
        console.print(line, markup=False, style="dim")

    def show_keys(title: str, keys: list[dict]) -> None:
        console.print(title, markup=False)
        for key in keys:
            console.print(f"  {key['type']}  {key['fingerprint']}", markup=False)

    result = offsite_ops.probe_remote(config, log=log)
    host_key = result.get("host_key") or {}

    if host_key.get("state") == "unknown":
        console.print(host_key["message"], markup=False)
        show_keys(f"{host_key['host']} offers:", host_key["keys"])
        offered = [k["fingerprint"] for k in host_key["keys"]]
        if accept:
            fingerprints = list(accept)
        elif confirm(
            "Is this the server's key? Save it and trust it from now on?",
            default=False,
            non_interactive=non_interactive,
        ):
            fingerprints = offered
        else:
            print_error(
                "Host key not confirmed. Nothing was saved, and nothing is uploaded "
                "to this host until it is. Without a terminal, pass the fingerprint "
                "with --accept-host-key."
            )
            raise SystemExit(1)
        try:
            offsite_ops.trust_host_keys(fingerprints)
        except RcloneError as e:
            print_error(escape(str(e)))
            raise SystemExit(1)
        console.print(
            f"Host key saved to {offsite_ops.known_hosts_path()}", markup=False, style="green"
        )
        result = offsite_ops.probe_remote(config, log=log)
        host_key = result.get("host_key") or {}

    if host_key.get("state") == "changed":
        print_error(escape(result["message"]))
        show_keys("The host offers now:", host_key["keys"])
        show_keys("You confirmed:", host_key["confirmed"])
        raise SystemExit(1)
    if result["ok"]:
        console.print(f"✓ {result['message']}", markup=False, style="green")
    else:
        print_error(escape(result["message"]))
        raise SystemExit(1)


@backup_group.command(name="push")
@click.argument("backup_file", required=False)
@click.pass_context
def backup_push_cmd(ctx, backup_file):  # noqa: ARG001
    """Upload a backup to the offsite target (newest one if not named)."""
    from app.ops import offsite_ops
    from app.ops.offsite_ops import RcloneError

    config = DayzConfig.load()
    path = _resolve_backup_zip(config, backup_file)
    if path is None:
        raise SystemExit(1)

    try:
        destinations = offsite_ops.push_backup(
            config, path, log=lambda line: console.print(line, markup=False)
        )
    except RcloneError as e:
        print_error(str(e))
        raise SystemExit(1)
    console.print(f"[green]✓ Uploaded {len(destinations)} file(s).[/green]")


def _resolve_backup_zip(config: DayzConfig, name: str | None) -> Path | None:
    """A backup zip by name (with or without .zip), or the newest one.

    Matched against the real listing rather than joined onto the backup dir —
    same rule as the web download route.
    """
    candidates = [b.zip_path for b in list_backups(config)]
    candidates += [b.zip_path for b in list_full_backups(config)]
    if not candidates:
        print_error("No backups found.")
        return None
    if not name:
        return candidates[0]

    wanted = name[:-4] if name.endswith(".zip") else name
    for path in candidates:
        if path.stem == wanted:
            return path
    print_error(f"No backup named {name!r}. Run `zsm backup list`.")
    return None
