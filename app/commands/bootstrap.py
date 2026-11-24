"""zsm bootstrap-vps — take a bare Linux VPS to "ready to run `zsm setup`".

A thin Rich renderer over `app/ops/bootstrap_ops.py`, which holds every rule
and is the only place provisioning logic lives (TODO A3). The cloud-init
template (A8) calls this command; it does not reimplement any of it.
"""

from __future__ import annotations

import click
from rich.console import Console
from rich.table import Table

from app.commands.context import load_context
from app.ops import bootstrap_ops
from app.ops.bootstrap_ops import NEEDED, OK, SKIPPED, UNKNOWN
from app.shared.cli_ui import confirm, print_error, print_header, print_success, print_warning
from app.shared.config import ConfigNotFound, DayzConfig

console = Console()

_STATE_STYLE = {
    OK: ("[green]ok[/green]", "green"),
    NEEDED: ("[yellow]to do[/yellow]", "yellow"),
    SKIPPED: ("[dim]skipped[/dim]", "dim"),
    UNKNOWN: ("[cyan]unknown[/cyan]", "cyan"),
}


@click.command(name="bootstrap-vps")
@click.option("--user", "username", default="", help="Account the server should run as.")
@click.option(
    "--apply", "do_apply", is_flag=True,
    help="Actually make the changes. Without it this only reports.",
)
@click.option(
    "--dry-run", is_flag=True,
    help="Report only — the default. Accepted so a script can say it explicitly.",
)
@click.option(
    "--firewall", is_flag=True,
    help="Also hand over to `zsm service firewall --apply` (deny-by-default).",
)
@click.pass_context
def bootstrap_vps(ctx, username: str, do_apply: bool, dry_run: bool, firewall: bool):
    """Prepare a fresh Linux VPS for zsm.

    Creates the non-root user, enables systemd lingering, adds swap on a small
    box, raises host file-handle limits and turns on NTP. Reports by default;
    pass --apply to make the changes. Every step is idempotent, so re-running
    is safe.
    """
    try:
        config, non_interactive = load_context(ctx)
    except ConfigNotFound:
        # **This command runs before `zsm init`, and that is the whole point.**
        # A8's cloud-init calls it on a box that has never seen zsm, so the
        # root group's "run `zsm init` first" gate would refuse the one
        # command whose job is to come first. The config is only read to ask
        # firewall_ops which ports a server *would* use, and the defaults
        # answer that.
        config = DayzConfig()
        non_interactive = ctx.obj.get("non_interactive", False)

    plan = bootstrap_ops.plan(config, username=username or None, firewall=firewall)
    print_header("Zedstead — VPS bootstrap")

    if plan.refusal:
        print_error(plan.refusal)
        raise SystemExit(1)

    table = Table(expand=False)
    table.add_column("Step", style="cyan")
    table.add_column("State")
    table.add_column("Detail", overflow="fold")
    for step in plan.steps:
        label, _style = _STATE_STYLE.get(step.state, (step.state, ""))
        table.add_row(step.label, label, step.detail)
    console.print(table)

    for note in plan.notes:
        console.print(f"[dim]{note}[/dim]")

    todo = plan.actionable
    if not todo:
        console.print()
        unsettled = _unsettled(plan)
        if unsettled:
            print_warning(f"Nothing Zedstead can change here, but {unsettled}")
        else:
            print_success("Nothing to do — this host is already bootstrapped.")
        return

    console.print()
    console.print("[bold]Commands:[/bold]")
    for step in todo:
        for path, _content in step.files:
            console.print(f"  write {path}", markup=False)
        for argv in step.commands:
            # soft_wrap: a line Rich wrapped at the terminal width would paste
            # as two commands.
            console.print(f"  {bootstrap_ops.display_command(argv)}", markup=False, soft_wrap=True)
        if step.key == "firewall":
            console.print("  zsm service firewall --apply", markup=False)

    if not do_apply or dry_run:
        console.print()
        # --dry-run beside --apply is a contradiction, and silently preferring
        # one would be the wrong kind of helpful.
        if dry_run and do_apply:
            print_warning("--dry-run overrides --apply. Nothing was changed.")
        else:
            print_warning("Report only. Re-run with --apply to make these changes.")
        return

    # `confirm(non_interactive=True)` returns its *default*, and the default
    # here has to be False — a destructive prompt must not say yes when someone
    # walks away. But --yes is zsm's "I mean it" flag and is exactly how
    # cloud-init (A8) drives this command, so it proceeds rather than being
    # answered with the safe default.
    if non_interactive:
        console.print("[dim]--yes given — applying without a prompt.[/dim]")
    elif not confirm("Apply these changes?", default=False):
        print_warning("Nothing was changed.")
        return

    console.print()
    code = bootstrap_ops.apply(
        plan,
        config,
        # soft_wrap for the same reason as the report: the log echoes each
        # command, and cloud-init's log is 80 columns wide (plans/40 F3).
        log=lambda line: console.print(line, markup=False, soft_wrap=True),
        assume_yes=non_interactive,
        # The firewall keeps its own gate: apply_rules shows the rules it would
        # write and refuses outright if a caller gives it neither a prompt nor
        # an explicit yes.
        confirm=lambda commands: confirm(
            "Apply these firewall rules?\n  " + "\n  ".join(commands), default=False
        ),
    )
    console.print()
    if code == 0:
        unsettled = _unsettled(plan)
        if unsettled:
            print_warning(f"Applied the rest, but {unsettled} Next: `zsm init` (or `zsm setup`).")
        else:
            print_success("Bootstrap complete. Next: `zsm init` (or `zsm setup`).")
    raise SystemExit(code)


def _unsettled(plan: bootstrap_ops.BootstrapPlan) -> str | None:
    """Name the rows zsm could not decide, for the closing line.

    An `UNKNOWN` step is never acted on, so "nothing to do" was literally true
    and still wrong: on a real VPS with no swap the report ended "this host is
    already bootstrapped" (plans/40 F2).
    """
    labels = [s.label for s in plan.steps if s.state == UNKNOWN]
    if not labels:
        return None
    rows = "its row" if len(labels) == 1 else "their rows"
    return f"this host is not fully bootstrapped: {', '.join(labels)} — see {rows} above."
