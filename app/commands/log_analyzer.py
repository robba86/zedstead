"""zsm log-analyzer — read the last server run out of its .RPT.

Presentation only. Every fact, finding and suppression rule lives in
``app/ops/log_analysis.py``, which the Doctor report and the web Analysis page
render from the same dict — see that module's docstring for what is classified
and why.
"""

from __future__ import annotations

import click
from rich.console import Console
from rich.markup import escape

from app.commands.service import SERVICE_NAME, START_BAT_NAME, SYSTEMD_DIR  # noqa: F401
from app.ops.log_analysis import (
    ORIGIN_MOD,
    ORIGIN_VANILLA,
    ORIGIN_YOURS,
    build_log_report,
)
from app.shared.cli_ui import print_step, print_success, print_warning
from app.shared.config import DayzConfig

console = Console()

_SEVERITY_STYLE = {"error": "red", "warning": "yellow", "notice": "cyan"}


def _fmt_bytes(size: int | None) -> str:
    if not size:
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def _print_run(report: dict) -> None:
    run = report.get("run") or {}
    stats = report.get("stats") or {}
    log = report.get("log") or {}

    console.print(
        f"  Log:      [bold]{log.get('name', '—')}[/bold] "
        f"({_fmt_bytes(log.get('size_bytes'))}, {log.get('lines', 0)} lines)",
        highlight=False,
    )
    if run.get("version"):
        console.print(f"  Version:  {run['version']}", highlight=False)
    if run.get("started_at"):
        console.print(f"  Started:  {run['started_at']}", highlight=False)
    if run.get("mission"):
        terrain = f" ({run['terrain']})" if run.get("terrain") else ""
        console.print(f"  Mission:  {run['mission']}{terrain}", highlight=False)
    mods = run.get("mod_args") or []
    console.print(
        f"  Mods:     {', '.join(mods) if mods else '[yellow]none in -mod=[/yellow]'}",
        highlight=False,
    )
    if run.get("server_mod_args"):
        console.print(f"  Server:   {', '.join(run['server_mod_args'])}", highlight=False)
    if run.get("ready_seconds") is not None:
        console.print(f"  Ready in: {run['ready_seconds']}s", highlight=False)
    if stats.get("loot_in_map") is not None:
        console.print(
            f"  Economy:  {stats['loot_in_map']} items in map "
            f"(nominal {stats.get('loot_nominal')}), "
            f"{stats.get('dynamic_events')} dynamic events",
            highlight=False,
        )

    outcome = run.get("outcome", "unknown")
    style = "green" if outcome == "clean_shutdown" else "yellow"
    console.print(
        f"  Outcome:  [{style}]{run.get('outcome_detail', outcome)}[/{style}]", highlight=False
    )


def _print_finding(f: dict) -> None:
    style = _SEVERITY_STYLE.get(f["severity"], "white")
    origin = f.get("origin")
    if origin in (ORIGIN_VANILLA, ORIGIN_MOD):
        style = "dim " + style
    count = f"  ×{f['count']}" if f["count"] > 1 else ""
    badge = ""
    if origin == ORIGIN_VANILLA:
        badge = " [dim italic](vanilla)[/dim italic]"
    elif origin == ORIGIN_MOD:
        badge = f" [dim italic]({escape(f.get('mod') or 'mod')})[/dim italic]"
    console.print(f"  [{style}]● {f['title']}[/{style}]{count}{badge}", highlight=False)
    if f.get("detail"):
        # Log text verbatim: markup off so a "[CE]" is not read as a style
        # tag, highlight off so Rich stops colouring the numbers inside it.
        console.print(f"      {f['detail']}", markup=False, highlight=False)
    console.print(f"      [dim]{f['why']}[/dim]", highlight=False)
    console.print(f"      [dim]→ {f['how']}[/dim]", highlight=False)
    console.print()


