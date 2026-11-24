"""Keeping the admin's edits to stock mission files through a server download (plans/67)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.ops import mission_stock
from app.shared.config_versions import load_index

CHERNARUS = "dayzOffline.chernarusplus"
LIVONIA = "dayzOffline.enoch"

# What "Steam" writes: every Steam-owned file of both missions.
STOCK = {
    f"{CHERNARUS}/db/globals.xml": '<variables><var name="TimeLogin" value="15"/></variables>\n',
    f"{CHERNARUS}/db/types.xml": "<types><type name=\"Apple\"><nominal>10</nominal></type></types>\n",
    f"{CHERNARUS}/cfgeconomycore.xml": "<economycore></economycore>\n",
    f"{CHERNARUS}/env/zombie_territories.xml": "<territory-type/>\n",
    f"{CHERNARUS}/init.c": "void main() {}\n",
    f"{LIVONIA}/db/globals.xml": '<variables><var name="TimeLogin" value="15"/></variables>\n',
    f"{LIVONIA}/db/types.xml": "<types><type name=\"Pear\"><nominal>4</nominal></type></types>\n",
}


def _install(config, stock: dict[str, str], buildid: str) -> None:
    """A download: Steam's files and build id, as validate leaves them."""
    root = config.server_dir_path / "mpmissions"
    for rel, text in stock.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    steamapps = config.server_dir_path / "steamapps"
    steamapps.mkdir(exist_ok=True)
    (steamapps / "appmanifest_223350.acf").write_text(
        f'"AppState"\n{{\n\t"appid"\t\t"223350"\n\t"buildid"\t\t"{buildid}"\n}}\n'
    )
    # Not Steam's: validate never touches these, and neither may we.
    mission = root / CHERNARUS
    (mission / "areaflags.map").write_bytes(b"\x00binary")
    (mission / "custom").mkdir(exist_ok=True)
    (mission / "storage_1").mkdir(exist_ok=True)


def _path(config, rel):
    return config.server_dir_path / "mpmissions" / rel


def _edit(config, rel, text):
    _path(config, rel).write_text(text)


def _download(config, stock=STOCK, buildid="100", *, resetting=None, ok=True, log=None):
    stash = mission_stock.before_download(config, resetting=resetting)
    if not ok:
        mission_stock.download_failed(config, stash)
        return None
    _install(config, stock, buildid)
    # The fixture mission is not a whole mission, so the mods merge is left out.
    return mission_stock.after_download(config, stash, log=log, remerge_mods=False)


def _forget_stock(config):
    """No old stock to merge against: the whole-file choice of plans/67 runs."""
    import shutil

    shutil.rmtree(config.paths.mission_stock_dir / "stock")


@pytest.fixture()
def server(sample_config):
    """A server installed once, so the record exists. On "ask", the default
    plans/67's tests were written for (plans/71 made "mine" the default)."""
    sample_config.mission_update_conflict = "ask"
    _install(sample_config, STOCK, "100")
    mission_stock.after_download(sample_config, mission_stock.before_download(sample_config))
    return sample_config


EDITED_GLOBALS = '<variables><var name="TimeLogin" value="11"/></variables>\n'


# --- which files ------------------------------------------------------------


def test_only_steams_files_are_owned(server):
    mission = server.server_dir_path / "mpmissions" / CHERNARUS
    (mission / ".zsm-tweaks.json").write_text("{}")
    (mission / "db" / "types.xml.backup").write_text("old")
    (mission / "custom" / "mine.xml").write_text("<x/>")
    (mission / "storage_1" / "players.json").write_text("{}")
    names = {p.relative_to(mission).as_posix() for p in mission_stock.owned_files(mission)}
    assert names == {"db/globals.xml", "db/types.xml", "cfgeconomycore.xml", "env/zombie_territories.xml", "init.c"}


def test_sakhals_pra_folder_and_config_cpp_are_steams_too(server):
    """Depot 223354 lists `dayzOffline.sakhal/config.cpp` and `pra/warheadstorage.json`."""
    mission = server.server_dir_path / "mpmissions" / LIVONIA
    (mission / "pra").mkdir()
    (mission / "pra" / "warheadstorage.json").write_text("{}")
    (mission / "config.cpp").write_text("class CfgMissions {};")
    names = {p.relative_to(mission).as_posix() for p in mission_stock.owned_files(mission)}
    assert {"pra/warheadstorage.json", "config.cpp"} <= names


def test_only_stock_missions_are_protected(server):
    other = server.server_dir_path / "mpmissions" / "empty.deerisle"
    (other / "db").mkdir(parents=True)
    assert [m.name for m in mission_stock.stock_missions(server)] == [CHERNARUS, LIVONIA]


def test_a_mission_the_catalog_knows_only_by_terrain_is_protected(server, unreleased_map):
    # An announced map has no mission in the catalog until a zsm release names
    # it, so its folder is found by terrain once the update that brings it has
    # run, and an edit to it then survives the next update like any other.
    new = {"dayzOffline.testlands/db/types.xml": "<types><type name=\"Fig\"/></types>\n"}
    _download(server, {**STOCK, **new}, buildid="101")
    assert "dayzOffline.testlands" in [m.name for m in mission_stock.stock_missions(server)]

    _edit(server, "dayzOffline.testlands/db/types.xml", "<types><type name=\"Mine\"/></types>\n")
    _download(server, {**STOCK, **new}, buildid="102")
    assert _path(server, "dayzOffline.testlands/db/types.xml").read_text() == (
        "<types><type name=\"Mine\"/></types>\n"
    )


def test_the_record_holds_the_stock_hashes(server):
    record = json.loads((server.paths.mission_stock_dir / "stock.json").read_text())
    assert record["app_id"] == "223350"
    assert record["buildid"] == "100"
    assert set(record["files"]) == set(STOCK)


# --- an edit, the same build --------------------------------------------------


def test_an_edit_survives_an_update_bohemia_did_not_change(server):
    _edit(server, f"{CHERNARUS}/db/globals.xml", EDITED_GLOBALS)
    outcome = _download(server)
    assert outcome.restored == [f"{CHERNARUS}/db/globals.xml"]
    assert outcome.conflicts == []
    assert _path(server, f"{CHERNARUS}/db/globals.xml").read_text() == EDITED_GLOBALS
    # The record still holds the stock hash, so the next update keeps it again.
    assert _download(server).restored == [f"{CHERNARUS}/db/globals.xml"]


