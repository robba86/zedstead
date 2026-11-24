"""zsm cleanup — server maintenance and cleanup."""

import shutil

import click
from rich.console import Console

from app.ops.backup_ops import BackupContents, create_backup
from app.ops.cleanup_ops import (
    backup_players_db,
    calculate_dirs_size,
    clear_mission_dir,
    copy_players_db,
    delete_backup_files,
    find_leftover_mod_data,
    find_log_files,
    find_storage,
    find_xml_backups,
    get_players_db_path,
    list_resettable_missions,
    list_workshop_mods,
    remove_mod_data_dirs,
    remove_workshop_mods,
    repair_workshop_manifest,
    remerge_reset_mission,
    reset_mission_zip,
    rmtree_mount_safe,
    storage_name,
)
from app.shared.config import DayzConfig
from app.shared.platform import IS_WINDOWS
from app.shared.steam import install_steamcmd, update_dayz_server
from app.shared.cli_ui import confirm, print_error, print_success, print_warning
from app.modmgr import modlist

console = Console()


def _ni(ctx) -> bool:
    """Pull non_interactive flag from the click context."""
    return bool(ctx.obj.get("non_interactive", False)) if ctx.obj else False


@click.group(name="cleanup")
@click.pass_context
def cleanup_group(ctx):  # noqa: ARG001
    """Server maintenance and cleanup."""
    pass


@cleanup_group.command(name="wipe-server")
@click.option("--no-backup", is_flag=True, default=False, help="Skip the pre-wipe persistence backup.")
@click.option(
    "--restart", is_flag=True, default=False,
    help="Stop a running server first and start it again afterwards, also when the wipe fails. "
    "The scheduled wipe runs this.",
)
@click.pass_context
def wipe_server(ctx, no_backup, restart):
    """Delete the persistence data (storage_<instanceId>) of the active mission."""
    config = DayzConfig.load()
    ni = _ni(ctx)

    # A running DayZServer writes its in-memory world back over the deleted
    # folder, or crashes, on every platform.
    if not restart:
        from app.commands.service import ensure_server_stopped
        if not ensure_server_stopped(config):
            ctx.exit(1)

    info = find_storage(config)
    if not info:
        print_error(f"Could not determine current mission or no {storage_name(config)} found.")
        return

    console.print(f"[dim]Mission: {info.mission}[/dim]")
    console.print(f"[dim]Path:    {info.path}[/dim]")

    if not confirm(f"Delete {info.path.name} for {info.mission}?", default=True, non_interactive=ni):
        return

    wiped = False

    def _wipe() -> None:
        nonlocal wiped
        if not no_backup:
            pre_contents = BackupContents(players=True, vehicles=True, world=True)
            zip_path = create_backup(
                config, pre_contents, note="pre-wipe auto-backup",
                log=lambda msg: console.print(msg),
            )
            if zip_path:
                console.print(f"  Backed up to {zip_path.name}")
        shutil.rmtree(info.path)
        console.print(f"  Deleted {info.path.name}/")
        wiped = True

    problem = None
    if restart:
        import asyncio

        from app.ops.update_cycle import run_while_stopped

        problem = asyncio.run(run_while_stopped(config, _wipe, emit=console.print))
    else:
        _wipe()

    # Announced even when the server did not come back: the wipe happened.
    if wiped:
        print_success("Server wipe complete.")
        try:
            from app.monitoring.discord_hooks import notify_wipe
            notify_wipe(config, mission=info.mission)
        except Exception:
            pass
    if problem:
        raise click.ClickException(problem[0].upper() + problem[1:] + ".")


@cleanup_group.command(name="clear-logs")
@click.pass_context
def clear_logs(ctx):
    """Delete log/RPT/mdmp files from the profiles directory."""
    config = DayzConfig.load()
    ni = _ni(ctx)

    info = find_log_files(config)
    if info is None:
        print_warning("Profiles directory not found.")
        return

    if not info.files:
        console.print("[dim]No log files found.[/dim]")
        return

    console.print(f"Found [cyan]{len(info.files)}[/cyan] log file(s) in {info.profiles_dir}")
    console.print(f"[dim]Total size: {info.total_size / 1024 / 1024:.1f} MB[/dim]")

    if not confirm("Delete all log files?", default=True, non_interactive=ni):
        return

    from app.ops.cleanup_ops import clear_log_files
    from app.server.service_control import is_server_process_alive

    deleted, kept = clear_log_files(info.files, running=is_server_process_alive(config))
    print_success(f"Deleted {deleted} log file(s).")
    if kept:
        console.print(f"[dim]Kept {kept}: the server is writing them, or they could not be deleted.[/dim]")


