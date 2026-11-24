"""Underground triggers on the map (`app/spawn_editor/underground_state.py`).

The fixture is Sakhal-shaped on purpose: Sakhal is the terrain this feature
exists for (thirty triggers, 11 KB) and the only one whose triggers carry the
extra keys — `Comment`, `ParentNetworkId`, `AmbientSoundSet`. Livonia's have
seven keys and Chernarus ships an empty stub. A fixture built from the
documented fields alone would not catch the thing most worth catching: that a
save keeps what this version of zsm has never heard of.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import CE_1_29, bohemia_ce, ce_lines

PASSWORD = "testpass123"

TRIGGERS = {
    "Triggers": [
        {
            # Transitional: it has breadcrumbs, whatever its accommodation.
            "Position": [9663.46, 211.88, 8466.49],
            "Orientation": [45, 0, 0],
            "Size": [30, 8, 20],
            "EyeAccommodation": 1,
            "UseLinePointFade": 1,
            "AmbientSoundType": "caveBig",
            "Comment": "Main entrance",
            "ParentNetworkId": 12345,
            "Breadcrumbs": [
                {
                    "Position": [9674.97, 213.50, 8469.38],
                    "EyeAccommodation": 1,
                    "UseRaycast": 0,
                    "Radius": -1,
                },
                {"Position": [9672.06, 213.06, 8467.80], "EyeAccommodation": 0.7},
            ],
        },
        {
            # Outer: no breadcrumbs, accommodation 1. Carries the *other*
            # ambient spelling — `AmbientSoundSet` is on all eight Livonia
            # triggers and sixteen of Sakhal's thirty, while the
            # `AmbientSoundType` above is on fourteen Sakhal triggers and no
            # other terrain. Reading one name and not the other is the bug
            # this pins.
            "Position": [9700.0, 210.0, 8500.0],
            "Orientation": [0, 0, 0],
            "Size": [15, 5.6, 10.8],
            "EyeAccommodation": 1,
            "AmbientSoundSet": "Underground_SoundSet",
            "Breadcrumbs": [],
            "InterpolationSpeed": 1,
        },
        {
            # Inner: no breadcrumbs, accommodation below 1.
            "Position": [9600.0, 205.0, 8400.0],
            "Orientation": [0, 0, 0],
            "Size": [55.6, 8.6, 104.6],
            "EyeAccommodation": 0,
            "Breadcrumbs": [],
            "InterpolationSpeed": 1,
        },
    ]
}


@pytest.fixture()
def underground_mission(tmp_mission_dir: Path) -> Path:
    (tmp_mission_dir / "cfgundergroundtriggers.json").write_text(
        json.dumps(TRIGGERS, indent=4), encoding="utf-8"
    )
    return tmp_mission_dir


@pytest.fixture()
def client(sample_config, underground_mission):
    from app.web import server
    from app.web.auth import hash_password

    sample_config.web_password_hash = hash_password(PASSWORD)
    tclient = TestClient(server.create_app(sample_config))
    assert tclient.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
    return tclient


@pytest.fixture()
def bare_client(sample_config, tmp_mission_dir):
    """A mission with no underground file at all — Chernarus, in effect."""
    from app.web import server
    from app.web.auth import hash_password

    sample_config.web_password_hash = hash_password(PASSWORD)
    tclient = TestClient(server.create_app(sample_config))
    tclient.post("/api/auth/login", json={"password": PASSWORD})
    return tclient


def _file(mission: Path) -> dict:
    return json.loads((mission / "cfgundergroundtriggers.json").read_text())


def _triggers(client) -> list[dict]:
    body = client.get("/api/map-editor/underground-triggers").json()
    return body["triggers"]


# ── reading ───────────────────────────────────────────────────────


def test_triggers_carry_their_world_box_and_a_map_position(client):
    first = _triggers(client)[0]
    assert (first["game_x"], first["game_y"], first["game_z"]) == (9663.46, 211.88, 8466.49)
    assert (first["size_x"], first["size_y"], first["size_z"]) == (30.0, 8.0, 20.0)
    assert first["yaw"] == 45.0
    assert first["lat"] and first["lng"]


def test_the_type_is_derived_from_the_two_rules_in_the_spec(client):
    """Bohemia states it as: breadcrumbs make it transitional; without them it
    is outer at accommodation 1 and inner otherwise. It is computed server-side
    so the map, the CLI and the config editor cannot disagree."""
    assert [t["type"] for t in _triggers(client)] == ["transitional", "outer", "inner"]


def test_a_trigger_without_a_comment_still_has_a_name(client):
    """`Comment` is Sakhal's own field and the format's only human label."""
    triggers = _triggers(client)
    assert triggers[0]["name"] == "Main entrance"
    assert triggers[1]["name"] == "Trigger 1"


def test_both_of_bohemias_ambient_sound_spellings_are_read(client):
    """Measured against the real files in 2026 Q3: Livonia writes
    `AmbientSoundSet` on every trigger, Sakhal uses both names across its
    thirty. The code read only `AmbientSoundType`, so every Livonia trigger
    came back with an empty sound — and the fixture agreed, because it had been
    written from the code instead of from Bohemia's data. Same mistake, same
    day, as `user=` where the file says `name=`."""
    triggers = _triggers(client)
    assert triggers[0]["ambient_sound"] == "caveBig"  # AmbientSoundType
    assert triggers[1]["ambient_sound"] == "Underground_SoundSet"  # AmbientSoundSet
    assert triggers[2]["ambient_sound"] == ""  # neither key


def test_the_ambient_sound_survives_an_edit_that_never_mentions_it(client, underground_mission):
    """It is read under two names and written under neither — the key the file
    already had has to come back unchanged."""
    resp = client.put(
        "/api/map-editor/underground-triggers/1", json={"eye_accommodation": 0.5}
    )
    assert resp.status_code == 200
    entry = _file(underground_mission)["Triggers"][1]
    assert entry["AmbientSoundSet"] == "Underground_SoundSet"
    assert "AmbientSoundType" not in entry


def test_a_breadcrumb_keeps_the_default_radius_as_itself(client):
    """-1 means "use the engine default" and is not the same as 0."""
    crumbs = _triggers(client)[0]["breadcrumbs"]
    assert crumbs[0]["radius"] == -1.0
    # The second breadcrumb omits Radius entirely; the default has to be the
    # sentinel, not zero, or a save would pin it to a real radius of nothing.
    assert crumbs[1]["radius"] == -1.0


def test_a_mission_with_no_file_answers_empty_rather_than_404(bare_client):
    """Chernarus ships an empty stub, so "none here" is the normal answer on
    the map most people run."""
    body = bare_client.get("/api/map-editor/underground-triggers").json()
    assert body["triggers"] == []


# ── writing ───────────────────────────────────────────────────────


def test_moving_a_trigger_keeps_its_height_and_its_unknown_keys(client, underground_mission):
    """The map is flat, so a drag can only say where in X/Z — and the decision
    of 2026 Q3 is that nothing this version does not render may be lost."""
    before = _triggers(client)[0]
    resp = client.put(
        "/api/map-editor/underground-triggers/0",
        json={"lat": before["lat"] + 0.01, "lng": before["lng"] + 0.01},
    )
    assert resp.status_code == 200

    entry = _file(underground_mission)["Triggers"][0]
    assert entry["Position"][1] == 211.88
    assert entry["Position"][0] != 9663.46
    assert entry["ParentNetworkId"] == 12345
    assert entry["AmbientSoundType"] == "caveBig"
    assert entry["UseLinePointFade"] == 1


def test_resizing_and_relighting_a_trigger(client, underground_mission):
    resp = client.put(
        "/api/map-editor/underground-triggers/2",
        json={"size": [60, 9, 110], "orientation": [90, 0, 0], "eye_accommodation": 0.25},
    )
    assert resp.status_code == 200

    entry = _file(underground_mission)["Triggers"][2]
    assert entry["Size"] == [60.0, 9.0, 110.0]
    assert entry["Orientation"] == [90.0, 0.0, 0.0]
    assert entry["EyeAccommodation"] == 0.25
    assert entry["InterpolationSpeed"] == 1


# Two of Bohemia's Livonia triggers, read at the pinned 1.29 commit: tabs,
# CRLF, `{ \t`. The first whole, the second with only its first breadcrumb:
# lines 1-25, the close of its breadcrumb list (74-75) and the last three lines.
def livonia_triggers() -> str:
    lines = ce_lines(bohemia_ce(CE_1_29, "dayzOffline.enoch/cfgundergroundtriggers.json"))
    return "".join(lines[:25] + lines[73:75] + lines[-3:])


@pytest.fixture()
def livonia(underground_mission: Path) -> Path:
    path = underground_mission / "cfgundergroundtriggers.json"
    path.write_bytes(livonia_triggers().encode())
    return path


def test_an_edit_changes_one_line_and_keeps_the_file_s_layout(livonia, client):
    """Bohemia lays the file out by hand, with tabs and CRLF. The first map
    edit used to reflow all of it (plans/46 C); now only the edited value
    changes, and the line endings stay."""
    resp = client.put("/api/map-editor/underground-triggers/1", json={"eye_accommodation": 0.25})
    assert resp.status_code == 200
    assert livonia.read_bytes().decode() == livonia_triggers().replace(
        '"EyeAccommodation": 0,', '"EyeAccommodation": 0.25,'
    )


def test_a_popup_save_with_no_edits_writes_nothing(livonia, client):
    """The popup used to send every field rounded to two decimals and an
    empty comment: Y 533.460144 became 533.46, whole sizes became floats and
    a `"Comment": ""` was added."""
    shown = _triggers(client)[0]
    resp = client.put("/api/map-editor/underground-triggers/0", json={
        "comment": "",
        "eye_accommodation": shown["eye_accommodation"],
        "size": [shown["size_x"], shown["size_y"], shown["size_z"]],
        "orientation": [shown["yaw"], shown["pitch"], shown["roll"]],
        "game_y": shown["game_y"],
    })
    assert resp.status_code == 200
    assert livonia.read_bytes().decode() == livonia_triggers()
    resp = client.put("/api/map-editor/underground-triggers/1/breadcrumbs/0", json={
        "radius": -1, "eye_accommodation": 1, "use_raycast": False,
    })
    assert resp.status_code == 200
    assert livonia.read_bytes().decode() == livonia_triggers()


def test_lighting_an_inner_trigger_all_the_way_up_makes_it_outer(client):
    """The derived type has to follow an edit, since nothing stores it."""
    assert _triggers(client)[2]["type"] == "inner"
    client.put(
        "/api/map-editor/underground-triggers/2", json={"eye_accommodation": 1}
    )
    assert _triggers(client)[2]["type"] == "outer"


def test_moving_a_breadcrumb_keeps_its_height_and_its_flags(client, underground_mission):
    before = _triggers(client)[0]["breadcrumbs"][0]
    resp = client.put(
        "/api/map-editor/underground-triggers/0/breadcrumbs/0",
        json={"lat": before["lat"] + 0.01, "lng": before["lng"]},
    )
    assert resp.status_code == 200

    crumb = _file(underground_mission)["Triggers"][0]["Breadcrumbs"][0]
    assert crumb["Position"][1] == 213.50
    assert crumb["EyeAccommodation"] == 1
    assert crumb["Radius"] == -1


def test_a_breadcrumb_weight_and_raycast_round_trip(client, underground_mission):
    resp = client.put(
        "/api/map-editor/underground-triggers/0/breadcrumbs/1",
        json={"eye_accommodation": 0.4, "use_raycast": True, "radius": 12.5},
    )
    assert resp.status_code == 200

    crumb = _file(underground_mission)["Triggers"][0]["Breadcrumbs"][1]
    assert crumb["EyeAccommodation"] == 0.4
    assert crumb["UseRaycast"] == 1
    assert crumb["Radius"] == 12.5


def test_a_breadcrumb_may_be_set_back_to_the_default_radius(client, underground_mission):
    """plans/51 B refuses a radius <= 0 on every write except this one: -1 is
    "the engine default", and Bohemia's own Enoch and Sakhal files use it."""
    resp = client.put(
        "/api/map-editor/underground-triggers/0/breadcrumbs/1", json={"radius": -1}
    )
    assert resp.status_code == 200
    assert _file(underground_mission)["Triggers"][0]["Breadcrumbs"][1]["Radius"] == -1


