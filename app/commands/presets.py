"""zsm presets — list, apply, save, delete server presets."""

from __future__ import annotations

from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from app.modmgr.mods import get_current_mission
from app.presets.preset_engine import apply_preset, preview_preset, snapshot_preset_targets
from app.presets.presets import BUILTIN_PRESETS, PresetDefinition
from app.presets.preset_storage import (
    PresetNameClash,
    delete_custom_preset,
    list_custom_presets,
    load_custom_preset,
    save_custom_preset,
    snapshot_current_config,
)
from app.shared.config import DayzConfig
from app.shared.cli_ui import confirm, print_error, print_success

console = Console()


def _presets_dir() -> Path:
    """This install's custom presets. A blank config's paths are the
    pre-instances layout, which is where they are when there is no config."""
    return (DayzConfig.load_or_none() or DayzConfig()).paths.presets_dir


def _resolve_preset(name: str) -> PresetDefinition | None:
    """Resolve a preset name → definition. Tries builtins by key/name, then custom files."""
    if name in BUILTIN_PRESETS:
        return BUILTIN_PRESETS[name]
    for key, p in BUILTIN_PRESETS.items():
        if p.name == name or key.replace("_", "-") == name:
            return p
    for fname, path in list_custom_presets(_presets_dir()):
        if fname == name:
            return load_custom_preset(path)
    return None


def _resolve_mission_dir(config: DayzConfig):
    mission = get_current_mission(config.server_cfg_path)
    if not mission:
        return None
    return config.server_dir_path / "mpmissions" / mission


@click.group(name="presets")
@click.pass_context
def presets_group(ctx):  # noqa: ARG001
    """Apply, list, save, or delete server presets."""
    pass


@presets_group.command(name="list")
@click.pass_context
def presets_list(ctx):  # noqa: ARG001
    """List built-in and custom presets."""
    table = Table(title="Built-in presets", expand=False)
    table.add_column("Key", style="cyan")
    table.add_column("Name")
    table.add_column("Description", overflow="fold")
    for key, preset in BUILTIN_PRESETS.items():
        table.add_row(key, preset.name, preset.description)
    console.print(table)

    customs = list_custom_presets(_presets_dir())
    if customs:
        table2 = Table(title="Custom presets", expand=False)
        table2.add_column("Name", style="cyan")
        table2.add_column("Path", overflow="fold")
        for fname, path in customs:
            table2.add_row(fname, str(path))
        console.print(table2)
    else:
        console.print("[dim]No custom presets saved.[/dim]")


@presets_group.command(name="show")
@click.argument("name")
@click.pass_context
def presets_show(ctx, name):
    """Preview the changes a preset would make against current config."""
    preset = _resolve_preset(name)
    if not preset:
        print_error(f"Unknown preset: {name}")
        ctx.exit(1)

    config = DayzConfig.load()
    mission_dir = _resolve_mission_dir(config)
    if not mission_dir:
        print_error("Could not determine current mission from serverDZ.cfg.")
        ctx.exit(1)

    _print_preview(preset, preview_preset(config, preset, mission_dir))


def _print_preview(preset, preview) -> None:
    """The same rows and warnings the Presets page shows."""
    console.print(f"[bold]{preset.name}[/bold] — {len(preview.changes)} change(s)")
    for c in preview.changes:
        label = f" ({c.label})" if c.label else ""
        console.print(f"  {c.file}: {c.parameter} {c.old_value} → {c.new_value}{label}", markup=False)
    for warning in preview.warnings:
        console.print(warning, style="yellow", markup=False)


@presets_group.command(name="apply")
@click.argument("name")
@click.option("--dry-run", is_flag=True, help="Preview without writing changes.")
@click.pass_context
def presets_apply(ctx, name, dry_run):
    """Apply a preset by NAME (built-in key or custom filename)."""
    preset = _resolve_preset(name)
    if not preset:
        print_error(f"Unknown preset: {name}")
        ctx.exit(1)

    config = DayzConfig.load()
    mission_dir = _resolve_mission_dir(config)
    if not mission_dir:
        print_error("Could not determine current mission from serverDZ.cfg.")
        ctx.exit(1)

    preview = preview_preset(config, preset, mission_dir)
    _print_preview(preset, preview)

    if dry_run:
        return

    if not preview.changes:
        console.print("[dim]Nothing to do — preset matches current state.[/dim]")
        return

    ni = bool(ctx.obj.get("non_interactive", False)) if ctx.obj else False
    if not confirm(f"Apply {preset.name}?", default=True, non_interactive=ni):
        return

    snapshot_preset_targets(config, mission_dir, trigger=f"preset_apply:{name}")
    modified = apply_preset(config, preset, mission_dir)
    print_success(f"Applied {preset.name} — {len(modified)} file(s) modified.")
    for path in modified:
        console.print(f"  [dim]{path}[/dim]")


@presets_group.command(name="save")
@click.argument("name")
@click.option("--filename", default=None, help="Custom filename (default: derived from NAME).")
@click.pass_context
def presets_save(ctx, name, filename):
    """Snapshot current server config to a custom preset."""
    config = DayzConfig.load()
    preset = snapshot_current_config(config, name)
    try:
        path = save_custom_preset(config.paths.presets_dir, preset, filename)
    except PresetNameClash as e:
        print_error(str(e))
        ctx.exit(1)
    print_success(f"Saved preset to {path}")


@presets_group.command(name="delete")
@click.argument("name")
@click.pass_context
def presets_delete(ctx, name):
    """Delete a custom preset by filename."""
    customs = list_custom_presets(_presets_dir())
    matches = [(fn, p) for fn, p in customs if fn == name]
    if not matches:
        names = ", ".join(fn for fn, _ in customs) or "(none)"
        print_error(f"No custom preset named {name!r}. Available: {names}")
        ctx.exit(1)

    _, path = matches[0]
    ni = bool(ctx.obj.get("non_interactive", False)) if ctx.obj else False
    if not confirm(f"Delete custom preset {name!r}?", default=True, non_interactive=ni):
        return

    delete_custom_preset(path)
    print_success(f"Deleted {path}")
