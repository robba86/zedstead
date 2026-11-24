"""zsm maps — change active map."""

import click
from rich.console import Console

from app.shared.config import DayzConfig
from app.ops.fileops import copy_mod_to_server
from app.maps.map_ops import (
    MAP_CFG_KEYS,
    MOD_XML_SKIPPED,
    SwitchChoices,
    apply_mod_xml_to_new_mission,
    backup_map_cfg,
    copy_mission_from_mod,
    download_and_extract_mission,
    check_switch_choices,
    ensure_map_ids_cached,
    get_active_map_name,
    leave_behind,
    map_available,
    partition_old_map_mods,
    remove_old_map_mods_from_server,
    switch_map_cfg,
    switch_plan,
    update_mods_list_for_map,
    vanilla_mission,
)
from app.shared.platform import IS_WINDOWS
from app.shared.seed_data import VANILLA_MAPS, WORKSHOP_MAPS
from app.parsers.servercfg import set_mission_template
from app.shared.steam import SteamcmdRefused, download_mod, is_mod_downloaded
from app.shared.cli_ui import print_step, print_success, print_warning

console = Console()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _download_map_mods(config: DayzConfig, map_data: dict) -> None:
    """Download all mods and deps for a workshop map."""
    all_mod_items = {**map_data.get("mods", {}), **map_data.get("deps", {})}
    for mod_name, mod_id in all_mod_items.items():
        if is_mod_downloaded(config, mod_id):
            console.print(f"  {mod_name} already downloaded.")
        else:
            console.print(f"  Downloading {mod_name} (ID: {mod_id})...")
            try:
                download_mod(config, mod_id)
            except SteamcmdRefused as e:
                raise click.ClickException(str(e)) from e


def _copy_map_mods(config: DayzConfig, map_data: dict) -> dict[str, str]:
    """Ensure cached IDs and copy map mods to server. Returns cached_ids."""
    from app.ops import mod_rollback

    cached_ids = ensure_map_ids_cached(config, map_data)
    all_mod_items = {**map_data.get("mods", {}), **map_data.get("deps", {})}
    generation = mod_rollback.Generation(config, log=console.print)
    for mod_name in all_mod_items:
        console.print(f"  Copying {mod_name}...")
        copy_mod_to_server(config, mod_name, cached_ids, generation=generation)
    return cached_ids


def _install_mission(config: DayzConfig, map_data: dict, cached_ids: dict[str, str]) -> None:
    """Install mission files from zip or mod source."""
    mission = map_data["mission"]
    template = mission["template"]
    mission_dest = config.server_dir_path / "mpmissions" / template
    source_type = mission.get("source")

    if source_type == "zip" and mission.get("zip_url") and mission.get("mission_path"):
        console.print(f"  Extracting {template} to mpmissions/...")
        try:
            download_and_extract_mission(
                mission["zip_url"],
                mission["mission_path"],
                mission_dest,
                log=console.print,
                record_source=True,
            )
        except ValueError as e:
            raise click.ClickException(str(e))
    elif source_type == "mod" and mission.get("source_mod") and mission.get("mission_path"):
        console.print(f"  Copying {template} from {mission['source_mod']}...")
        try:
            count = copy_mission_from_mod(
                config, mission["source_mod"], mission["mission_path"],
                mission_dest, cached_ids,
            )
            console.print(f"  Copied {count} file(s) to {mission_dest.name}/")
        except ValueError as e:
            raise click.ClickException(str(e))
    else:
        console.print("  No mission source configured — mission files must be installed manually.")


# ---------------------------------------------------------------------------
# Activation
# ---------------------------------------------------------------------------