def test_an_unedited_file_is_not_copied(server):
    stash = mission_stock.before_download(server)
    assert stash.copied == {}
    assert not (server.paths.mission_stock_dir / "pending").exists()


def test_a_file_validate_leaves_alone_is_not_put_back(server):
    """A Steam-owned file the download did not replace (validate skipped it)."""
    _edit(server, f"{CHERNARUS}/init.c", "void main() { edited(); }\n")
    stash = mission_stock.before_download(server)
    outcome = mission_stock.after_download(server, stash)
    assert outcome.restored == [] and outcome.conflicts == []
    assert "edited" in _path(server, f"{CHERNARUS}/init.c").read_text()


def test_the_copies_are_gone_after_a_good_download(server):
    _edit(server, f"{CHERNARUS}/db/globals.xml", EDITED_GLOBALS)
    _download(server)
    assert not (server.paths.mission_stock_dir / "pending").exists()


# --- an edit, Bohemia changed the file, and no old stock to merge against -------


NEW_BUILD = {**STOCK, f"{CHERNARUS}/db/globals.xml": '<variables><var name="TimeLogin" value="20"/></variables>\n'}


def test_ask_keeps_dayzs_file_live_and_holds_the_admins(server):
    _edit(server, f"{CHERNARUS}/db/globals.xml", EDITED_GLOBALS)
    _forget_stock(server)
    outcome = _download(server, NEW_BUILD, "200")
    assert outcome.conflicts == [f"{CHERNARUS}/db/globals.xml"]
    assert outcome.policy == "ask"
    assert _path(server, f"{CHERNARUS}/db/globals.xml").read_text() == NEW_BUILD[f"{CHERNARUS}/db/globals.xml"]
    [row] = mission_stock.list_conflicts(server)
    assert row["file"] == f"{CHERNARUS}/db/globals.xml"
    assert (row["build_before"], row["build_after"]) == ("100", "200")


def test_a_conflict_saves_both_versions(server):
    _edit(server, f"{CHERNARUS}/db/globals.xml", EDITED_GLOBALS)
    _forget_stock(server)
    _download(server, NEW_BUILD, "200")
    entries = load_index(server.paths.versions_dir).get_versions("globals.xml")
    notes = [e.note for e in entries]
    assert "The version you had before DayZ build 200" in notes
    assert "DayZ build 200's new file" in notes
    # Newest first, and the live file (DayZ's) is the newest.
    assert entries[0].note == "DayZ build 200's new file"


def test_keep_mine_puts_the_held_version_back(server):
    _edit(server, f"{CHERNARUS}/db/globals.xml", EDITED_GLOBALS)
    _forget_stock(server)
    _download(server, NEW_BUILD, "200")
    assert mission_stock.resolve(server, None, "mine") == [f"{CHERNARUS}/db/globals.xml"]
    assert _path(server, f"{CHERNARUS}/db/globals.xml").read_text() == EDITED_GLOBALS
    assert mission_stock.list_conflicts(server) == []


def test_keep_dayz_drops_the_held_version(server):
    _edit(server, f"{CHERNARUS}/db/globals.xml", EDITED_GLOBALS)
    _forget_stock(server)
    _download(server, NEW_BUILD, "200")
    mission_stock.resolve(server, [f"{CHERNARUS}/db/globals.xml"], "dayz")
    assert _path(server, f"{CHERNARUS}/db/globals.xml").read_text() == NEW_BUILD[f"{CHERNARUS}/db/globals.xml"]
    assert mission_stock.list_conflicts(server) == []
    assert not any((server.paths.mission_stock_dir / "conflicts").rglob("*.xml"))


def test_resolve_ignores_a_name_that_is_not_waiting(server):
    _edit(server, f"{CHERNARUS}/db/globals.xml", EDITED_GLOBALS)
    _forget_stock(server)
    _download(server, NEW_BUILD, "200")
    assert mission_stock.resolve(server, ["../../../etc/passwd", f"{CHERNARUS}/init.c"], "mine") == []
    assert len(mission_stock.list_conflicts(server)) == 1


def test_resolve_refuses_an_unknown_side(server):
    with pytest.raises(ValueError):
        mission_stock.resolve(server, None, "both")


# --- the diff between the two -------------------------------------------------


MOD_BLOCK = '\n<!-- START MOD: @CF -->\n<type name="CF_Thing"><nominal>1</nominal></type>\n<!-- END MOD: @CF -->\n'


def test_the_diff_shows_the_admins_lines_as_plus_and_dayzs_as_minus(server):
    _edit(server, f"{CHERNARUS}/db/globals.xml", EDITED_GLOBALS)
    _forget_stock(server)
    _download(server, NEW_BUILD, "200")
    lines = mission_stock.conflict_diff(server, f"{CHERNARUS}/db/globals.xml").splitlines()
    assert lines[0].endswith("(DayZ's new file, live)")
    assert lines[1].endswith("(the version you had)")
    assert any(line.startswith("+") and 'value="11"' in line for line in lines[2:])
    assert any(line.startswith("-") and 'value="20"' in line for line in lines[2:])


def test_the_diff_leaves_mod_blocks_out(server):
    rel = f"{CHERNARUS}/db/types.xml"
    _edit(server, rel, '<types><type name="Apple"><nominal>99</nominal></type>' + MOD_BLOCK + "</types>\n")
    _forget_stock(server)
    _download(server, {**STOCK, rel: '<types><type name="Apple"><nominal>12</nominal></type></types>\n'}, "200")
    diff = mission_stock.conflict_diff(server, rel)
    assert "<nominal>99</nominal>" in diff and "<nominal>12</nominal>" in diff
    assert "CF_Thing" not in diff


def test_an_empty_diff_means_only_mod_blocks_differ(sample_config):
    """No record and a new build: a file only the mods changed is a conflict too."""
    sample_config.mission_update_conflict = "ask"
    _install(sample_config, STOCK, "100")
    rel = f"{CHERNARUS}/db/types.xml"
    _edit(sample_config, rel, STOCK[rel].replace("</types>", MOD_BLOCK + "</types>"))
    _download(sample_config, NEW_BUILD, "200")
    assert rel in [row["file"] for row in mission_stock.list_conflicts(sample_config)]
    assert mission_stock.conflict_diff(sample_config, rel) == ""


