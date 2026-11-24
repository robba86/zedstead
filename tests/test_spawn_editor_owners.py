"""The map editor writes a spawn position where it is kept.

A position inside a manual XML file's ``@user:<id>`` block is re-spliced from
the file on every merge, so a move written only into cfgeventspawns.xml went
back at the next "Re-configure all mod XML". It is now written into the manual
file and the file is applied again. A mod's position is refused unless the
request asks for the copy into a manual file (``copy=``), and a write names
the entry that was dragged (owner + ordinal), not the first of its name.
"""
from __future__ import annotations

import textwrap

import pytest
from fastapi.testclient import TestClient

PASSWORD = "testpass123"
MISSION = "dayzOffline.chernarusplus"

MANUAL = textwrap.dedent("""\
    <?xml version="1.0" encoding="UTF-8"?>
    <eventposdef>
    \t<event name="StaticBikes">
    \t\t<pos x="1000.000000" z="2000.000000" a="0"/>
    \t\t<pos x="1100.000000" z="2100.000000" a="0"/>
    \t</event>
    </eventposdef>
""")


def _client(config) -> TestClient:
    from app.web import server
    from app.web.auth import hash_password

    config.web_password_hash = hash_password(PASSWORD)
    client = TestClient(server.create_app(config))
    assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
    return client


def _write_mission(mission_dir, body: str) -> None:
    (mission_dir / "db").mkdir(exist_ok=True)
    (mission_dir / "db" / "events.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<events>\n</events>\n', encoding="utf-8",
    )
    (mission_dir / "cfgeventspawns.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<eventposdef>\n' + body + "</eventposdef>\n",
        encoding="utf-8",
    )


@pytest.fixture()
def with_manual(sample_config, tmp_mission_dir):
    """A mission whose StaticBikes positions come from the manual file `bikes`,
    beside the mission's own StaticBikes entry and a mod's block."""
    from app.xml.xml_manual import apply_direct_files, create_file

    _write_mission(tmp_mission_dir, textwrap.dedent("""\
        \t<event name="StaticBikes">
        \t\t<pos x="10.000000" z="20.000000" a="0"/>
        \t</event>
        \t<!-- START MOD: @Bicycles -->
        \t<event name="StaticBikes">
        \t\t<pos x="500.000000" z="600.000000" a="0"/>
        \t</event>
        \t<!-- END MOD: @Bicycles -->
    """))
    manual_dir = sample_config.paths.custom_xml_dir
    create_file("bikes", "cfgeventspawns", MANUAL, manual_dir=manual_dir, terrain="chernarusplus")
    assert apply_direct_files(
        sample_config.server_dir_path, MISSION, manual_dir=manual_dir, only_file="bikes",
    ) == []
    return sample_config


def _event(client, owner):
    events = client.get("/api/map-editor/spawns").json()["events"]
    return next(e for e in events if e["name"] == "StaticBikes" and e["owner"] == owner)


def test_spawns_say_who_owns_each_event_and_position(with_manual):
    client = _client(with_manual)
    events = [e for e in client.get("/api/map-editor/spawns").json()["events"]
              if e["name"] == "StaticBikes"]
    assert sorted((e["owner"] or "", e["entry"]) for e in events) == [
        ("", 0), ("@Bicycles", 0), ("@user:bikes", 0),
    ]
    for e in events:
        assert {p["owner"] for p in e["positions"]} == {e["owner"]}


def test_a_moved_manual_position_is_written_into_the_file_and_survives_a_merge(with_manual):
    from app.xml.xml_manual import file_content_path
    from app.xml.xml_merge import merge_all_direct_configs

    config = with_manual
    client = _client(config)
    resp = client.put(
        "/api/map-editor/spawns/StaticBikes/1/game?owner=%40user%3Abikes&entry=0",
        json={"x": 1234.5, "z": 2345.5},
    )
    assert resp.status_code == 200, resp.text

    path = file_content_path("bikes", manual_dir=config.paths.custom_xml_dir)
    written = path.read_bytes()
    assert b'x="1234.500000" z="2345.500000"' in written
    # Only the moved line changed in the admin's file.
    assert written.decode().replace(
        'x="1234.500000" z="2345.500000"', 'x="1100.000000" z="2100.000000"'
    ) == MANUAL

    failed = merge_all_direct_configs(
        config.server_dir_path, MISSION, manual_dir=config.paths.custom_xml_dir,
    )
    assert "bikes" not in failed
    assert path.read_bytes() == written
    moved = _event(client, "@user:bikes")["positions"][1]
    assert (moved["game_x"], moved["game_z"]) == (1234.5, 2345.5)
    # The mission's own entry and the mod's were not touched.
    assert _event(client, None)["positions"][0]["game_x"] == 10.0