@cleanup_group.command(name="remove-mods")
@click.argument("workshop_ids", nargs=-1)
@click.option("--all", "all_mods", is_flag=True, help="Remove every downloaded workshop mod.")
@click.pass_context
def remove_mods(ctx, workshop_ids, all_mods):
    """Remove downloaded workshop mods. Pass workshop IDs or --all."""
    config = DayzConfig.load()
    ni = _ni(ctx)

    if not workshop_ids and not all_mods:
        raise click.UsageError("Provide one or more workshop IDs, or --all.")

    mods = list_workshop_mods(config)
    if mods is None:
        console.print("[dim]Workshop directory not found.[/dim]")
        return
    if not mods:
        console.print("[dim]No workshop mods found.[/dim]")
        return

    if all_mods:
        to_remove = [m.path for m in mods]
    else:
        wanted = set(workshop_ids)
        matched = [m for m in mods if m.workshop_id in wanted]
        missing = wanted - {m.workshop_id for m in matched}
        for mid in missing:
            console.print(f"[yellow]Workshop ID {mid} not found in workshop dir — skipping[/yellow]")
        if not matched:
            return
        to_remove = [m.path for m in matched]

    total_size = calculate_dirs_size(to_remove)
    console.print(f"[dim]Selected {len(to_remove)} mod(s) — {total_size / 1024 / 1024:.0f} MB[/dim]")

    if not confirm(f"Remove {len(to_remove)} workshop mod(s)?", default=True, non_interactive=ni):
        return

    remove_workshop_mods(config, to_remove, log=lambda msg: console.print(f"[dim]{msg}[/dim]"))
    print_success(f"Removed {len(to_remove)} workshop mod(s).")


@cleanup_group.command(name="mod-data")
@click.argument("names", nargs=-1)
@click.option("--all", "all_data", is_flag=True, help="Remove every leftover mod data dir.")
@click.pass_context
def mod_data(ctx, names, all_data):
    """List or delete profiles data left by uninstalled mods.

    Uninstalling a mod keeps its `<profiles>/<Name>/` directory — TraderPlus
    stock/banking, garage vehicles and the like live there, and must survive
    an uninstall/reinstall. With no arguments this only lists them.
    """
    config = DayzConfig.load()
    ni = _ni(ctx)

    leftover = find_leftover_mod_data(config)
    if not leftover:
        console.print("[dim]No leftover mod data found.[/dim]")
        return

    for entry in leftover:
        size_mb = entry.size / 1024 / 1024
        console.print(
            f"  {entry.name} — {entry.files} file(s), {size_mb:.1f} MB "
            f"[dim](from {entry.mod}: {entry.path})[/dim]"
        )

    if not names and not all_data:
        console.print("[dim]Pass a name or --all to delete.[/dim]")
        return

    if all_data:
        to_remove = [e.name for e in leftover]
    else:
        known = {e.name.casefold(): e.name for e in leftover}
        to_remove = []
        for raw in names:
            match = known.get(raw.lstrip("@").casefold())
            if match is None:
                print_warning(f"{raw} is not a leftover mod data dir — skipping")
                continue
            to_remove.append(match)
        if not to_remove:
            return

    if not confirm(f"Delete {len(to_remove)} mod data dir(s)?", non_interactive=ni):
        return

    removed = remove_mod_data_dirs(config, to_remove, log=lambda msg: console.print(f"[dim]{msg}[/dim]"))
    print_success(f"Deleted {len(removed)} mod data dir(s).")


@cleanup_group.command(name="repair-workshop")
def repair_workshop():
    """Drop stale entries from SteamCMD's workshop manifest.

    Fixes `ERROR! Download item <id> failed (Failure).` caused by an
    appworkshop_221100.acf that still lists mods whose folders are gone.
    """
    config = DayzConfig.load()
    removed = repair_workshop_manifest(config, log=lambda msg: console.print(msg))
    if removed:
        print_success(f"Repaired workshop manifest ({len(removed)} stale entry(s) removed).")


