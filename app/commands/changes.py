"""CLI command: show what changed since last server start and since setup."""

import click
from rich.console import Console


@click.command("changes")
@click.option("--baseline", is_flag=True, help="Show changes since setup baseline.")
def changes_cmd(baseline: bool):
    """Show what changed since the last server start (or since setup with --baseline)."""
    from app.ops.changeset_ops import (
        compute_baseline_changes,
        compute_changes,
        format_change_report,
    )
    from app.shared.config import DayzConfig

    console = Console()
    config = DayzConfig.load()

    if baseline:
        report = compute_baseline_changes(config)
        if report is None:
            console.print(
                "[dim]No baseline snapshot yet — one is created automatically "
                "on first server start, or take one from the web UI's Analysis page.[/dim]"
            )
            return
        for line in format_change_report(report, label="setup"):
            console.print(line)
        return

    # Default: show both reports
    report = compute_changes(config)
    if report is None:
        console.print(
            "[dim]No snapshot yet — one is taken automatically "
            "when you start the server.[/dim]"
        )
    else:
        console.print("[bold underline]Since Last Start[/bold underline]")
        for line in format_change_report(report, label="last start"):
            console.print(line)

    baseline_report = compute_baseline_changes(config)
    if baseline_report is not None:
        if report is not None:
            console.print()
        console.print("[bold underline]Since Setup[/bold underline]")
        for line in format_change_report(baseline_report, label="setup"):
            console.print(line)