def test_adding_and_deleting_in_a_manual_block_go_through_the_file(with_manual):
    from app.xml.xml_manual import file_content_path

    config = with_manual
    client = _client(config)
    path = file_content_path("bikes", manual_dir=config.paths.custom_xml_dir)

    assert client.delete(
        "/api/map-editor/spawns/StaticBikes/0?owner=%40user%3Abikes&entry=0"
    ).status_code == 200
    assert b'x="1000.000000"' not in path.read_bytes()
    assert len(_event(client, "@user:bikes")["positions"]) == 1

    pos = _event(client, "@user:bikes")["positions"][0]
    resp = client.post(
        "/api/map-editor/spawns/StaticBikes?owner=%40user%3Abikes&entry=0",
        json={"lat": pos["lat"], "lng": pos["lng"]},
    )
    assert resp.status_code == 200, resp.text
    assert path.read_text().count("<pos ") == 2
    assert len(_event(client, "@user:bikes")["positions"]) == 2


def test_a_mods_position_is_refused_and_left_alone(with_manual, tmp_mission_dir):
    client = _client(with_manual)
    before = (tmp_mission_dir / "cfgeventspawns.xml").read_bytes()
    resp = client.put(
        "/api/map-editor/spawns/StaticBikes/0/game?owner=%40Bicycles&entry=0",
        json={"x": 1.0, "z": 2.0},
    )
    assert resp.status_code == 409
    assert "@Bicycles" in resp.json()["detail"]
    assert "Custom config" in resp.json()["detail"]
    assert (tmp_mission_dir / "cfgeventspawns.xml").read_bytes() == before


def test_the_missions_own_position_saves_in_the_mission(with_manual, tmp_mission_dir):
    from app.xml.xml_manual import file_content_path

    config = with_manual
    client = _client(config)
    manual = file_content_path("bikes", manual_dir=config.paths.custom_xml_dir).read_bytes()
    resp = client.put(
        "/api/map-editor/spawns/StaticBikes/0/game?owner=&entry=0",
        json={"x": 11.5, "z": 22.5},
    )
    assert resp.status_code == 200, resp.text
    text = (tmp_mission_dir / "cfgeventspawns.xml").read_text()
    assert 'x="11.500000" z="22.500000"' in text
    assert file_content_path("bikes", manual_dir=config.paths.custom_xml_dir).read_bytes() == manual


def test_a_write_hits_the_entry_that_was_dragged_not_the_first_of_its_name(
    sample_config, tmp_mission_dir,
):
    _write_mission(tmp_mission_dir, textwrap.dedent("""\
        \t<event name="Dup">
        \t\t<pos x="1.000000" z="1.000000" a="0"/>
        \t</event>
        \t<event name="Dup">
        \t\t<pos x="2.000000" z="2.000000" a="0"/>
        \t</event>
    """))
    client = _client(sample_config)
    resp = client.put(
        "/api/map-editor/spawns/Dup/0/game?owner=&entry=1", json={"x": 9.5, "z": 9.5},
    )
    assert resp.status_code == 200, resp.text
    text = (tmp_mission_dir / "cfgeventspawns.xml").read_text()
    assert 'x="1.000000" z="1.000000"' in text
    assert 'x="9.500000" z="9.500000"' in text
    assert 'x="2.000000"' not in text


def test_a_manual_file_changed_since_it_was_applied_is_refused(with_manual):
    from app.xml.xml_manual import file_content_path

    config = with_manual
    path = file_content_path("bikes", manual_dir=config.paths.custom_xml_dir)
    path.write_text(MANUAL.replace("1000.000000", "1001.000000"), encoding="utf-8")
    client = _client(config)
    resp = client.put(
        "/api/map-editor/spawns/StaticBikes/1/game?owner=%40user%3Abikes&entry=0",
        json={"x": 5.5, "z": 5.5},
    )
    assert resp.status_code == 409
    assert "no longer matches" in resp.json()["detail"]