@cleanup_group.command(name="reinstall-server")
@click.pass_context
def reinstall_server(ctx):
    """Delete the entire server directory and reinstall via SteamCMD."""
    config = DayzConfig.load()
    ni = _ni(ctx)

    # A running server holds the files open on Windows and keeps the old ones
    # in memory on Linux, as the web route's refusal says.
    from app.commands.service import ensure_server_stopped
    if not ensure_server_stopped(config):
        ctx.exit(1)

    server_dir = config.server_dir_path
    if not server_dir.is_dir():
        print_warning(f"Server directory not found: {server_dir}")
        return
    # The web route's guard: never delete a dir that holds Zedstead's config,
    # another server or the home folder, or one that is no DayZ server.
    from app.ops.cleanup_ops import reinstall_refusal

    refusal = reinstall_refusal(config)
    if refusal:
        raise click.ClickException(refusal)
    # Asked before the delete: a username SteamCMD would refuse leaves no server.
    from app.shared.steam import SteamcmdRefused, steamcmd_username

    try:
        steamcmd_username(config)
    except SteamcmdRefused as e:
        raise click.ClickException(str(e)) from e

    print_warning("This will delete the entire server directory and reinstall.")
    console.print("[dim]You will lose: the mod list, serverDZ.cfg, mpmissions, and all server files.[/dim]")
    console.print(f"[dim]Path: {server_dir}[/dim]")

    if not confirm("Are you sure you want to reinstall the DayZ server?", default=True, non_interactive=ni):
        return
    if not confirm("⚠︎ This is permanent! Confirm?", default=True, non_interactive=ni):
        return

    console.print("[bold]Removing server directory...[/bold]")
    rmtree_mount_safe(server_dir)
    console.print("[bold]Reinstalling DayZ server...[/bold]")
    update_dayz_server(config)
    from app.commands.setup import apply_query_port

    apply_query_port(config)

    # A reinstalled server has no mods, so the list starts empty again.
    modlist.clear(config)
    print_success("DayZ server reinstalled.")


@cleanup_group.command(name="reinstall-steamcmd")
@click.pass_context
def reinstall_steamcmd(ctx):
    """Delete and reinstall SteamCMD."""
    config = DayzConfig.load()
    ni = _ni(ctx)

    steamcmd_dir = config.steamcmd_path.parent
    if not steamcmd_dir.is_dir():
        print_warning(f"SteamCMD directory not found: {steamcmd_dir}")
        return

    console.print(f"[dim]Path: {steamcmd_dir}[/dim]")

    if not confirm("Remove and reinstall SteamCMD?", default=True, non_interactive=ni):
        return

    console.print("[bold]Removing SteamCMD...[/bold]")
    rmtree_mount_safe(steamcmd_dir)
    install_steamcmd(steamcmd_dir)


@cleanup_group.command(name="migrate-players")
@click.option("--from", "from_mission", required=True, help="Source mpmission folder name.")
@click.option("--to", "to_mission", required=True, help="Target mpmission folder name.")
@click.option("--overwrite", is_flag=True, default=False, help="Overwrite existing players.db on target.")
@click.option("--no-backup", is_flag=True, default=False, help="Skip backup of target players.db.")
@click.pass_context
def migrate_players(ctx, from_mission, to_mission, overwrite, no_backup):
    """Copy the storage folder's players.db between mpmissions.

    Every character keeps its position, so use it between missions on the
    same map: on another map, a player can wake up in the sea, under the
    ground or off the map.
    """
    config = DayzConfig.load()
    ni = _ni(ctx)

    from app.parsers.servercfg import get_available_missions
    available = get_available_missions(config.server_dir_path)
    for label, value in (("from", from_mission), ("to", to_mission)):
        if value not in available:
            raise click.UsageError(
                f"--{label}={value!r} is not a known mpmission. Available: {', '.join(available)}"
            )
    if from_mission == to_mission:
        raise click.UsageError("--from and --to must differ.")

    # The running server keeps players.db open and writes it back over the
    # copy, as the web route's refusal says.
    from app.commands.service import ensure_server_stopped
    if not ensure_server_stopped(config):
        ctx.exit(1)

    source_db = get_players_db_path(config, from_mission)
    if not source_db.exists():
        print_error(f"No players.db found at:\n  {source_db}")
        return

    size_kb = source_db.stat().st_size / 1024
    console.print(f"[dim]Found players.db ({size_kb:.1f} KB) in {from_mission}[/dim]")

    target_db = get_players_db_path(config, to_mission)
    if target_db.exists():
        target_size_kb = target_db.stat().st_size / 1024
        console.print(f"[yellow]Target already has players.db ({target_size_kb:.1f} KB)[/yellow]")
        if not overwrite:
            if not confirm("Overwrite target players.db?", default=True, non_interactive=ni):
                return
        if not no_backup:
            backup = backup_players_db(target_db)
            console.print(f"  Backed up to {backup.name}")

    copy_players_db(source_db, target_db)
    print_success(f"Migrated players.db: {from_mission} → {to_mission}")


