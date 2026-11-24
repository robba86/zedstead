"""Tests for the zsm run pipeline (app/commands/run.py)."""
from __future__ import annotations

import app.commands.run as run_mod


def test_merge_all_xml_configs_collects_failures(sample_config, monkeypatch):
    failing = run_mod.ALL_MERGE_CONFIGS["cfgeventspawns"]

    def fake_merge(server_dir, mission, cfg, *, manual_dir, disabled_mods=None, log=None):
        assert manual_dir == sample_config.paths.custom_xml_dir
        return None if cfg is failing else ("ok", [])

    monkeypatch.setattr("app.xml.xml_merge.merge_xml", fake_merge)

    failed = run_mod._merge_all_xml_configs(
        sample_config, "dayzOffline.chernarusplus", set()
    )
    assert "cfgeventspawns" in failed
    assert "cfgeventgroups" not in failed


def test_merge_all_xml_configs_all_ok(sample_config, monkeypatch):
    monkeypatch.setattr("app.xml.xml_merge.merge_xml", lambda *a, **k: ("ok", []))
    failed = run_mod._merge_all_xml_configs(
        sample_config, "dayzOffline.chernarusplus", set()
    )
    assert failed == []


# ── `run all` refuses a live server; `run update` handles it (TODO C3) ──


def _invoke(command, args, config, monkeypatch, running):
    from click.testing import CliRunner

    from app.server import service_control

    monkeypatch.setattr(run_mod, "load_context", lambda ctx: (config, True))
    monkeypatch.setattr(service_control, "is_server_running", lambda cfg=None: running)
    return CliRunner().invoke(command, args)


def test_run_all_refuses_while_the_server_is_up(sample_config, monkeypatch):
    """The web route has always 409'd on this; the CLI let it through, and a
    scheduled task took the CLI path."""
    from app.commands.run import run_all

    result = _invoke(run_all, [], sample_config, monkeypatch, running=True)
    assert result.exit_code == 1
    assert "server is running" in result.output.lower()
    assert "zsm run update" in result.output


def _stub_pipeline(monkeypatch):
    """Stop `run all` from running the real pipeline.

    Patch **run_mod.run_pipeline**, not app.ops.pipeline.run_pipeline: run.py
    imports the name at module level, so patching the source module leaves the
    bound reference alone and the test silently executes SteamCMD, the XML
    merge and finalize's systemd unit write against the dev box.
    """

    async def _noop(steps, ctx):
        return [], []

    monkeypatch.setattr(run_mod, "run_pipeline", _noop)
    # The snapshot reads the workshop manifest, which sample_config does not
    # sandbox (root CLAUDE.md, Testing).
    from app.ops import post_update

    monkeypatch.setattr(post_update, "snapshot", lambda config: post_update.UpdateSnapshot(1.0, ""))


def test_run_all_force_overrides_the_guard(sample_config, monkeypatch):
    from app.commands.run import run_all

    _stub_pipeline(monkeypatch)
    result = _invoke(run_all, ["--force"], sample_config, monkeypatch, running=True)
    assert result.exit_code == 0, result.output


def test_run_all_exits_non_zero_when_a_step_failed(sample_config, monkeypatch):
    """It printed "Pipeline finished with errors." and exited 0, so a script
    or cron job went on with a half updated server."""
    from app.commands.run import run_all
    from app.ops.pipeline import StepResult

    _stub_pipeline(monkeypatch)

    async def _failed(steps, ctx):
        return [StepResult("download_mods", "Download mods", "failed", 1.0, "boom")], []

    monkeypatch.setattr(run_mod, "run_pipeline", _failed)
    result = _invoke(run_all, [], sample_config, monkeypatch, running=False)
    assert result.exit_code == 1
    assert "Pipeline finished with errors." in result.output


def test_xml_only_exits_non_zero_when_a_merge_failed(sample_config, tmp_mission_dir, monkeypatch):
    """It printed the failure, then "Mod XML applied.", and exited 0."""
    from app.commands.run import xml_only

    monkeypatch.setattr(run_mod, "get_current_mission", lambda path: "dayzOffline.chernarusplus")
    monkeypatch.setattr(run_mod, "merge_economy_core", lambda *a, **k: None)
    monkeypatch.setattr("app.xml.xml_merge.merge_xml", lambda *a, **k: None)
    result = _invoke(xml_only, [], sample_config, monkeypatch, running=False)
    assert result.exit_code == 1
    assert "Mod XML applied." not in result.output


def test_xml_only_exits_zero_when_every_merge_worked(sample_config, tmp_mission_dir, monkeypatch):
    from app.commands.run import xml_only

    monkeypatch.setattr(run_mod, "get_current_mission", lambda path: "dayzOffline.chernarusplus")
    monkeypatch.setattr(run_mod, "merge_economy_core", lambda *a, **k: None)
    monkeypatch.setattr("app.xml.xml_merge.merge_xml", lambda *a, **k: ("ok", []))
    result = _invoke(xml_only, [], sample_config, monkeypatch, running=False)
    assert result.exit_code == 0, result.output
    assert "Mod XML applied." in result.output


