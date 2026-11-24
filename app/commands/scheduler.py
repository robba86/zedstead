"""zsm scheduler — run and inspect scheduled tasks.

Exists for **one** reason: ``announce`` and ``rcon`` tasks carry user-written
text, and that text must never reach the generated unit. ``ExecStart=`` is
parsed by systemd — not by a shell, but it has its own quoting rules and ``%``
specifiers, so a message containing ``%`` or a quote would be mangled or
refuse to parse, and the Windows equivalent has the same problem with its
``<Arguments>`` string.

So the unit runs ``zsm scheduler run <task id>``, which is argv-safe
(``_SAFE_ID_RE`` allows only letters, digits, ``-`` and ``_``), and this
command reads the payload back out of config.json. Nothing user-typed is ever
split into argv or handed to a shell. ``plans/07`` flagged a free-command task
type as needing exactly this review before it was built; this is the answer.

The RCon authority is deliberately not narrowed: anyone who can add a
scheduled task already has panel access, and can restart or wipe the server
from it. A scheduled ``#lock`` is the same authority as typing it, just later.
"""

from __future__ import annotations

import asyncio

import click
from rich.console import Console
from rich.markup import escape

from app.commands.context import load_context
from app.server import scheduler as sched
from app.shared.cli_ui import print_error, print_success, print_warning

console = Console()


@click.group(name="scheduler")
@click.pass_context
def scheduler_group(ctx):  # noqa: ARG001
    """Run and inspect scheduled tasks."""
    pass


@scheduler_group.command(name="list")
@click.pass_context
def scheduler_list_cmd(ctx):  # noqa: ARG001
    """List scheduled tasks."""
    from rich.table import Table

    config, _ni = load_context(ctx)
    tasks = sched.load_tasks(config)
    if not tasks:
        console.print("[yellow]No scheduled tasks.[/yellow]")
        return

    table = Table(title=f"Scheduled tasks ({len(tasks)})", expand=False)
    table.add_column("ID", style="cyan")
    table.add_column("Type")
    table.add_column("Schedule", style="dim")
    table.add_column("Next", style="dim")
    table.add_column("Enabled")
    table.add_column("Payload", style="dim", overflow="fold")
    for task in tasks:
        table.add_row(
            task.id,
            task.label,
            task.description,
            task.next_run_display,
            "yes" if task.enabled else "no",
            sched.task_payload(task) or "—",
        )
    console.print(table)


@scheduler_group.command(name="run")
@click.argument("task_id")
@click.pass_context
def scheduler_run_cmd(ctx, task_id: str):
    """Run one scheduled task's payload now (what its timer invokes)."""
    config, _ni = load_context(ctx)
    task = sched.get_task(config, task_id)
    if task is None:
        print_error(f"No scheduled task with id {task_id!r}.")
        raise SystemExit(1)

    payload = sched.task_payload(task)
    if task.task_type not in sched.PAYLOAD_TASK_TYPES:
        print_error(
            f"Task {task_id!r} is a {task.label} task — it has no payload to run. "
            f"Its timer runs `zsm {sched.TASK_COMMANDS.get(task.task_type, '')}`."
        )
        raise SystemExit(1)
    if not payload:
        # validate_task refuses to store one of these empty, so reaching here
        # means config.json was hand-edited.
        print_error(f"Task {task_id!r} has no {sched.PAYLOAD_TASK_TYPES[task.task_type]} set.")
        raise SystemExit(1)

    raise SystemExit(_run_payload(config, task, payload))


def _run_payload(config, task, payload: str) -> int:
    """Send the task's payload over RCon. Returns the process exit code."""
    from app.rcon import rcon_ops
    from app.server import service_control

    if not rcon_ops.is_rcon_configured(config):
        print_error("RCon is not configured — this task cannot send anything.")
        return 1

    # An announcement to a server that is not up is not a failure; there is
    # simply nobody to announce to. Reporting it as one would make a nightly
    # timer red for a server the admin stopped on purpose. This machine's
    # unit says nothing about a server on a remote RCon host.
    if not rcon_ops.is_remote_rcon(config) and not service_control.is_server_running(config):
        print_warning(f"Server is not running — skipping {task.label.lower()} {task.id!r}.")
        return 0

    # send_global_message for an announcement, not a hand-built `say -1 …`:
    # berconpy's own `send` is what every other broadcast in the app uses, so
    # the wire format has one owner.
    try:
        if task.task_type == "announce":
            console.print(f"[dim]Broadcast:[/dim] {escape(payload)}")
            response = asyncio.run(rcon_ops.send_global_message(config, payload))
        else:
            console.print(f"[dim]RCon:[/dim] {escape(payload)}")
            response = asyncio.run(rcon_ops.rcon_send(config, payload))
    except rcon_ops.RconError as e:
        print_error(str(e))
        return 1

    if response:
        console.print(response, markup=False)
    print_success(f"{task.label} {task.id!r} sent.")
    return 0
