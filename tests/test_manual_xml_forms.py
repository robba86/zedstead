"""Manual XML files in the config editors (plans/92).

A manual file's rows in a mission file's table are locked and name the file,
since every merge puts that file back and drops an edit made there.
"""

from __future__ import annotations

import pytest

from app.xml.xml_manual import manual_owner_id, owner_map
from app.xml.xml_utils import load_xml
from tests.test_web_configs import _mission_root, client  # noqa: F401 - fixture

EVENTSPAWNS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<eventposdef>
    <event name="StaticHeliCrash">
        <pos x="1000.000000" z="2000.000000" a="-1.000000"/>
    </event>
    <!-- START MOD: @user:bikes -->
    <event name="BikeSpawn">
        <pos x="500.000000" z="600.000000" a="0.000000"/>
    </event>
    <!-- END MOD: @user:bikes -->
    <!-- START MOD: @Cars -->
    <event name="CarSpawn">
        <pos x="700.000000" z="800.000000" a="0.000000"/>
    </event>
    <!-- END MOD: @Cars -->
</eventposdef>
"""


def test_owner_map_inherits_a_block_owner_down_the_tree(tmp_path):
    path = tmp_path / "cfgeventspawns.xml"
    path.write_text(EVENTSPAWNS_XML)
    root = load_xml(path, preserve_comments=True).getroot()

    owners = owner_map(root)
    by_name = {e.get("name"): e for e in root.iter("event")}
    assert by_name["StaticHeliCrash"] not in owners
    assert owners[by_name["BikeSpawn"]] == "@user:bikes"
    assert owners[by_name["BikeSpawn"][0]] == "@user:bikes"
    assert owners[by_name["CarSpawn"]] == "@Cars"


def test_manual_owner_id_reads_only_a_manual_marker():
    assert manual_owner_id("@user:bikes") == "bikes"
    assert manual_owner_id("@Cars") is None
    assert manual_owner_id(None) is None


@pytest.fixture()
def spawns(client, sample_config):  # noqa: F811 - fixture
    path = _mission_root(sample_config) / "cfgeventspawns.xml"
    path.write_text(EVENTSPAWNS_XML)
    return path


def test_a_manual_files_rows_are_locked_and_name_the_file(client, spawns):  # noqa: F811
    body = client.get("/api/configs/eventspawns").json()
    rows = {r["values"]["event"]: r for r in body["rows"]}

    assert rows["BikeSpawn"]["readonly"] is True
    assert rows["BikeSpawn"]["manual"] == "bikes"
    assert rows["BikeSpawn"]["owner"] == "@user:bikes"
    # A mod's row is locked too, and names its mod for the Take over link.
    assert rows["CarSpawn"]["owner"] == "@Cars"
    assert rows["CarSpawn"]["readonly"] is True and rows["CarSpawn"]["mod"] == "@Cars"
    assert "owner" not in rows["StaticHeliCrash"] and "readonly" not in rows["StaticHeliCrash"]
    assert "manual XML file" in body["readonly_reason"]


def test_a_save_that_keeps_the_locked_rows_goes_through(client, spawns):  # noqa: F811
    rows = client.get("/api/configs/eventspawns").json()["rows"]
    rows[0]["values"]["x"] = "1234.5"

    assert client.put("/api/configs/eventspawns", json={"rows": rows}).status_code == 200
    assert 'x="1234.500000"' in spawns.read_text()


def test_a_changed_or_removed_manual_row_is_refused(client, spawns):  # noqa: F811
    before = spawns.read_text()
    rows = client.get("/api/configs/eventspawns").json()["rows"]
    bike = next(r for r in rows if r["values"]["event"] == "BikeSpawn")

    bike["values"]["x"] = "1.0"
    changed = client.put("/api/configs/eventspawns", json={"rows": rows})
    removed = client.put(
        "/api/configs/eventspawns", json={"rows": [r for r in rows if r is not bike]}
    )

    assert changed.status_code == 400 and "bikes" in changed.text
    assert removed.status_code == 400
    assert spawns.read_text() == before


def test_a_whole_number_sent_back_as_an_int_is_not_a_change(client, spawns):  # noqa: F811
    """JavaScript sends a loaded 11506.0 back as 11506, and the text compare
    refused every save of Event Spawns (browser test 2026-11-17, F1)."""
    rows = client.get("/api/configs/eventspawns").json()["rows"]
    for row in rows:
        row["values"] = {
            k: int(v) if isinstance(v, float) and v.is_integer() else v for k, v in row["values"].items()
        }

    assert client.put("/api/configs/eventspawns", json={"rows": rows}).status_code == 200


def test_a_number_that_really_changed_is_still_refused(client, spawns):  # noqa: F811
    before = spawns.read_text()
    rows = client.get("/api/configs/eventspawns").json()["rows"]
    next(r for r in rows if r["values"]["event"] == "BikeSpawn")["values"]["x"] = 500.5

    resp = client.put("/api/configs/eventspawns", json={"rows": rows})
    assert resp.status_code == 400 and "bikes" in resp.text
    assert spawns.read_text() == before


def test_a_changed_mod_row_is_refused(client, spawns):  # noqa: F811
    rows = client.get("/api/configs/eventspawns").json()["rows"]
    next(r for r in rows if r["values"]["event"] == "CarSpawn")["values"]["x"] = "1.0"

    response = client.put("/api/configs/eventspawns", json={"rows": rows})

    assert response.status_code == 400 and "@Cars" in response.text


def test_a_new_row_in_an_event_only_a_manual_file_holds_is_refused(client, spawns):  # noqa: F811
    rows = client.get("/api/configs/eventspawns").json()["rows"]
    rows.append({"id": "new-1", "values": {"event": "BikeSpawn", "x": 7, "z": 8, "a": 0}})

    response = client.put("/api/configs/eventspawns", json={"rows": rows})

    assert response.status_code == 400 and "bikes" in response.text


# --- The Form tab: a manual file in its kind's own editor --------------------

MANUAL_SPAWNS = """<?xml version="1.0" encoding="UTF-8"?>
<eventposdef>
    <event name="BikeSpawn">
        <pos x="500.000000" z="600.000000" a="0.000000"/>
    </event>
