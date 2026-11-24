"""zsm mods — list, add, remove mods."""

import click
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from app.shared.config import DayzConfig
from app.modmgr.mod_ops import (
    ensure_at_prefix,
    get_missing_deps,
    get_mod_info_list,
    get_removable_deps,
    resolve_mod_workshop_id,
    resolve_names_to_ids,
    uninstall_mods,
)
from app.modmgr import modlist
from app.modmgr.mods import get_current_mission
from app.shared.platform import IS_WINDOWS
from app.shared.steam import is_mod_downloaded
from app.shared.cli_ui import confirm

console = Console()


@click.group(name="mods")
@click.pass_context
def mods_group(ctx):
    """Manage mods (list, add, remove)."""
    pass


def _show_mod_table(config):
    """Display the mod table. Returns the mod list."""
    mods = get_mod_info_list(config)
    disabled_set = modlist.disabled(config)

    table = Table(title=f"Installed Mods ({len(mods)})", expand=False)
    table.add_column("#", style="dim")
    table.add_column("Mod", style="cyan")
    table.add_column("Workshop ID")
    table.add_column("Downloaded")
    table.add_column("Enabled")

    for i, mod in enumerate(mods, 1):
        if mod.workshop_id:
            downloaded = is_mod_downloaded(config, mod.workshop_id)
            status = "[green]Yes[/green]" if downloaded else "[red]No[/red]"
        else:
            status = "[yellow]No ID[/yellow]"
        is_disabled = mod.name in disabled_set
        enabled = "[yellow]Disabled[/yellow]" if is_disabled else "[green]Yes[/green]"
        name_display = f"[dim]{escape(mod.name)}[/dim]" if is_disabled else escape(mod.name)
        table.add_row(str(i), name_display, mod.workshop_id or "-", status, enabled)

    console.print(table)
    return [m.name for m in mods]


def _mark_known_server_side(config: DayzConfig, names: list[str]) -> None:
    """Mark mods the catalog knows are server-only so they go to -serverMod=."""
    from app.modmgr.mod_ops import known_server_side
    from app.modmgr.mods import mark_server_side

    cached_ids = modlist.workshop_ids(config)
    changed = [n for n in known_server_side(names, cached_ids) if mark_server_side(config, n, True)]
    if changed:
        config.save()
        for n in changed:
            console.print(
                f"[cyan]{escape(n)} is a server-side mod: it loads through -serverMod=, so players "
                f"don't need it.[/cyan] [dim](undo: zsm mods server-side {escape(n)} --off)[/dim]"
            )


def _resolve_and_add_mod(config: DayzConfig, name: str) -> None:
    """Resolve workshop ID, add the mod to the mod list, and print next-step note.

    Assumes name already has @ prefix and is not already in the mod list.
    """
    mod_id, was_cached = resolve_mod_workshop_id(config, name)
    if mod_id:
        if was_cached:
            console.print(f"[green]Found cached workshop ID: {mod_id}[/green]")
        else:
            console.print(f"[green]Resolved workshop ID: {mod_id}[/green]")

    modlist.add(config, name, mod_id or "")
    console.print(f"[green]Added {escape(name)} to the mod list[/green]")
    _mark_known_server_side(config, [name])

    # Check for missing dependencies
    if mod_id:
        cached_ids = modlist.workshop_ids(config)
        installed_mods = modlist.installed(config)
        installed_ids = set(resolve_names_to_ids(installed_mods, cached_ids).values())
        missing = get_missing_deps([mod_id], installed_ids)
        if missing:
            dep_names = escape(", ".join(n for _, n in missing))
            console.print(f"\n[yellow]{escape(name)} requires: {dep_names}[/yellow]")
            if confirm(f"Also add these dependencies?"):
                for dep_id, dep_name in missing:
                    modlist.remember_id(config, dep_name, dep_id)
                    if modlist.add(config, dep_name):
                        console.print(f"  [green]Added {escape(dep_name)} to the mod list[/green]")
                    else:
                        console.print(f"  [dim]{escape(dep_name)} already in the mod list[/dim]")
            else:
                console.print(f"[yellow]Skipped dependencies. {escape(name)} may not work without them.[/yellow]")

    console.print()
    console.print("[dim]Note: The mod has been added to the mod list but is not yet downloaded.[/dim]")
    console.print('[dim]  → To download:           zsm mods download[/dim]')
    console.print('[dim]  → To copy + apply its XML: zsm mods copy[/dim]')
    console.print('[dim]  → Or run the full pipeline: zsm run all[/dim]')

    try:
        from app.monitoring.discord_hooks import notify_mods
        notify_mods(config, action="installed", mods=[name])
    except Exception:
        pass


