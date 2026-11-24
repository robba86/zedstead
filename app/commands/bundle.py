"""zsm bundle — export / import complete server configuration bundles."""

from __future__ import annotations

import zipfile
from pathlib import Path

import click
from rich.console import Console

from app.ops.bundle_ops import (
    battleye_entries,
    detect_source_mission,
    export_bundle,
    file_entries,
    get_bundle_dir,
    MODS_ARC_NAME,
    import_mod_list,
    importable_config,
    list_available_bundles,
    mission_entry_dest,
    profiles_entry_dest,
    profiles_entries as bundle_profiles_entries,
    refuse_battleye_import_while_running,
    refused_battleye_entries,
    remap_mission_entry,
)
from app.parsers.battleye_cfg import BeserverCfgInUse
from app.shared.config import DayzConfig
from app.commands.context import load_context
from app.shared.cli_ui import confirm, print_error, print_success, print_warning
from app.shared.json_utils import atomic_write_bytes

console = Console()


def _bundle_export(config: DayzConfig, ni: bool) -> str | None:
    """Create a zip archive of the server configuration. Returns zip path on success."""
    console.print()
    zip_path = export_bundle(config, log=lambda line: console.print(line, markup=False))
    if zip_path is None:
        return None
    console.print()
    print_success(f"Bundle exported to {zip_path}")
    return str(zip_path)