def _activate_workshop_map(
    ctx,
    config: DayzConfig,
    map_name: str,
    map_data: dict,
    *,
    choices: SwitchChoices | None = None,
    run_service_update: bool = False,
) -> None:
    """Full orchestration for switching to a workshop map."""
    choices = choices or SwitchChoices()
    if IS_WINDOWS:
        from app.commands.service import ensure_server_stopped
        if not ensure_server_stopped(config):
            return
    mission = map_data["mission"]
    template = mission["template"]
    all_mod_items = {**map_data.get("mods", {}), **map_data.get("deps", {})}
    total_steps = 5

    print_step(1, total_steps, "Downloading map mods")
    _download_map_mods(config, map_data)

    print_step(2, total_steps, "Copying map mods to server")
    cached_ids = _copy_map_mods(config, map_data)

    print_step(3, total_steps, "Mission files")
    _install_mission(config, map_data, cached_ids)

    print_step(4, total_steps, "Updating the mod list")
    left = leave_behind(config, map_name, choices, _print_plain)
    old_map_mods, kept_deps = partition_old_map_mods(config, map_data)
    update_mods_list_for_map(config, map_data)
    if old_map_mods:
        removed = remove_old_map_mods_from_server(config, old_map_mods)
        if removed:
            console.print(f"  Removed: {', '.join(removed)}")
    _report_kept_deps(kept_deps)
    added = list(all_mod_items.keys())
    console.print(f"  Added: {', '.join(added)}")

    print_step(5, total_steps, "Setting mission template")
    backup = backup_map_cfg(config)
    if backup:
        console.print(f"  Backed up config to {backup.name}")
    set_mission_template(config.server_cfg_path, template)
    console.print(f'  template = "{template}"')

    switch_map_cfg(
        config, map_name, template, mission,
        restore_previous=choices.restore_previous,
        apply_recommended=choices.apply_recommended,
        log=_print_plain,
        warn=print_warning,
    )
    _finish_switch(ctx, config, map_name, choices, left, run_service_update)


def _finish_switch(
    ctx, config: DayzConfig, map_name: str, choices: SwitchChoices, left: dict,
    run_service_update: bool,
) -> None:
    """The closing merge (or the line saying it was skipped), then the unit."""
    if choices.apply_mod_xml:
        apply_mod_xml_to_new_mission(config, _print_plain)
    else:
        _print_plain(MOD_XML_SKIPPED)

    console.print()
    print_success(f"Map changed to {map_name}.")
    if run_service_update:
        from app.commands.service import service_update
        ctx.invoke(service_update)
    elif left["left_mods"] or left["turned_on"]:
        # -mod= lives in the unit, as after `zsm mods disable`.
        from app.commands import mods as mods_cmd

        mods_cmd._regenerate_unit(config)


def _print_plain(line: str) -> None:
    """A line from map_ops, which writes markup-free text for the job log too."""
    console.print(line, markup=False)


def _report_kept_deps(kept_deps: list[str]) -> None:
    """Say which of the old map's deps stayed, and why.

    An unexplained leftover reads as a bug; a leftover with a reason reads as a
    decision. The reason is real — `@CF` is Deer Isle's dep and every Expansion
    module's, and nothing in the map registry can tell those apart.
    """
    if not kept_deps:
        return
    # Two prints, not one long one: Rich wraps at the terminal width and the
    # continuation line loses the two-space indent, so a single sentence came
    # out with its tail hard against the left margin.
    console.print(f"  Kept: {', '.join(kept_deps)}")
    console.print("  Other mods are still installed and may need them.")


def _activate_vanilla_map(
    ctx,
    config: DayzConfig,
    map_name: str,
    template: str,
    *,
    choices: SwitchChoices | None = None,
    run_service_update: bool = False,
) -> None:
    """Switch to a vanilla map — remove workshop map mods and set template."""
    choices = choices or SwitchChoices()
    if IS_WINDOWS:
        from app.commands.service import ensure_server_stopped
        if not ensure_server_stopped(config):
            return
    left = leave_behind(config, map_name, choices, _print_plain)
    old_map_mods, kept_deps = partition_old_map_mods(config, None)
    update_mods_list_for_map(config, None)
    if old_map_mods:
        removed = remove_old_map_mods_from_server(config, old_map_mods)
        if removed:
            console.print(f"  Removed: {', '.join(removed)}")
    console.print("  Removed workshop map mods from the mod list.")
    _report_kept_deps(kept_deps)

    backup = backup_map_cfg(config)
    if backup:
        console.print(f"  Backed up config to {backup.name}")
    set_mission_template(config.server_cfg_path, template)
    console.print(f'  template = "{template}"')

    switch_map_cfg(
        config, map_name, template,
        restore_previous=choices.restore_previous,
        log=_print_plain,
    )
    _finish_switch(ctx, config, map_name, choices, left, run_service_update)


