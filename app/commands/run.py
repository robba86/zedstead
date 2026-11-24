"""zsm run — combined pipeline flows replacing update_mods.py."""

from __future__ import annotations

import asyncio

import click
from rich.console import Console
from rich.table import Table

from app.shared.config import DayzConfig
from app.commands.context import load_context
from app.ops.fileops import (
    copy_all_mods_to_server,
    copy_bikey_files,
    remove_server_mods,
)
from app.ops.pipeline import (
    PipelineContext,
    PipelineStep,
    build_update_steps,
    rollback_failed_message,
    rollback_pipeline,
    run_pipeline,
)
from app.modmgr.mods import MissionNotFound, get_current_mission, require_mission
from app.modmgr import modlist
from app.shared.steam import SteamcmdRefused, download_all_mods, run_passthrough, update_dayz_server
from app.shared.cli_ui import confirm, print_error, print_header, print_step, print_success, print_warning
from app.xml.xml_configs import ALL_MERGE_CONFIGS
from app.xml.xml_economy import merge_economy_core
from app.xml.xml_merge import apply_failed_message

console = Console()


class _CliRunner:
    """Runner duck-type for pipeline steps run from the CLI.

    Subprocess output still reaches the terminal as it arrives (raw SteamCMD
    progress output, as before), through `steam.run_passthrough`, which also
    keeps its tail in `last_output` so a failed SteamCMD run can say why
    (`steam.runner_no_subscription_error`).
    """

    last_output = ""

    async def run_command(self, cmd: list[str]) -> int:
        returncode, self.last_output = await asyncio.to_thread(run_passthrough, cmd)
        return returncode

    def write(self, text: str) -> None:
        console.print(text)


def _print_summary(results: list[tuple[str, str, float]]) -> bool:
    """Print pipeline summary table. Returns True if any failures."""
    console.print()
    table = Table(title="Pipeline Summary", expand=False)
    table.add_column("Step", style="cyan")
    table.add_column("Status")
    table.add_column("Duration", justify="right")

    for step_name, step_status, duration in results:
        if step_status == "success":
            style = "[green]success[/green]"
        elif step_status == "skipped":
            style = "[yellow]skipped[/yellow]"
        elif step_status == "dry-run":
            style = "[cyan]dry-run[/cyan]"
        else:
            style = "[red]failed[/red]"
        dur_str = f"{duration:.1f}s" if duration > 0 else "-"
        table.add_row(step_name, style, dur_str)

    console.print(table)
    return any(s == "failed" for _, s, _ in results)


def _offer_rollback(
    completed_undoable: list[PipelineStep],
    ctx: PipelineContext,
    ni: bool,
) -> None:
    """Offer to roll back undoable completed steps after a pipeline failure."""
    if not completed_undoable:
        return

    console.print()
    print_warning("Some steps failed. The following completed steps can be rolled back:")
    for step in completed_undoable:
        console.print(f"  [dim]• {step.label}[/dim]")
    console.print()

    if confirm("Roll back completed steps?", non_interactive=ni, default=False):
        # The shared rollback, so ctx.rollback_keys is set (BUG-016).
        failed = asyncio.run(rollback_pipeline(completed_undoable, ctx))
        if failed:
            print_error(rollback_failed_message(failed))
        else:
            print_success("Rollback complete.")


def _plain(line: str) -> None:
    """Print a line as it is: log lines quoted from the server are not markup."""
    console.print(line, markup=False)


def _check_boot(config: DayzConfig, before, ni: bool) -> None:
    """After a pipeline that ran, offer to start the server and watch it boot
    (plans/73). Asked, never assumed: this command used to leave the server
    stopped, and a script run with --yes still does."""
    from app.ops import post_update
    from app.server import service_control

    if ni or before is None or service_control.is_server_running(config):
        return
    console.print()
    if not confirm("Start the server now and check that it comes up?", default=True):
        return
    report = asyncio.run(post_update.start_and_check(config, before, emit=_plain))
    if post_update.failed(report):
        from app.commands.mods import offer_set_aside

        offer_set_aside(config, report)