def _bundle_import(zip_path: Path, ni: bool, target_mission: str | None = None) -> None:
    """Unpack a zip bundle, importing config selectively."""
    import json as _json

    if not zip_path.exists():
        print_error(f"File not found: {zip_path}")
        return

    if not zipfile.is_zipfile(zip_path):
        print_error(f"Not a valid zip file: {zip_path}")
        return

    with zipfile.ZipFile(zip_path, "r") as zf:
        names = file_entries(zf)

        console.print()
        console.print(f"[bold]Bundle contents:[/bold] {zip_path.name}")
        for name in names:
            info = zf.getinfo(name)
            size_kb = info.file_size / 1024
            console.print(f"  [dim]{name}[/dim]  ({size_kb:.1f} KB)")
        console.print()

        if not confirm("Proceed with import?", non_interactive=ni, default=True):
            return

        try:
            config = DayzConfig.load()
        except Exception as e:
            print_error(f"Cannot load config: {e}")
            return
        try:
            refuse_battleye_import_while_running(config, names)
        except BeserverCfgInUse as e:
            print_error(str(e))
            return

        # --- the mod list ---
        if MODS_ARC_NAME in names:
            if not config.paths.mods_json.exists() or confirm(
                "Replace this instance's mod list?", non_interactive=ni, default=True,
            ):
                line = import_mod_list(config, names, zf.read(MODS_ARC_NAME))
                (print_success if line.startswith("+") else print_warning)(line[2:])
        elif "mods.txt" in names:
            print_warning(import_mod_list(config, names, None)[2:])

        # --- serverDZ.cfg ---
        if "serverDZ.cfg" in names:
            dest = config.server_cfg_path
            if not dest.exists() or confirm(f"Overwrite {dest}?", non_interactive=ni, default=True):
                atomic_write_bytes(dest, zf.read("serverDZ.cfg"))
                print_success(f"Imported serverDZ.cfg → {dest}")

        # --- config.json (safe fields only) ---
        if "config.json" in names:
            try:
                bundle_cfg = _json.loads(zf.read("config.json"))
            except (OSError, _json.JSONDecodeError):
                bundle_cfg = {}

            if bundle_cfg:
                console.print()
                console.print("[dim]Importing safe config fields (paths are not overwritten):[/dim]")
                importable, refused = importable_config(bundle_cfg, config)
                for line in refused:
                    print_warning(line)
                for k, v in importable.items():
                    console.print(f"  {k} = {v!r}")

                if confirm("Apply these settings?", non_interactive=ni, default=True):
                    config = DayzConfig.load()
                    for k, v in importable.items():
                        setattr(config, k, v)
                    config.save()
                    print_success("Config settings imported.")

        # --- BattlEye config ---
        for be_name in refused_battleye_entries(config, names):
            print_warning(f"Skipped bundle entry that is not the BattlEye cfg: {be_name}")
        be_entries = battleye_entries(config, names)
        if be_entries:
            for be_name in be_entries:
                dest = config.beserver_cfg_path
                dest_label = f"battleye/{dest.name}"
                if not dest.exists() or confirm(f"Overwrite {dest_label}?", non_interactive=ni, default=True):
                    atomic_write_bytes(dest, zf.read(be_name), owner_only=True)
                    print_success(f"Imported {dest_label} → {dest}")

        # --- Profiles config files ---
        profiles_entries = bundle_profiles_entries(config, names)
        if profiles_entries:
            if confirm(f"Import {len(profiles_entries)} profiles config file(s)?", non_interactive=ni, default=True):
                for arc_name in profiles_entries:
                    try:
                        dest = profiles_entry_dest(config, arc_name)
                    except ValueError:
                        print_warning(f"Skipped unsafe bundle entry: {arc_name}")
                        continue
                    if dest.exists() and not confirm(f"Overwrite {arc_name}?", non_interactive=ni, default=True):
                        continue
                    atomic_write_bytes(dest, zf.read(arc_name))
                    console.print(f"  [green]✓[/green] {arc_name}")

        # --- Mission XMLs ---
        mission_files = [n for n in names if n.startswith("mpmissions/")]
        if mission_files:
            source_mission = detect_source_mission(names)

            # Ask which mission to import into (CLI mode)
            if not target_mission and source_mission and not ni:
                from app.parsers.servercfg import get_available_missions
                available = get_available_missions(config.server_dir_path)
                if len(available) > 1:
                    from app.shared.cli_ui import select
                    console.print(f"[dim]Bundle mission: {source_mission}[/dim]")
                    target_mission = select(
                        "Import mission files to which mpmission?",
                        available,
                        default=source_mission if source_mission in available else None,
                    )
                elif len(available) == 1:
                    target_mission = available[0]

            # Fall back to source mission if no target selected
            if not target_mission:
                target_mission = source_mission

            console.print()
            if target_mission and source_mission and target_mission != source_mission:
                console.print(f"[dim]Remapping: {source_mission} → {target_mission}[/dim]")

            if confirm(f"Import {len(mission_files)} mission XML file(s)?", non_interactive=ni, default=True):
                for arc_name in mission_files:
                    dest_name = remap_mission_entry(arc_name, source_mission, target_mission)
                    try:
                        dest_name, dest = mission_entry_dest(config.server_dir_path, dest_name)
                    except ValueError:
                        print_warning(f"Skipped unsafe bundle entry: {dest_name}")
                        continue
                    if dest.exists() and not confirm(f"Overwrite {dest_name}?", non_interactive=ni, default=True):
                        continue
                    atomic_write_bytes(dest, zf.read(arc_name))
                    console.print(f"  [green]✓[/green] {dest_name}")

    print_success("Bundle import complete.")


# ---------------------------------------------------------------------------
# Click group
# ---------------------------------------------------------------------------

@click.group(name="bundle")
def bundle_group() -> None:
    """Export or import a server configuration bundle (mod list, serverDZ.cfg, mission XMLs, app config)."""
    pass


@bundle_group.command(name="export")
@click.pass_context
def bundle_export(ctx) -> None:
    """Snapshot the mod list, serverDZ.cfg, mission XMLs, and app config into a zip."""
    config, ni = load_context(ctx)
    _bundle_export(config, ni)  # return value ignored in CLI mode


@bundle_group.command(name="import")
@click.argument("zip_file", type=click.Path(exists=True, dir_okay=False))
@click.pass_context
def bundle_import(ctx, zip_file: str) -> None:
    """Unpack a bundle zip and restore configuration."""
    _ctx_config, ni = load_context(ctx)
    _bundle_import(Path(zip_file), ni)
