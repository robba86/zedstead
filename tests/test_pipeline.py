"""Tests for app/ops/pipeline.py — the shared update-pipeline engine."""

from __future__ import annotations

import asyncio

import pytest

from app.ops import pipeline
from app.ops.pipeline import (
    PipelineContext,
    PipelineStep,
    StepResult,
    build_update_steps,
    rollback_pipeline,
    run_pipeline,
)


class _FakeRunner:
    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode
        self.commands: list[list[str]] = []
        self.writes: list[str] = []

    async def run_command(self, cmd: list[str]) -> int:
        self.commands.append(cmd)
        return self.returncode

    def write(self, text: str) -> None:
        self.writes.append(text)


def _make_ctx(config, **overrides) -> PipelineContext:
    defaults = dict(
        config=config,
        emit=lambda s: None,
        runner=_FakeRunner(),
        should_run=lambda key: True,
        dry_run=False,
    )
    defaults.update(overrides)
    return PipelineContext(**defaults)


# ---------------------------------------------------------------------------
# run_pipeline engine semantics (fake steps — isolate the generic engine
# from the real step implementations, which get their own tests below)
# ---------------------------------------------------------------------------


async def test_run_pipeline_runs_steps_in_order(sample_config):
    calls: list[str] = []

    async def make_step(key):
        async def _run(ctx):
            calls.append(key)
        return PipelineStep(key, key, _run)

    steps = [await make_step("a"), await make_step("b"), await make_step("c")]
    ctx = _make_ctx(sample_config)

    results, _ = await run_pipeline(steps, ctx)

    assert calls == ["a", "b", "c"]
    assert [r.status for r in results] == ["success", "success", "success"]
    assert [r.key for r in results] == ["a", "b", "c"]


async def test_run_pipeline_should_run_false_skips_without_running(sample_config):
    ran = []

    async def _run(ctx):
        ran.append(True)

    steps = [PipelineStep("x", "X", _run)]
    ctx = _make_ctx(sample_config, should_run=lambda key: False)

    results, _ = await run_pipeline(steps, ctx)

    assert ran == []
    assert results[0].status == "skipped"


async def test_run_pipeline_dry_run_never_calls_step(sample_config):
    ran = []

    async def _run(ctx):
        ran.append(True)

    steps = [PipelineStep("x", "X", _run)]
    ctx = _make_ctx(sample_config, dry_run=True)

    results, _ = await run_pipeline(steps, ctx)

    assert ran == []
    assert results[0].status == "dry-run"


async def test_run_pipeline_failure_isolation(sample_config):
    """A failing step doesn't stop later steps from running (matches the
    original CLI pipeline's behavior)."""
    calls: list[str] = []

    async def _ok(ctx):
        calls.append("ok")

    async def _boom(ctx):
        calls.append("boom")
        raise RuntimeError("kaboom")

    steps = [
        PipelineStep("first", "First", _boom),
        PipelineStep("second", "Second", _ok),
    ]
    ctx = _make_ctx(sample_config)

    results, _ = await run_pipeline(steps, ctx)

    assert calls == ["boom", "ok"]
    assert results[0].status == "failed"
    assert "kaboom" in results[0].error
    assert results[1].status == "success"


async def test_run_pipeline_collects_only_successful_undoable_steps(sample_config):
    undo_calls: list[str] = []

    async def _ok(ctx):
        pass

    async def _boom(ctx):
        raise RuntimeError("nope")

    def _undo(ctx):
        undo_calls.append("undone")

    steps = [
        PipelineStep("succeeds_undoable", "A", _ok, undo=_undo),
        PipelineStep("fails_undoable", "B", _boom, undo=_undo),
        PipelineStep("succeeds_no_undo", "C", _ok),
    ]
    ctx = _make_ctx(sample_config)

    _, completed_undoable = await run_pipeline(steps, ctx)

    assert [s.key for s in completed_undoable] == ["succeeds_undoable"]
    assert undo_calls == []  # undo functions aren't called during the run


