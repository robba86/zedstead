"""zsm instances — the named servers this machine runs (TODO E1, plans/44 step 5).

An instance is a name in `instances.json` with its own `instances/<name>/`
config dir. Every other command acts on one of them: `--instance NAME`, or the
default this group's `use` sets. The logic lives in `app/server/instances.py`
so the panel's switcher (E2) can reuse it; this module only prints.
"""

from __future__ import annotations

import click
from rich.console import Console
from rich.table import Table

from app.server import instances
from app.shared.cli_ui import confirm, print_error, print_success, print_warning
from app.shared.config import DayzConfig
from app.shared.paths import instance_root

console = Console()


@click.group(name="instances")
def instances_group():
    """Named server instances: list, add, clone, remove, use."""


def _fail(ctx: click.Context, message: str) -> None:
    print_error(message)
    ctx.exit(1)


@instances_group.command(name="list")
def instances_list():
    """List the instances, their server dirs and ports."""
    rows = instances.instance_rows()
    if not rows:
        print_warning("No instances yet. `zsm init` creates the first one, `main`.")
        return
    table = Table(expand=False)
    for column in ("Instance", "Label", "Server dir", "Game", "Query", "RCon", "Conflicts"):
        table.add_column(column)
    for row in rows:
        name = f"{row.id} (default)" if row.default else row.id
        if row.config is None:
            table.add_row(name, row.label, "[red]config.json missing[/red]", "", "", "", "")
            continue
        ports = row.ports
        conflicts = "; ".join(row.conflicts)
        table.add_row(name, row.label, row.config.server_dir, str(ports.game), str(ports.query),
                      str(ports.rcon), f"[red]{conflicts}[/red]" if conflicts else "")
    console.print(table)


def _report_new(created: instances.NewInstance, verb: str) -> None:
    config, ports = created.config, created.ports
    print_success(f"{verb} instance '{config.instance_id}' in {config.paths.root}.")
    console.print(f"  Server dir: {config.server_dir}", markup=False)
    console.print(f"  Ports: game {ports.game}, query {ports.query}, RCon {ports.rcon}", markup=False)
    for note in created.notes:
        console.print(f"  {note}", markup=False)
    console.print(
        f"  Next: `zsm --instance {config.instance_id} setup` installs the server and "
        f"writes the query port into its serverDZ.cfg. Set RCon port {ports.rcon} in the "
        "app's BattlEye editor once the server exists.",
        markup=False,
    )


_SERVER_DIR = click.option(
    "--server-dir", required=True, metavar="DIR",
    help="The new instance's DayZ server directory. No other instance may use it.",
)
_LABEL = click.option("--label", default="", help="A display name.")
_BRANCH = click.option(
    "--branch", type=click.Choice(["stable", "experimental"]), default=None,
    help="The DayZ server it installs: stable, or Bohemia's Experimental build "
         "(only Experimental clients can join). Default: stable, or SOURCE's for clone.",
)


@instances_group.command(name="add")
@click.argument("name")
@_SERVER_DIR
@_LABEL
@_BRANCH
@click.pass_context
def instances_add(ctx, name, server_dir, label, branch):
    """Create an instance with default settings and the next free ports.

    SteamCMD, the Steam account and the workshop dir are taken from the
    default instance: they belong to this machine, not to one server.
    """
    try:
        created = instances.create_instance(name, server_dir=server_dir, label=label, branch=branch)
    except ValueError as exc:
        _fail(ctx, str(exc))
        return
    _report_new(created, "Added")


@instances_group.command(name="clone")
@click.argument("source")
@click.argument("name")
@_SERVER_DIR
@_LABEL
@_BRANCH
@click.pass_context
def instances_clone(ctx, source, name, server_dir, label, branch):
    """Copy SOURCE's settings into a new instance NAME.

    Copies config.json (with the next free ports), custom-xml/ and presets/.
    Not its backups, telemetry, versions or server files, and not what
    identifies SOURCE to the outside: the zedstead.com id and listing key and the
    Discord status message. While app accounts exist, not the app's
    fallback password either.
    """
    if not instances.load_registry().get(source):
        _fail(ctx, f"No instance named '{source}'.")
        return
    try:
        created = instances.create_instance(
            name, server_dir=server_dir, label=label, source=DayzConfig.for_instance(source),
            branch=branch,
        )
    except ValueError as exc:
        _fail(ctx, str(exc))
        return
    _report_new(created, f"Cloned '{source}' into")


@instances_group.command(name="remove")
@click.argument("name")
@click.option("--purge", is_flag=True,
              help="Also delete instances/NAME. Never the server directory.")
@click.pass_context
def instances_remove(ctx, name, purge):
    """Remove an instance from the list; its files stay unless --purge.

    Refuses the last instance, one whose server is running, and one that still
    has its systemd unit or timers installed.
    """
    registry = instances.load_registry()
    problem = instances.removal_problem(name, registry, purge=purge)
    if problem:
        _fail(ctx, problem)
        return
    # `-y` skips the question; asked, a bare Enter keeps the files.
    skip = ctx.obj.get("non_interactive", False) if ctx.obj else False
    if purge and not skip and not confirm(f"Delete {instance_root(name)} and everything in it?"):
        console.print("[dim]Cancelled.[/dim]")
        return
    try:
        root = instances.delete_instance(name, purge=purge)
    except ValueError as exc:
        _fail(ctx, str(exc))
        return
    print_success(f"Removed instance '{name}'.")
    if purge:
        console.print(f"  Deleted {root}.", markup=False)
    else:
        console.print(f"  Its files stay in {root}; `--purge` deletes them.", markup=False)
    if registry.default_id == name:
        console.print(f"  The default is now '{instances.load_registry().default_id}'.", markup=False)


@instances_group.command(name="use")
@click.argument("name")
@click.pass_context
def instances_use(ctx, name):
    """Make NAME the instance commands act on without --instance."""
    try:
        instances.save_registry(instances.set_default(instances.load_registry(), name))
    except ValueError as exc:
        _fail(ctx, str(exc))
        return
    print_success(f"'{name}' is the default instance.")
    console.print(
        "  A running app keeps the instance it started with, and installed "
        "timers keep theirs: restart the app to switch it.",
        markup=False,
    )