def _download_mods(config: DayzConfig) -> None:
    """Download/update all mods via SteamCMD."""
    from app.modmgr import modlist
    from app.shared.steam import download_all_mods

    mod_list, cached_ids = modlist.installed_and_ids(config)
    if not mod_list:
        console.print("[dim]No mods in the mod list.[/dim]")
        return

    results = download_all_mods(config, mod_list, cached_ids)
    failed = sum(1 for s in results.values() if s == "failed")
    if failed:
        console.print(f"[red]Done with {failed} failure(s).[/red]")
    else:
        console.print("[green]All mods downloaded successfully.[/green]")


def _copy_mods_to_server(config: DayzConfig) -> None:
    """Copy mods + bikeys to server + apply their XML to the mission."""
    from app.ops.fileops import (
        remove_server_mods,
        copy_all_mods_to_server,
        copy_bikey_files,
    )
    from app.xml.xml_economy import merge_economy_core
    from app.xml.xml_merge import apply_failed_message, merge_all_direct_configs

    steps = [
        ("Removing old mod folders", lambda: remove_server_mods(config)),
        ("Copying mods to server directory", lambda: copy_all_mods_to_server(config)),
        ("Copying bikey files", lambda: copy_bikey_files(config)),
    ]

    failed = 0
    for label, func in steps:
        console.print(f"[dim]{label}...[/dim]")
        try:
            func()
            console.print(f"[green]  {label} — done.[/green]")
        except Exception as e:
            console.print(f"[red]  {label} — failed: {escape(str(e))}[/red]")
            failed += 1

    console.print("[dim]Applying mod XML...[/dim]")
    try:
        mission = get_current_mission(config.server_cfg_path)
        if not mission:
            console.print("[red]  Could not determine mission folder from serverDZ.cfg[/red]")
            failed += 1
        else:
            _disabled = modlist.disabled(config)
            manual_dir = config.paths.custom_xml_dir
            merge_economy_core(
                config.server_dir_path, mission, manual_dir=manual_dir, disabled_mods=_disabled,
            )
            # The shared merge, not a loop over merge_xml: that loop strips the
            # manual files' `@user:` blocks and never puts them back.
            merge_failed = merge_all_direct_configs(
                config.server_dir_path, mission,
                manual_dir=manual_dir, disabled_mods=_disabled,
            )
            if merge_failed:
                console.print(f"[red]  {escape(apply_failed_message(merge_failed))}[/red]")
                failed += 1
            else:
                console.print("[green]  Applying mod XML — done.[/green]")
    except Exception as e:
        console.print(f"[red]  Applying mod XML — failed: {escape(str(e))}[/red]")
        failed += 1

    if failed:
        console.print(f"\n[red]Done with {failed} failure(s).[/red]")
    else:
        console.print("\n[green]All copy steps completed successfully.[/green]")


@mods_group.command(name="list")
@click.pass_context
def mods_list(ctx):
    """Show every mod in the mod list with its status."""
    config = DayzConfig.load()
    _show_mod_table(config)