def test_the_diff_is_only_for_a_waiting_file(server):
    _edit(server, f"{CHERNARUS}/db/globals.xml", EDITED_GLOBALS)
    _forget_stock(server)
    _download(server, NEW_BUILD, "200")
    assert mission_stock.conflict_diff(server, f"{CHERNARUS}/init.c") is None
    assert mission_stock.conflict_diff(server, "../../../etc/passwd") is None


def test_cli_diff_prints_the_diff_and_refuses_a_file_not_waiting(server, monkeypatch):
    from click.testing import CliRunner

    from app.commands import update_conflicts

    monkeypatch.setattr(update_conflicts.DayzConfig, "load", classmethod(lambda cls, *a, **k: server))
    _edit(server, f"{CHERNARUS}/db/globals.xml", EDITED_GLOBALS)
    _forget_stock(server)
    _download(server, NEW_BUILD, "200")
    result = CliRunner().invoke(update_conflicts.update_conflicts_group, ["diff", f"{CHERNARUS}/db/globals.xml"])
    assert result.exit_code == 0, result.output
    assert 'value="11"' in result.output and 'value="20"' in result.output
    refused = CliRunner().invoke(update_conflicts.update_conflicts_group, ["diff", f"{CHERNARUS}/init.c"])
    assert refused.exit_code == 2
    assert "not waiting for a choice" in refused.output


def test_policy_dayz_keeps_dayzs_file_and_asks_nothing(server):
    server.mission_update_conflict = "dayz"
    _edit(server, f"{CHERNARUS}/db/globals.xml", EDITED_GLOBALS)
    _forget_stock(server)
    outcome = _download(server, NEW_BUILD, "200")
    assert outcome.conflicts == [f"{CHERNARUS}/db/globals.xml"]
    assert _path(server, f"{CHERNARUS}/db/globals.xml").read_text() == NEW_BUILD[f"{CHERNARUS}/db/globals.xml"]
    assert mission_stock.list_conflicts(server) == []


def test_policy_mine_puts_the_admins_file_back(server):
    server.mission_update_conflict = "mine"
    _edit(server, f"{CHERNARUS}/db/globals.xml", EDITED_GLOBALS)
    _forget_stock(server)
    _download(server, NEW_BUILD, "200")
    assert _path(server, f"{CHERNARUS}/db/globals.xml").read_text() == EDITED_GLOBALS
    assert mission_stock.list_conflicts(server) == []
    # The live file is the admin's, so it is the newest version.
    entries = load_index(server.paths.versions_dir).get_versions("globals.xml")
    assert entries[0].note == "The version you had before DayZ build 200"
    # And the record moved on to Bohemia's new stock: the next update of the
    # same build puts the admin's file back without asking.
    outcome = _download(server, NEW_BUILD, "200")
    assert outcome.restored == [f"{CHERNARUS}/db/globals.xml"] and outcome.conflicts == []


def test_the_default_policy_keeps_the_admins_value():
    """D1 (plans/71): a value both changed keeps the admin's, listed for review."""
    from app.shared.config import DayzConfig

    assert DayzConfig().mission_update_conflict == "mine"


@pytest.mark.parametrize("value", ["", "both", "ASK "])
def test_an_unknown_policy_reads_as_ask(sample_config, value):
    sample_config.mission_update_conflict = value
    assert mission_stock.conflict_policy(sample_config) == "ask"


def test_a_bohemia_change_to_an_unedited_file_is_just_taken(server):
    outcome = _download(server, NEW_BUILD, "200")
    assert outcome.restored == [] and outcome.conflicts == []
    assert _path(server, f"{CHERNARUS}/db/globals.xml").read_text() == NEW_BUILD[f"{CHERNARUS}/db/globals.xml"]


# --- no record yet --------------------------------------------------------


def test_no_record_and_the_same_build_puts_every_replaced_file_back(sample_config):
    _install(sample_config, STOCK, "100")
    _edit(sample_config, f"{CHERNARUS}/db/globals.xml", EDITED_GLOBALS)
    _edit(sample_config, f"{LIVONIA}/db/types.xml", "<types/>\n")
    stash = mission_stock.before_download(sample_config)
    assert set(stash.copied) == set(STOCK)  # everything, there is nothing to compare with
    _install(sample_config, STOCK, "100")
    outcome = mission_stock.after_download(sample_config, stash)
    assert sorted(outcome.restored) == [f"{CHERNARUS}/db/globals.xml", f"{LIVONIA}/db/types.xml"]
    assert _path(sample_config, f"{CHERNARUS}/db/globals.xml").read_text() == EDITED_GLOBALS
    # The record now holds stock, not the admin's file.
    record = json.loads((sample_config.paths.mission_stock_dir / "stock.json").read_text())
    assert record["files"][f"{CHERNARUS}/db/globals.xml"] != mission_stock._sha256(
        _path(sample_config, f"{CHERNARUS}/db/globals.xml"))


def test_no_record_and_a_new_build_makes_every_replaced_file_a_conflict(sample_config):
    _install(sample_config, STOCK, "100")
    _edit(sample_config, f"{CHERNARUS}/db/globals.xml", EDITED_GLOBALS)
    outcome = _download(sample_config, NEW_BUILD, "200")
    assert outcome.conflicts == [f"{CHERNARUS}/db/globals.xml"]
    assert outcome.restored == []


def test_a_record_for_the_other_branch_is_no_record(server):
    record_path = server.paths.mission_stock_dir / "stock.json"
    record = json.loads(record_path.read_text())
    record["app_id"] = "1042420"
    record_path.write_text(json.dumps(record))
    stash = mission_stock.before_download(server)
    assert stash.reference is None
    assert set(stash.copied) == set(STOCK)


def test_a_first_install_has_nothing_to_keep(sample_config):
    stash = mission_stock.before_download(sample_config)
    assert stash.copied == {}
    _install(sample_config, STOCK, "100")
    assert mission_stock.after_download(sample_config, stash).restored == []
    assert (sample_config.paths.mission_stock_dir / "stock.json").is_file()


# --- reset-mission and failures ---------------------------------------------