def _dry_run_xml_preview(config: DayzConfig) -> None:
    """Print which mission XML files a dry-run would apply mod XML to."""
    try:
        mission = get_current_mission(config.server_cfg_path)
        if not mission:
            console.print("  [dim]  (mission not detected)[/dim]")
            return
        console.print(f"  [dim]  Mission: {mission}[/dim]")
        console.print("  [dim]  Would link mod files in: cfgeconomycore.xml[/dim]")
        for name in ALL_MERGE_CONFIGS:
            console.print(f"  [dim]  Would merge mod entries into: {name}.xml[/dim]")
    except OSError:
        pass


def _dry_run_mods_preview(config: DayzConfig) -> None:
    """Print the mod list that would be downloaded in a dry-run."""
    try:
        mod_list, _ = modlist.installed_and_ids(config)
        console.print(f"  [dim]  Would download {len(mod_list)} mod(s):[/dim]")
        for m in mod_list[:10]:
            console.print(f"  [dim]    {m}[/dim]")
        if len(mod_list) > 10:
            console.print(f"  [dim]    ... (+{len(mod_list) - 10} more)[/dim]")
    except OSError:
        pass


def _mods_pipeline(config: DayzConfig, ni: bool) -> None:
    """Download mods, remove old, copy to server, copy bikeys."""
    if confirm("Download/update mods?", default=True, non_interactive=ni):
        mod_list, cached_ids = modlist.installed_and_ids(config)
        download_all_mods(config, mod_list, cached_ids)

    if confirm("Remove old mods from server?", default=True, non_interactive=ni):
        remove_server_mods(config)

    if confirm("Copy mods to server?", default=True, non_interactive=ni):
        copy_all_mods_to_server(config)

    if confirm("Copy bikey files?", default=True, non_interactive=ni):
        copy_bikey_files(config)


def _merge_all_xml_configs(
    config: DayzConfig, mission: str, disabled_mods: set[str], log=None
) -> list[str]:
    """Merge every config in ALL_MERGE_CONFIGS.

    Returns the names of configs whose merge FAILED (merge_xml returned None
    because the target mission XML was missing or invalid).
    """
    from app.xml.xml_merge import merge_all_direct_configs

    return merge_all_direct_configs(
        config.server_dir_path, mission,
        manual_dir=config.paths.custom_xml_dir, disabled_mods=disabled_mods, log=log,
    )


def _xml_pipeline(config: DayzConfig, ni: bool) -> bool:
    """Detect the mission folder and apply mod XML: link the CE files in
    cfgeconomycore.xml, then merge into every file of ALL_MERGE_CONFIGS.
    Returns False when the mission is unknown or a merge failed, so the
    command exits 1 for a script or cron job.

    The direct half goes through `_merge_all_xml_configs` as one step, never a
    loop over `merge_xml`: each merge strips the manual files' `@user:` blocks
    too, and only `merge_all_direct_configs` puts them back."""
    try:
        mission = require_mission(config)
    except MissionNotFound as e:
        print_error(str(e))
        return False

    console.print(f"[dim]Mission folder: {mission}[/dim]")

    _disabled = modlist.disabled(config)
    manual_dir = config.paths.custom_xml_dir
    if confirm("Link mod files in cfgeconomycore.xml?", default=True, non_interactive=ni):
        merge_economy_core(
            config.server_dir_path, mission, manual_dir=manual_dir, disabled_mods=_disabled,
        )

    if confirm(
        "Merge mod and manual entries into the mission files?", default=True, non_interactive=ni
    ):
        failed = _merge_all_xml_configs(config, mission, _disabled)
        if failed:
            print_error(apply_failed_message(failed))
            return False
    return True


@click.group()
@click.pass_context
def run(ctx):
    """Run combined pipeline flows."""
    pass