def _print_findings(report: dict) -> None:
    findings = report.get("findings") or []
    if not findings:
        print_success("Nothing worth reporting in this log.")
        return

    # Separate groups, never one list: the baseline half is what a stock
    # install prints on every boot, and mixing it in is what made a fresh
    # server look broken. See VANILLA_BASELINE and MOD_BASELINE in
    # app/ops/log_analysis.py.
    yours = [f for f in findings if f.get("origin", ORIGIN_YOURS) == ORIGIN_YOURS]
    vanilla = [f for f in findings if f.get("origin") == ORIGIN_VANILLA]
    from_mods = [f for f in findings if f.get("origin") == ORIGIN_MOD]

    summary = report.get("summary") or {}
    if yours:
        console.print(
            f"  [red]{summary.get('error', 0)} error(s)[/red], "
            f"[yellow]{summary.get('warning', 0)} warning(s)[/yellow], "
            f"[cyan]{summary.get('notice', 0)} notice(s)[/cyan]",
            highlight=False,
        )
        console.print()
        for f in yours:
            _print_finding(f)
    else:
        print_success("Nothing in this run needs your attention.")
        console.print()

    if vanilla:
        console.print(
            f"  [bold dim]Comes with vanilla DayZ ({len(vanilla)})[/bold dim]", highlight=False
        )
        console.print(
            "  [dim]Every stock server prints these — Bohemia's own mission files, "
            "not anything you configured.[/dim]",
            highlight=False,
        )
        console.print()
        for f in vanilla:
            _print_finding(f)

    if from_mods:
        console.print(
            f"  [bold dim]Comes with your mods ({len(from_mods)})[/bold dim]", highlight=False
        )
        console.print(
            "  [dim]Every server running these mods prints them — the mods' own files, "
            "not anything you configured.[/dim]",
            highlight=False,
        )
        console.print()
        for f in from_mods:
            _print_finding(f)


def _print_noise(report: dict) -> None:
    noise = report.get("noise") or {}
    if not noise.get("suppressed"):
        return
    labels = ", ".join(f"{c['label']} ×{c['count']}" for c in noise.get("categories", [])[:4])
    console.print(
        f"  [dim]{noise['suppressed']} cosmetic engine lines suppressed ({labels})[/dim]",
        highlight=False,
    )


def _print_mod_validation(report: dict) -> None:
    if not report.get("service_command"):
        print_warning("No installed service/start script to check the -mod list against.")
        return
    for item in report.get("mod_validation") or []:
        if item["level"] == "ok":
            print_success(item["message"])
        elif item["level"] == "warning":
            print_warning(item["message"])
        else:
            console.print(f"[red]  {item['message']}[/red]")

    mods = report.get("service_mods") or []
    if mods:
        console.print(f"\n  [bold]Mod load order ({len(mods)} mods):[/bold]")
        for i, mod in enumerate(mods, 1):
            console.print(f"    {i:>2}. {mod}", highlight=False)


@click.command(name="log-analyzer")
@click.option("--file", "file_name", default="", help="Analyse this .RPT instead of the newest one.")
@click.pass_context
def log_analyzer(ctx, file_name: str):
    """Scan the newest server log for real problems and validate mod setup."""
    from app.ops.log_analysis import profiles_dir, resolve_log

    config = DayzConfig.load()
    total_steps = 3

    print_step(1, total_steps, "Reading the latest log")
    directory = profiles_dir(config)
    if not directory.is_dir():
        print_warning(f"Profiles directory not found: {directory}")
        report = {}
    else:
        chosen = resolve_log(config, file_name) if file_name else None
        if file_name and chosen is None:
            raise click.ClickException(f"No such log in {directory}: {file_name}")
        report = build_log_report(config, chosen)
        if not report.get("latest_log"):
            print_warning("No .RPT files found in the profiles directory.")
        else:
            _print_run(report)

    console.print()
    print_step(2, total_steps, "Findings")
    _print_findings(report)
    _print_noise(report)

    console.print()
    print_step(3, total_steps, "Validating the installed mod list")
    _print_mod_validation(report)