def test_a_trigger_size_must_be_positive_numbers(client, underground_mission):
    """An emptied size box arrived as null and `_as_float` wrote 0.0."""
    before = _file(underground_mission)
    for size in ([10, None, 4], [10, 0, 4], [10, "x", 4]):
        resp = client.put("/api/map-editor/underground-triggers/0", json={"size": size})
        assert resp.status_code == 400, size
    assert _file(underground_mission) == before


def test_unknown_indexes_are_404s_and_write_nothing(client, underground_mission):
    before = _file(underground_mission)
    assert client.put(
        "/api/map-editor/underground-triggers/99", json={"eye_accommodation": 1}
    ).status_code == 404
    assert client.put(
        "/api/map-editor/underground-triggers/0/breadcrumbs/99", json={"radius": 1}
    ).status_code == 404
    assert _file(underground_mission) == before


def test_writing_to_a_mission_with_no_file_is_a_404_not_a_crash(bare_client):
    resp = bare_client.put(
        "/api/map-editor/underground-triggers/0", json={"eye_accommodation": 1}
    )
    assert resp.status_code == 404
    assert "cfgundergroundtriggers.json" in resp.json()["detail"]


# ── what the file can hold (underground-areas-configuration.wiki) ──


@pytest.mark.parametrize("body", [
    {"size": [10, 4]},                       # a vector is X, Y, Z (:32)
    {"size": [10, 4, 2, 1]},
    {"orientation": [90, 0]},                # yaw, pitch, roll (:31)
    {"eye_accommodation": 1.5},              # 0 dark .. 1 normal (:13)
    {"eye_accommodation": -0.1},
])
def test_a_trigger_value_the_file_cannot_hold_is_a_400(body, client, underground_mission):
    before = _file(underground_mission)
    resp = client.put("/api/map-editor/underground-triggers/0", json=body)
    assert resp.status_code == 400, body
    assert _file(underground_mission) == before