@run.command(name="all")
@click.option("--dry-run", is_flag=True, help="Show what would happen without executing.")
@click.option(
    "--force",
    is_flag=True,
    help="Run even with the server up (it will be updated underneath itself).",
)
@click.pass_context
def run_all(ctx, dry_run: bool, force: bool):
    """Full update pipeline against a stopped server."""
    config, ni = load_context(ctx)

    # The web route has refused this since it existed; the CLI did not, and a
    # scheduled task took the CLI path. Rewriting mod directories and merging
    # mission XML under a live DayZServer leaves it running the old PBOs and
    # the XML it read at boot. `zsm run update` is the version that stops and
    # starts the server for you. Not gated on --yes: this is a safety check,
    # not a prompt.
    from app.server import service_control

    if not dry_run and not force and service_control.is_server_running(config):
        print_error(
            "The server is running. Stop it first, use `zsm run update` "
            "(stops, updates, starts again), or pass --force."
        )
        raise SystemExit(1)

    print_header("DayZ Server — Full Update Pipeline")
    if dry_run:
        console.print("[cyan][DRY RUN MODE — no changes will be made][/cyan]")
    console.print()

    steps = build_update_steps(config)
    total = len(steps)
    labels = {step.key: step.label for step in steps}

    def _should_run(key: str) -> bool:
        step_num = next(i for i, step in enumerate(steps, 1) if step.key == key)
        print_step(step_num, total, labels[key])
        return confirm("Proceed?", timeout=60, default=True, non_interactive=ni)

    pipeline_ctx = PipelineContext(
        config=config,
        emit=console.print,
        runner=_CliRunner(),
        should_run=_should_run,
        dry_run=dry_run,
    )

    from app.ops import post_update

    before = None if dry_run else post_update.snapshot(config)
    results, completed_undoable = asyncio.run(run_pipeline(steps, pipeline_ctx))
    console.print()

    has_failures = _print_summary([(r.label, r.status, r.duration) for r in results])
    console.print()
    if has_failures:
        print_error("Pipeline finished with errors.")
        _offer_rollback(completed_undoable, pipeline_ctx, ni)
        # A script or cron job reads the exit code, as for `run update`.
        raise SystemExit(1)
    if dry_run:
        console.print("[cyan]Dry run complete — no changes were made.[/cyan]")
    else:
        print_success("Pipeline complete. Server is ready.")
        _check_boot(config, before, ni)


@run.command(name="update-and-copy")
@click.option("--dry-run", is_flag=True, help="Show what would happen without executing.")
@click.pass_context
def update_and_copy(ctx, dry_run: bool):
    """Update server + download mods + copy to server."""
    config, ni = load_context(ctx)

    print_header("DayZ Server — Update & Copy")
    if dry_run:
        console.print("[cyan][DRY RUN MODE][/cyan]")
    console.print()

    if dry_run:
        console.print("[dim][DRY RUN] Would update DayZ server[/dim]")
        console.print("[dim][DRY RUN] Would run mods pipeline[/dim]")
        _dry_run_mods_preview(config)
        return

    if confirm("Update DayZ server?", default=True, non_interactive=ni):
        try:
            update_dayz_server(config)
        except SteamcmdRefused as e:
            raise click.ClickException(str(e)) from e
        from app.commands.setup import apply_query_port

        apply_query_port(config)

    _mods_pipeline(config, ni)
    print_success("Update & copy complete.")


@run.command(name="mods-only")
@click.option("--dry-run", is_flag=True, help="Show what would happen without executing.")
@click.pass_context
def mods_only(ctx, dry_run: bool):
    """Download/update mods and copy to server (no server update, no XML merge)."""
    config, ni = load_context(ctx)

    print_header("DayZ Server — Update & Copy Mods")
    if dry_run:
        console.print("[cyan][DRY RUN MODE][/cyan]")
    console.print()

    if dry_run:
        console.print("[dim][DRY RUN] Would run mods pipeline:[/dim]")
        _dry_run_mods_preview(config)
        return

    _mods_pipeline(config, ni)
    print_success("Mods update & copy complete.")


@run.command(name="mods-and-xml")
@click.option("--dry-run", is_flag=True, help="Show what would happen without executing.")
@click.pass_context
def mods_and_xml(ctx, dry_run: bool):
    """Download/update mods, copy to server, and apply their XML to the mission."""
    config, ni = load_context(ctx)

    print_header("DayZ Server — Update Mods & Apply Mod XML")
    if dry_run:
        console.print("[cyan][DRY RUN MODE][/cyan]")
    console.print()

    if dry_run:
        console.print("[dim][DRY RUN] Would run mods pipeline:[/dim]")
        _dry_run_mods_preview(config)
        console.print("[dim][DRY RUN] Would apply mod XML:[/dim]")
        _dry_run_xml_preview(config)
        return

    _mods_pipeline(config, ni)
    if not _xml_pipeline(config, ni):
        raise SystemExit(1)
    print_success("Mods updated and copied, mod XML applied.")