</eventposdef>
"""


def _manual(client, sample_config, file_id, kind, content):  # noqa: F811
    _mission_root(sample_config)
    response = client.post(
        "/api/modxml/files", json={"id": file_id, "kind": kind, "content": content}
    )
    assert response.status_code == 200, response.text
    return sample_config.paths.custom_xml_dir / f"{file_id}.xml"


def test_the_form_loads_and_saves_the_manual_files_own_xml(client, sample_config):  # noqa: F811
    mission_spawns = _mission_root(sample_config) / "cfgeventspawns.xml"
    mission_spawns.write_text(EVENTSPAWNS_XML)
    before = mission_spawns.read_text()
    path = _manual(client, sample_config, "bikes2", "cfgeventspawns", MANUAL_SPAWNS)

    body = client.get("/api/modxml/files/bikes2/form").json()
    assert body["editor"] == "eventspawns"
    assert [r["values"]["x"] for r in body["rows"]] == [500.0]

    rows = body["rows"]
    rows[0]["values"]["x"] = "555.5"
    rows.append({"id": "new-1", "values": {"event": "BikeSpawn", "x": 7, "z": 8, "a": 0}})
    assert client.put("/api/modxml/files/bikes2/form", json={"rows": rows}).status_code == 200

    saved = path.read_text()
    assert 'x="555.500000"' in saved and 'x="7.000000"' in saved
    # The mission file is the apply job's to change, never the form's.
    assert mission_spawns.read_text() == before


def test_a_free_form_merge_has_no_form(client, sample_config):  # noqa: F811
    _mission_root(sample_config)
    response = client.post(
        "/api/modxml/files",
        json={"id": "other", "kind": "merge", "target": "cfgweather.xml", "content": "<weather/>"},
    )
    assert response.status_code == 200, response.text
    assert client.get("/api/modxml/files/other/form").status_code == 422


def test_the_form_of_an_unknown_file_is_404(client):  # noqa: F811
    assert client.get("/api/modxml/files/nope/form").status_code == 404


# --- BUG-001: a mod block that repeats a mission event -----------------------

REPEATED_EVENT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<eventposdef>
    <event name="VehicleBike">
        <pos x="100.000000" z="100.000000" a="0.000000"/>
        <pos x="200.000000" z="200.000000" a="0.000000"/>
    </event>
    <!-- START MOD: @Bikes -->
    <event name="VehicleBike">
        <pos x="900.000000" z="900.000000" a="0.000000"/>
    </event>
    <!-- END MOD: @Bikes -->
</eventposdef>
"""


