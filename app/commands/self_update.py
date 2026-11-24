"""zsm self-update — update zsm itself (the panel's "Update Zedstead" card)."""

from __future__ import annotations

import click
from rich.console import Console
from rich.markup import escape

from app.ops import self_update
from app.shared.cli_ui import confirm, print_error, print_success, print_warning

# soft_wrap: a command Rich wraps at the terminal width pastes as two lines.
console = Console(soft_wrap=True)


def _panel_restart_hint() -> str | None:
    """The command that restarts the panel's unit, when one runs it."""
    from app.server.panel_unit import panel_commands, panel_state
    from app.shared.config import ConfigNotFound, DayzConfig

    try:
        config = DayzConfig.load()
    except ConfigNotFound:
        return None
    if panel_state(config).get("active") != "active":
        return None
    return panel_commands(config)[0]


@click.command("self-update")
@click.option("--check", "check_only", is_flag=True, help="Only show what an update would bring.")
@click.pass_context
def self_update_cmd(ctx, check_only):
    """Update zsm from the branch it was installed from."""
    info = self_update.check()
    if not info.supported and info.reason != "windows":
        print_warning(info.message)
        console.print(info.command, markup=False)
        ctx.exit(1)
    if info.error:
        print_error(info.error)
        ctx.exit(1)
    if not info.behind:
        print_success(f"Zedstead is up to date ({info.branch} at {info.current}).")
        return
    console.print(f"{info.behind} new change(s) on {escape(info.branch)} ({info.current} -> {info.latest}):")
    for subject in info.subjects:
        console.print(f"  - {subject}", markup=False)
    if info.behind > len(info.subjects):
        console.print(f"  … and {info.behind - len(info.subjects)} more", markup=False)
    if info.reason == "windows":
        print_warning(info.message)
        console.print(info.command, markup=False)
        return
    if check_only:
        return
    if not ctx.obj.get("non_interactive") and not confirm("Update now?", default=True):
        return
    try:
        result = self_update.apply(lambda line: console.print(line, markup=False))
    except self_update.SelfUpdateError as e:
        print_error(str(e))
        ctx.exit(1)
    if result["changed"]:
        restart = _panel_restart_hint()
        if restart:
            print_warning("The app still runs the old version. Restart it:")
            console.print(restart, markup=False)
        else:
            console.print("Restart zsm web, if it is running, to use the new version.")