@run.command(name="xml-only")
@click.option("--dry-run", is_flag=True, help="Show what would happen without executing.")
@click.pass_context
def xml_only(ctx, dry_run: bool):
    """Apply the mods' XML to the mission without updating or copying mods."""
    config, ni = load_context(ctx)

    print_header("DayZ Server — Apply Mod XML Only")
    if dry_run:
        console.print("[cyan][DRY RUN MODE][/cyan]")
    console.print()

    if dry_run:
        console.print("[dim][DRY RUN] Would apply mod XML:[/dim]")
        _dry_run_xml_preview(config)
        return

    if not _xml_pipeline(config, ni):
        raise SystemExit(1)
    print_success("Mod XML applied.")


@run.command(name="rollback-xml")
@click.pass_context
def rollback_xml(ctx):
    """Take every mod's and every manual file's XML out of the mission.

    It strips the START/END MOD blocks and the custom/ registrations, and keeps
    Bohemia's current content and the admin's own lines. It no longer copies
    the `.backup` files: those date from the first merge, so they brought back
    an older game version's files (xml_undo, F8)."""
    config, ni = load_context(ctx)

    print_header("DayZ Server — XML Rollback")
    console.print()

    try:
        mission = require_mission(config)
    except MissionNotFound as e:
        print_error(str(e))
        raise SystemExit(1)

    console.print(f"[dim]Mission: {mission}[/dim]")
    console.print()

    if not confirm(
        "Remove every mod's and manual file's XML from the mission? The next merge puts it back.",
        non_interactive=ni, default=False,
    ):
        # -y does not confirm this step: exit 1, so a cron job does not read
        # a skipped rollback as done (F5). A person's "n" stays exit 0.
        if ni:
            raise SystemExit(1)
        return

    from app.xml.xml_undo import strip_mod_xml

    removed = strip_mod_xml(config.server_dir_path, mission, log=lambda line: console.print(line))
    print_success(f"Removed {removed} mod block(s) and custom/ registration(s) from {mission}.")


@run.command(name="update")
@click.option("--dry-run", is_flag=True, help="Show what would happen without executing.")
@click.option("--force", is_flag=True, help="Update even with players online.")
@click.pass_context
def run_update(ctx, dry_run: bool, force: bool):
    """Stop the server, run the full update pipeline, start it again.

    This is what the scheduler's "Server + Mod Update" task runs. It defers
    when players are online (see the update_max_players setting), takes a
    quick backup first, rolls the mods and XML back if the pipeline fails,
    and brings the server back up either way.
    """
    from app.ops import post_update
    from app.ops.update_cycle import run_update_cycle

    config, _ni = load_context(ctx)
    print_header("DayZ Server — Scheduled Update")
    if dry_run:
        console.print("[cyan][DRY RUN MODE — no changes will be made][/cyan]")
    console.print()

    result = asyncio.run(
        run_update_cycle(
            config,
            # Log lines quoted from the server go through here; "[CE]" is not markup.
            emit=lambda line: console.print(line, markup=False),
            runner=_CliRunner(),
            dry_run=dry_run,
            force=force,
        )
    )
    console.print()

    if not result.ran:
        print_warning(f"Update did not run: {result.deferred_reason}")
        # Exit 0: deferring because players are online is the policy working,
        # not a failure, and a timer that reports failure for it would train
        # the admin to ignore the unit's status. A stop that did not work is
        # a different matter.
        if result.error:
            raise SystemExit(1)
        return

    _print_summary([(r.label, r.status, r.duration) for r in result.results])
    console.print()
    if result.rolled_back:
        print_error("Update failed and was rolled back — the server is back on its previous mods.")
        raise SystemExit(1)
    if result.failed_steps:
        print_error("Update finished with errors.")
        raise SystemExit(1)
    if result.was_running and not result.server_started:
        print_error("Update succeeded but the server did not come back up.")
        raise SystemExit(1)
    if post_update.failed(result.boot):
        # Reports only: a scheduled run never disables a mod (plans/73).
        print_error("The update ran, but the server did not become joinable.")
        console.print("Run `zsm mods triage` to see the offer to set the suspects aside.", markup=False)
        raise SystemExit(1)
    print_success("Update complete.")