_MANUAL_BIKES = (
    '<?xml version="1.0" encoding="UTF-8"?>\n<eventposdef>\n'
    '\t<event name="StaticBikes">\n\t\t<pos x="1000.000000" z="2000.000000" a="0"/>\n\t</event>\n'
    "</eventposdef>\n"
)


def _mission_with_manual_block(config, mission_dir) -> None:
    """A mission whose cfgeventspawns.xml holds the manual file `bikes`'s block."""
    from app.xml.xml_manual import apply_direct_files, create_file

    (mission_dir / "cfgeventspawns.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<eventposdef>\n'
        '\t<event name="VehicleSedan">\n\t\t<pos x="10.000000" z="20.000000" a="0"/>\n\t</event>\n'
        "</eventposdef>\n",
        encoding="utf-8",
    )
    manual_dir = config.paths.custom_xml_dir
    create_file("bikes", "cfgeventspawns", _MANUAL_BIKES, manual_dir=manual_dir, terrain="chernarusplus")
    assert apply_direct_files(
        config.server_dir_path, mission_dir.name, manual_dir=manual_dir, only_file="bikes",
    ) == []
    assert "StaticBikes" in (mission_dir / "cfgeventspawns.xml").read_text(encoding="utf-8")


def test_xml_only_keeps_the_manual_files_blocks(sample_config, tmp_mission_dir, monkeypatch):
    """It looped merge_xml itself: each merge strips the `@user:` blocks, and
    only merge_all_direct_configs puts them back, so the CLI dropped them."""
    from app.commands.run import xml_only

    _mission_with_manual_block(sample_config, tmp_mission_dir)
    monkeypatch.setattr(run_mod, "merge_economy_core", lambda *a, **k: None)
    result = _invoke(xml_only, [], sample_config, monkeypatch, running=False)
    assert result.exit_code == 0, result.output
    assert "StaticBikes" in (tmp_mission_dir / "cfgeventspawns.xml").read_text(encoding="utf-8")


def test_mods_copy_keeps_the_manual_files_blocks(sample_config, tmp_mission_dir, monkeypatch):
    """`zsm mods copy` had the same loop as `run xml-only`."""
    from app.commands import mods as mods_cmd
    from app.ops import fileops

    _mission_with_manual_block(sample_config, tmp_mission_dir)
    for name in ("remove_server_mods", "copy_all_mods_to_server", "copy_bikey_files"):
        monkeypatch.setattr(fileops, name, lambda *a, **k: None)
    monkeypatch.setattr("app.xml.xml_economy.merge_economy_core", lambda *a, **k: None)
    mods_cmd._copy_mods_to_server(sample_config)
    assert "StaticBikes" in (tmp_mission_dir / "cfgeventspawns.xml").read_text(encoding="utf-8")


def test_rollback_xml_exits_non_zero_without_a_mission(sample_config, monkeypatch):
    from app.commands.run import rollback_xml

    monkeypatch.setattr("app.modmgr.mods.get_current_mission", lambda path: None)
    result = _invoke(rollback_xml, [], sample_config, monkeypatch, running=False)
    assert result.exit_code == 1
    assert "Could not determine current mission" in result.output


# ── a mission folder that does not exist (browser test 2026-11-17, F5) ──

_MISSING = "serverDZ.cfg loads dayzOffline.chernarusplus, but mpmissions/ has no such folder."


def test_xml_only_names_a_missing_mission_folder(sample_config, monkeypatch):
    """It exited 1 through a FileNotFoundError traceback from custom/'s mkdir."""
    from app.commands.run import xml_only

    result = _invoke(xml_only, [], sample_config, monkeypatch, running=False)
    assert result.exit_code == 1
    assert not isinstance(result.exception, FileNotFoundError)
    assert _MISSING in " ".join(result.output.split())
    assert not (sample_config.server_dir_path / "mpmissions" / "dayzOffline.chernarusplus").exists()


def test_rollback_xml_names_a_missing_mission_folder(sample_config, monkeypatch):
    from app.commands.run import rollback_xml

    result = _invoke(rollback_xml, [], sample_config, monkeypatch, running=False)
    assert result.exit_code == 1
    assert _MISSING in " ".join(result.output.split())


def test_rollback_xml_exits_non_zero_when_yes_skips_it(sample_config, tmp_mission_dir, monkeypatch):
    """-y does not confirm this step, and the command exited 0, so a cron job
    read a skipped rollback as done."""
    from app.commands.run import rollback_xml

    stripped: list = []
    monkeypatch.setattr("app.xml.xml_undo.strip_mod_xml", lambda *a, **k: stripped.append(a) or 0)
    result = _invoke(rollback_xml, [], sample_config, monkeypatch, running=False)
    assert result.exit_code == 1
    assert stripped == []


def test_rollback_xml_answered_no_still_exits_zero(sample_config, tmp_mission_dir, monkeypatch):
    from click.testing import CliRunner

    from app.commands.run import rollback_xml

    monkeypatch.setattr(run_mod, "load_context", lambda ctx: (sample_config, False))
    monkeypatch.setattr(run_mod, "confirm", lambda *a, **k: False)
    assert CliRunner().invoke(rollback_xml, []).exit_code == 0


