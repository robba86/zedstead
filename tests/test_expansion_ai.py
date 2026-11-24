"""Tests for app/ops/expansion_ai.py — Expansion AI's own patrols against its
load rules (plans/64). Every document here is hand-written from the key names
and the shape plans/64 Step 0 measured; nothing from Expansion's files is
copied (licence)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.ops import expansion_ai
from app.ops.expansion_ai import build_report, doctor_row
from tests.conftest import mods_lines, set_test_flags, write_test_ids, write_test_mods

MOD = "@DayZ-Expansion-AI"
FACTIONS_PBO = (
    b"\x00junk eAIRegisterFaction(eAIFactionEast); eAIRegisterFaction(eAIFactionWest);"
    b" eAIRegisterFaction(eAIFactionGuards) \x00"
)
LOADOUTS = ("FreshSpawnLoadout.json", "PoliceLoadout.json", "NBCLoadout.json")

# The load-balancing table Expansion writes by default (plans/64 B12).
CATEGORIES = {
    "Global": [
        {"MinPlayers": 0, "MaxPlayers": 10, "MaxPatrols": 20},
        {"MinPlayers": 11, "MaxPlayers": 20, "MaxPatrols": 16},
        {"MinPlayers": 21, "MaxPlayers": 30, "MaxPatrols": 12},
        {"MinPlayers": 31, "MaxPlayers": 40, "MaxPatrols": 8},
        {"MinPlayers": 41, "MaxPlayers": 50, "MaxPatrols": 4},
        {"MinPlayers": 51, "MaxPlayers": 255, "MaxPatrols": 0},
    ],
    **{
        name: [{"MinPlayers": 0, "MaxPlayers": 255, "MaxPatrols": limit}]
        for name, limit in (
            ("ObjectPatrol", 5), ("Patrol", 5), ("Quest", -1), ("HelicopterWreck", 3),
            ("ContaminatedArea", 2), ("MilitaryRoaming", 5), ("MilitaryStatic", 5),
            ("Survivor", 15), ("Example", -1),
        )
    },
}


def _patrol(**over) -> dict:
    """A roaming patrol with every value inherited, the way a default one is."""
    patrol = {
        "Name": "Survivors", "Persist": 1, "Faction": "RANDOM", "Formation": "",
        "Loadout": "FreshSpawnLoadout", "Units": [], "NumberOfAI": 1, "NumberOfAIMax": 3,
        "Behaviour": "ROAMING", "Speed": "JOG", "UnderThreatSpeed": "SPRINT",
        "DefaultStance": "STANDING", "LootingBehaviour": "DEFAULT | CLOTHING_BODY | UPGRADE",
        "AccuracyMin": -1.0, "AccuracyMax": -1.0, "MinDistRadius": -1.0, "MaxDistRadius": -1.0,
        "Chance": 0.5, "RespawnTime": -2.0, "LootDropOnDeath": "", "ObjectClassName": "",
        "WaypointInterpolation": "", "LoadBalancingCategory": "Survivor",
        "Waypoints": [[7500.0, 210.0, 7400.0]],
    }
    patrol.update(over)
    return patrol


def _object_patrol(cls="Land_Mil_GuardTower", **over) -> dict:
    """Guards on every building of a class; the waypoint is metres from it."""
    return _patrol(**{
        "Name": "", "Persist": 0, "Faction": "Guards", "Loadout": "PoliceLoadout",
        "ObjectClassName": cls, "LoadBalancingCategory": "", "Behaviour": "HALT",
        "Speed": "WALK", "DefaultStance": "CROUCHED", "Waypoints": [[0.5, 0.0, 2.0]], **over,
    })


def _settings(**over) -> dict:
    doc = {
        "m_Version": 32, "Enabled": 1, "FormationScale": -1.0, "DespawnTime": 600.0,
        "RespawnTime": -1.0, "MinDistRadius": 400.0, "MaxDistRadius": 1000.0,
        "DespawnRadius": 1100.0, "AccuracyMin": -1.0, "AccuracyMax": -1.0,
        "LoadBalancingCategories": CATEGORIES,
        "Patrols": [
            _object_patrol(),
            _object_patrol("Land_Mil_Barracks1", Faction="East", Loadout="NBCLoadout",
                           LoadBalancingCategory="MilitaryStatic"),
            _object_patrol("Wreck_Mi8_Crashed", Loadout="", Waypoints=[],
                           LoadBalancingCategory="HelicopterWreck"),
            _patrol(),
            _patrol(Name="Military", Faction="West", Loadout="", Behaviour="LOOP_OR_ALTERNATE",
                    WaypointInterpolation="UniformCubic", MaxDistRadius=21722.0,
                    LoadBalancingCategory="MilitaryRoaming",
                    Waypoints=[[3000.0, 100.0, 9000.0], [3100.0, 100.0, 9100.0]]),
        ],
    }
    doc.update(over)
    return doc


@pytest.fixture()
def eai(sample_config, tmp_mission_dir):
    """An active Expansion AI registering East, West and Guards, three
    loadouts, the loot drops folder, and a patrol file in the stock shape."""
    cfg = sample_config
    write_test_mods(cfg, mods_lines(f"@CF\n{MOD}\n"))
    cfg.save()
    addons = cfg.server_dir_path / MOD / "addons"
    addons.mkdir(parents=True)
    (addons / "ai_scripts.pbo").write_bytes(FACTIONS_PBO)
    loadouts = expansion_ai.ai_refs.loadouts_dir(cfg)
    loadouts.mkdir(parents=True)
    for name in LOADOUTS:
        (loadouts / name).write_text("{}", encoding="utf-8")
    drops = expansion_ai.loot_drops_dir(cfg)
    drops.mkdir(parents=True)
    (drops / "Example.json").write_text("{}", encoding="utf-8")
    expansion_ai.patrol_path(cfg).parent.mkdir(parents=True)
    _write(cfg, _settings())
    return cfg


def _write(cfg, doc, raw: str | None = None) -> Path:
    path = expansion_ai.patrol_path(cfg)
    path.write_text(raw if raw is not None else json.dumps(doc, indent=4), encoding="utf-8")
    return path


def _write_ai_settings(cfg, doc: dict) -> None:
    path = expansion_ai.ai_settings_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")


def _ids(cfg) -> dict[str, dict]:
    return {f["id"]: f for f in build_report(cfg)["findings"]}


def _with(cfg, *patrols, **top) -> dict[str, dict]:
    _write(cfg, _settings(Patrols=list(patrols), **top))
    return _ids(cfg)


# ── when it applies ───────────────────────────────────────────────────


def test_nothing_when_the_mod_is_not_installed(sample_config):
    write_test_mods(sample_config, ['@CF'])
    assert build_report(sample_config) is None
    assert doctor_row(sample_config) is None


def test_nothing_when_the_mod_is_disabled(eai):
    set_test_flags(eai, disabled=[MOD])
    assert build_report(eai) is None


def test_found_under_another_folder_name_by_workshop_id(eai):
    write_test_mods(eai, ['@ExpAI'])
    write_test_ids({'@ExpAI': '2792982069'})
    assert build_report(eai)["mod"] == "@ExpAI"


def test_the_stock_shape_passes(eai):
    """RANDOM, empty loadouts, object patrols with relative waypoints and the
    default categories are how Expansion writes the file: nothing to report."""
    report = build_report(eai)
    assert report["findings"] == []
    assert report["factions"] == ["East", "Guards", "West"]
    assert (report["patrols"], report["object_patrols"]) == (5, 3)
    assert report["file"] == "mpmissions/dayzOffline.chernarusplus/expansion/settings/AIPatrolSettings.json"
    assert report["files"] == {"patrols": True, "ai_settings": False}
    assert "Global" in report["categories"]
    assert doctor_row(eai) == ("Expansion AI", True, "Setup OK · 5 patrols, 3 on objects")


def test_the_report_never_writes(eai):
    before = {p: p.read_bytes() for p in eai.server_dir_path.rglob("*") if p.is_file()}
    build_report(eai)
    after = {p: p.read_bytes() for p in eai.server_dir_path.rglob("*") if p.is_file()}
    # The one new file is the faction scan's own cache.
    assert {p.name for p in set(after) - set(before)} == {".pbo_faction_cache.json"}
    assert all(after[p] == before[p] for p in before)


# ── the file ──────────────────────────────────────────────────────────


def test_a_missing_file_is_a_notice(eai):
    expansion_ai.patrol_path(eai).unlink()
    report = build_report(eai)
    assert [f["id"] for f in report["findings"]] == ["expansion_ai_patrols_missing"]
    assert report["findings"][0]["severity"] == "notice"
    assert report["files"]["patrols"] is False and report["patrols"] == 0


def test_no_mission_folder_is_a_notice(eai, monkeypatch):
    monkeypatch.setattr(expansion_ai, "patrol_path", lambda config: None)
    report = build_report(eai)
    assert report["file"] is None
    assert report["findings"][0]["id"] == "expansion_ai_patrols_missing"
    assert "No mission folder" in report["findings"][0]["detail"]


def test_an_unreadable_file_runs_the_built_in_defaults(eai):
    _write(eai, None, raw='{"m_Version": 32, "Patrols": [}')
    f = _ids(eai)["expansion_ai_patrols_unreadable"]
    assert f["severity"] == "error"
    assert "line 1" in f["detail"]
    assert "built-in default patrols" in f["why"]
    assert f["links"] == [{"label": "AI Patrols", "to": "/configs/expansion-ai-patrols"}]


def test_switched_off_is_a_notice(eai):
    _write(eai, _settings(Enabled=0))
    f = _ids(eai)["expansion_ai_patrols_off"]
    assert f["severity"] == "notice"
    assert f["links"] == [{"label": "AI Patrols", "to": "/configs/expansion-ai-patrols"}]
    assert doctor_row(eai)[1] is True


def test_an_older_version_is_a_notice(eai):
    _write(eai, _settings(m_Version=29))
    f = _ids(eai)["expansion_ai_version_upgrade"]
    assert f["severity"] == "notice" and "m_Version 29" in f["detail"]
    _write(eai, _settings(m_Version=32))
    assert "expansion_ai_version_upgrade" not in _ids(eai)


# ── patrols ───────────────────────────────────────────────────────────


def test_an_unknown_faction_spawns_as_civilian(eai):
    ids = _with(eai, _patrol(Faction="Bandits"), _object_patrol(Faction="guards"), _patrol(Name="B", Faction=""))
    f = ids["expansion_ai_unknown_faction"]
    assert f["severity"] == "warning" and f["count"] == 3
    assert "Patrol 'Survivors': Bandits is not a registered faction" in f["detail"]
    # Object patrols are named by their building.
    assert "Object patrol Land_Mil_GuardTower: guards differs from Guards only in case" in f["detail"]
    assert "Patrol 'B': no faction named" in f["detail"]
    assert "Civilian" in f["why"] and "East, Guards, West" in f["why"]


def test_no_faction_check_when_the_scan_found_none(eai):
    (eai.server_dir_path / MOD / "addons" / "ai_scripts.pbo").write_bytes(b"x")
    assert "expansion_ai_unknown_faction" not in _with(eai, _patrol(Faction="Anything"))


def test_a_loadout_is_found_with_or_without_json(eai):
    ids = _with(eai, _patrol(Loadout="PoliceLoadout.json"), _patrol(Name="B", Loadout="PoliceLoadout"),
                _patrol(Name="C", Loadout=""))
    assert "expansion_ai_unknown_loadout" not in ids


def test_an_unknown_loadout_is_named(eai):
    f = _with(eai, _patrol(Loadout="HumanLoadout"))["expansion_ai_unknown_loadout"]
    assert f["detail"] == "Patrol 'Survivors': HumanLoadout is not in ExpansionMod/Loadouts"
    assert "Unknown loadout requested" in f["why"]


def test_an_unknown_category_falls_back_to_global(eai):
    ids = _with(eai, _patrol(LoadBalancingCategory="Bandits"), _patrol(Name="B", LoadBalancingCategory=""))
    f = ids["expansion_ai_category_fallback"]
    # "" is Patrol, which the stock table has.
    assert f["detail"] == "Patrol 'Survivors': Bandits"
    assert "only Global's limits apply" in f["why"]


def test_an_empty_category_uses_the_patrol_kind_default(eai):
    categories = {k: v for k, v in CATEGORIES.items() if k not in ("ObjectPatrol", "Global")}
    ids = _with(eai, _patrol(LoadBalancingCategory=""), _object_patrol(),
                LoadBalancingCategories=categories)
    f = ids["expansion_ai_category_fallback"]
    assert f["detail"] == "Object patrol Land_Mil_GuardTower: ObjectPatrol"
    assert "no Global category, so nothing limits it" in f["why"]


def test_number_of_ai_zero(eai):
    ids = _with(eai, _patrol(NumberOfAI=0), _patrol(Name="B", NumberOfAI=-4))
    assert ids["expansion_ai_number_zero"]["detail"] == "Patrol 'Survivors'"


def test_a_normal_patrol_needs_a_waypoint(eai):
    ids = _with(eai, _patrol(Waypoints=[]), _object_patrol(Waypoints=[]))
    # The object patrol gets waypoints generated around its building.
    assert ids["expansion_ai_no_waypoints"]["detail"] == "Patrol 'Survivors'"


def test_waypoints_off_the_map_or_at_the_origin(eai):
    ids = _with(
        eai,
        _patrol(Waypoints=[[16000.0, 0.0, 300.0]]),
        _patrol(Name="Unset", Waypoints=[[0.0, 0.0, 0.0], [10.0, 0.0, 10.0]]),
        # Relative to its building: never a map position.
        _object_patrol(Waypoints=[[-3.0, 0.0, 0.0]]),
    )
    f = ids["expansion_ai_waypoint_off_map"]
    assert f["detail"] == "Patrol 'Unset': at 0, 0; Patrol 'Survivors': beyond the edge"
    assert "15360 m square" in f["why"]


def test_off_map_is_not_judged_on_an_unknown_map(eai, monkeypatch):
    monkeypatch.setattr(expansion_ai, "current_terrain", lambda config: "somemodmap")
    ids = _with(eai, _patrol(Waypoints=[[16000.0, 0.0, 300.0]]))
    assert "expansion_ai_waypoint_off_map" not in ids


def test_chance_zero_is_a_notice(eai):
    f = _with(eai, _patrol(Chance=0.0))["expansion_ai_never_spawns"]
    assert f["severity"] == "notice"


def test_spawn_distances_the_wrong_way_round(eai):
    ids = _with(
        eai,
        _patrol(MinDistRadius=1200.0),                       # 1200 > the file's 1000
        _patrol(Name="B", MinDistRadius=1200.0, MaxDistRadius=2000.0),
        _patrol(Name="C", MaxDistRadius=0.0),                # 0 is "the file's"
    )
    assert ids["expansion_ai_radius_swapped"]["detail"] == "Patrol 'Survivors': 1200 > 1000"


def test_accuracy_inherits_down_to_ai_settings(eai):
    assert "expansion_ai_accuracy" not in _with(eai, _patrol())
    _write_ai_settings(eai, {"AccuracyMin": 0.9, "AccuracyMax": 0.5})
    f = _ids(eai)["expansion_ai_accuracy"]
    assert "Patrol 'Survivors': 0.9–0.5" in f["detail"]
    # The file's own values win over AISettings.json's.
    ids = _with(eai, _patrol(), AccuracyMin=0.2, AccuracyMax=0.8)
    assert "expansion_ai_accuracy" not in ids
    ids = _with(eai, _patrol(AccuracyMax=1.5), AccuracyMin=0.2, AccuracyMax=0.8)
    assert ids["expansion_ai_accuracy"]["detail"] == "Patrol 'Survivors': 0.2–1.5"


def test_persist_on_an_object_patrol_is_a_notice(eai):
    f = _with(eai, _object_patrol(Persist=1))["expansion_ai_persist_object"]
    assert f["severity"] == "notice"


def test_a_persistent_patrol_needs_a_name_of_its_own(eai):
    ids = _with(eai, _patrol(), _patrol(), _patrol(Name=""), _patrol(Name="Other", Persist=0))
    f = ids["expansion_ai_persist_name"]
    assert f["detail"] == (
        "Patrol 'Survivors': Name used by an earlier persistent patrol; Patrol #3: no Name")


def test_a_missing_loot_drop_file(eai):
    ids = _with(eai, _patrol(LootDropOnDeath="Example"), _patrol(Name="B", LootDropOnDeath="Example.json"),
                _patrol(Name="C", LootDropOnDeath="Missing"))
    assert ids["expansion_ai_lootdrop_missing"]["detail"] == "Patrol 'C': Missing"


def test_values_expansion_does_not_document(eai):
    ids = _with(
        eai,
        _patrol(Behaviour="PATROL", Speed="RUN", LootingBehaviour="WEAPONS |  CLOTHING_HATS",
                WaypointInterpolation="Bezier", Formation="Anything"),
        _object_patrol(DefaultStance="SITTING", UnderThreatSpeed="sprint"),
    )
    f = ids["expansion_ai_unknown_value"]
    assert f["detail"] == (
        "Patrol 'Survivors': Behaviour PATROL; Patrol 'Survivors': Speed RUN; "
        "Patrol 'Survivors': WaypointInterpolation Bezier; Patrol 'Survivors': LootingBehaviour CLOTHING_HATS; "
        "Object patrol Land_Mil_GuardTower: UnderThreatSpeed sprint; "
        "Object patrol Land_Mil_GuardTower: DefaultStance SITTING")


def test_every_looting_part_the_stock_file_uses_is_known(eai):
    """Plans/64 Step 0: the ten tokens a real boot wrote, with its spacing."""
    looting = ("ALL | UPGRADE | CLOTHING_BODY | CLOTHING_LEGS | CLOTHING_FEET | CLOTHING_GLOVES"
               " | CLOTHING_HEADGEAR | CLOTHING_MASK | CLOTHING_SIMILAR | DEFAULT")
    assert "expansion_ai_unknown_value" not in _with(eai, _patrol(LootingBehaviour=looting))


# ── links (step 5) ────────────────────────────────────────────────────


def test_a_finding_about_one_named_patrol_opens_the_table_on_it(eai):
    f = _with(eai, _patrol(Name="North Camp", Chance=0))["expansion_ai_never_spawns"]
    assert f["links"] == [{"label": "Patrol list", "to": "/configs/expansion-ai-patrol-list?q=North%20Camp"}]


def test_a_finding_about_several_or_unnamed_patrols_opens_the_whole_table(eai):
    table = {"label": "Patrol list", "to": "/configs/expansion-ai-patrol-list"}
    ids = _with(eai, _patrol(Name="A", Chance=0), _patrol(Name="B", Chance=0))
    assert ids["expansion_ai_never_spawns"]["links"] == [table]
    ids = _with(eai, _object_patrol(Chance=0))
    assert ids["expansion_ai_never_spawns"]["links"] == [table]


def test_waypoint_findings_also_open_the_map(eai):
    ids = _with(eai, _patrol(Waypoints=[]), _patrol(Name="Z", Waypoints=[[0.0, 0.0, 0.0]]))
    for fid in ("expansion_ai_no_waypoints", "expansion_ai_waypoint_off_map"):
        assert ids[fid]["links"][1] == {"label": "Map editor", "to": "/map-editor?layers=expaipatrols"}


# ── load balancing (step 3) ───────────────────────────────────────────


def _slots(cfg, n: int) -> None:
    cfg.server_cfg_path.write_text(
        f'hostname = "Test Server";\nmaxPlayers = {n};\n'
        'class Missions { class DayZ { template = "dayzOffline.chernarusplus"; }; };\n',
        encoding="utf-8")


def _categories(**cats) -> dict:
    return {name: [{"MinPlayers": lo, "MaxPlayers": hi, "MaxPatrols": n} for lo, hi, n in entries]
            for name, entries in cats.items()}


INF = float("inf")


@pytest.mark.parametrize("lo, hi, ranges, gaps", [
    (0, 60, [(0, 10), (11, 20)], [(21, 60)]),       # 0-10 and 11-20 touch
    (0, 60, [(5, INF)], [(0, 4)]),
    (0, 60, [(30, 40), (0, 9)], [(10, 29), (41, 60)]),
    (40, INF, [(0, INF)], []),
    (40, INF, [(0, 255)], [(256, INF)]),
    (10, 5, [], []),
])
def test_uncovered_counts(lo, hi, ranges, gaps):
    assert expansion_ai._uncovered(lo, hi, expansion_ai._merge(ranges)) == gaps


def test_the_stock_table_on_60_slots_is_one_notice_and_a_green_row(eai):
    """Expansion's own Global entry allows no patrol above 50 players: the
    container's real situation (plans/64 Step 0). Correct, and not a problem."""
    _slots(eai, 60)
    report = build_report(eai)
    assert [(f["id"], f["detail"]) for f in report["findings"]] == [
        ("expansion_ai_category_none_allowed", "Global: 51–60 players")]
    assert "every patrol despawns" in report["findings"][0]["why"]
    label, ok, detail = doctor_row(eai)
    assert ok and "Player counts at which a load-balancing category allows no patrols" in detail