# ---------------------------------------------------------------------------
# Click commands
# ---------------------------------------------------------------------------

@click.group(name="maps")
@click.pass_context
def maps_group(ctx):  # noqa: ARG001
    """Change the active map."""
    pass


@maps_group.command(name="list")
@click.pass_context
def maps_list_cmd(ctx):  # noqa: ARG001
    """List available maps (workshop + vanilla)."""
    config = DayzConfig.load()
    active = get_active_map_name(config)

    console.print("[bold]Workshop maps:[/bold]")
    for name in WORKSHOP_MAPS:
        marker = " [green]✓ active[/green]" if name == active else ""
        console.print(f"  {name}{marker}")
    console.print()
    console.print("[bold]Vanilla maps:[/bold]")
    for name in VANILLA_MAPS:
        if not map_available(config, name):
            # Listed so the roadmap is visible; `switch` refuses it.
            console.print(f"  {name} [dim](not released yet)[/dim]")
            continue
        marker = " [green]✓ active[/green]" if name == active else ""
        console.print(f"  {name}{marker}")


def _dry_run_lines(plan: dict, choices: SwitchChoices) -> list[str]:
    """`zsm maps switch --dry-run`: the switch dialog's content as text, with
    the given choices applied. Markup-free."""
    target = plan["map_id"]
    old = plan["from"] or plan["from_terrain"] or "the current map"
    off = set(choices.disable_mods)
    leave = set(choices.leave_manual_files)
    lines = [f"Switch from {old} to {target} (dry run: nothing is changed)", ""]

    lines.append(f"Comes with {target}:")
    if not plan["map_mods"]:
        lines.append(f"  {target} comes with the server and needs no mods.")
    for m in plan["map_mods"]:
        what = "dependency" if m["dep"] else "map mod"
        extra = ", turned on by the switch" if m["turn_on"] else ""
        lines.append(f"  {m['name']} ({what}{extra})")

    leaving = plan["leaving"]
    if leaving["removed"]:
        lines.append(f"Leaves with {old}: {', '.join(leaving['removed'])} (removed)")
    if leaving["kept_deps"]:
        lines.append(
            f"  Kept: {', '.join(leaving['kept_deps'])}; other mods are still installed and may need them."
        )

    lines.append("Your mods:")
    if not plan["mods"]:
        lines.append("  (none)")
    for m in plan["mods"]:
        if m["name"] not in off:
            lines.append(f"  {m['name']}: goes along")
            continue
        lines.append(f"  {m['name']}: turned off, still installed, off on every map")
        needed_by = [r for r in m["required_by"] if r not in off]
        if needed_by:
            lines.append(
                f"    Warning: {', '.join(needed_by)} needs {m['name']}; with it off it may not load."
            )
    if plan["off_mods"]:
        lines.append(f"Already off: {', '.join(plan['off_mods'])}")

    lines.append(
        "Mod XML: applied to the new mission" if choices.apply_mod_xml
        else f"Mod XML: {MOD_XML_SKIPPED}"
    )

    if plan["manual_files"]:
        lines.append("Manual XML files:")
    for f in plan["manual_files"]:
        if f["scope"] == "every":
            where = f"stays with {old}" if f["id"] in leave else "goes along (every map)"
        elif f["scope"] == "to":
            where = f"applies on {target}"
        elif f["scope"] == "from":
            where = f"stays with {old} (scoped to it)"
        else:
            where = f"scoped to another map ({f['terrain']})"
        lines.append(f"  {f['id']}: {where}")

    cfg = plan["cfg"]
    if cfg["backup"] and choices.restore_previous:
        lines.append(f"serverDZ.cfg: your saved config for {target} (passwords kept)")
    elif cfg["recommended"] and choices.apply_recommended:
        lines.append(
            "serverDZ.cfg: your current config, plus the map's recommended "
            + ", ".join(cfg["keys"])
        )
    else:
        lines.append("serverDZ.cfg: your current config; only the mission changes")
    return lines