@mods_group.command()
@click.argument("name")
@click.pass_context
def add(ctx, name):
    """Add a mod to the mod list. NAME is the mod name (e.g. '@CF')."""
    config = DayzConfig.load()

    name = ensure_at_prefix(name)

    if name in modlist.installed(config):
        console.print(f"[yellow]{escape(name)} is already in the mod list[/yellow]")
        return

    _resolve_and_add_mod(config, name)


@mods_group.command()
@click.argument("names", nargs=-1, required=True)
@click.option("--with-deps", is_flag=True, help="Also remove orphaned dependencies.")
@click.pass_context
def remove(ctx, names, with_deps):
    """Remove one or more mods from the mod list. NAMES are mod names (e.g. '@CF')."""
    config = DayzConfig.load()
    if IS_WINDOWS:
        from app.commands.service import ensure_server_stopped
        if not ensure_server_stopped(config):
            return

    mod_list = modlist.installed(config)
    if not mod_list:
        console.print("[dim]No mods in the mod list.[/dim]")
        return

    to_remove = []
    for raw in names:
        name = ensure_at_prefix(raw.strip())
        if name not in mod_list:
            console.print(f"[yellow]{escape(name)} is not in the mod list — skipping[/yellow]")
            continue
        to_remove.append(name)

    if not to_remove:
        return

    if with_deps:
        cached_ids = modlist.workshop_ids(config)
        name_to_id = resolve_names_to_ids(to_remove, cached_ids)
        removing_ids = list(name_to_id.values())
        all_installed_ids = set(resolve_names_to_ids(mod_list, cached_ids).values())
        removable = get_removable_deps(removing_ids, all_installed_ids)
        if removable:
            dep_names = [n for _, n in removable]
            console.print(f"[dim]Also removing orphaned deps: {escape(', '.join(dep_names))}[/dim]")
            to_remove = to_remove + dep_names

    ni = ctx.obj.get("non_interactive", False) if ctx.obj else False
    if not confirm(f"Remove {len(to_remove)} mod(s)?", non_interactive=ni):
        return

    uninstall_mods(
        config,
        to_remove,
        on_mod=lambda m: console.print(f"  - {m}", markup=False),
        log=lambda line: console.print(line, style="yellow", markup=False),
    )
    console.print(f"[green]Removed {len(to_remove)} mod(s) from the mod list.[/green]")

    try:
        from app.monitoring.discord_hooks import notify_mods
        notify_mods(config, action="uninstalled", mods=to_remove)
    except Exception:
        pass


@mods_group.command()
@click.argument("name")
def enable(name):
    """Enable a disabled mod. NAME is the mod name (e.g. '@CF')."""
    from app.modmgr.mods import set_mod_enabled

    config = DayzConfig.load()
    name = ensure_at_prefix(name.strip())
    if name not in modlist.installed(config):
        console.print(f"[red]{escape(name)} is not in the mod list[/red]")
        return
    if name not in modlist.disabled(config):
        console.print(f"[dim]{escape(name)} is already enabled.[/dim]")
        return
    set_mod_enabled(config, name)
    console.print(f"[green]Enabled {escape(name)}[/green]")
    _regenerate_unit(config)


@mods_group.command()
@click.argument("name")
def disable(name):
    """Disable a mod (keeps it installed but excludes from server startup). NAME is the mod name (e.g. '@CF')."""
    from app.modmgr.mods import set_mod_disabled

    config = DayzConfig.load()
    name = ensure_at_prefix(name.strip())
    if name not in modlist.installed(config):
        console.print(f"[red]{escape(name)} is not in the mod list[/red]")
        return
    if name in modlist.disabled(config):
        console.print(f"[dim]{escape(name)} is already disabled.[/dim]")
        return
    set_mod_disabled(config, name)
    console.print(f"[yellow]Disabled {escape(name)}[/yellow]")
    _regenerate_unit(config)


