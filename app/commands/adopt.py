"""zsm adopt — take over a DayZ server zsm did not install (plans/78).

Reads the server by default and changes nothing. `--apply` writes zsm's mod
list and launch settings from what the server runs with; the fixes are their
own flags and run only when given. Every name shown comes from the server's
folders and files, so it goes through `rich.markup.escape`.
"""

from __future__ import annotations

import click
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from app.commands.context import load_context
from app.ops import adopt
from app.shared.cli_ui import confirm, print_error, print_success, print_warning
from app.shared.config import DayzConfig
from app.shared.systemd_scope import zsm_command

console = Console()


def _plain(line: str) -> None:
    console.print(line, markup=False)


def print_report(report: adopt.Report, found: dict | None, config: DayzConfig | None = None) -> None:
    launch = report.launch
    console.print(f"[bold]Server:[/bold] {escape(report.server_dir)}")
    if launch is None:
        print_warning("No launch command found (no server log, unit or start script).")
    else:
        console.print(f"[bold]Launch command[/bold] (from the {launch.source_label}, {escape(launch.path)}):")
        _plain(f"  {launch.command}")
        if launch.uses_variables:
            print_warning("It reads shell variables, so which mods it loads is not in the file.")
    console.print(f"[bold]Mission:[/bold] {escape(report.mission or 'not found')}")

    if report.mods:
        table = Table(title="Mod folders", expand=False)
        for column in ("Folder", "Workshop ID", "Loaded", "Note"):
            table.add_column(column)
        for m in report.mods:
            loaded = "server-side" if m.server_only else ("yes" if m.loaded else "no")
            note = m.problem or ("local mod (no Workshop ID)" if m.local else "")
            table.add_row(escape(m.name), escape(m.workshop_id or "-"), loaded, escape(note))
        console.print(table)
    for name in report.missing:
        print_warning(f"{escape(name)} is in the launch command and has no folder.")
    for u in report.units:
        whose = "Zedstead's" if u.zsm else "your own"
        console.print(f"Unit: {escape(u.name)} ({u.scope}, {whose}) {escape(u.path)}")
    for p in report.pasted:
        if p.classes:
            console.print(
                f"Pasted into db/types.xml: {escape(p.mod)}, {len(p.classes)} class(es) "
                f"({escape(', '.join(p.classes[:5]))}{', …' if len(p.classes) > 5 else ''})"
            )
        if p.changed:
            console.print(
                f"In db/types.xml, different from {escape(p.mod)}'s entry (DayZ's own, or a paste "
                f"you changed): {escape(', '.join(p.changed))}"
            )

    if found is None:
        return
    table = Table(title="Checks", expand=False)
    table.add_column("Check", style="cyan")
    table.add_column("Status", justify="center")
    table.add_column("Detail", overflow="fold")
    for row in found["checks"]:
        status = "[green]OK[/green]" if row["ok"] else "[red]FAIL[/red]"
        table.add_row(escape(row["label"]), status, escape(row["detail"]))
    console.print(table)
    for s in found["skipped"]:
        console.print(f"[dim]Skipped {escape(s['label'])}: {escape(s['reason'])}.[/dim]")
    log = found.get("log") or {}
    summary = log.get("summary") or {}
    if log.get("latest_log"):
        console.print(
            f"Log {escape(log['latest_log'])}: {summary.get('error', 0)} error(s), "
            f"{summary.get('warning', 0)} warning(s). Details: {zsm_command(config, 'log-analyzer')}"
        )


def _fix_hints(config: DayzConfig, report: adopt.Report, found: dict | None) -> None:
    """The flags that fix what the checks found."""
    fixes = {row.get("fix") for row in (found or {}).get("checks", [])}
    cmd = escape(zsm_command(config, "adopt"))
    if adopt.FIX_COPY_KEYS in fixes:
        console.print(f"[dim]Copy the missing keys: {cmd} --copy-keys[/dim]")
    for p in report.pasted:
        console.print(
            f"[dim]{escape(p.mod)}: keep yours with {cmd} --keep-pasted \"{escape(p.mod)}\", "
            f"or remove yours with {cmd} --remove-pasted \"{escape(p.mod)}\"[/dim]"
        )
    for u in adopt.offered_units(report):
        console.print(
            f"[dim]{escape(u.name)} runs this server under another name: "
            f"stop it from starting at boot with {cmd} --disable-unit {escape(u.name)}[/dim]"
        )


def _pasted(report: adopt.Report, name: str) -> adopt.PastedTypes:
    entry = next((p for p in report.pasted if p.mod == name), None)
    if entry is None:
        raise click.BadParameter(f"{name} has no types pasted into the mission.")
    return entry


def apply_with_prompt(config: DayzConfig, report: adopt.Report, *, ni: bool) -> bool:
    """Ask once, then apply. Shared by `zsm adopt --apply` and init."""
    if not confirm(
        "Write Zedstead's mod list and launch settings from this server? "
        "Nothing on the server is moved or changed.",
        default=True, non_interactive=ni,
    ):
        return False
    try:
        lines = adopt.apply(config, report)
    except adopt.AdoptError as exc:
        print_error(escape(str(exc)))
        return False
    for line in lines:
        _plain(line)
    print_success("Adopted. The checks that read the mod list now run in zsm doctor.")
    return True


@click.command(name="adopt")
@click.option("--apply", "apply_", is_flag=True, help="Write the mod list and launch settings.")
@click.option("--copy-keys", is_flag=True, help="Copy loaded mods' keys that keys/ lacks.")
@click.option("--keep-pasted", "keep", metavar="MOD", multiple=True,
              help="Keep the mission's pasted types of MOD and turn the mod's file off.")
@click.option("--remove-pasted", "remove", metavar="MOD", multiple=True,
              help="Remove the mission's pasted types of MOD and let Zedstead add the mod's.")
@click.option("--disable-unit", "disable", metavar="NAME", multiple=True,
              help="Stop your own unit NAME from starting at boot (its file stays).")
@click.option("--no-checks", is_flag=True, help="Report only; skip Doctor and the log analysis.")
@click.pass_context
def adopt_cmd(ctx, apply_, copy_keys, keep, remove, disable, no_checks):
    """Read a server zsm did not install, and adopt it."""
    config, ni = load_context(ctx)
    report = adopt.scan(config)
    acting = apply_ or copy_keys or keep or remove or disable

    if not acting:
        found = None if no_checks else adopt.findings(config, report)
        print_report(report, found, config)
        console.print()
        console.print(f"[dim]Adopt it: {escape(zsm_command(config, 'adopt --apply'))}[/dim]")
        _fix_hints(config, report, found)
        return

    try:
        for name in keep:
            for line in adopt.keep_pasted(config, _pasted(report, name)):
                _plain(line)
        for name in remove:
            for line in adopt.remove_pasted(config, report.mission, _pasted(report, name)):
                _plain(line)
        if copy_keys:
            for line in adopt.copy_missing_keys(config, report):
                _plain(line)
        for name in disable:
            for line in adopt.disable_unit(report, name):
                _plain(line)
    except adopt.AdoptError as exc:
        raise click.ClickException(str(exc)) from exc
    if apply_:
        apply_with_prompt(config, report, ni=ni)