def test_reset_mission_restores_nothing_in_the_mission_it_resets(server):
    _edit(server, f"{CHERNARUS}/db/globals.xml", EDITED_GLOBALS)
    _edit(server, f"{LIVONIA}/db/globals.xml", EDITED_GLOBALS)
    outcome = _download(server, resetting=CHERNARUS)
    assert outcome.restored == [f"{LIVONIA}/db/globals.xml"]
    assert _path(server, f"{CHERNARUS}/db/globals.xml").read_text() == STOCK[f"{CHERNARUS}/db/globals.xml"]
    assert _path(server, f"{LIVONIA}/db/globals.xml").read_text() == EDITED_GLOBALS


def test_reset_mission_drops_that_missions_waiting_conflicts(server):
    _edit(server, f"{CHERNARUS}/db/globals.xml", EDITED_GLOBALS)
    _forget_stock(server)
    _download(server, NEW_BUILD, "200")
    assert len(mission_stock.list_conflicts(server)) == 1
    _download(server, NEW_BUILD, "200", resetting=CHERNARUS)
    assert mission_stock.list_conflicts(server) == []


def test_a_failed_download_puts_nothing_back_and_says_where_the_copies_are(server):
    _edit(server, f"{CHERNARUS}/db/globals.xml", EDITED_GLOBALS)
    lines: list[str] = []
    stash = mission_stock.before_download(server)
    mission_stock.download_failed(server, stash, log=lines.append)
    assert (server.paths.mission_stock_dir / "pending" / f"{CHERNARUS}/db/globals.xml").is_file()
    assert "no mission files were put back" in lines[0]
    # The next run takes fresh copies from disk, so a stale copy is never restored.
    _edit(server, f"{CHERNARUS}/db/globals.xml", STOCK[f"{CHERNARUS}/db/globals.xml"])
    assert _download(server).restored == []


def test_an_edit_survives_a_download_that_reset_it_and_then_failed(server):
    """Validate reset the file, then SteamCMD failed. The next try emptied
    pending/ and saw a stock file, so the only copy of the edit was deleted."""
    rel = f"{CHERNARUS}/db/globals.xml"
    _edit(server, rel, EDITED_GLOBALS)
    lines: list[str] = []
    stash = mission_stock.before_download(server)
    _edit(server, rel, STOCK[rel])  # validate got this far
    mission_stock.download_failed(server, stash, log=lines.append)

    assert _path(server, rel).read_text() == EDITED_GLOBALS
    assert lines == [
        f"The server download failed after it replaced some mission files. Put your version back: {rel}"
    ]
    assert _download(server).restored == [rel]
    assert _path(server, rel).read_text() == EDITED_GLOBALS


async def test_a_cancelled_download_puts_the_reset_files_back(server):
    import asyncio

    rel = f"{CHERNARUS}/db/globals.xml"

    class _Runner:
        async def run_command(self, cmd):
            _edit(server, rel, STOCK[rel])
            raise asyncio.CancelledError

    _edit(server, rel, EDITED_GLOBALS)
    with pytest.raises(asyncio.CancelledError):
        await mission_stock.run_download(_Runner(), server, ["steamcmd"])
    assert _path(server, rel).read_text() == EDITED_GLOBALS


def test_the_log_names_what_was_kept(server):
    _edit(server, f"{CHERNARUS}/db/globals.xml", EDITED_GLOBALS)
    lines: list[str] = []
    stash = mission_stock.before_download(server, log=lines.append)
    _install(server, STOCK, "100")
    mission_stock.after_download(server, stash, log=lines.append)
    assert lines == [
        "Set aside 1 changed stock mission file(s) for this update.",
        f"Kept your changes after the update: {CHERNARUS}/db/globals.xml",
    ]
    assert not any("[" in line for line in lines)  # markup-free: it reaches a job log


def test_needs_merge_only_for_the_configured_mission(server):
    assert mission_stock.needs_merge(server, [f"{CHERNARUS}/db/globals.xml"])
    assert not mission_stock.needs_merge(server, [f"{LIVONIA}/db/globals.xml"])


# --- through the callers ------------------------------------------------------


async def test_the_pipeline_update_step_keeps_an_edit(server, monkeypatch):
    from app.ops import pipeline

    monkeypatch.setattr("app.shared.steam.build_server_update_cmd", lambda config: ["steamcmd"])

    class _Runner:
        async def run_command(self, cmd):
            _install(server, STOCK, "100")
            return 0

    _edit(server, f"{CHERNARUS}/db/globals.xml", EDITED_GLOBALS)
    lines: list[str] = []
    ctx = pipeline.PipelineContext(config=server, emit=lines.append, runner=_Runner(), should_run=lambda key: True)
    step = next(s for s in pipeline.build_update_steps(server) if s.key == "update_server")
    results, _ = await pipeline.run_pipeline([step], ctx)
    assert results[0].status == "success"
    assert _path(server, f"{CHERNARUS}/db/globals.xml").read_text() == EDITED_GLOBALS
    assert any("Kept your changes" in line for line in lines)


async def test_a_failed_pipeline_download_restores_nothing(server, monkeypatch):
    from app.ops import pipeline

    monkeypatch.setattr("app.shared.steam.build_server_update_cmd", lambda config: ["steamcmd"])

    class _Runner:
        async def run_command(self, cmd):
            return 1

        last_output = ""

    _edit(server, f"{CHERNARUS}/db/globals.xml", EDITED_GLOBALS)
    ctx = pipeline.PipelineContext(config=server, emit=lambda line: None, runner=_Runner(), should_run=lambda key: True)
    step = next(s for s in pipeline.build_update_steps(server) if s.key == "update_server")
    results, _ = await pipeline.run_pipeline([step], ctx)
    assert results[0].status == "failed"
    assert (server.paths.mission_stock_dir / "pending" / f"{CHERNARUS}/db/globals.xml").is_file()


def test_update_dayz_server_keeps_an_edit(server, monkeypatch):
    from app.shared import steam

    def _fake(cmd):
        _install(server, STOCK, "100")
        return 0, ""

    monkeypatch.setattr(steam, "run_passthrough", _fake)
    _edit(server, f"{CHERNARUS}/db/globals.xml", EDITED_GLOBALS)
    steam.update_dayz_server(server, log=lambda line: None)
    assert _path(server, f"{CHERNARUS}/db/globals.xml").read_text() == EDITED_GLOBALS


