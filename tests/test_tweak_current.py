"""Tests for quick_tweaks.current_multiplier and the partial serverDZ save that
the My server page uses. The XML here is invented, in the shape of the files."""

from __future__ import annotations

import pytest

from app.presets.quick_tweaks import apply_tweak, current_multiplier, reset_tweak
from app.xml.types_xml import parse_types_file, save_types_files, set_numeric_field

TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>\n<types>\n'
    '    <type name="MadeUpCan"><nominal>40</nominal><min>20</min><category name="food"/></type>\n'
    '    <type name="MadeUpBar"><nominal>30</nominal><min>10</min><category name="food"/></type>\n'
    "</types>\n"
)
GLOBALS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>\n<variables>\n'
    '    <var name="ZombieMaxCount" type="0" value="1000"/>\n</variables>\n'
)


@pytest.fixture()
def mission(tmp_path):
    (tmp_path / "db").mkdir()
    (tmp_path / "db" / "types.xml").write_text(TYPES, encoding="utf-8")
    (tmp_path / "db" / "globals.xml").write_text(GLOBALS, encoding="utf-8")
    return tmp_path


def _food_tier(tweaks_key: str = "food_amount") -> float:
    from app.presets.quick_tweaks import QUICK_TWEAKS

    return next(t.multiplier for t in QUICK_TWEAKS[tweaks_key].tiers if t.multiplier not in (0, 1))


def test_an_untouched_mission_is_at_normal(mission):
    assert current_multiplier("food_amount", mission) == 1.0
    assert current_multiplier("zombie_count", mission) == 1.0
    assert current_multiplier("predator_count", mission) == 1.0


def test_the_applied_tier_is_read_back(mission):
    tier = _food_tier()
    apply_tweak("food_amount", None, mission, tier)
    assert current_multiplier("food_amount", mission) == tier


def test_reading_writes_nothing(mission):
    apply_tweak("food_amount", None, mission, _food_tier())
    before = {p.name: p.read_bytes() for p in mission.rglob("*") if p.is_file()}
    current_multiplier("food_amount", mission)
    current_multiplier("zombie_count", mission)
    assert {p.name: p.read_bytes() for p in mission.rglob("*") if p.is_file()} == before


def test_a_reset_goes_back_to_normal(mission):
    apply_tweak("food_amount", None, mission, _food_tier())
    reset_tweak("food_amount", None, mission)
    assert current_multiplier("food_amount", mission) == 1.0


def test_one_hand_edit_after_a_tweak_is_custom(mission):
    apply_tweak("food_amount", None, mission, _food_tier())
    tf = parse_types_file(mission / "db" / "types.xml", preserve_comments=True)
    set_numeric_field(tf.entries[0], "nominal", 777)
    save_types_files([tf])
    assert current_multiplier("food_amount", mission) is None


def test_zombies_read_their_tier_and_a_hand_edit_is_custom(mission):
    apply_tweak("zombie_count", None, mission, 3.0)
    assert current_multiplier("zombie_count", mission) == 3.0
    path = mission / "db" / "globals.xml"
    path.write_text(path.read_text(encoding="utf-8").replace('value="3000"', 'value="1234"'), encoding="utf-8")
    assert current_multiplier("zombie_count", mission) is None


def test_the_route_reports_the_current_tier(sample_config, mission, monkeypatch):
    from fastapi.testclient import TestClient

    from app.web import server
    from app.web.auth import hash_password
    from app.web.routers import presets

    monkeypatch.setattr(presets, "mission_dir", lambda config: mission)
    apply_tweak("zombie_count", None, mission, 3.0)
    sample_config.web_password_hash = hash_password("testpass123")
    with TestClient(server.create_app(sample_config)) as client:
        client.post("/api/auth/login", json={"password": "testpass123"})
        tweaks = {t["id"]: t for t in client.get("/api/tweaks").json()}
        monkeypatch.setattr(presets, "mission_dir", lambda config: None)
        without_mission = client.get("/api/tweaks").json()
    assert tweaks["zombie_count"]["current"] == {"": 3.0}
    assert all(t["current"] == {} for t in without_mission)


def test_a_save_of_one_key_keeps_every_other_line(sample_config):
    """The My server page sends only what the admin changed."""
    from app.web.editors.server_forms import save_serverdz

    text = (
        'hostname = "Old";\n// a comment\nmaxPlayers = 60;\npassword = "keep";\n'
        "serverTimeAcceleration = 4.0;\ninstanceId = 1;\n"
    )
    sample_config.server_cfg_path.parent.mkdir(parents=True, exist_ok=True)
    sample_config.server_cfg_path.write_text(text, encoding="utf-8")
    save_serverdz(sample_config, {"maxPlayers": 10})
    assert sample_config.server_cfg_path.read_text(encoding="utf-8") == text.replace("maxPlayers = 60", "maxPlayers = 10")


def test_the_tweak_bound_lets_every_tier_through_and_refuses_the_rest():
    """POST /api/presets/tweaks/<id> checks through check_multipliers; the None
    tier of the zombie tweak is 0, so the tweak bound lets 0 through."""
    from fastapi import HTTPException

    from app.presets.quick_tweaks import QUICK_TWEAKS
    from app.web.multipliers import check_multipliers

    tweaks = QUICK_TWEAKS.values() if isinstance(QUICK_TWEAKS, dict) else QUICK_TWEAKS
    for tweak in tweaks:
        for tier in getattr(tweak, "tiers", None) or []:
            check_multipliers(allow_zero=True, tweak=tier.multiplier)
    for bad in (-1, 10.5, 1000):
        with pytest.raises(HTTPException) as exc_info:
            check_multipliers(allow_zero=True, tweak=bad)
        assert exc_info.value.status_code == 422
    with pytest.raises(HTTPException):
        check_multipliers(price=0)
