"""zsm settings — get/set app configuration values.

Exposes a curated subset of `DayzConfig` fields organized by the same sections
shown on the web UI's Settings page, which is the better surface for browsing;
this command is for scripting/automation.
"""

from __future__ import annotations

import click
from rich.console import Console
from rich.table import Table

from app.ops.bundle_ops import SENSITIVE_CONFIG_FIELDS
from app.shared.config import DayzConfig
from app.shared.cli_ui import print_error, print_success
from app.shared.settings_registry import (
    _BY_KEY,
    _REGISTRY,
    _Setting,
    _format,
    _parse,
    _read,
    _write,
)

console = Console()

# The config fields that are credentials: the bundle warning's list, so the
# two cannot disagree. `list` is the screen people paste into a "here is my
# setup" post, so it says only whether one is set; `get <key>` prints it.
_SECRET_KEYS = frozenset(field for field, _label in SENSITIVE_CONFIG_FIELDS)


def _listed_value(setting: _Setting, config: DayzConfig) -> str:
    value = _read(setting, config)
    if setting.key in _SECRET_KEYS:
        return "<set>" if value else "<not set>"
    return _format(setting, value)


# ---------------------------------------------------------------------------
# Click commands
# ---------------------------------------------------------------------------

@click.group(name="settings")
@click.pass_context
def settings_group(ctx):  # noqa: ARG001
    """Get/set app settings (the web UI's Settings page is the browsing surface)."""
    pass


@settings_group.command(name="list")
@click.option("--section", default=None, help="Filter to a section (e.g. Base, Discord).")
@click.pass_context
def settings_list(ctx, section):  # noqa: ARG001
    """List app settings grouped by section, with current values.

    A credential (the Steam API key, the Discord webhook URL) shows as
    <set> or <not set>; `zsm settings get <key>` prints it.
    """
    config = DayzConfig.load()

    sections: dict[str, list[_Setting]] = {}
    for s in _REGISTRY:
        if section and s.section.lower() != section.lower():
            continue
        sections.setdefault(s.section, []).append(s)

    if not sections:
        print_error(f"No section matching {section!r}.")
        ctx.exit(1)

    for section_name, items in sections.items():
        table = Table(title=section_name, expand=False)
        table.add_column("Key", style="cyan")
        table.add_column("Type")
        table.add_column("Value")
        table.add_column("Description", overflow="fold")
        for s in items:
            table.add_row(s.key, s.kind, _listed_value(s, config), s.description)
        console.print(table)


@settings_group.command(name="get")
@click.argument("key")
@click.pass_context
def settings_get(ctx, key):
    """Print the current value of a single setting."""
    setting = _BY_KEY.get(key)
    if setting is None:
        print_error(f"Unknown setting: {key}. Use `zsm settings list` to see available keys.")
        ctx.exit(1)
    config = DayzConfig.load()
    console.print(_format(setting, _read(setting, config)))


# A value may start with "-" (update_max_players -1, server_args -dologs);
# without this Click reads it as an unknown option and refuses the command.
@settings_group.command(name="set", context_settings={"ignore_unknown_options": True})
@click.argument("key")
@click.argument("value")
@click.pass_context
def settings_set(ctx, key, value):
    """Update a setting and persist the config file. Booleans accept true/false."""
    setting = _BY_KEY.get(key)
    if setting is None:
        print_error(f"Unknown setting: {key}. Use `zsm settings list` to see available keys.")
        ctx.exit(1)

    parsed = _parse(setting, value)
    config = DayzConfig.load()
    _write(setting, config, parsed)
    config.save()
    print_success(f"{key} = {_format(setting, _read(setting, config))}")