def test_cli_reset_mission_keeps_the_other_missions_edits(server, monkeypatch, tmp_path):
    from click.testing import CliRunner

    from app.commands import cleanup
    from app.ops.cleanup_ops import MissionResetInfo

    target = MissionResetInfo(
        template=CHERNARUS, path=server.server_dir_path / "mpmissions" / CHERNARUS,
        map_name="Chernarus", source_type="vanilla",
    )
    monkeypatch.setattr(cleanup, "IS_WINDOWS", False)
    monkeypatch.setattr("app.commands.service.is_server_running", lambda config=None: False)
    monkeypatch.setattr(cleanup.DayzConfig, "load", classmethod(lambda cls, *a, **k: server))
    monkeypatch.setattr(cleanup, "list_resettable_missions", lambda c: [target])
    monkeypatch.setattr("app.shared.steam.build_server_update_cmd", lambda config: ["steamcmd"])

    def _validate(cmd, check=False):
        _install(server, STOCK, "100")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("subprocess.run", _validate)
    _edit(server, f"{CHERNARUS}/db/globals.xml", EDITED_GLOBALS)
    _edit(server, f"{LIVONIA}/db/globals.xml", EDITED_GLOBALS)
    result = CliRunner().invoke(cleanup.cleanup_group, ["reset-mission", "--mission", CHERNARUS],
                                obj={"non_interactive": True})
    assert result.exit_code == 0, result.output
    assert _path(server, f"{CHERNARUS}/db/globals.xml").read_text() == STOCK[f"{CHERNARUS}/db/globals.xml"]
    assert _path(server, f"{LIVONIA}/db/globals.xml").read_text() == EDITED_GLOBALS


# --- the stock files themselves (plans/71) ------------------------------------

GLOBALS = f"{CHERNARUS}/db/globals.xml"
NEW_GLOBALS = '<variables><var name="TimeLogin" value="20"/></variables>\n'


def _store(config):
    return config.paths.mission_stock_dir / "stock"