def _regenerate_unit(config: DayzConfig) -> None:
    """Rebuild the service unit, which is where -mod= and -serverMod= live.

    Enable/disable used to stop at config.json, so a disabled mod kept loading
    until something else rebuilt the unit (found on a container boot,
    2026 Q3). The web routes have always done this.
    """
    from app.server.service_ops import generate_service_files

    try:
        generate_service_files(config)
        console.print("[dim]Service file regenerated; the next server start uses it.[/dim]")
    except Exception as e:  # noqa: BLE001 - the setting is saved either way
        console.print(f"[yellow]Could not regenerate the service file: {escape(str(e))}[/yellow]")
        console.print("[dim]  → zsm service generate, then zsm service install[/dim]")


def _plain(line: str) -> None:
    console.print(line, markup=False)


def offer_set_aside(
    config: DayzConfig,
    report: dict,
    names: list[str] | None = None,
    *,
    ni: bool = False,
    yes: bool = False,
) -> dict | None:
    """Show what setting the suspects of a failed boot aside would do, and do
    it only on a yes (plans/73 C). Returns the updated report, or None when
    nothing was done. Non-interactive without *yes* never disables anything."""
    import asyncio

    from app.ops import post_update

    plan = post_update.offer(config, report, names)
    if not plan["mods"]:
        _plain("No enabled mod is named by the failed boot, so there is nothing to set aside.")
        return None
    _plain("Setting these aside disables them until their authors catch up:")
    for mod in plan["mods"]:
        _plain(f"  {mod}")
    if plan["dependents"]:
        _plain("These need them and are set aside with them:")
        for mod in plan["dependents"]:
            _plain(f"  {mod}")
    console.print(plan["warning"], style="yellow", markup=False)
    backup = plan["backup"]
    if backup:
        _plain(f"Newest backup: {backup['name']} ({backup['timestamp']}).")
    else:
        _plain("There is no backup of this mission yet.")

    backup_first = False
    if not (backup and backup["fresh"]):
        backup_first = yes or confirm("Take a backup first?", default=True, non_interactive=ni)
    total = len(plan["mods"]) + len(plan["dependents"])
    if not (yes or confirm(f"Disable {total} mod(s) and restart the server?", default=False, non_interactive=ni)):
        _plain("Nothing was changed.")
        return None

    async def _mutate(fn):
        fn(config)
        config.save()
        return config

    async def _regenerate(cfg):
        await asyncio.to_thread(_regenerate_unit, cfg)

    return asyncio.run(post_update.set_aside(
        config, plan["mods"], mutate=_mutate, regenerate=_regenerate, emit=_plain,
        backup_first=backup_first,
    ))


@mods_group.command(name="triage")
@click.option("--disable", "names", multiple=True, help="Set this suspect aside (repeatable).")
@click.option("--all", "take_all", is_flag=True, help="Set every suspect aside.")
@click.option("--yes", is_flag=True, help="Do it without asking (a backup is taken first when none is fresh).")
@click.pass_context
def triage(ctx, names, take_all, yes):
    """The mods behind the last boot that failed after an update.

    Lists the suspects of the last failed boot and the mods set aside after
    earlier updates. `--disable NAME` or `--all` sets suspects aside (with
    every mod that needs them), restarts the server and checks it once.
    `zsm mods enable NAME` brings one back.
    """
    from app.commands.context import load_context
    from app.ops import post_update

    config, ni = load_context(ctx)
    report = post_update.load_report(config)
    if not post_update.failed(report):
        if report:
            for line in post_update.summary_lines(report):
                _plain(line)
        else:
            _plain("No boot after an update is on record.")
    else:
        for line in post_update.summary_lines(report):
            _plain(line)
        recheck = report.get("recheck")
        if recheck:
            _plain(f"Set aside: {', '.join(report.get('set_aside') or [])}. "
                   f"After that the server: {recheck.get('detail', recheck.get('outcome'))}")

    entries = post_update.triage_entries(config)
    if entries:
        _plain("Set aside after an update:")
        for e in entries:
            since = f"; updated since, enable it again with: zsm mods enable {e['mod']}" if e["updated_at"] else ""
            state = "" if e["disabled"] else " (enabled again)"
            _plain(f"  {e['mod']}: DayZ build {e['build'] or '?'}, {e['at']}{state}{since}")

    if names or take_all:
        if not post_update.failed(report):
            _plain("There is no failed boot to act on.")
            raise SystemExit(1)
        offered = post_update.offerable(report)
        chosen = offered if take_all else [ensure_at_prefix(n.strip()) for n in names]
        unknown = [n for n in chosen if n not in offered]
        if unknown:
            console.print(f"Not a suspect of the last failed boot: {', '.join(unknown)}", style="red", markup=False)
            raise SystemExit(2)
        result = offer_set_aside(config, report, chosen, ni=ni, yes=yes)
        if result is not None and post_update.failed(result.get("recheck")):
            raise SystemExit(1)
    elif post_update.offerable(report):
        _plain("Run with --disable NAME or --all to set suspects aside.")