def test_a_repeated_event_keeps_each_blocks_rows(client, sample_config):  # noqa: F811
    path = _mission_root(sample_config) / "cfgeventspawns.xml"
    path.write_text(REPEATED_EVENT_XML)
    rows = client.get("/api/configs/eventspawns").json()["rows"]
    assert len({r["id"] for r in rows}) == 3

    assert client.put("/api/configs/eventspawns", json={"rows": rows}).status_code == 200
    assert path.read_text() == REPEATED_EVENT_XML

    rows[1]["values"]["x"] = "250"
    assert client.put("/api/configs/eventspawns", json={"rows": rows}).status_code == 200
    saved = path.read_text()
    assert 'x="250.000000"' in saved and 'x="900.000000"' in saved


# --- BUG-021, BUG-006, BUG-038, BUG-012: manual files around the merge --------


def test_a_manual_ce_file_registers_after_every_mods_file():
    from app.xml.xml_economy import ce_entry_order

    entries = [("vpp-types.xml", "types"), ("user-my-guns.xml", "types"), ("ai-types.xml", "types"),
               ("ai-events.xml", "events")]
    assert [e[0] for e in sorted(entries, key=ce_entry_order)] == [
        "ai-types.xml", "vpp-types.xml", "user-my-guns.xml", "ai-events.xml",
    ]


def test_a_manual_file_id_with_a_double_dash_is_refused():
    from app.xml.xml_manual import is_safe_file_id

    assert is_safe_file_id("heli-north")
    assert not is_safe_file_id("heli--north")


def test_a_manual_file_in_another_encoding_keeps_the_list_loading(client, sample_config):  # noqa: F811
    path = _manual(client, sample_config, "latin", "cfgeventspawns", MANUAL_SPAWNS)
    path.write_bytes(MANUAL_SPAWNS.replace("BikeSpawn", "Caf\xe9").encode("latin-1"))

    assert client.get("/api/modxml/files").status_code == 200


def test_a_manual_file_write_is_refused_while_a_job_runs(client, sample_config):  # noqa: F811
    from app.web.jobs import Job

    _mission_root(sample_config)
    jobs = client.app.state.ctx.jobs
    jobs._active_exclusive = Job(id="busyjob", name="n", kind="k", hub=jobs.hub)
    try:
        response = client.post(
            "/api/modxml/files", json={"id": "bikes3", "kind": "cfgeventspawns", "content": MANUAL_SPAWNS}
        )
    finally:
        jobs._active_exclusive = None
    assert response.status_code == 409
    assert not (sample_config.paths.custom_xml_dir / "bikes3.xml").exists()


# --- Step 3: the Types editor pinned to a manual types file -------------------

MANUAL_TYPES = """<?xml version="1.0" encoding="UTF-8"?>
<types>
    <type name="MyGun">
        <nominal>4</nominal>
        <lifetime>3600</lifetime>
        <restock>0</restock>
        <min>2</min>
        <quantmin>-1</quantmin>
        <quantmax>-1</quantmax>
        <cost>100</cost>
        <category name="weapons"/>
    </type>
</types>
"""


@pytest.fixture()
def mission_types(client, sample_config):  # noqa: F811 - fixture
    db = _mission_root(sample_config) / "db"
    db.mkdir(exist_ok=True)
    path = db / "types.xml"
    path.write_text(MANUAL_TYPES.replace("MyGun", "AK74"))
    return path


def test_the_types_editor_lists_and_writes_only_the_manual_file(client, sample_config, mission_types):  # noqa: F811
    before = mission_types.read_text()
    path = _manual(client, sample_config, "guns", "types", MANUAL_TYPES)

    assert "types" in client.get("/api/modxml/files").json()["form_kinds"]
    files = client.get("/api/types/files", params={"manual": "guns"}).json()["files"]
    assert files == [str(path)]
    entries = client.get("/api/types", params={"manual": "guns", "file": files[0]}).json()["entries"]
    assert [e["name"] for e in entries] == ["MyGun"]

    response = client.put(
        "/api/types/entry", params={"manual": "guns"},
        json={"source_file": files[0], "name": "MyGun", "nominal": 9},
    )
    assert response.status_code == 200, response.text
    assert "<nominal>9</nominal>" in path.read_text()
    assert mission_types.read_text() == before


def test_the_types_editor_refuses_a_manual_file_of_another_kind(client, sample_config):  # noqa: F811
    _manual(client, sample_config, "bikes4", "cfgeventspawns", MANUAL_SPAWNS)

    assert client.get("/api/types/files", params={"manual": "bikes4"}).status_code == 422
    assert client.get("/api/types/files", params={"manual": "nope"}).status_code == 404