def test_drags_in_a_manual_block_keep_one_version_per_file_per_session(
    with_manual, tmp_mission_dir,
):
    from app.shared import config_versions
    from app.spawn_editor import versioning
    from app.xml.xml_manual import file_content_path

    def versions(path):
        return config_versions.load_index(config_versions.store_for(path)).get_versions(
            path.name, source_path=str(path)
        )

    config = with_manual
    mission_file = tmp_mission_dir / "cfgeventspawns.xml"
    manual = file_content_path("bikes", manual_dir=config.paths.custom_xml_dir)
    client = _client(config)
    versioning.new_session()
    before = len(versions(mission_file))
    for x in (1200.5, 1300.5, 1400.5):
        assert client.put(
            "/api/map-editor/spawns/StaticBikes/1/game?owner=%40user%3Abikes&entry=0",
            json={"x": x, "z": 2100.0},
        ).status_code == 200
    assert len(versions(mission_file)) == before + 1
    assert len(versions(manual)) == 1


# ---------------------------------------------------------------------------
# "Copy into a manual file and move": a mod's position
# ---------------------------------------------------------------------------

MOD_FILE = textwrap.dedent("""\
    <?xml version="1.0" encoding="UTF-8"?>
    <eventposdef>
    \t<event name="StaticBikes">
    \t\t<pos x="500.000000" z="600.000000" a="0"/>
    \t\t<pos x="510.000000" z="610.000000" a="0"/>
    \t</event>
    </eventposdef>
""")

OTHER_MOD_FILE = textwrap.dedent("""\
    <?xml version="1.0" encoding="UTF-8"?>
    <eventposdef>
    \t<event name="StaticTrikes">
    \t\t<pos x="700.000000" z="800.000000" a="0"/>
    \t</event>
    </eventposdef>
""")

MOD_OWNER = "owner=%40Bicycles&entry=0"


def _install_mod(config, mission_dir, files: dict[str, str]) -> None:
    """Put @Bicycles' files on disk and merge them into the mission, the way an
    install does, beside the mission's own StaticBikes entry."""
    from app.xml.xml_merge import merge_all_direct_configs

    _write_mission(mission_dir, textwrap.dedent("""\
        \t<event name="StaticBikes">
        \t\t<pos x="10.000000" z="20.000000" a="0"/>
        \t</event>
    """))
    for rel, text in files.items():
        path = config.server_dir_path / "@Bicycles" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    assert "cfgeventspawns" not in merge_all_direct_configs(
        config.server_dir_path, MISSION, manual_dir=config.paths.custom_xml_dir,
    )


def test_the_copy_takes_the_file_the_merge_record_names(sample_config, tmp_mission_dir):
    # Livonia's variant is the mod's file too, but not the one this mission got.
    _install_mod(sample_config, tmp_mission_dir, {
        "cfgeventspawns.xml": MOD_FILE,
        "livonia/cfgeventspawns_Livonia.xml": OTHER_MOD_FILE,
    })
    client = _client(sample_config)
    found = client.get("/api/map-editor/spawns-copy-source?mod=%40Bicycles").json()
    assert found == {
        "source": "@Bicycles/cfgeventspawns.xml", "sources": ["@Bicycles/cfgeventspawns.xml"],
    }