def test_the_stock_table_on_50_slots_is_silent(eai):
    _slots(eai, 50)
    assert build_report(eai)["findings"] == []


def test_counts_without_slots_are_not_guessed(eai):
    """No maxPlayers: the gap and none-allowed checks skip."""
    ids = _with(eai, _patrol(), LoadBalancingCategories=_categories(
        Global=[(5, 10, 0)], Survivor=[(0, 255, 15)]))
    assert "expansion_ai_category_gap" not in ids
    assert "expansion_ai_category_none_allowed" not in ids


def test_a_gap_means_no_limit_then(eai):
    _slots(eai, 40)
    ids = _with(eai, _patrol(), LoadBalancingCategories=_categories(
        Global=[(0, 10, 20), (21, 0, 8)], Survivor=[(5, 30, 15)]))
    f = ids["expansion_ai_category_gap"]
    assert f["detail"] == "Global: 11–20 players; Survivor: 0–4, 31–40 players"
    assert f["severity"] == "notice" and "40 slots" in f["why"]
    assert f["links"] == [{"label": "AI Patrols (Raw)", "to": "/configs/expansion-ai-patrols?tab=Raw"}]


def test_an_entry_the_ones_above_always_beat(eai):
    ids = _with(eai, _patrol(), LoadBalancingCategories=_categories(
        Global=[(0, 10, 20), (11, 0, 16), (30, 40, 4)], Survivor=[(0, 255, 15)]))
    assert ids["expansion_ai_category_shadowed"]["detail"] == "Global #3: 30–40 players"