@mods_group.command("server-side")
@click.argument("name")
@click.option("--off", is_flag=True, help="Load it through -mod= again (players need it).")
def server_side(name, off):
    """Load a mod through -serverMod= (server only) instead of -mod=.

    Only for mods whose author says they are server-side; a mod with items,
    models or UI players see must stay in -mod=. The service unit is rebuilt
    straight away, since that is where the two lists live.
    """
    from app.modmgr.mods import mark_server_side

    config = DayzConfig.load()
    name = ensure_at_prefix(name.strip())
    if name not in modlist.installed(config):
        console.print(f"[red]{escape(name)} is not in the mod list[/red]")
        return
    if not mark_server_side(config, name, not off):
        where = "-mod=" if off else "-serverMod="
        console.print(f"[dim]{escape(name)} already loads through {where}.[/dim]")
        return
    config.save()
    if off:
        console.print(f"[green]{escape(name)} loads through -mod= again.[/green]")
    else:
        console.print(f"[green]{escape(name)} now loads through -serverMod=.[/green]")
    _regenerate_unit(config)


@mods_group.command()
@click.pass_context
def download(ctx):  # noqa: ARG001
    """Download/update every mod in the mod list via SteamCMD."""
    config = DayzConfig.load()
    _download_mods(config)


@mods_group.command()
@click.pass_context
def copy(ctx):  # noqa: ARG001
    """Copy mods + bikeys to server dir and apply their XML to the mission."""
    config = DayzConfig.load()
    _copy_mods_to_server(config)


def _announce_triage_updates(config: DayzConfig, check) -> None:
    """Say which mods set aside after an update have updated since (plans/73
    D), here and on Discord, once per update. Never enables them."""
    from app.monitoring.discord_hooks import notify_triage_updated
    from app.ops import post_update

    fresh = post_update.newly_updated(config, {u.mod_id: u.remote_updated for u in check.updates})
    if not fresh:
        return
    lines = [post_update.triage_update_line(e) for e in fresh]
    for line in lines:
        console.print(line, style="cyan", markup=False)
    console.print("Enable one with: zsm mods enable NAME", markup=False)
    try:
        notify_triage_updated(config, lines)
    except Exception:  # noqa: BLE001 - a notification never fails the check
        pass
    post_update.mark_notified(config, fresh)
    config.save()