def test_with_no_one_file_on_record_the_page_gets_the_list(sample_config, tmp_mission_dir):
    from app.xml.xml_manual import load_files

    _install_mod(sample_config, tmp_mission_dir, {
        "cfgeventspawns.xml": MOD_FILE,
        "extra/cfgeventspawns.xml": OTHER_MOD_FILE,
    })
    client = _client(sample_config)
    found = client.get("/api/map-editor/spawns-copy-source?mod=%40Bicycles").json()
    assert found["source"] is None
    assert sorted(found["sources"]) == [
        "@Bicycles/cfgeventspawns.xml", "@Bicycles/extra/cfgeventspawns.xml",
    ]

    before = (tmp_mission_dir / "cfgeventspawns.xml").read_bytes()
    resp = client.put(
        f"/api/map-editor/spawns/StaticBikes/0/game?{MOD_OWNER}&copy=",
        json={"x": 1.5, "z": 2.5},
    )
    assert resp.status_code == 409
    assert "Choose the file" in resp.json()["detail"]
    # The wrong file is refused before anything is copied.
    resp = client.put(
        f"/api/map-editor/spawns/StaticBikes/0/game?{MOD_OWNER}"
        "&copy=%40Bicycles%2Fextra%2Fcfgeventspawns.xml",
        json={"x": 1.5, "z": 2.5},
    )
    assert resp.status_code == 409
    assert "does not hold StaticBikes" in resp.json()["detail"]
    assert (tmp_mission_dir / "cfgeventspawns.xml").read_bytes() == before
    assert load_files(manual_dir=sample_config.paths.custom_xml_dir) == []

    # The file the admin picks is copied, and the move goes into the copy.
    resp = client.put(
        f"/api/map-editor/spawns/StaticBikes/0/game?{MOD_OWNER}"
        "&copy=%40Bicycles%2Fcfgeventspawns.xml",
        json={"x": 1.5, "z": 2.5},
    )
    assert resp.status_code == 200, resp.text
    text = (tmp_mission_dir / "cfgeventspawns.xml").read_text()
    assert 'x="1.500000" z="2.500000"' in text
    # The other file stays the mod's.
    assert "START MOD: @Bicycles" in text and 'x="700.000000"' in text


def test_copy_and_move_survives_a_reconfigure_byte_for_byte(sample_config, tmp_mission_dir):
    from app.xml.xml_manual import file_content_path, load_files, load_source_exclusions
    from app.xml.xml_merge import merge_all_direct_configs

    config = sample_config
    _install_mod(config, tmp_mission_dir, {"cfgeventspawns.xml": MOD_FILE})
    client = _client(config)
    resp = client.put(
        f"/api/map-editor/spawns/StaticBikes/1/game?{MOD_OWNER}&copy=",
        json={"x": 1234.5, "z": 2345.5},
    )
    assert resp.status_code == 200, resp.text
    assert (resp.json()["game_x"], resp.json()["game_z"]) == (1234.5, 2345.5)

    manual_dir = config.paths.custom_xml_dir
    [link] = load_files(manual_dir=manual_dir)
    assert link["forked_from"] == "@Bicycles/cfgeventspawns.xml"
    assert "@Bicycles/cfgeventspawns.xml" in load_source_exclusions(manual_dir=manual_dir)
    copy = file_content_path(link["id"], manual_dir=manual_dir).read_text()
    assert 'x="1234.500000" z="2345.500000"' in copy and 'x="500.000000"' in copy

    mission_file = tmp_mission_dir / "cfgeventspawns.xml"
    after = mission_file.read_bytes()
    assert b"START MOD: @Bicycles" not in after
    assert f"START MOD: @user:{link['id']}".encode() in after
    assert after.count(b'x="1234.500000" z="2345.500000"') == 1
    assert b'x="510.000000"' not in after

    assert "cfgeventspawns" not in merge_all_direct_configs(
        config.server_dir_path, MISSION, manual_dir=manual_dir,
    )
    assert mission_file.read_bytes() == after
    owners = {e["owner"] for e in client.get("/api/map-editor/spawns").json()["events"]}
    assert owners == {None, f"@user:{link['id']}"}
    assert _event(client, None)["positions"][0]["game_x"] == 10.0


def test_a_mods_position_without_the_copy_flag_is_still_a_409(sample_config, tmp_mission_dir):
    from app.xml.xml_manual import load_files

    _install_mod(sample_config, tmp_mission_dir, {"cfgeventspawns.xml": MOD_FILE})
    client = _client(sample_config)
    before = (tmp_mission_dir / "cfgeventspawns.xml").read_bytes()
    for method, url, body in (
        ("put", f"/api/map-editor/spawns/StaticBikes/0/game?{MOD_OWNER}", {"x": 1.0, "z": 2.0}),
        ("delete", f"/api/map-editor/spawns/StaticBikes/0?{MOD_OWNER}", None),
    ):
        resp = client.request(method, url, json=body)
        assert resp.status_code == 409, resp.text
        assert "@Bicycles" in resp.json()["detail"]
    assert (tmp_mission_dir / "cfgeventspawns.xml").read_bytes() == before
    assert load_files(manual_dir=sample_config.paths.custom_xml_dir) == []