async def test_run_pipeline_stops_remaining_steps_on_cancel(sample_config):
    calls: list[str] = []
    cancel_event = asyncio.Event()

    async def _cancel_after(ctx):
        calls.append("first")
        cancel_event.set()

    async def _never_called(ctx):
        calls.append("should not run")

    steps = [
        PipelineStep("first", "First", _cancel_after),
        PipelineStep("second", "Second", _never_called),
        PipelineStep("third", "Third", _never_called),
    ]
    ctx = _make_ctx(sample_config, cancel_event=cancel_event)

    results, _ = await run_pipeline(steps, ctx)

    assert calls == ["first"]
    assert results[0].status == "success"
    assert results[1].status == "cancelled"
    assert results[2].status == "cancelled"


async def test_ctx_results_accumulate_as_steps_run(sample_config):
    """A later step can see earlier steps' results via ctx.results (used by
    the real `finalize` step to build its Discord summary)."""
    seen_at_third: list[StepResult] = []

    async def _ok(ctx):
        pass

    async def _boom(ctx):
        raise RuntimeError("x")

    async def _observe(ctx):
        seen_at_third.extend(ctx.results)

    steps = [
        PipelineStep("a", "A", _ok),
        PipelineStep("b", "B", _boom),
        PipelineStep("c", "C", _observe),
    ]
    ctx = _make_ctx(sample_config)

    await run_pipeline(steps, ctx)

    assert [r.status for r in seen_at_third] == ["success", "failed"]


# ---------------------------------------------------------------------------
# rollback_pipeline
# ---------------------------------------------------------------------------


async def test_rollback_pipeline_runs_undo_in_reverse_order(sample_config):
    order: list[str] = []

    steps = [
        PipelineStep("a", "A", run=None, undo=lambda ctx: order.append("a")),
        PipelineStep("b", "B", run=None, undo=lambda ctx: order.append("b")),
    ]
    ctx = _make_ctx(sample_config)

    await rollback_pipeline(steps, ctx)

    assert order == ["b", "a"]


async def test_rollback_pipeline_skips_steps_without_undo(sample_config):
    called = []
    steps = [PipelineStep("a", "A", run=None, undo=None)]
    ctx = _make_ctx(sample_config, emit=called.append)

    await rollback_pipeline(steps, ctx)

    assert not any("Rolling back" in msg for msg in called)


async def test_rollback_pipeline_is_best_effort(sample_config):
    """One undo raising doesn't stop the others from running."""
    order: list[str] = []

    def _boom(ctx):
        raise RuntimeError("undo failed")

    steps = [
        PipelineStep("a", "A", run=None, undo=lambda ctx: order.append("a")),
        PipelineStep("b", "B", run=None, undo=_boom),
    ]
    ctx = _make_ctx(sample_config)

    failed = await rollback_pipeline(steps, ctx)

    assert order == ["a"]
    # The caller learns which undo failed, so it never reports success.
    assert failed == ["B"]


# ---------------------------------------------------------------------------
# build_update_steps — the real steps, with steam/fileops/xml monkeypatched
# ---------------------------------------------------------------------------


def test_build_update_steps_keys_and_order(sample_config):
    steps = build_update_steps(sample_config)
    assert [s.key for s in steps] == [
        "update_server",
        "download_mods",
        "remove_old_mods",
        "copy_mods",
        "copy_bikeys",
        "merge_xml",
        "finalize",
    ]
    undoable = {s.key for s in steps if s.undo is not None}
    assert undoable == {"remove_old_mods", "copy_mods", "merge_xml"}


async def test_the_xml_step_says_apply_since_it_links_most_files(sample_config, monkeypatch):
    """The key stays `merge_xml` (the web API's contract). Label, closing line
    and failure say "apply": the step links most of a mod's files and merges
    only some, and "Merged XML configs" under a list of "Linked …" lines read
    as wrong (first-hour test, 2026 Q4). The CLI and the Update page print
    these same strings."""
    from app.ops import pipeline

    step = next(s for s in build_update_steps(sample_config) if s.key == "merge_xml")
    assert step.label == "Apply mod XML"

    monkeypatch.setattr("app.modmgr.mods.get_current_mission", lambda cfg_path: "dayzOffline.enoch")
    monkeypatch.setattr("app.modmgr.mods.require_mission", lambda config: "dayzOffline.enoch")
    monkeypatch.setattr("app.xml.xml_economy.merge_economy_core", lambda *a, **k: None)
    failed: list[str] = []
    monkeypatch.setattr("app.commands.run._merge_all_xml_configs", lambda *a, **k: failed)

    lines: list[str] = []
    ctx = pipeline.PipelineContext(
        config=sample_config, emit=lines.append, runner=None, should_run=lambda key: True
    )
    await step.run(ctx)
    assert lines == ["Mod XML applied for mission dayzOffline.enoch."]

    failed.append("cfgeventgroups")
    with pytest.raises(RuntimeError) as err:
        await step.run(ctx)
    assert str(err.value) == (
        "Mod XML could not be applied for: cfgeventgroups (target missing or invalid XML)"
    )


