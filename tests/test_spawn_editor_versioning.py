"""One version snapshot per file per map-editor session (plans/54 C, F74).

A drag saves at once and the editor has no undo, so the snapshot is what lets
an accidental drag be put back. It has to hold the content from *before* the
session's first write, and later drags must not push it out of the 50 kept.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.shared import config_versions
from app.spawn_editor import versioning

PASSWORD = "testpass123"

TRIGGERS = {
    "Triggers": [
        {
            "Position": [9663.46, 211.88, 8466.49],
            "Orientation": [45, 0, 0],
            "Size": [30, 8, 20],
            "EyeAccommodation": 1,
            "Breadcrumbs": [],
        },
    ]
}


@pytest.fixture(autouse=True)
def _fresh_session():
    versioning.new_session()
    yield
    versioning.new_session()


def _versions(path: Path) -> list[config_versions.VersionEntry]:
    return config_versions.load_index(config_versions.store_for(path)).get_versions(
        path.name, source_path=str(path)
    )


def _saved_content(entry: config_versions.VersionEntry) -> str:
    store = config_versions.store_for(Path(entry.source_path))
    return (store / config_versions._version_filename(entry)).read_text()


def test_two_writes_in_one_session_keep_one_version_of_the_original(tmp_path):
    path = tmp_path / "zone.json"
    path.write_text("original\n")

    versioning.write_text(path, "first drag\n")
    versioning.write_text(path, "second drag\n")

    [entry] = _versions(path)
    assert entry.trigger == "map_editor"
    assert _saved_content(entry) == "original\n"
    assert path.read_text() == "second drag\n"


def test_a_new_session_takes_one_more(tmp_path):
    path = tmp_path / "zone.json"
    path.write_text("original\n")
    versioning.write_text(path, "session one\n")

    versioning.new_session()
    versioning.write_text(path, "session two\n")
    versioning.write_text(path, "session two again\n")

    assert [_saved_content(e) for e in reversed(_versions(path))] == ["original\n", "session one\n"]


def test_each_file_gets_its_own(tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text("a\n")
    b.write_text("b\n")

    for _ in range(2):
        versioning.write_text(a, "a edited\n")
        versioning.write_text(b, "b edited\n")

    assert [_saved_content(e) for e in _versions(a)] == ["a\n"]
    assert [_saved_content(e) for e in _versions(b)] == ["b\n"]


def test_a_file_the_session_created_is_not_snapshotted_later(tmp_path):
    """The first add creates the file; there was nothing before it to keep,
    and a copy of this session's own content is not a pre-session state."""
    path = tmp_path / "new.json"

    versioning.write_text(path, "created\n")
    versioning.write_text(path, "edited\n")

    assert _versions(path) == []


def test_a_failing_snapshot_does_not_block_the_save(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise OSError("versions dir is read-only")

    monkeypatch.setattr(config_versions, "snapshot", boom)
    path = tmp_path / "zone.json"
    path.write_text("original\n")

    versioning.write_text(path, "moved\n")

    assert path.read_text() == "moved\n"


# ── through the editor ────────────────────────────────────────────


@pytest.fixture()
def mission(tmp_mission_dir: Path) -> Path:
    (tmp_mission_dir / "cfgundergroundtriggers.json").write_text(
        json.dumps(TRIGGERS, indent=4), encoding="utf-8"
    )
    return tmp_mission_dir


@pytest.fixture()
def client(sample_config, mission):
    from app.web import server
    from app.web.auth import hash_password

    sample_config.web_password_hash = hash_password(PASSWORD)
    tclient = TestClient(server.create_app(sample_config))
    assert tclient.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
    return tclient


def _drag(client, dlat: float) -> None:
    [trigger] = client.get("/api/map-editor/underground-triggers").json()["triggers"]
    resp = client.put(
        "/api/map-editor/underground-triggers/0",
        json={"lat": trigger["lat"] + dlat, "lng": trigger["lng"]},
    )
    assert resp.status_code == 200, resp.text


def test_drags_in_the_editor_keep_the_pre_session_file(client, mission):
    """F74: dragging a zone left no version of the file it rewrote."""
    path = mission / "cfgundergroundtriggers.json"
    original = path.read_text()

    _drag(client, 0.01)
    _drag(client, 0.01)
    _drag(client, 0.01)

    [entry] = _versions(path)
    assert entry.trigger == "map_editor"
    assert _saved_content(entry) == original
    assert path.read_text() != original


def test_a_reload_starts_a_new_session(client, mission):
    path = mission / "cfgundergroundtriggers.json"
    _drag(client, 0.01)
    after_first_session = path.read_text()

    assert client.post("/api/map-editor/reload").status_code == 200
    _drag(client, 0.01)
    _drag(client, 0.01)

    assert _saved_content(_versions(path)[0]) == after_first_session
    assert len(_versions(path)) == 2


# ── the XML layers (once per session too) ─────────────────────────

EVENTSPAWNS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<eventposdef>
\t<event name="VehicleTest">
\t\t<pos x="1500.123456" z="850.336915" a="45.000000" />
\t</event>
</eventposdef>
"""


def test_spawn_drags_keep_one_version_of_the_xml(client, mission):
    """save_xml snapshotted every drag, so 50 drags in one sitting pushed out
    the copy from before the session."""
    path = mission / "cfgeventspawns.xml"
    path.write_text(EVENTSPAWNS, encoding="utf-8")
    assert client.post("/api/map-editor/reload").status_code == 200

    for z in (900.0, 950.0, 1000.0):
        resp = client.put("/api/map-editor/spawns/VehicleTest/0/game", json={"x": 1600.0, "z": z, "angle": 45.0})
        assert resp.status_code == 200, resp.text

    [entry] = _versions(path)
    assert entry.trigger == "map_editor"
    assert _saved_content(entry) == EVENTSPAWNS


def test_other_xml_saves_still_snapshot_every_write(tmp_path):
    import xml.etree.ElementTree as ET

    from app.xml.xml_utils import save_xml

    path = tmp_path / "types.xml"
    path.write_text("<types />\n", encoding="utf-8")
    for n in range(2):
        tree = ET.ElementTree(ET.fromstring(f'<types><type name="T{n}" /></types>'))
        save_xml(tree, path)

    assert [e.trigger for e in _versions(path)] == ["auto_save", "auto_save"]
