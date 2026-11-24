"""Tests for app/ops/spawn_test_ops.py (plans/37, Package A)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.ops import spawn_test_ops as st
from tests.conftest import CE_1_29, bohemia_ce, set_instance_id, write_test_mods


# dayzOffline.chernarusplus/init.c from Bohemia's DayZ-Central-Economy repo,
# read at the pinned 1.29 commit. The file ships with CRLF line endings and no
# trailing newline, so stock_init_c_crlf() is byte-identical to it;
# stock_init_c() is the LF form.
def stock_init_c_crlf() -> str:
    return bohemia_ce(CE_1_29, "dayzOffline.chernarusplus/init.c")


def stock_init_c() -> str:
    return stock_init_c_crlf().replace("\r\n", "\n")


MOD = "@Bikes"
CLASSES = ["Bicycle", "BicycleBase", "bicycle"]


@pytest.fixture()
def env(sample_config, tmp_mission_dir, monkeypatch):
    """A mission with the stock init.c, a storage_1 and a mod with three classes."""
    (tmp_mission_dir / "init.c").write_bytes(stock_init_c_crlf().encode())
    storage = tmp_mission_dir / "storage_1"
    (storage / "data").mkdir(parents=True)
    (storage / "players.db").write_bytes(b"players-before")
    (storage / "data" / "dynamic_0.bin").write_bytes(b"world-before")
    monkeypatch.setattr(
        "app.modmgr.mod_conflict.extract_mod_classnames_cached",
        lambda server_dir, mods, *a, **k: {m: list(CLASSES) for m in mods},
    )
    monkeypatch.setattr("app.ops.vps_readiness.mem_mb", lambda: (8192, 0))
    return sample_config, tmp_mission_dir


def _tree(root):
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _config_root(config):
    return config.paths.spawn_test_dir


def _marker(config):
    return config.server_dir_path / "profiles" / st.MARKER_FILE


# ── init.c ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_inject_then_strip_gives_the_file_back_byte_for_byte(newline):
    text = stock_init_c().replace("\n", newline)
    injected = st.inject_spawn_test_block(text, "Bicycle", 10)
    assert injected != text
    assert st.strip_spawn_test_block(injected) == text


def test_strip_is_linear_in_start_lines_without_an_end():
    """A START line with no END after it is left alone. The lazy regex the
    strip used searched the rest of init.c again from every such line: 4.2 s
    at 4000 of them (plans/68 B25)."""
    import time

    injected = st.inject_spawn_test_block(stock_init_c(), "Bicycle", 10)
    junk = (st.BLOCK_START + "\n") * 5000

    started = time.perf_counter()
    stripped = st.strip_spawn_test_block(injected + junk)
    elapsed = time.perf_counter() - started

    assert stripped == stock_init_c() + junk
    assert elapsed < 1.0, f"took {elapsed:.2f}s"


def test_block_goes_before_main_and_the_call_inside_it():
    injected = st.inject_spawn_test_block(stock_init_c(), "Bicycle", 10)
    block_at = injected.index(st.BLOCK_START)
    main_at = injected.index("void main()")
    assert block_at < injected.index(st.BLOCK_END) < main_at
    after_main = injected[main_at:].split("\n")
    assert after_main[1] == "{"
    assert after_main[2] == st.CALL_LINE
    assert 'string cls = "Bicycle";' in injected
    assert "int count = 10;" in injected
    assert "int perRow = 4;" in injected
    # Global functions only: nothing is added inside CustomMission.
    assert injected.index("class CustomMission") > main_at
    assert "override" not in injected[block_at:main_at]


def test_a_location_block_moves_the_player_and_starts_the_grid_there():
    injected = st.inject_spawn_test_block(stock_init_c(), "Bicycle", 4, (5340.7, 11380.0))
    assert "float ox = 5340.7;" in injected
    assert "float oz = 11380.0;" in injected
    assert "vector origin = Vector(ox, GetGame().SurfaceY(ox, oz), oz);" in injected
    assert "player.SetPosition(origin);" in injected
    assert "player.GetPosition()" not in injected
    assert st.strip_spawn_test_block(injected) == stock_init_c()


def test_a_block_without_a_location_does_not_move_the_player():
    injected = st.inject_spawn_test_block(stock_init_c(), "Bicycle", 4)
    assert "SetPosition" not in injected
    assert "vector origin = player.GetPosition();" in injected


def test_crlf_file_gets_crlf_block():
    injected = st.inject_spawn_test_block(stock_init_c_crlf(), "Bicycle", 1)
    assert "\n" not in injected.replace("\r\n", "")


def test_code_on_the_brace_line_is_refused():
    with pytest.raises(st.SpawnTestError, match="same line as its opening brace"):
        st.inject_spawn_test_block("void main() { Print(1);\n}\n", "Bicycle", 1)


# ── Start / end ─────────────────────────────────────────────────────────


def test_start_arms_the_test_and_end_restores_storage_exactly(env):
    config, mission = env
    storage = mission / "storage_1"
    before = _tree(storage)
    _marker(config).write_text("Bicycle\n")  # left over from an earlier run

    lines: list[str] = []
    state = st.start_near_player(config, MOD, "bicycle", 10, lines.append)
    assert state["classname"] == "Bicycle"  # the mod's own spelling
    assert st.active_test(config) == state
    assert not _marker(config).exists()  # re-armed
    assert st.BLOCK_START in (mission / "init.c").read_text()
    assert any("Spawn test armed" in line for line in lines)

    # What the server does during the test.
    (storage / "players.db").write_bytes(b"players-after")
    (storage / "data" / "dynamic_9.bin").write_bytes(b"new")
    _marker(config).write_text("Bicycle\n")

    result = st.end_test(config, lines.append)
    assert result == {"restored_files": ["init.c"], "storage": "restored", "drifted": []}
    assert _tree(storage) == before
    assert not (storage / "data" / "dynamic_9.bin").exists()  # the restore_backup trap
    assert (mission / "init.c").read_bytes() == stock_init_c_crlf().encode()
    assert not _marker(config).exists()
    assert st.active_test(config) is None
    assert not (mission / "storage_1.zsm-restore").exists()


def test_end_removes_a_storage_the_server_created_during_the_test(env):
    config, mission = env
    import shutil

    shutil.rmtree(mission / "storage_1")
    st.start_near_player(config, MOD, "Bicycle", 5)
    assert st.active_test(config)["storage_existed"] is False
    (mission / "storage_1" / "data").mkdir(parents=True)
    (mission / "storage_1" / "players.db").write_bytes(b"x")

    assert st.end_test(config)["storage"] == "removed"
    assert not (mission / "storage_1").exists()


def test_the_storage_copied_and_restored_is_the_one_instance_id_names(env):
    """instanceId = 3 puts the world in storage_3 (measured on 1.29). A
    storage_1 beside it is an earlier id's and is not the test's to touch."""
    config, mission = env
    set_instance_id(config.server_dir_path, 3)
    stale = mission / "storage_1"
    live = mission / "storage_3"
    (live / "data").mkdir(parents=True)
    (live / "players.db").write_bytes(b"live-before")
    stale_before, live_before = _tree(stale), _tree(live)

    state = st.start_near_player(config, MOD, "Bicycle", 5)
    assert state["storage_name"] == "storage_3"
    assert _tree(Path(state["session_dir"]) / st.STORAGE_COPY) == live_before

    (live / "players.db").write_bytes(b"during-the-test")
    (live / "data" / "dynamic_9.bin").write_bytes(b"spawned")
    lines: list[str] = []
    assert st.end_test(config, lines.append)["storage"] == "restored"

    assert _tree(live) == live_before
    assert _tree(stale) == stale_before
    assert any(line.startswith("storage_3 restored") for line in lines)


def test_end_restores_the_folder_the_test_started_with(env):
    """The cfg can be edited while a test is armed. End puts back what start
    copied, by the name start wrote down."""
    config, mission = env
    set_instance_id(config.server_dir_path, 3)
    live = mission / "storage_3"
    live.mkdir()
    (live / "players.db").write_bytes(b"live-before")
    st.start_near_player(config, MOD, "Bicycle", 5)
    (live / "players.db").write_bytes(b"during-the-test")

    cfg = config.server_cfg_path
    cfg.write_text(cfg.read_text().replace("instanceId = 3;", "instanceId = 1;"))
    stale_before = _tree(mission / "storage_1")
    st.end_test(config)

    assert (live / "players.db").read_bytes() == b"live-before"
    assert _tree(mission / "storage_1") == stale_before


def test_a_test_armed_before_the_name_was_recorded_restores_storage_1(env):
    config, mission = env
    storage = mission / "storage_1"
    before = _tree(storage)
    st.start_near_player(config, MOD, "Bicycle", 5)
    active = _config_root(config) / "active.json"
    state = json.loads(active.read_text())
    del state["storage_name"]
    active.write_text(json.dumps(state))
    (storage / "players.db").write_bytes(b"during-the-test")

    assert st.end_test(config)["storage"] == "restored"
    assert _tree(storage) == before


def test_an_init_c_edit_during_the_test_survives_end(env):
    config, mission = env
    st.start_near_player(config, MOD, "Bicycle", 5)
    init_c = mission / "init.c"
    edited = init_c.read_bytes().replace(b"Apple", b"Banana")
    init_c.write_bytes(edited)

    lines: list[str] = []
    result = st.end_test(config, lines.append)
    text = init_c.read_bytes()
    assert result["drifted"] == ["init.c"]
    assert b"Zedstead SPAWN TEST" not in text
    assert text == stock_init_c_crlf().replace("Apple", "Banana").encode()
    assert any("edited during the test" in line for line in lines)


def _nothing_started(config, mission):
    root = _config_root(config)
    assert not (root / "active.json").exists()
    assert not root.exists() or not any(p.is_dir() for p in root.iterdir())
    assert (mission / "init.c").read_bytes() == stock_init_c_crlf().encode()


@pytest.mark.parametrize(
    "classname, count, status, message",
    [
        ("Car", 1, 422, "is not a class in"),
        ('Bad"; Print("x', 1, 422, "is not a class in"),
        ("Bicycle", -1, 422, "whole number"),
        ("Bicycle", 2**31, 422, "whole number"),
        ("Bicycle", 1.5, 422, "whole number"),
        ("Bicycle", True, 422, "whole number"),
    ],
)
def test_bad_input_is_refused_without_side_effects(env, classname, count, status, message):
    config, mission = env
    with pytest.raises(st.SpawnTestError, match=message) as exc:
        st.start_near_player(config, MOD, classname, count)
    assert exc.value.status == status
    _nothing_started(config, mission)


def test_init_c_without_main_is_refused(env):
    config, mission = env
    (mission / "init.c").write_text("class CustomMission: MissionServer {};\n")
    with pytest.raises(st.SpawnTestError, match="no void main") as exc:
        st.start_near_player(config, MOD, "Bicycle", 1)
    assert exc.value.status == 409
    assert not (_config_root(config) / "active.json").exists()


def test_a_stale_block_is_refused(env):
    config, mission = env
    stale = st.inject_spawn_test_block(stock_init_c_crlf(), "Bicycle", 3)
    (mission / "init.c").write_bytes(stale.encode())
    with pytest.raises(st.SpawnTestError, match="earlier test") as exc:
        st.start_near_player(config, MOD, "Bicycle", 1)
    assert exc.value.status == 409
    assert (mission / "init.c").read_bytes() == stale.encode()


def test_second_start_and_end_without_a_test_are_refused(env):
    config, mission = env
    with pytest.raises(st.SpawnTestError, match="No spawn test is active") as exc:
        st.end_test(config)
    assert exc.value.status == 409

    st.start_near_player(config, MOD, "Bicycle", 1)
    injected = (mission / "init.c").read_bytes()
    with pytest.raises(st.SpawnTestError, match="already active for @Bikes") as exc:
        st.start_near_player(config, MOD, "Bicycle", 2)
    assert exc.value.status == 409
    assert (mission / "init.c").read_bytes() == injected


def test_a_high_count_warns_but_is_never_refused(env, monkeypatch):
    config, mission = env
    monkeypatch.setattr("app.ops.vps_readiness.mem_mb", lambda: (3814, 0))
    warn_above, reason = st.spawn_count_warning()
    assert st.count_warning_text(100000, warn_above, reason)
    state = st.start_near_player(config, MOD, "Bicycle", 100000)
    assert state["count"] == 100000
    assert "int count = 100000;" in (mission / "init.c").read_text()


def test_zero_is_accepted(env):
    config, mission = env
    assert st.start_near_player(config, MOD, "Bicycle", 0)["count"] == 0


@pytest.mark.parametrize("failing", ["_write_text_atomic", "_write_json_atomic"])
def test_a_failed_write_mid_start_changes_nothing(env, monkeypatch, failing):
    config, mission = env
    before = _tree(mission / "storage_1")

    def _boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(st, failing, _boom)
    with pytest.raises(st.SpawnTestError, match="disk full") as exc:
        st.start_near_player(config, MOD, "Bicycle", 1)
    assert exc.value.status == 500
    _nothing_started(config, mission)
    assert _tree(mission / "storage_1") == before


def test_only_the_newest_session_dir_is_kept(env):
    config, mission = env
    from pathlib import Path

    st.start_near_player(config, MOD, "Bicycle", 1)
    first = Path(st.active_test(config)["session_dir"])
    st.end_test(config)
    assert first.is_dir()  # a finished session stays until the next start

    st.start_near_player(config, MOD, "Bicycle", 2)
    second = st.active_test(config)["session_dir"]
    dirs = [p for p in _config_root(config).iterdir() if p.is_dir()]
    assert [str(p) for p in dirs] == [second]


def test_end_refuses_before_touching_anything_when_the_copy_is_gone(env):
    config, mission = env
    import shutil

    st.start_near_player(config, MOD, "Bicycle", 1)
    session = st.active_test(config)["session_dir"]
    shutil.rmtree(f"{session}/storage_1")
    injected = (mission / "init.c").read_bytes()

    with pytest.raises(st.SpawnTestError, match="nothing was restored"):
        st.end_test(config)
    assert (mission / "init.c").read_bytes() == injected
    assert st.active_test(config) is not None


def test_unreadable_state_file_blocks_start(env):
    config, mission = env
    root = _config_root(config)
    root.mkdir(parents=True)
    (root / "active.json").write_text("{not json")
    assert st.active_test(config) is None
    with pytest.raises(st.SpawnTestError, match="can't be read"):
        st.start_near_player(config, MOD, "Bicycle", 1)


# ── Count warning ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "measured, warn_above, reason",
    [
        (None, None, "could not read this machine's RAM"),
        (2048, 10, "under 4 GB RAM"),
        (3557, 25, "4–6 GB RAM"),  # an advertised 4 GB box, not "under 4 GB"
        (6144, 50, "6–8 GB RAM"),
        (8192, 100, "8–16 GB RAM"),
        (32768, 200, "16 GB RAM or more"),
    ],
)
def test_spawn_count_warning_tiers(monkeypatch, measured, warn_above, reason):
    monkeypatch.setattr("app.ops.vps_readiness.mem_mb", lambda: (measured, 0))
    assert st.spawn_count_warning() == (warn_above, reason)


def test_count_warning_text_threshold():
    assert st.count_warning_text(25, 25, "4–6 GB RAM") is None
    text = st.count_warning_text(26, 25, "4–6 GB RAM")
    assert text.startswith("26 is above the 25 suggested for this server (4–6 GB RAM).")
    assert "at your own risk" in text
    assert st.count_warning_text(10**6, None, "could not read this machine's RAM") is None


def test_mod_classes_dedupes_and_sorts(env):
    config, _ = env
    assert st.mod_classes(config, MOD) == ["Bicycle", "BicycleBase"]


# ── Pipeline reminder ───────────────────────────────────────────────────


def _pipeline_emits(config) -> list[str]:
    from app.ops.pipeline import PipelineContext, run_pipeline

    emitted: list[str] = []
    ctx = PipelineContext(config=config, emit=emitted.append, runner=None, should_run=lambda k: True)
    asyncio.run(run_pipeline([], ctx))
    return emitted


def test_pipeline_reminds_about_an_active_test(env):
    config, _ = env
    assert _pipeline_emits(config) == []
    st.start_near_player(config, MOD, "Bicycle", 10)
    emitted = _pipeline_emits(config)
    assert len(emitted) == 1
    assert "Spawn test active for @Bikes (10 x Bicycle)" in emitted[0]


def test_state_file_is_json_with_the_documented_keys(env):
    config, _ = env
    st.start_near_player(config, MOD, "Bicycle", 3)
    data = json.loads((_config_root(config) / "active.json").read_text())
    assert set(data) == {
        "mode", "scope", "mod", "classname", "count", "mission",
        "started_at", "session_dir", "storage_existed", "storage_name", "written",
    }
    assert data["storage_name"] == "storage_1"
    assert data["mode"] == "near_player" and data["scope"] is None
    assert data["written"] == {}  # near_player rewrites init.c in place, not economy files


# ── CLI: zsm mods spawn-test ───────────────────────────────────────────


def _cli(args):
    from click.testing import CliRunner

    from app.commands.mods import mods_group

    return CliRunner().invoke(mods_group, ["spawn-test", *args])


@pytest.fixture()
def cli_env(env, monkeypatch):
    config, mission = env
    write_test_mods(config, ['@Bikes'])
    config.save()
    monkeypatch.setattr("app.server.service_control.is_server_running", lambda config=None: False)
    return config, mission


def test_cli_start_status_end(cli_env, monkeypatch):
    config, mission = cli_env
    monkeypatch.setattr("app.ops.vps_readiness.mem_mb", lambda: (3814, 0))

    result = _cli(["start", "Bikes", "Bicycle", "--count", "30"])
    assert result.exit_code == 0, result.output
    assert "at your own risk" in result.output  # above 25 on a 4 GB box, and started anyway
    assert "Spawn test armed" in result.output

    result = _cli(["status"])
    assert "@Bikes: 30 x Bicycle" in result.output

    result = _cli(["end"])
    assert result.exit_code == 0, result.output
    assert (mission / "init.c").read_bytes() == stock_init_c_crlf().encode()
    assert "No spawn test is active." in _cli(["status"]).output


def test_cli_refuses_while_running_and_on_bad_input(cli_env, monkeypatch):
    config, _ = cli_env
    result = _cli(["start", "@Bikes", "Car"])
    assert result.exit_code == 1
    assert "Car is not a class in @Bikes." in result.output

    assert _cli(["start", "@Nope", "Bicycle"]).exit_code == 1

    monkeypatch.setattr("app.server.service_control.is_server_running", lambda config=None: True)
    result = _cli(["start", "@Bikes", "Bicycle"])
    assert result.exit_code == 1
    assert "Server is running. Stop it first." in result.output
    assert st.active_test(config) is None


# ── What the 1.29 compiler rejected (plan 37 step A9, 2026 Q3) ───────


def test_block_uses_no_percent_operator():
    # Enforce has no `%`: the first version's `(i % perRow)` produced
    # "Unknown operator '%'" and took the whole mission script module down with
    # it — the server ran, but no client could finish joining.
    # string.Format's %1/%2 placeholders are a string literal and compiled fine,
    # so they are the only "%" allowed.
    offenders = [
        line
        for line in st._block_lines("Bicycle", 10)
        if "%" in line and "string.Format" not in line
    ]
    assert offenders == []


def test_block_builds_the_position_in_one_expression():
    # The two follow-on errors ("Incompatible parameter 'pos'", "Cannot convert
    # 'float' to 'vector'") were the broken line degrading pos's type. Building
    # it with Vector(x, y, z) leaves nothing to degrade.
    body = "\n".join(st._block_lines("Bicycle", 10))
    assert "vector pos = Vector(px, GetGame().SurfaceY(px, pz), pz);" in body
    assert "pos[" not in body


def test_grid_counters_wrap_at_per_row():
    body = "\n".join(st._block_lines("Bicycle", 10))
    assert "int perRow = 4;" in body
    assert "if (col >= perRow)" in body


# ── At a location ───────────────────────────────────────────────────────


def test_at_location_arms_records_the_place_and_ends_like_near_player(env):
    config, mission = env
    lines: list[str] = []
    state = st.start_at_location(config, MOD, "Bicycle", 3, 5340.74, 11380, "Adamovka", lines.append)
    assert state["mode"] == "at_location"
    assert state["location"] == {"name": "Adamovka", "x": 5340.7, "z": 11380.0}
    assert st.active_test(config) == state
    text = (mission / "init.c").read_text()
    assert "float ox = 5340.7;" in text and "player.SetPosition(origin);" in text
    assert any("Adamovka (5340.7, 11380)" in line and "moved there" in line for line in lines)
    assert "at Adamovka (5340.7, 11380)" in st.active_test_warning(config)

    result = st.end_test(config)
    assert result["restored_files"] == ["init.c"]
    assert (mission / "init.c").read_bytes() == stock_init_c_crlf().encode()
    assert st.active_test(config) is None


@pytest.mark.parametrize(
    "x, z, message",
    [
        (-1, 100, "X -1 is off the map"),
        (100, 20000, r"Z 20000 is off the map \(0 to 15360\)"),
        (float("nan"), 100, "X must be a number"),
        (True, 100, "X must be a number"),
        ("100", 100, "X must be a number"),
    ],
)
def test_a_position_off_the_map_is_refused_before_anything_is_written(env, x, z, message):
    config, mission = env
    before = (mission / "init.c").read_bytes()
    with pytest.raises(st.SpawnTestError, match=message) as exc:
        st.start_at_location(config, MOD, "Bicycle", 1, x, z)
    assert exc.value.status == 422
    assert (mission / "init.c").read_bytes() == before
    assert st.active_test(config) is None


def test_a_location_name_is_one_line(env):
    config, _mission = env
    with pytest.raises(st.SpawnTestError, match="one short line"):
        st.start_at_location(config, MOD, "Bicycle", 1, 100, 100, "Town\nvoid main()")


def test_mission_locations_are_the_maps_places_by_name(env):
    """The places come from the admin's own label file: Zedstead ships none (plans/86)."""
    from app.shared import paths

    config, _mission = env
    user_dir = paths.locations_dir()
    user_dir.mkdir(parents=True, exist_ok=True)
    (user_dir / "locations_chernarusplus.json").write_text(json.dumps([
        {"name": "Mill Hill", "x": 900.0, "z": 900.0, "type": "Hill"},
        {"name": "brookside", "x": 5340.7, "z": 11380.0, "type": "Village"},
        {"name": "Alder Bay", "x": 100.0, "z": 200.0, "type": "Marine"},
    ]), encoding="utf-8")

    places, size = st.mission_locations(config)
    assert size == 15360
    names = [p["name"] for p in places]
    assert names == ["Alder Bay", "brookside", "Mill Hill"]
    assert {"name": "brookside", "x": 5340.7, "z": 11380.0} in places


def test_class_counts_name_every_installed_mod_and_keep_the_cache(sample_config, monkeypatch):
    """The Items tab's mod picker: 0 for a mod with no item class, and one
    mod's read never drops another mod's cached classes."""
    from app.modmgr import mod_conflict, modlist
    from app.ops import spawn_test_ops

    monkeypatch.setattr(modlist, "installed", lambda config: ["@Items", "@Empty"])
    parsed: list[str] = []

    def fake_extract(server_dir, mod_name, sections):
        parsed.append(mod_name)
        return {"CfgVehicles": ["Crate"], "CfgWeapons": ["Rifle", "rifle"]}.get(sections[0], []) if mod_name == "@Items" else []

    monkeypatch.setattr(mod_conflict, "extract_mod_classnames", fake_extract)
    monkeypatch.setattr(mod_conflict, "_get_mod_pbo_mtime", lambda server_dir, mod: 1.0)

    assert spawn_test_ops.class_counts(sample_config) == {"@Items": 2, "@Empty": 0}
    spawn_test_ops.mod_classes(sample_config, "@Items")
    parsed.clear()
    assert spawn_test_ops.class_counts(sample_config) == {"@Items": 2, "@Empty": 0}
    assert parsed == []