@mods_group.command(name="check-updates")
@click.pass_context
def check_updates(ctx):  # noqa: ARG001
    """Check Steam Workshop for available mod updates."""
    from app.modmgr.mod_ops import detect_mod_updates
    from app.modmgr.workshop_api import UpdateCheckError, save_detected_updates

    config = DayzConfig.load()
    if not config.steam_api_key:
        console.print(
            "[yellow]No Steam API key set. Configure it with "
            "'zsm settings set steam_api_key <key>'.[/yellow]"
        )
        # Exit non-zero: this command is the `mod_check` scheduled task, and
        # returning 0 made a check that never ran look like a check that found
        # nothing — the warning scrolls past in the journal while the oneshot
        # records Result=success (TODO C3, point 4).
        raise SystemExit(1)

    try:
        check = detect_mod_updates(config)
    except UpdateCheckError as exc:
        # Same rule as the missing key above: a check that did not happen must
        # not exit 0 and read as "up to date" (plans/50 B).
        console.print(exc.message, style="red", markup=False)
        raise SystemExit(1) from None

    # Saved on every successful check, so one that finds nothing clears a
    # stale list. The web Updates tab and the notifier read the same file.
    save_detected_updates(config.paths.mod_updates, check)
    _announce_triage_updates(config, check)

    unanswered = check.total - check.checked
    if unanswered:
        console.print(
            f"[yellow]Steam gave no details for {unanswered} of {check.total} mod(s) — "
            "deleted, hidden or private on the Workshop.[/yellow]"
        )
    if not check.updates:
        console.print(f"[green]All {check.checked} checked mods are up to date.[/green]")
        return

    table = Table(title=f"Mod Updates Available ({len(check.updates)})", expand=False)
    table.add_column("Mod", style="cyan")
    table.add_column("Workshop ID", style="dim")
    for u in check.updates:
        # A Workshop title is the mod author's text, and Rich reads "[/x]" in
        # a cell as a closing tag: MarkupError, after the list was saved, in
        # the scheduled mod_check too (plans/68 CON-16).
        table.add_row(escape(u.title), escape(u.mod_id))
    console.print(table)


# ---------------------------------------------------------------------------
# Spawn test (plans/37) — the logic lives in app/ops/spawn_test_ops.py
# ---------------------------------------------------------------------------


@mods_group.group(name="spawn-test")
def spawn_test_group():
    """Spawn N of a mod's class next to the first player or at a map location, then restore."""


def _refuse_while_running(config: DayzConfig) -> None:
    from app.server.service_control import is_server_running

    if is_server_running(config):
        console.print("Server is running. Stop it first.", style="red", markup=False)
        raise SystemExit(1)


def _run_spawn_test(action) -> dict:
    from app.ops.spawn_test_ops import SpawnTestError

    try:
        return action(lambda line: console.print(line, markup=False))
    except SpawnTestError as e:
        console.print(e.message, style="red", markup=False)
        raise SystemExit(1)


@spawn_test_group.command(name="status")
def spawn_test_status():
    """Show the active spawn test, if any."""
    from app.ops.spawn_test_ops import ONLY_X_SCOPES, active_test, location_text

    state = active_test(DayzConfig.load_or_none() or DayzConfig())
    if state is None:
        console.print("No spawn test is active.", style="dim", markup=False)
        return
    mode = "Only X" if state.get("mode") == "only_x" else "Spawn next to me"
    scope = state.get("scope")
    if scope:
        mode += f", {ONLY_X_SCOPES.get(scope, scope)}"
    if isinstance(state.get("location"), dict):
        mode = f"At {location_text(state['location'])}"
    console.print(
        f"{state.get('mod')}: {state.get('count')} x {state.get('classname')} "
        f"[{mode}] (mission {state.get('mission')}, started {state.get('started_at')})",
        markup=False,
    )


@spawn_test_group.command(name="start")
@click.argument("mod")
@click.argument("classname")
@click.option("--count", type=click.IntRange(min=0), default=10, show_default=True,
              help="How many to spawn. No maximum; a high count prints a warning.")
@click.option("--mode", type=click.Choice(["near-player", "at-location", "only-x"]),
              default="near-player", show_default=True,
              help="near-player: next to the first player who joins. "
                   "at-location: at --location or --x/--z, the first player moved there. "
                   "only-x: the only thing the loot economy spawns.")