@cleanup_group.command(name="reset-mission")
@click.option("--mission", required=True, help="Mission folder name (mpmissions/<mission>).")
@click.option(
    "--preserve-storage/--no-preserve-storage",
    default=True,
    help="Keep the storage folders (player data) when resetting.",
)
@click.pass_context
def reset_mission(ctx, mission, preserve_storage):
    """Reset an mpmission folder to its default state."""
    import subprocess
    from app.ops import mission_stock
    from app.shared import steamcmd_lock
    from app.shared.steam import SteamcmdRefused, build_server_update_cmd

    config = DayzConfig.load()
    ni = _ni(ctx)

    # A running server holds the files open on Windows and keeps the old ones
    # in memory on Linux, as the web route's refusal says.
    from app.commands.service import ensure_server_stopped
    if not ensure_server_stopped(config):
        ctx.exit(1)

    missions = list_resettable_missions(config)
    if not missions:
        print_warning("No resettable missions found in mpmissions/.")
        return

    matches = [m for m in missions if m.template == mission]
    if not matches:
        names = ", ".join(m.template for m in missions)
        raise click.UsageError(f"Mission {mission!r} not resettable. Available: {names}")
    target = matches[0]

    console.print(f"[dim]Mission:  {target.template}[/dim]")
    console.print(f"[dim]Map:      {target.map_name}[/dim]")
    console.print(f"[dim]Source:   {'GitHub ZIP' if target.source_type == 'zip' else 'SteamCMD validate'}[/dim]")

    if not confirm(
        f"Reset {target.template} to default? All XML configs will be replaced.",
        default=True, non_interactive=ni,
    ):
        return

    if not target.has_storage:
        preserve_storage = False

    if target.source_type == "zip":
        console.print(f"\n[bold]Resetting {target.template} from GitHub...[/bold]")
        try:
            count = reset_mission_zip(
                target.zip_url,
                target.mission_path,
                target.path,
                preserve_storage,
                log=lambda msg: console.print(msg),
            )
        except ValueError as e:
            raise click.ClickException(str(e))
        remerge_reset_mission(config, target.template, lambda msg: console.print(msg, markup=False))
        print_success(f"Reset {target.template} — {count} files extracted.")
    else:
        # Built before the clear: a refused build (the directory holds the other
        # branch's server, plans/66) must leave the mission as it was.
        try:
            cmd = build_server_update_cmd(config)
        except SteamcmdRefused as e:
            raise click.ClickException(str(e)) from e
        console.print(f"\n[bold]Clearing {target.template}...[/bold]")
        clear_mission_dir(target.path, preserve_storage)

        console.print("[bold]Validating via SteamCMD...[/bold]")
        # The validate resets the other stock missions too; keep their edits (plans/67).
        plain = lambda line: console.print(line, markup=False)  # noqa: E731
        stash = mission_stock.before_download(config, resetting=target.template, log=plain)
        with steamcmd_lock.hold(cmd, on_wait=console.print):
            result = subprocess.run(cmd, check=False)
        if result.returncode != 0:
            mission_stock.download_failed(config, stash, log=plain)
        else:
            mission_stock.after_download(config, stash, log=plain)
        print_success(f"Reset {target.template} via SteamCMD validate.")


@cleanup_group.command(name="clear-xml-backups")
@click.pass_context
def clear_xml_backups(ctx):
    """Delete .backup files from the active mission directory."""
    config = DayzConfig.load()
    ni = _ni(ctx)

    info = find_xml_backups(config)
    if info is None:
        print_error("Could not determine current mission from serverDZ.cfg.")
        return

    if not info.files:
        console.print("[dim]No XML backup files found.[/dim]")
        return

    console.print(f"Found [cyan]{len(info.files)}[/cyan] backup file(s) in {info.mission_dir.name}/")
    console.print(f"[dim]Total size: {info.total_size / 1024 / 1024:.1f} MB[/dim]")

    if not confirm("Delete all backup files?", default=True, non_interactive=ni):
        return

    delete_backup_files(info.files)
    print_success(f"Deleted {len(info.files)} backup file(s).")
