"""The DayZ server update pipeline as a reusable, headless step list.

Single source of truth for "update the server": both `zsm run all` (CLI)
and `POST /api/update/run` (web) drive the SAME `build_update_steps()` list
through `run_pipeline()`, differing only in the `PipelineContext` they build
(interactive confirm + Rich console for the CLI; pre-selected step keys +
job log for the web). Keeps the two surfaces from drifting apart.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from app.shared.config import DayzConfig
from app.modmgr import modlist

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass
class PipelineContext:
    config: DayzConfig
    emit: Callable[[str], None]
    # duck-typed: async run_command(cmd) -> int; sync write(text); optional
    # last_output (str), the tail of what the last command printed
    runner: Any
    should_run: Callable[[str], bool]
    dry_run: bool = False
    cancel_event: "asyncio.Event | None" = None
    # Populated by run_pipeline as steps complete, in order. Lets a later
    # step (finalize's discord summary) see how earlier steps went.
    results: "list[StepResult]" = field(default_factory=list)
    # The mods `remove_old_mods` set aside in this run; None when this context
    # did not run it (the web's rollback endpoint builds a fresh one).
    stashed_mods: "list[str] | None" = None
    # The step keys `rollback_pipeline` is undoing, so one undo can tell
    # whether another runs after it.
    rollback_keys: "frozenset[str]" = frozenset()


@dataclass
class PipelineStep:
    key: str
    label: str
    run: Callable[[PipelineContext], Awaitable[None]]
    undo: Callable[[PipelineContext], None] | None = None
    # Runs SteamCMD, so it cannot work without a cached Steam login.
    needs_steam: bool = False


@dataclass
class StepResult:
    key: str
    label: str
    status: str  # success | skipped | failed | dry-run | cancelled
    duration: float
    error: str | None = None


# ---------------------------------------------------------------------------
# Step implementations
# ---------------------------------------------------------------------------


SERVER_FILES_DONE = "SteamCMD is done: the DayZ server files are downloaded and verified."


async def _run_update_server(ctx: PipelineContext) -> None:
    from app.ops import mission_stock
    from app.shared.steam import build_server_update_cmd, runner_no_subscription_error

    cmd = build_server_update_cmd(ctx.config)
    # validate puts back every stock mission file; this keeps the admin's (plans/67).
    # A merged mission file has no mod blocks; merge_xml puts them back, or
    # the download does when this run skips that step (plans/71).
    returncode = await mission_stock.run_download(
        ctx.runner, ctx.config, cmd, log=ctx.emit, remerge_mods=not ctx.should_run("merge_xml"),
    )
    if returncode != 0:
        raise RuntimeError(
            runner_no_subscription_error(ctx.runner, ctx.config)
            or f"SteamCMD exited with code {returncode} while updating the server."
        )
    # SteamCMD's own output is not a closing line: with its output in a pipe
    # on Windows its four start-up lines arrive last, so the log ended
    # "[----] Verifying installation..." under a finished job.
    ctx.emit(SERVER_FILES_DONE)
    await asyncio.to_thread(_apply_query_port, ctx)


def _apply_query_port(ctx: PipelineContext) -> None:
    """Write the query port an added instance was given into serverDZ.cfg,
    as `zsm setup` and the web reinstall do. Until the server is installed
    that port lives only in config.json, so an instance installed from the
    web (Update, Doctor's Install server) or by `zsm run` got a stock cfg,
    bound 27016 beside main's, while Instances and Doctor said 27116."""
    from app.shared.container import sync_query_port

    for key, value in sync_query_port(ctx.config).items():
        ctx.emit(f"serverDZ.cfg {key} = {value}")


async def _run_download_mods(ctx: PipelineContext) -> None:
    from app.modmgr import modlist
    from app.shared.steam import download_mod_async, resolve_mod_id

    mod_list, cached_ids = modlist.installed_and_ids(ctx.config)
    # A local mod has no Workshop item to fetch (plans/78 D1); searching the
    # Workshop for its name failed the whole update.
    local = modlist.local(ctx.config)
    for name in sorted(n for n in mod_list if n in local):
        ctx.emit(f"Skipped {name}: a local mod, nothing to download.")
    mod_list = [n for n in mod_list if n not in local]
    cached_names = set(cached_ids.keys())
    total = len(mod_list)
    failed: list[str] = []

    for i, mod_name in enumerate(mod_list, 1):
        if ctx.cancel_event is not None and ctx.cancel_event.is_set():
            raise RuntimeError(f"Cancelled before downloading {mod_name}.")
        mod_id = cached_ids.get(mod_name)
        if not mod_id:
            mod_id = await asyncio.to_thread(resolve_mod_id, mod_name, cached_names)
            if mod_id:
                modlist.remember_id(ctx.config, mod_name, mod_id)
                cached_names.add(mod_name)

        if not mod_id:
            ctx.emit(f"[{i}/{total}] No workshop ID found for {mod_name} — skipping.")
            failed.append(mod_name)
            continue

        ctx.emit(f"[{i}/{total}] Downloading {mod_name}...")
        ok = await download_mod_async(ctx.runner, ctx.config, mod_id, mod_name)
        if not ok:
            failed.append(mod_name)

    if failed:
        raise RuntimeError(f"Failed to download: {', '.join(failed)}")


async def _run_remove_old_mods(ctx: PipelineContext) -> None:
    from app.ops import fileops

    kept = await asyncio.to_thread(fileops.remove_server_mods, ctx.config, log=ctx.emit)
    ctx.stashed_mods = list(kept or [])


def _undo_remove_old_mods(ctx: PipelineContext) -> None:
    """Put back the versions `remove_old_mods` set aside.

    Runs after `_undo_copy_mods` removed what replaced them; without it a
    failed update left the server with no mods at all, and a boot without a
    mod deletes its saved objects. A run that set nothing aside leaves an
    older stash alone, since it is not this update's. A fresh context cannot
    tell, and restores the stash that is there.
    """
    from app.ops import mod_rollback

    if ctx.stashed_mods is not None and not ctx.stashed_mods:
        return
    if not mod_rollback.rollback_state(ctx.config)["available"]:
        ctx.emit("No previous mod versions were kept, so none were put back.")
        return
    result = mod_rollback.restore(ctx.config, log=ctx.emit)
    if not result.ok:
        raise RuntimeError(result.message)
    ctx.emit(result.message)


async def _run_copy_mods(ctx: PipelineContext) -> None:
    from app.ops import fileops

    await asyncio.to_thread(fileops.copy_all_mods_to_server, ctx.config, log=ctx.emit)


def _undo_copy_mods(ctx: PipelineContext) -> None:
    """Remove the copied mods, but only when the mods before them can come
    back: a server left with no mods deletes their saved objects at the next
    boot.

    A run whose `remove_old_mods` set nothing aside had no mods before, so the
    copied ones go. Otherwise they go only when a stash is there, and when
    `remove_old_mods`' undo is not part of this rollback, this one puts the
    stash back itself. With no stash, the copied mods stay.

    A fresh context (the web's rollback) cannot tell whose stash it is: one
    mod's reinstall leaves a stash of that mod alone. So it removes nothing
    and only puts back what the stash holds, since `restore` replaces those
    folders itself."""
    from app.ops import fileops, mod_rollback

    if ctx.stashed_mods is None:
        ctx.emit("Only the mods the stash holds go back. The other mods stay as they are.")
        if "remove_old_mods" not in ctx.rollback_keys:
            _undo_remove_old_mods(ctx)
        return
    nothing_before = not ctx.stashed_mods
    if not nothing_before and not mod_rollback.rollback_state(ctx.config)["available"]:
        ctx.emit("No previous mod versions were kept, so the copied mods stay on the server.")
        return
    # stash=False: this is the rollback of a copy, not a new update. Stashing
    # here would replace the previous-versions stash `remove_old_mods` just
    # took with the very mods being thrown away (TODO C5).
    fileops.remove_server_mods(ctx.config, log=ctx.emit, stash=False)
    if not nothing_before and "remove_old_mods" not in ctx.rollback_keys:
        _undo_remove_old_mods(ctx)


async def _run_copy_bikeys(ctx: PipelineContext) -> None:
    from app.ops import fileops

    await asyncio.to_thread(fileops.copy_bikey_files, ctx.config, log=ctx.emit)


def _merge_xml_sync(ctx: PipelineContext) -> str:
    """Runs on a worker thread via to_thread — do the actual merge here so
    the (possibly slow, file-IO-heavy) merge never blocks the event loop."""
    from app.commands.run import _merge_all_xml_configs
    from app.modmgr.mods import require_mission
    from app.traderplus.trader_common import ensure_upstream_defaults
    from app.xml.xml_economy import merge_economy_core
    from app.xml.xml_merge import apply_failed_message
    from app.xml.xml_undo import keep_before_merge, record_after_merge

    mission = require_mission(ctx.config)
    # Files @TraderPlus stopped shipping come from GitHub; the merge below applies them.
    ensure_upstream_defaults(ctx.config, log=ctx.emit)

    disabled = modlist.disabled(ctx.config)
    # What the undo puts back, never the first merge's `.backup` (xml_undo).
    keep_before_merge(ctx.config, mission)
    merge_economy_core(
        ctx.config.server_dir_path, mission,
        manual_dir=ctx.config.paths.custom_xml_dir, disabled_mods=disabled, log=ctx.emit,
    )
    failed = _merge_all_xml_configs(ctx.config, mission, disabled, log=ctx.emit)
    if failed:
        raise RuntimeError(apply_failed_message(failed))
    record_after_merge(ctx.config, mission)
    return mission


async def _run_merge_xml(ctx: PipelineContext) -> None:
    mission = await asyncio.to_thread(_merge_xml_sync, ctx)
    ctx.emit(f"Mod XML applied for mission {mission}.")


def _undo_merge_xml(ctx: PipelineContext) -> None:
    """Put back the mission files as the last merge found them. It raises when
    none were kept, so the rollback reports a failure instead of "done"."""
    from app.modmgr.mods import require_mission
    from app.xml.xml_undo import undo_merge

    mission = require_mission(ctx.config)
    undo_merge(ctx.config, mission, log=ctx.emit)


async def _run_finalize(ctx: PipelineContext) -> None:
    from app.ops.changeset_ops import take_snapshot
    from app.server.service_ops import generate_service_files_async

    ok = await generate_service_files_async(ctx.runner, ctx.config)
    await asyncio.to_thread(take_snapshot, ctx.config)

    try:
        from app.monitoring.discord_hooks import notify_update

        done = sum(1 for r in ctx.results if r.status == "success")
        skipped = sum(1 for r in ctx.results if r.status == "skipped")
        failed = sum(1 for r in ctx.results if r.status == "failed")
        summary = f"{done} done, {skipped} skipped, {failed} failed"
        await asyncio.to_thread(notify_update, ctx.config, summary=summary)
    except Exception:
        pass

    if not ok:
        raise RuntimeError(
            "Service file install failed (see log above) — the installed unit "
            "is stale until `zsm service install` succeeds."
        )


def build_update_steps(config: DayzConfig) -> list[PipelineStep]:
    """The 7-step update pipeline. Keys are a stable contract (used by the
    web API's `steps: [key, ...]` request bodies) — do not rename."""
    return [
        PipelineStep("update_server", "Update DayZ server", _run_update_server, needs_steam=True),
        PipelineStep("download_mods", "Download/update mods", _run_download_mods, needs_steam=True),
        PipelineStep(
            "remove_old_mods", "Remove old mods from server", _run_remove_old_mods, undo=_undo_remove_old_mods,
        ),
        PipelineStep("copy_mods", "Copy mods to server", _run_copy_mods, undo=_undo_copy_mods),
        PipelineStep("copy_bikeys", "Copy bikey files", _run_copy_bikeys),
        # The key stays `merge_xml` (the contract above). The label says
        # "apply": the step links most files and merges only some.
        PipelineStep("merge_xml", "Apply mod XML", _run_merge_xml, undo=_undo_merge_xml),
        PipelineStep("finalize", "Finalize (service files, snapshot, notify)", _run_finalize),
    ]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


async def run_pipeline(
    steps: list[PipelineStep], ctx: PipelineContext
) -> tuple[list[StepResult], list[PipelineStep]]:
    """Run *steps* in order against *ctx*.

    Does NOT stop on a step failure (matches the original CLI pipeline:
    later steps still run so e.g. mods get copied even if the server update
    step failed) — the caller decides what a failure means. A set
    ctx.cancel_event DOES stop the pipeline: remaining steps are marked
    "cancelled" rather than run.

    Returns (results, completed_undoable) — completed_undoable is every
    successfully-run step that has an `undo`, in run order (reverse it to
    build a rollback plan).
    """
    from app.ops.spawn_test_ops import active_test_warning

    completed_undoable: list[PipelineStep] = []

    # A reminder, never a refusal: an update run is also how a test server gets
    # its mods in the first place.
    spawn_test_warning = active_test_warning(ctx.config)
    if spawn_test_warning:
        ctx.emit(spawn_test_warning)

    for step in steps:
        if ctx.cancel_event is not None and ctx.cancel_event.is_set():
            ctx.results.append(StepResult(step.key, step.label, "cancelled", 0.0))
            continue

        if not ctx.should_run(step.key):
            ctx.results.append(StepResult(step.key, step.label, "skipped", 0.0))
            continue

        if ctx.dry_run:
            ctx.emit(f"[DRY RUN] Would run: {step.label}")
            ctx.results.append(StepResult(step.key, step.label, "dry-run", 0.0))
            continue

        start = time.monotonic()
        try:
            await step.run(ctx)
            duration = time.monotonic() - start
            ctx.results.append(StepResult(step.key, step.label, "success", duration))
            if step.undo is not None:
                completed_undoable.append(step)
        except Exception as e:
            duration = time.monotonic() - start
            # A step cut short by the cancel (a download loop that stopped) is
            # cancelled, not failed.
            if ctx.cancel_event is not None and ctx.cancel_event.is_set():
                ctx.results.append(StepResult(step.key, step.label, "cancelled", duration, str(e)))
                ctx.emit(f"Cancelled: {e}")
                continue
            ctx.results.append(StepResult(step.key, step.label, "failed", duration, str(e)))
            ctx.emit(f"Failed: {e}")

    return ctx.results, completed_undoable


async def rollback_pipeline(steps: list[PipelineStep], ctx: PipelineContext) -> list[str]:
    """Undo *steps* in reverse order. No auto-rollback — callers decide when
    to invoke this (the CLI offers it after a failed run; the web exposes it
    as its own endpoint).

    Best effort: one failed undo does not stop the others. Returns the labels
    of the undos that failed, so a caller never reports a rollback that left
    the server without its mods as a success."""
    ctx.rollback_keys = frozenset(step.key for step in steps if step.undo is not None)
    failed: list[str] = []
    for step in reversed(steps):
        if step.undo is None:
            continue
        ctx.emit(f"Rolling back: {step.label}...")
        try:
            await asyncio.to_thread(step.undo, ctx)
            ctx.emit(f"Rolled back: {step.label}")
        except Exception as e:
            ctx.emit(f"Rollback failed for {step.label}: {e}")
            failed.append(step.label)
    return failed


def rollback_failed_message(failed: list[str]) -> str:
    """The one error text for a rollback whose undos in *failed* raised."""
    return "Rollback failed for: " + ", ".join(failed)