@click.option("--scope", type=click.Choice(["loot", "loot-vehicles", "everything"]),
              default="loot", show_default=True,
              help="Only for --mode only-x: how much of the economy to zero.")
@click.option("--location", "location_name", default=None,
              help="Only for --mode at-location: a named place on the current map.")
@click.option("--x", "x", type=float, default=None,
              help="Only for --mode at-location: map X, used with --z instead of --location.")
@click.option("--z", "z", type=float, default=None,
              help="Only for --mode at-location: map Z, used with --x instead of --location.")
def spawn_test_start(mod, classname, count, mode, scope, location_name, x, z):
    """Arm a spawn test. MOD is the mod name (e.g. '@CF'); CLASSNAME one of its classes."""
    from app.ops import spawn_test_ops

    config = DayzConfig.load()
    mod = ensure_at_prefix(mod.strip())
    if mod not in modlist.installed(config):
        console.print(f"{mod} is not in the mod list", style="red", markup=False)
        raise SystemExit(1)
    _refuse_while_running(config)
    warning = spawn_test_ops.count_warning_text(count, *spawn_test_ops.spawn_count_warning())
    if warning:
        console.print(warning, style="yellow", markup=False)
    if mode == "only-x":
        _run_spawn_test(
            lambda log: spawn_test_ops.start_only_x(
                config, mod, classname, count, scope.replace("-", "_"), log
            )
        )
    elif mode == "at-location":
        if location_name:
            places, _size = spawn_test_ops.mission_locations(config)
            match = next(
                (p for p in places if p["name"].lower() == location_name.strip().lower()), None
            )
            if match is None:
                console.print(
                    f"No place called {location_name} on this map. "
                    "Use --x and --z for a position of your own.",
                    style="red", markup=False,
                )
                raise SystemExit(1)
            location_name, x, z = match["name"], match["x"], match["z"]
        elif x is None or z is None:
            console.print("--mode at-location needs --location, or both --x and --z.",
                          style="red", markup=False)
            raise SystemExit(1)
        _run_spawn_test(
            lambda log: spawn_test_ops.start_at_location(
                config, mod, classname, count, x, z, location_name, log
            )
        )
    else:
        _run_spawn_test(
            lambda log: spawn_test_ops.start_near_player(config, mod, classname, count, log)
        )


@spawn_test_group.command(name="end")
def spawn_test_end():
    """End the spawn test: put init.c and the storage folder back."""
    from app.ops import spawn_test_ops

    config = DayzConfig.load()
    _refuse_while_running(config)
    _run_spawn_test(lambda log: spawn_test_ops.end_test(config, log))


# ---------------------------------------------------------------------------
# Rollback (TODO C5)
# ---------------------------------------------------------------------------


@mods_group.command(name="rollback")
@click.option("--restore", "do_restore", is_flag=True, help="Put the previous mod versions back.")
@click.option("--discard", "do_discard", is_flag=True, help="Throw the kept versions away.")
def mods_rollback(do_restore: bool, do_discard: bool):
    """The mod versions the last update replaced, and how to go back to them.

    Reports by default. `--restore` swaps them back in; `--discard` frees the
    disk. Exits non-zero when there is nothing kept, so a script can tell
    "nothing to roll back to" from "rolled back".
    """
    from app.ops import mod_rollback

    if do_restore and do_discard:
        console.print("--restore and --discard are opposites.", style="red", markup=False)
        raise SystemExit(2)

    config = DayzConfig.load()
    state = mod_rollback.should_offer_rollback(config)
    if not state["available"]:
        console.print(
            "No previous mod versions are kept. One is set aside automatically "
            "the next time an update replaces your mods.",
            markup=False,
        )
        raise SystemExit(1)

    size_mb = state["size_bytes"] / (1024 * 1024)
    console.print(
        f"Kept {len(state['mods'])} mod(s) from {state['taken_at'] or 'an earlier update'} "
        f"({size_mb:.0f} MB):",
        markup=False,
    )
    for name in state["mods"]:
        console.print(f"  {name}", markup=False)
    if state["offer"]:
        console.print(state["reason"], style="yellow", markup=False)

    if do_discard:
        _refuse_while_running(config)
        mod_rollback.discard(config)
        console.print("Discarded.", style="green", markup=False)
        return

    if not do_restore:
        console.print(
            "\nRun with --restore to put them back, or --discard to free the disk.",
            markup=False,
        )
        return

    _refuse_while_running(config)
    result = mod_rollback.restore(config, log=lambda s: console.print(s, markup=False))
    console.print(
        result.message, style="green" if result.ok else "red", markup=False
    )
    if not result.ok:
        raise SystemExit(1)
    console.print(
        # The same verb as the Mods page's rollback help (re-apply).
        "Afterwards re-apply the mod XML before you start the server: zsm run xml-only",
        markup=False,
    )