def test_an_entry_that_matches_no_count(eai):
    ids = _with(eai, _patrol(), LoadBalancingCategories=_categories(
        Global=[(0, 0, 20), (20, 10, 5), (40, 0, 1)], Survivor=[(0, 255, 15)]))
    assert ids["expansion_ai_category_never_applies"]["detail"] == "Global #2: MinPlayers 20 above MaxPlayers 10"
    # MaxPlayers 0 is "no upper bound", never "below the minimum"; the third
    # entry is merely beaten by the first, which has no bound either.
    assert ids["expansion_ai_category_shadowed"]["detail"] == "Global #3: 40 and up players"


def test_max_patrols_zero_only_where_it_applies(eai):
    _slots(eai, 30)
    ids = _with(eai, _patrol(), LoadBalancingCategories=_categories(
        Global=[(0, 255, 20)], Survivor=[(0, 10, 0), (11, 255, 15)], Quest=[(40, 255, 0), (0, 39, -1)]))
    f = ids["expansion_ai_category_none_allowed"]
    # Quest's 0 starts above the 30 slots: never reached.
    assert f["detail"] == "Survivor: 0–10 players"
    assert "every patrol despawns" not in f["why"]


# ── player factions (step 3) ──────────────────────────────────────────


def test_unknown_player_factions(eai):
    _write_ai_settings(eai, {"PlayerFactions": ["West", "RANDOM", "Bandits", "east"]})
    f = _ids(eai)["expansion_ai_player_faction_unknown"]
    assert f["severity"] == "warning"
    assert f["detail"] == "Bandits is not a registered faction; east differs from East only in case"
    assert f["links"] == [{"label": "Expansion AI", "to": "/configs/expansion-ai"}]