@maps_group.command(name="switch")
@click.argument("name")
@click.option(
    "--restore-previous-config/--no-restore-previous-config",
    "restore_previous",
    default=True,
    help="Restore the previous server config backup for this map (default: enabled).",
)
@click.option(
    "--apply-recommended/--no-apply-recommended",
    "apply_recommended",
    default=True,
    help=(
        "Take the map's recommended settings from its own serverDZ.cfg "
        f"({', '.join(MAP_CFG_KEYS)}); the rest of your cfg stays as it is. "
        "Workshop maps only (default: enabled)."
    ),
)
@click.option(
    "--disable-mod",
    "disable_mods",
    multiple=True,
    metavar="MOD",
    help=(
        "Leave MOD behind: it is turned off (still installed), on every map, until "
        "you turn it on again. Repeatable. The new map's own mods cannot be."
    ),
)
@click.option(
    "--leave-manual-file",
    "leave_manual_files",
    multiple=True,
    metavar="ID",
    help=(
        "Keep the manual XML file ID with the current map: it is scoped to that "
        "map's terrain and stops applying on the new one. Repeatable."
    ),
)
@click.option(
    "--apply-mod-xml/--no-apply-mod-xml",
    "apply_mod_xml",
    default=True,
    help=(
        "Apply your mods' XML and the manual XML files to the new mission "
        "(default: enabled)."
    ),
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Print what the switch would take along and leave behind, and change nothing.",
)
@click.option(
    "--service-update",
    "run_service_update",
    is_flag=True,
    default=False,
    help="Run `service update` after switching.",
)
@click.pass_context
def maps_switch(
    ctx, name, restore_previous, apply_recommended, disable_mods, leave_manual_files,
    apply_mod_xml, dry_run, run_service_update,
):
    """Switch to map NAME (workshop or vanilla, e.g. 'Namalsk' or 'Chernarus')."""
    config = DayzConfig.load()

    # Announced-but-unreleased maps are listed but have no mission to install.
    if name in VANILLA_MAPS or name in WORKSHOP_MAPS:
        if not map_available(config, name):
            hint = (
                " Once it is out, update the server and its mission will be found."
                if VANILLA_MAPS.get(name, {}).get("terrain") else ""
            )
            raise click.ClickException(
                f"'{name}' has not been released yet — there is nothing to install.{hint}"
            )
    else:
        known = sorted(list(WORKSHOP_MAPS) + list(VANILLA_MAPS))
        raise click.ClickException(
            f"Unknown map '{name}'. Known: {', '.join(known)}"
        )

    choices = SwitchChoices(
        restore_previous=restore_previous,
        apply_recommended=apply_recommended,
        apply_mod_xml=apply_mod_xml,
        disable_mods=tuple(disable_mods),
        leave_manual_files=tuple(leave_manual_files),
    )
    if disable_mods or leave_manual_files or dry_run:
        plan = switch_plan(config, name)
        problem = check_switch_choices(plan, choices)
        if problem:
            raise click.ClickException(problem)
        if dry_run:
            from app.modmgr.mod_ops import ensure_at_prefix

            shown = SwitchChoices(
                restore_previous=restore_previous,
                apply_recommended=apply_recommended,
                apply_mod_xml=apply_mod_xml,
                disable_mods=tuple(ensure_at_prefix(n.strip()) for n in disable_mods),
                leave_manual_files=tuple(leave_manual_files),
            )
            for line in _dry_run_lines(plan, shown):
                _print_plain(line)
            return

    if name in WORKSHOP_MAPS:
        _activate_workshop_map(
            ctx, config, name, WORKSHOP_MAPS[name],
            choices=choices,
            run_service_update=run_service_update,
        )
    else:
        _activate_vanilla_map(
            ctx, config, name, vanilla_mission(config, name),
            choices=choices,
            run_service_update=run_service_update,
        )