def _sha(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def _assert_store_mirrors_record(config):
    record = json.loads((config.paths.mission_stock_dir / "stock.json").read_text())["files"]
    store = _store(config)
    held = {p.relative_to(store).as_posix() for p in store.rglob("*") if p.is_file()}
    assert held == set(record)
    for rel, digest in record.items():
        assert _sha((store / rel).read_bytes()) == digest, rel


def test_the_store_holds_every_stock_file_after_an_install(server):
    _assert_store_mirrors_record(server)
    assert (_store(server) / GLOBALS).read_text() == STOCK[GLOBALS]
    assert not (server.paths.mission_stock_dir / "stock.next").exists()


def test_the_store_keeps_stock_while_an_edit_is_put_back(server):
    _edit(server, GLOBALS, EDITED_GLOBALS)
    _download(server)
    assert _path(server, GLOBALS).read_text() == EDITED_GLOBALS
    assert (_store(server) / GLOBALS).read_text() == STOCK[GLOBALS]
    _assert_store_mirrors_record(server)


def test_a_new_build_replaces_the_stored_copy(server):
    _download(server, {**STOCK, GLOBALS: NEW_GLOBALS}, "101")
    assert (_store(server) / GLOBALS).read_text() == NEW_GLOBALS
    _assert_store_mirrors_record(server)


@pytest.mark.parametrize("policy", ["ask", "dayz", "mine"])
def test_a_conflict_stores_dayzs_new_file_whichever_side_stays(server, policy):
    server.mission_update_conflict = policy
    _edit(server, GLOBALS, EDITED_GLOBALS)
    _download(server, {**STOCK, GLOBALS: NEW_GLOBALS}, "101")
    assert (_store(server) / GLOBALS).read_text() == NEW_GLOBALS
    _assert_store_mirrors_record(server)


def test_no_record_stores_what_the_download_wrote_not_the_edit(sample_config):
    _install(sample_config, STOCK, "100")
    _edit(sample_config, GLOBALS, EDITED_GLOBALS)
    _download(sample_config)
    assert _path(sample_config, GLOBALS).read_text() == EDITED_GLOBALS
    assert (_store(sample_config) / GLOBALS).read_text() == STOCK[GLOBALS]
    _assert_store_mirrors_record(sample_config)


def test_reset_mission_leaves_the_store_mirroring_the_record(server):
    _edit(server, GLOBALS, EDITED_GLOBALS)
    _download(server, resetting=CHERNARUS)
    _assert_store_mirrors_record(server)


def test_a_file_the_download_deleted_keeps_its_old_stock_copy(server):
    _edit(server, GLOBALS, EDITED_GLOBALS)
    stash = mission_stock.before_download(server)
    _install(server, STOCK, "100")
    _path(server, GLOBALS).unlink()
    mission_stock.after_download(server, stash)
    assert _path(server, GLOBALS).read_text() == EDITED_GLOBALS
    assert (_store(server) / GLOBALS).read_text() == STOCK[GLOBALS]
    _assert_store_mirrors_record(server)


def test_a_mission_that_is_gone_leaves_the_store(server):
    import shutil

    shutil.rmtree(server.server_dir_path / "mpmissions" / LIVONIA)
    _download(server, {k: v for k, v in STOCK.items() if not k.startswith(LIVONIA)})
    assert not (_store(server) / LIVONIA).exists()
    _assert_store_mirrors_record(server)


def test_base_for_reads_the_store_and_checks_the_hash(server):
    stock = STOCK[GLOBALS].encode()
    assert mission_stock.base_for(server, GLOBALS, _sha(stock)) == stock
    assert mission_stock.base_for(server, GLOBALS, "0" * 64) is None
    assert mission_stock.base_for(server, GLOBALS, "") is None


def test_base_for_falls_back_to_a_version_with_the_stock_hash(server):
    import shutil

    from app.shared.config_versions import snapshot

    shutil.rmtree(_store(server))
    stock = STOCK[GLOBALS].encode()
    snapshot(_path(server, GLOBALS), store=server.paths.versions_dir)
    _edit(server, GLOBALS, EDITED_GLOBALS)
    snapshot(_path(server, GLOBALS), store=server.paths.versions_dir)
    assert mission_stock.base_for(server, GLOBALS, _sha(stock)) == stock


def test_base_for_takes_a_backup_only_when_its_hash_matches(server):
    import shutil

    shutil.rmtree(_store(server))
    stock = STOCK[GLOBALS].encode()
    backup = _path(server, GLOBALS).with_name("globals.xml.backup")
    backup.write_text(EDITED_GLOBALS)
    assert mission_stock.base_for(server, GLOBALS, _sha(stock)) is None
    backup.write_bytes(stock)
    assert mission_stock.base_for(server, GLOBALS, _sha(stock)) == stock


# --- plans/71 Step 3: a file both sides changed is merged value by value ---------


GLOBALS2 = (
    '<variables>\n\t<var name="TimeLogin" type="0" value="15"/>\n'
    '\t<var name="TimeLogout" type="0" value="15"/>\n</variables>\n'
)
TYPES2 = (
    '<types>\n\t<type name="Apple">\n\t\t<nominal>10</nominal>\n\t</type>\n'
    '\t<type name="Pear">\n\t\t<nominal>4</nominal>\n\t</type>\n</types>\n'
)
G_REL = f"{CHERNARUS}/db/globals.xml"
T_REL = f"{CHERNARUS}/db/types.xml"
STOCK2 = {**STOCK, G_REL: GLOBALS2, T_REL: TYPES2}


@pytest.fixture()
def merging(sample_config):
    """A server installed once with the richer files, so the store holds them."""
    _install(sample_config, STOCK2, "100")
    mission_stock.after_download(sample_config, mission_stock.before_download(sample_config), remerge_mods=False)
    return sample_config


def _globals(**values):
    text = GLOBALS2
    for name, value in values.items():
        text = text.replace(f'name="{name}" type="0" value="15"', f'name="{name}" type="0" value="{value}"')
    return text


def _values(config):
    [row] = mission_stock.list_conflicts(config)
    return row["values"]


def test_different_values_of_one_file_both_survive_the_update(merging):
    _edit(merging, G_REL, _globals(TimeLogin=11))
    outcome = _download(merging, {**STOCK2, G_REL: _globals(TimeLogout=20)}, "200")
    assert outcome.merged == [G_REL] and outcome.conflicts == []
    live = _path(merging, G_REL).read_text()
    assert 'name="TimeLogin" type="0" value="11"' in live and 'name="TimeLogout" type="0" value="20"' in live
    assert mission_stock.list_conflicts(merging) == []


@pytest.mark.parametrize(("policy", "live_value", "waiting"), [
    ("mine", "11", False), ("dayz", "20", False), ("ask", "20", True),
])
def test_a_value_both_changed_follows_the_policy_and_is_listed(merging, policy, live_value, waiting):
    merging.mission_update_conflict = policy
    _edit(merging, G_REL, _globals(TimeLogin=11, TimeLogout=30))
    _download(merging, {**STOCK2, G_REL: _globals(TimeLogin=20)}, "200")
    live = _path(merging, G_REL).read_text()
    assert f'name="TimeLogin" type="0" value="{live_value}"' in live
    assert 'name="TimeLogout" type="0" value="30"' in live  # the admin's other edit, under every policy
    [row] = _values(merging)
    assert (row["entry"], row["field"], row["base"], row["mine"], row["theirs"]) == ("TimeLogin", "@value", "15", "11", "20")
    assert row["waiting"] is waiting
    assert row["live"] == ("mine" if policy == "mine" else "dayz")


def test_a_merge_saves_both_versions_and_the_merged_file(merging):
    _edit(merging, G_REL, _globals(TimeLogin=11))
    _download(merging, {**STOCK2, G_REL: _globals(TimeLogout=20)}, "200")
    notes = [e.note for e in load_index(merging.paths.versions_dir).get_versions("globals.xml")]
    assert notes[:3] == [
        "Merged with DayZ build 200, before the mods went back in",
        "The version you had before DayZ build 200",
        "DayZ build 200's new file",
    ]


def test_a_file_only_the_mods_changed_takes_dayzs_bytes(merging):
    _edit(merging, T_REL, TYPES2.replace("</types>", MOD_BLOCK + "</types>"))
    new = TYPES2.replace("<nominal>10</nominal>", "<nominal>12</nominal>")
    outcome = _download(merging, {**STOCK2, T_REL: new}, "200")
    assert outcome.merged == [T_REL]
    assert _path(merging, T_REL).read_text() == new
    assert mission_stock.list_conflicts(merging) == []


def test_a_tweaked_value_is_scaled_from_dayzs_new_number(merging):
    from app.presets.field_baselines import load_baselines, save_baselines

    mission = _path(merging, CHERNARUS)
    save_baselines(mission, {"db/types.xml": {"Apple": {"nominal": {"base": 10, "written": 5}}}}, ".zsm-tweaks.json")
    _edit(merging, T_REL, TYPES2.replace("<nominal>10</nominal>", "<nominal>5</nominal>"))
    lines: list[str] = []
    _download(merging, {**STOCK2, T_REL: TYPES2.replace("<nominal>10</nominal>", "<nominal>20</nominal>")}, "200", log=lines.append)
    assert "<nominal>10</nominal>" in _path(merging, T_REL).read_text()
    assert load_baselines(mission, ".zsm-tweaks.json") == {"db/types.xml": {"Apple": {"nominal": {"base": 20, "written": 10}}}}
    assert mission_stock.list_conflicts(merging) == []
    assert any("scaled 1 tweaked value(s)" in line for line in lines)


def test_an_entry_dayz_removed_goes_under_every_policy(merging):
    merging.mission_update_conflict = "mine"
    _edit(merging, T_REL, TYPES2.replace("<nominal>4</nominal>", "<nominal>8</nominal>"))
    without_pear = TYPES2[:TYPES2.index('\t<type name="Pear">')] + "</types>\n"
    _download(merging, {**STOCK2, T_REL: without_pear}, "200")
    assert "Pear" not in _path(merging, T_REL).read_text()
    [row] = _values(merging)
    assert (row["kind"], row["live"], row["waiting"]) == ("dayz_removed", "dayz", False)


def test_a_disabled_wrapper_in_cfgeconomycore_survives_the_merge(merging):
    rel = f"{CHERNARUS}/cfgeconomycore.xml"
    wrapper = '<!-- DISABLED @Bikes: <ce folder="custom"><file name="bikes-types.xml" type="types"/></ce> -->'
    _edit(merging, rel, f'<economycore><ce folder="custom"><file name="cf-types.xml" type="types"/></ce>{wrapper}</economycore>\n')
    _download(merging, {**STOCK2, rel: '<economycore><classes><rootclass name="DefaultWeapon"/></classes></economycore>\n'}, "200")
    live = _path(merging, rel).read_text()
    assert "DISABLED @Bikes" in live and "DefaultWeapon" in live and "cf-types.xml" in live


def test_a_file_that_cannot_be_merged_gets_the_whole_file_choice(merging):
    merging.mission_update_conflict = "ask"
    _edit(merging, G_REL, _globals(TimeLogin=11))
    lines: list[str] = []
    outcome = _download(merging, {**STOCK2, G_REL: "<variables><var name=\n"}, "200", log=lines.append)
    assert outcome.conflicts == [G_REL] and outcome.merged == []
    assert any("cannot be merged value by value" in line for line in lines)
    assert [row["file"] for row in mission_stock.list_conflicts(merging)] == [G_REL]


def test_init_c_is_merged_line_by_line_or_not_at_all(merging):
    rel = f"{CHERNARUS}/init.c"
    stock = "void main()\n{\n\tint a = 1;\n\n\n\tint b = 2;\n}\n"
    _download(merging, {**STOCK2, rel: stock}, "150")
    _edit(merging, rel, stock.replace("a = 1", "a = 5"))
    outcome = _download(merging, {**STOCK2, rel: stock.replace("b = 2", "b = 7")}, "200")
    assert outcome.merged == [rel]
    assert _path(merging, rel).read_text() == stock.replace("a = 1", "a = 5").replace("b = 2", "b = 7")
    _edit(merging, rel, _path(merging, rel).read_text().replace("b = 7", "b = 8"))
    outcome = _download(merging, {**STOCK2, rel: stock.replace("b = 2", "b = 9")}, "300")
    assert outcome.conflicts == [rel]


def test_reset_mission_still_merges_nothing(merging):
    _edit(merging, G_REL, _globals(TimeLogin=11))
    outcome = _download(merging, {**STOCK2, G_REL: _globals(TimeLogout=20)}, "200", resetting=CHERNARUS)
    assert outcome.merged == [] and outcome.conflicts == []
    assert _path(merging, G_REL).read_text() == _globals(TimeLogout=20)


def test_the_log_line_says_what_the_merge_did(merging):
    merging.mission_update_conflict = "mine"
    _edit(merging, G_REL, _globals(TimeLogin=11, TimeLogout=30))
    lines: list[str] = []
    _download(merging, {**STOCK2, G_REL: _globals(TimeLogin=20)}, "200", log=lines.append)
    [line] = [line for line in lines if line.startswith("Merged ")]
    assert line == (
        f"Merged {G_REL} with DayZ build 200: took 0 of DayZ's changes, kept 1 of yours. "
        "1 value(s) were changed by both sides: kept yours. See the Update page or: zsm update-conflicts list"
    )
    assert "[" not in line


# --- settling a merged file ------------------------------------------------------------


def _both_changed(config, policy="ask"):
    config.mission_update_conflict = policy
    _edit(config, G_REL, _globals(TimeLogin=11, TimeLogout=30))
    _download(config, {**STOCK2, G_REL: _globals(TimeLogin=20)}, "200")


def test_the_diff_of_a_merged_file_names_the_merge_as_live(merging):
    _both_changed(merging)
    lines = mission_stock.conflict_diff(merging, G_REL).splitlines()
    assert lines[0].endswith("(the merged file, live)")
    assert any(line.startswith("+") and 'value="11"' in line for line in lines[2:])


def test_choosing_my_value_writes_it_and_clears_the_row(merging, monkeypatch):
    remerged = []
    monkeypatch.setattr(mission_stock, "remerge", lambda config, files, log=None: remerged.append(files) or True)
    _both_changed(merging)
    [row] = _values(merging)
    settled, merged = mission_stock.settle_values(merging, G_REL, [row["id"]], "mine")
    assert settled == [row["id"]] and merged is True and remerged == [[G_REL]]
    live = _path(merging, G_REL).read_text()
    assert 'name="TimeLogin" type="0" value="11"' in live and 'name="TimeLogout" type="0" value="30"' in live
    assert mission_stock.list_conflicts(merging) == []


def test_ok_takes_the_row_off_the_list_and_writes_nothing(merging):
    _both_changed(merging, "mine")
    before = _path(merging, G_REL).read_bytes()
    [row] = _values(merging)
    assert mission_stock.settle_values(merging, G_REL, [row["id"]], "ok") == ([row["id"]], False)
    assert _path(merging, G_REL).read_bytes() == before
    assert mission_stock.list_conflicts(merging) == []


def test_a_value_gone_from_the_file_since_is_reported_not_written(merging):
    _both_changed(merging)
    [row] = _values(merging)
    _edit(merging, G_REL, '<variables>\n\t<var name="TimeLogout" type="0" value="30"/>\n</variables>\n')
    lines: list[str] = []
    mission_stock.settle_values(merging, G_REL, [row["id"]], "mine", log=lines.append)
    assert any("is gone from the file" in line for line in lines)
    assert "TimeLogin" not in _path(merging, G_REL).read_text()


def test_settle_values_ignores_an_unknown_file_and_side(merging):
    _both_changed(merging)
    assert mission_stock.settle_values(merging, "../../etc/passwd", ["1"], "mine") == ([], False)
    with pytest.raises(ValueError):
        mission_stock.settle_values(merging, G_REL, ["1"], "both")


def test_my_whole_file_and_dayzs_whole_file_still_work_on_a_merged_file(merging):
    _both_changed(merging)
    assert mission_stock.resolve(merging, [G_REL], "dayz") == [G_REL]
    assert _path(merging, G_REL).read_text() == _globals(TimeLogin=20)
    _both_changed(merging)
    mission_stock.resolve(merging, [G_REL], "mine")
    assert _path(merging, G_REL).read_text() == _globals(TimeLogin=11, TimeLogout=30)


def test_a_waiting_value_is_carried_through_the_next_update(merging):
    _both_changed(merging, "ask")
    third = _globals(TimeLogin=20, TimeLogout=40)
    _download(merging, {**STOCK2, G_REL: third}, "300")
    rows = _values(merging)
    carried = [row for row in rows if row["entry"] == "TimeLogin"]
    assert len(carried) == 1 and carried[0]["waiting"] and carried[0]["mine"] == "11" and carried[0]["theirs"] == "20"
    later = [row for row in rows if row["entry"] == "TimeLogout"]
    assert later and later[0]["mine"] == "30" and later[0]["theirs"] == "40"


# --- the mods go back in --------------------------------------------------------------


def test_after_download_merges_the_mods_back_only_when_asked(merging, monkeypatch):
    calls = []
    monkeypatch.setattr(mission_stock, "remerge", lambda config, files, log=None: calls.append(files) or True)
    _edit(merging, G_REL, _globals(TimeLogin=11))
    stash = mission_stock.before_download(merging)
    _install(merging, {**STOCK2, G_REL: _globals(TimeLogout=20)}, "200")
    mission_stock.after_download(merging, stash)
    assert calls == [[G_REL]]


def test_a_failed_mods_merge_after_the_update_is_reported_not_raised(merging, monkeypatch):
    def _boom(config, files, log=None):
        raise RuntimeError("Mod XML could not be applied for: cfgeventgroups")

    monkeypatch.setattr(mission_stock, "remerge", _boom)
    _edit(merging, G_REL, _globals(TimeLogin=11))
    stash = mission_stock.before_download(merging)
    _install(merging, {**STOCK2, G_REL: _globals(TimeLogout=20)}, "200")
    lines: list[str] = []
    outcome = mission_stock.after_download(merging, stash, log=lines.append)
    assert outcome.merged == [G_REL]
    assert any("Could not merge your mods back in" in line and "cfgeventgroups" in line for line in lines)


@pytest.mark.parametrize(("skip_merge", "expected"), [(False, []), (True, [[G_REL]])])
async def test_the_pipeline_merges_the_mods_back_only_when_it_skips_merge_xml(merging, monkeypatch, skip_merge, expected):
    from app.ops import pipeline

    monkeypatch.setattr("app.shared.steam.build_server_update_cmd", lambda config: ["steamcmd"])
    calls = []
    monkeypatch.setattr(mission_stock, "remerge", lambda config, files, log=None: calls.append(files) or True)

    class _Runner:
        async def run_command(self, cmd):
            _install(merging, {**STOCK2, G_REL: _globals(TimeLogout=20)}, "200")
            return 0

    _edit(merging, G_REL, _globals(TimeLogin=11))
    should_run = (lambda key: key != "merge_xml") if skip_merge else (lambda key: True)
    ctx = pipeline.PipelineContext(config=merging, emit=lambda line: None, runner=_Runner(), should_run=should_run)
    step = next(s for s in pipeline.build_update_steps(merging) if s.key == "update_server")
    results, _ = await pipeline.run_pipeline([step], ctx)
    assert results[0].status == "success"
    assert calls == expected


def test_cli_lists_values_and_settles_one(merging, monkeypatch):
    from click.testing import CliRunner

    from app.commands import update_conflicts

    monkeypatch.setattr(update_conflicts.DayzConfig, "load", classmethod(lambda cls, *a, **k: merging))
    monkeypatch.setattr(mission_stock, "remerge", lambda config, files, log=None: True)
    _both_changed(merging)
    listed = CliRunner().invoke(update_conflicts.update_conflicts_group, ["list"])
    assert listed.exit_code == 0, listed.output
    assert f"{G_REL}  (merged, DayZ build 100 -> 200" in listed.output
    assert "[1] TimeLogin @value: yours 11, DayZ's 20, was 15 (both changed; waiting, DayZ's is live)" in listed.output
    refused = CliRunner().invoke(update_conflicts.update_conflicts_group, ["keep-mine", "--all", "--value", "1"])
    assert refused.exit_code == 2
    done = CliRunner().invoke(update_conflicts.update_conflicts_group, ["keep-mine", G_REL, "--value", "1"])
    assert done.exit_code == 0, done.output
    assert 'name="TimeLogin" type="0" value="11"' in _path(merging, G_REL).read_text()
    assert mission_stock.list_conflicts(merging) == []


def test_cli_dismiss_leaves_the_file_alone(merging, monkeypatch):
    from click.testing import CliRunner

    from app.commands import update_conflicts

    monkeypatch.setattr(update_conflicts.DayzConfig, "load", classmethod(lambda cls, *a, **k: merging))
    _both_changed(merging, "mine")
    before = _path(merging, G_REL).read_bytes()
    result = CliRunner().invoke(update_conflicts.update_conflicts_group, ["dismiss", G_REL, "--value", "1", "--value", "9"])
    assert result.exit_code == 0, result.output
    assert "Not listed for" in result.output and "9" in result.output
    assert _path(merging, G_REL).read_bytes() == before
    assert mission_stock.list_conflicts(merging) == []



# --- Restore from backup gives the installed version's file (F10) ------------


def test_restore_puts_back_the_installed_versions_file_not_an_old_backup(server):
    """A `.backup` is written once, so after a game update Restore from backup
    gave the old version's file back (browser test 2026-11-17, F10)."""
    from app.xml.xml_utils import restore_file

    core = f"{CHERNARUS}/cfgeconomycore.xml"
    new_core = '<economycore><rootclass name="MotorbikeScript"/></economycore>\n'
    _download(server, {**STOCK, core: new_core}, buildid="101")
    backup = _path(server, core + ".backup")
    backup.write_text("<economycore><!-- 1.29 --></economycore>\n")
    _edit(server, core, "<economycore><!-- mine --></economycore>\n")

    live = _path(server, core)
    assert restore_file(live, mission_stock.shipped_copy(server, live)) is True
    assert live.read_text() == new_core
    assert backup.read_text() == "<economycore><!-- 1.29 --></economycore>\n"


def test_a_deleted_stock_file_still_has_its_shipped_copy(server):
    live = _path(server, f"{CHERNARUS}/db/globals.xml")
    live.unlink()
    assert mission_stock.shipped_copy(server, live).read_text() == STOCK[f"{CHERNARUS}/db/globals.xml"]


def test_no_shipped_copy_outside_steams_files(server):
    """A community mission, a Zedstead file and a path outside mpmissions/ keep
    the `.backup` fallback."""
    root = server.server_dir_path / "mpmissions"
    other = root / "empty.deerisle" / "cfgeconomycore.xml"
    other.parent.mkdir(parents=True)
    other.write_text("<economycore/>")
    assert mission_stock.shipped_copy(server, other) is None
    assert mission_stock.shipped_copy(server, root / CHERNARUS / "custom" / "x.xml") is None
    assert mission_stock.shipped_copy(server, root / CHERNARUS / "storage_1" / "x.json") is None
    assert mission_stock.shipped_copy(server, server.server_dir_path / "serverDZ.cfg") is None