@pytest.mark.parametrize("body", [
    {"radius": -5},                          # metres, or -1 for the default (:57)
    {"radius": 0},
    {"eye_accommodation": 2},
])
def test_a_breadcrumb_value_the_file_cannot_hold_is_a_400(body, client, underground_mission):
    before = _file(underground_mission)
    resp = client.put("/api/map-editor/underground-triggers/0/breadcrumbs/0", json=body)
    assert resp.status_code == 400, body
    assert _file(underground_mission) == before


@pytest.mark.parametrize("data", [
    {"size": ["nan", 1, 1]},
    {"size": [float("nan"), 1, 1]},
    {"orientation": [float("inf"), 0, 0]},
    {"eye_accommodation": float("nan")},
    {"game_y": float("nan")},
])
async def test_the_state_refuses_nan_and_short_vectors_before_it_changes_anything(
    data, underground_mission: Path
):
    """Called past the route's guard, `_as_float("nan")` used to write
    `"Size": [NaN, 1.0]`, which is not JSON."""
    from app.spawn_editor.underground_state import UndergroundTriggerState

    class _Quiet:
        async def broadcast(self, message):
            pass

    state = UndergroundTriggerState(underground_mission, "chernarusplus", _Quiet())
    state.load()
    before = _file(underground_mission)
    with pytest.raises(ValueError):
        await state.update_trigger(0, data)
    assert _file(underground_mission) == before
    assert state._raw["Triggers"][0]["Size"] == [30, 8, 20]