async def test_real_pipeline_end_to_end_with_mocks(sample_config, tmp_mission_dir, monkeypatch):
    calls: list[str] = []

    monkeypatch.setattr(
        "app.shared.steam.build_server_update_cmd", lambda config: ["echo", "update"]
    )
    monkeypatch.setattr(
        "app.modmgr.modlist.installed_and_ids", lambda config: ([], {})
    )
    monkeypatch.setattr(
        "app.ops.fileops.remove_server_mods",
        lambda config, log=None: calls.append("remove_old_mods"),
    )

    def _fake_copy_mods(config, log=None):
        calls.append("copy_mods")
        return 0

    monkeypatch.setattr("app.ops.fileops.copy_all_mods_to_server", _fake_copy_mods)
    monkeypatch.setattr(
        "app.ops.fileops.copy_bikey_files",
        lambda config, log=None: calls.append("copy_bikeys"),
    )
    monkeypatch.setattr(
        "app.modmgr.mods.get_current_mission", lambda cfg_path: "dayzOffline.chernarusplus"
    )
    monkeypatch.setattr(
        "app.xml.xml_economy.merge_economy_core",
        lambda server_dir, mission, *, manual_dir, disabled_mods=None, log=None: calls.append(
            "merge_economy_core"
        ),
    )
    monkeypatch.setattr(
        "app.commands.run._merge_all_xml_configs",
        lambda config, mission, disabled, log=None: calls.append("merge_all_xml") or [],
    )

    async def _fake_generate_service_files_async(runner, config, **kwargs):
        calls.append("finalize_service_files")
        return True

    monkeypatch.setattr(
        "app.server.service_ops.generate_service_files_async",
        _fake_generate_service_files_async,
    )
    monkeypatch.setattr(
        "app.ops.changeset_ops.take_snapshot", lambda config: calls.append("take_snapshot")
    )
    monkeypatch.setattr(
        "app.monitoring.discord_hooks.notify_update",
        lambda config, summary="": calls.append(f"notify:{summary}"),
    )

    steps = build_update_steps(sample_config)
    ctx = _make_ctx(sample_config)

    results, completed_undoable = await run_pipeline(steps, ctx)

    assert [r.status for r in results] == ["success"] * 7
    assert calls == [
        "remove_old_mods",
        "copy_mods",
        "copy_bikeys",
        "merge_economy_core",
        "merge_all_xml",
        "finalize_service_files",
        "take_snapshot",
        # finalize is step 7 of 7 — ctx.results only has the prior 6 entries
        # at the point it builds this summary (its own outcome isn't known
        # until it returns), so this deliberately reads "6 done" not "7".
        "notify:6 done, 0 skipped, 0 failed",
    ]
    assert {s.key for s in completed_undoable} == {"remove_old_mods", "copy_mods", "merge_xml"}


async def test_the_download_step_ends_with_a_line_of_its_own(sample_config, monkeypatch):
    """On Windows SteamCMD's start-up lines arrive last, so the job log ended
    "[----] Verifying installation..." under a job that had finished."""
    from app.ops import mission_stock, pipeline

    async def _download(runner, config, cmd, **kwargs):
        return await runner.run_command(cmd)

    monkeypatch.setattr("app.shared.steam.build_server_update_cmd", lambda config: ["steamcmd"])
    monkeypatch.setattr(mission_stock, "run_download", _download)
    update_step = next(s for s in build_update_steps(sample_config) if s.key == "update_server")

    lines: list[str] = []
    results, _ = await run_pipeline([update_step], _make_ctx(sample_config, emit=lines.append))
    assert results[0].status == "success"
    assert pipeline.SERVER_FILES_DONE in lines

    lines.clear()
    ctx = _make_ctx(sample_config, emit=lines.append, runner=_FakeRunner(returncode=1))
    results, _ = await run_pipeline([update_step], ctx)
    assert results[0].status == "failed"
    assert pipeline.SERVER_FILES_DONE not in lines