def test_no_player_factions_is_fine(eai):
    _write_ai_settings(eai, {"PlayerFactions": []})
    assert "expansion_ai_player_faction_unknown" not in _ids(eai)
    _write_ai_settings(eai, {"PlayerFactions": ["RANDOM"]})
    assert "expansion_ai_player_faction_unknown" not in _ids(eai)


# ── Doctor ────────────────────────────────────────────────────────────


def test_a_failing_row_names_the_problem(eai):
    _write(eai, _settings(Patrols=[_patrol(NumberOfAI=0)]))
    label, ok, detail = doctor_row(eai)
    assert (label, ok) == ("Expansion AI", False)
    assert detail == "Patrols with NumberOfAI 0 — Patrol 'Survivors'"


def test_doctor_runs_the_row_only_with_the_mod(eai):
    from app.commands import doctor

    assert doctor._check_expansion_ai(eai) == ("Expansion AI", True, "Setup OK · 5 patrols, 3 on objects")
    write_test_mods(eai, ['@CF'])
    assert doctor._check_expansion_ai(eai) is None


def test_a_crashing_check_does_not_sink_the_doctor(eai, monkeypatch):
    from app.commands import doctor

    def _boom(config):
        raise RuntimeError("bad")

    monkeypatch.setattr(expansion_ai, "doctor_row", _boom)
    assert doctor._check_expansion_ai(eai) == ("Expansion AI", False, "Check failed: bad")


def test_the_web_row_links_to_the_editors(eai):
    from app.ops.doctor_report import _links_for

    patrols = {"label": "AI Patrols", "to": "/configs/expansion-ai-patrols"}
    assert _links_for("Expansion AI", False, "Patrols with NumberOfAI 0 — x")[0] == [patrols]
    assert _links_for("Expansion AI", False, "Player factions Expansion does not know — x")[0] == [
        patrols, {"label": "Expansion AI", "to": "/configs/expansion-ai"}]
    assert _links_for("Expansion AI", True, "Setup OK")[0] == []