def test_the_web_merge_job_fails_with_the_missing_folder_sentence(sample_config):
    """The web "Re-configure all XML" job showed the raw "[Errno 2]" line."""
    import pytest

    from app.modmgr.mods import MissionNotFound
    from app.ops import pipeline

    ctx = pipeline.PipelineContext(
        config=sample_config, emit=lambda _l: None, runner=None, should_run=lambda _k: True
    )
    with pytest.raises(MissionNotFound) as err:
        pipeline._merge_xml_sync(ctx)
    assert str(err.value) == _MISSING



def test_run_all_rollback_uses_the_shared_rollback(sample_config, monkeypatch):
    """The CLI offer runs pipeline.rollback_pipeline, so ctx.rollback_keys is
    set (BUG-016), and a failed undo prints the shared error."""
    from app.ops.pipeline import PipelineContext, PipelineStep

    seen: dict = {}

    def _undo(ctx):
        seen["keys"] = ctx.rollback_keys
        raise OSError("disk full")

    errors: list[str] = []
    monkeypatch.setattr(run_mod, "confirm", lambda *a, **k: True)
    monkeypatch.setattr(run_mod, "print_error", errors.append)
    ctx = PipelineContext(config=sample_config, emit=lambda _l: None, runner=None, should_run=lambda _k: True)

    run_mod._offer_rollback([PipelineStep("copy_mods", "Copy mods", run=None, undo=_undo)], ctx, ni=True)

    assert seen["keys"] == frozenset({"copy_mods"})
    assert errors == ["Rollback failed for: Copy mods"]

def test_run_all_dry_run_is_not_blocked(sample_config, monkeypatch):
    from app.commands.run import run_all

    _stub_pipeline(monkeypatch)
    result = _invoke(run_all, ["--dry-run"], sample_config, monkeypatch, running=True)
    assert result.exit_code == 0, result.output


def test_run_update_exits_zero_when_it_defers(sample_config, monkeypatch):
    """Deferring because players are online is the policy working. A timer
    that reports failure for it trains the admin to ignore the unit."""
    from app.commands.run import run_update
    from app.ops import update_cycle

    async def _deferred(config, **kwargs):
        return update_cycle.UpdateCycleResult(ran=False, deferred_reason="2 player(s) online")

    monkeypatch.setattr(update_cycle, "run_update_cycle", _deferred)
    result = _invoke(run_update, [], sample_config, monkeypatch, running=True)
    assert result.exit_code == 0, result.output
    assert "2 player(s) online" in result.output


def test_run_update_exits_non_zero_when_it_rolled_back(sample_config, monkeypatch):
    from app.commands.run import run_update
    from app.ops import update_cycle

    async def _rolled_back(config, **kwargs):
        return update_cycle.UpdateCycleResult(ran=True, rolled_back=True, was_running=True, server_started=True)

    monkeypatch.setattr(update_cycle, "run_update_cycle", _rolled_back)
    result = _invoke(run_update, [], sample_config, monkeypatch, running=True)
    assert result.exit_code == 1
    assert "rolled back" in result.output.lower()


def test_run_update_exits_non_zero_when_the_server_stayed_down(sample_config, monkeypatch):
    from app.commands.run import run_update
    from app.ops import update_cycle

    async def _no_restart(config, **kwargs):
        return update_cycle.UpdateCycleResult(ran=True, was_running=True, server_started=False)

    monkeypatch.setattr(update_cycle, "run_update_cycle", _no_restart)
    result = _invoke(run_update, [], sample_config, monkeypatch, running=True)
    assert result.exit_code == 1
    assert "did not come back up" in result.output


def test_run_update_exits_non_zero_when_the_boot_failed(sample_config, monkeypatch):
    """plans/73: the timer's status shows a server that did not come back."""
    from app.commands.run import run_update
    from app.ops import update_cycle

    async def _crashed(config, **kwargs):
        return update_cycle.UpdateCycleResult(
            ran=True, was_running=True, server_started=True,
            boot={"outcome": "crashed", "suspects": [{"mod": "@Broken", "lines": []}]},
        )

    monkeypatch.setattr(update_cycle, "run_update_cycle", _crashed)
    result = _invoke(run_update, [], sample_config, monkeypatch, running=True)
    assert result.exit_code == 1
    assert "zsm mods triage" in result.output


def test_run_update_exits_zero_when_the_boot_was_joinable(sample_config, monkeypatch):
    from app.commands.run import run_update
    from app.ops import update_cycle

    async def _fine(config, **kwargs):
        return update_cycle.UpdateCycleResult(
            ran=True, was_running=True, server_started=True, boot={"outcome": "joinable"},
        )

    monkeypatch.setattr(update_cycle, "run_update_cycle", _fine)
    result = _invoke(run_update, [], sample_config, monkeypatch, running=True)
    assert result.exit_code == 0, result.output