_STOCK_CFG = 'hostname = "EXAMPLE NAME";\nclass Missions\n{\n};\n'


async def test_an_install_writes_the_query_port_the_instance_was_given(sample_config, monkeypatch):
    """srv-04: an added instance's query port lives only in config.json until
    serverDZ.cfg exists. Installed from the web (Update, Doctor's Install
    server) or by `zsm run`, the stock cfg had no steamQueryPort, so the
    server bound 27016 beside main's while Instances and Doctor said 27116."""
    from app.ops import mission_stock
    from app.parsers.servercfg import read_cfg_value

    cfg = sample_config.server_cfg_path
    cfg.unlink()
    sample_config.steam_query_port = 27116

    async def _download(runner, config, cmd, **kwargs):
        returncode = await runner.run_command(cmd)
        if returncode == 0 and not cfg.exists():
            cfg.write_text(_STOCK_CFG)  # what a fresh SteamCMD install leaves
        return returncode

    monkeypatch.setattr("app.shared.steam.build_server_update_cmd", lambda config: ["steamcmd"])
    monkeypatch.setattr(mission_stock, "run_download", _download)
    update_step = next(s for s in build_update_steps(sample_config) if s.key == "update_server")

    lines: list[str] = []
    ctx = _make_ctx(sample_config, emit=lines.append, runner=_FakeRunner(returncode=1))
    results, _ = await run_pipeline([update_step], ctx)
    assert results[0].status == "failed"
    assert not cfg.exists()

    results, _ = await run_pipeline([update_step], _make_ctx(sample_config, emit=lines.append))
    assert results[0].status == "success"
    assert read_cfg_value(cfg, "steamQueryPort") == "27116"
    assert lines[-2:] == [pipeline.SERVER_FILES_DONE, "serverDZ.cfg steamQueryPort = 27116"]

    lines.clear()
    results, _ = await run_pipeline([update_step], _make_ctx(sample_config, emit=lines.append))
    assert results[0].status == "success"
    assert lines[-1] == pipeline.SERVER_FILES_DONE, "nothing to write the second time"
    assert cfg.read_text().count("steamQueryPort") == 1



async def test_the_download_step_skips_a_local_mod(sample_config, monkeypatch):
    """plans/78 step 9: an adopted server's local mod was searched for on the
    Workshop, found nowhere, and failed the whole update."""
    from app.modmgr import modlist

    modlist.save(sample_config, modlist.ModList([
        modlist.Mod("@CF", "1559212036"), modlist.Mod("@MyRadZone", local=True),
    ]))
    searched, downloaded = [], []
    monkeypatch.setattr("app.shared.steam.resolve_mod_id", lambda name, known: searched.append(name))

    async def _download(runner, config, mod_id, name):
        downloaded.append(name)
        return True

    monkeypatch.setattr("app.shared.steam.download_mod_async", _download)
    step = next(s for s in build_update_steps(sample_config) if s.key == "download_mods")
    lines: list[str] = []
    results, _ = await run_pipeline([step], _make_ctx(sample_config, emit=lines.append))

    assert results[0].status == "success"
    assert downloaded == ["@CF"] and searched == []
    assert any("@MyRadZone" in l and "local mod" in l for l in lines)

async def test_real_update_server_step_failure(sample_config, monkeypatch):
    monkeypatch.setattr(
        "app.shared.steam.build_server_update_cmd", lambda config: ["steamcmd"]
    )
    steps = build_update_steps(sample_config)
    update_step = next(s for s in steps if s.key == "update_server")
    ctx = _make_ctx(sample_config, runner=_FakeRunner(returncode=1))

    results, _ = await run_pipeline([update_step], ctx)

    assert results[0].status == "failed"
    assert "SteamCMD exited with code 1" in results[0].error