@mods_group.command(name="xml-autoconfig")
@click.argument("name")
@click.option("--dry-run", is_flag=True, help="Show the entries, write nothing.")
@click.option("--no-split", is_flag=True, help="Each variant keeps its vanilla parent's full nominal and min.")
@click.option("--no-category", is_flag=True, help="No category guess for an item with no known parent.")
@click.option("--no-starter", is_flag=True, help="Nominal 0 for an item with no known parent.")
def xml_autoconfig(name, dry_run, no_split, no_category, no_starter):
    """Write types.xml entries for a mod that ships no XML (Auto configure).

    The author's <type> blocks from the Workshop description come first. Every
    other item copies its nearest vanilla parent's values. Variants of one
    parent share its nominal and min, and an item with no known parent gets a
    category guess and a small starter nominal, unless a --no-* flag turns
    that off. The result is a manual XML file of the mod, applied to the mission.
    """
    from app.ops import mod_autoconfig

    config = DayzConfig.load()
    if name not in modlist.installed(config):
        raise click.ClickException(f"{name} is not in the mod list.")
    if name in modlist.disabled(config):
        raise click.ClickException(f"{name} is disabled. Enable it first: zsm mods enable {name}")
    try:
        options = mod_autoconfig.AutoConfigOptions(
            split_nominal=not no_split, guess_category=not no_category, starter_nominal=not no_starter
        )
        preview = mod_autoconfig.build(config, name, options=options)
    except mod_autoconfig.AutoConfigError as e:
        raise click.ClickException(str(e)) from None
    console.print(f"{preview.count} entries ({preview.source or 'none'}) for {name}", markup=False)
    if preview.workshop_note:
        console.print(f"Workshop: {preview.workshop_note}", markup=False)
    for row in preview.from_parent:
        console.print(f"  {row['name']}: values from {row['parent']}", markup=False)
    for row in preview.split:
        console.print(
            f"  {len(row['names'])} variants of {row['parent']} share it: nominal {row['nominal']}, min {row['min']} each",
            markup=False,
        )
    guessed = {row["name"]: row for row in preview.guessed}
    for cls in preview.from_defaults:
        row = guessed.get(cls, {})
        what = ", ".join(f"{k} {row[k]}" for k in ("category", "nominal", "min") if k in row) or "nominal 0"
        if "not_loot" in row:
            what += f" (no category or starter nominal: {row['not_loot']} is no loot base)"
        console.print(f"  {cls}: no known parent, {what}", markup=False)
    for row in preview.skipped:
        console.print(f"  skipped {row['name']}: {row['reason']}", style="dim", markup=False)
    if dry_run:
        console.print(preview.xml or "(nothing to add)", markup=False)
        return
    try:
        result = mod_autoconfig.apply(config, preview)
    except mod_autoconfig.AutoConfigError as e:
        raise click.ClickException(str(e)) from None
    state = "applied to the mission" if result["applied"] else "stored, not applied"
    console.print(f"Manual file {result['file_id']}: {state}.", markup=False)
