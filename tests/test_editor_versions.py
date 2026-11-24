"""Every config editor save keeps the file it replaced as a version, and the
editor shows those versions and a list editor's removed names itself
(routers/configs.py: PUT /api/configs/{id}, GET /api/configs/{id}/versions,
GET /api/configs/{id}/removed; config_versions.keep_replaced, removed_names).

User test, 2026 Q4: an entry removed in the Events editor "just disappeared,
and there is no backup", because the editor's Restore button only knew the
merge backup and the versions sat on the Analysis page.
"""

from __future__ import annotations

import pytest

from app.shared.config_versions import load_index, version_path
from app.web import editors
from tests.test_web_configs import EVENTS_XML, IGNORELIST_XML, _mission_db, client  # noqa: F401


def _versions(sample_config, path):
    return load_index(sample_config.paths.versions_dir).get_versions(path.name, source_path=str(path))


def test_removing_an_event_keeps_the_old_file_as_one_version(client, sample_config):
    path = _mission_db(sample_config) / "events.xml"
    path.write_text(EVENTS_XML, encoding="utf-8")

    assert client.put("/api/configs/events", json={"rows": []}).status_code == 200

    assert "StaticWreck" not in path.read_text()
    versions = _versions(sample_config, path)
    # The editor's own snapshot already held it: the route adds no second copy.
    assert len(versions) == 1
    assert version_path(versions[0], sample_config.paths.versions_dir).read_text() == EVENTS_XML


@pytest.fixture()
def careless_editor(sample_config):
    """An editor whose save writes its file and takes no snapshot."""
    from app.web.editors._raw import RawTarget

    path = sample_config.server_dir_path / "careless.txt"
    path.write_text("before\n", encoding="utf-8")

    def _save(config, payload):
        if payload == "refuse":
            raise ValueError("refused")
        path.write_text(payload, encoding="utf-8")

    spec = editors.EditorSpec(
        id="careless-test",
        title="Careless",
        group="Test",
        kind="text",
        exists=lambda config: True,
        load=lambda config: {"kind": "text", "content": path.read_text()},
        save=_save,
        raw_targets=lambda config: [RawTarget(name=path.name, path=path, language="txt")],
    )
    editors.register(spec)
    yield path
    editors._REGISTRY.pop(spec.id, None)


def test_a_save_without_its_own_snapshot_still_leaves_a_version(client, sample_config, careless_editor):
    assert client.put("/api/configs/careless-test", json={"content": "after\n"}).status_code == 200

    versions = _versions(sample_config, careless_editor)
    assert [v.trigger for v in versions] == ["editor"]
    assert version_path(versions[0], sample_config.paths.versions_dir).read_text() == "before\n"
    assert careless_editor.read_text() == "after\n"


def test_a_refused_save_leaves_no_version(client, sample_config, careless_editor):
    assert client.put("/api/configs/careless-test", json={"content": "refuse"}).status_code == 400
    assert _versions(sample_config, careless_editor) == []


def test_a_save_that_changes_nothing_leaves_no_version(client, sample_config, careless_editor):
    assert client.put("/api/configs/careless-test", json={"content": "before\n"}).status_code == 200
    assert _versions(sample_config, careless_editor) == []


def test_the_editor_lists_its_own_versions_and_restores_one(client, sample_config):
    path = _mission_db(sample_config) / "events.xml"
    path.write_text(EVENTS_XML, encoding="utf-8")
    assert client.put("/api/configs/events", json={"rows": []}).status_code == 200

    body = client.get("/api/configs/events/versions").json()
    assert body["file"] == "events.xml"
    [entry] = body["entries"]
    assert entry["source_path"] == str(path) and entry["identical"] is False

    diff = client.get(
        "/api/versions/diff",
        params={"file": entry["file_name"], "version": entry["version"], "source_path": entry["source_path"]},
    ).json()["diff"]
    assert "-" in diff and "StaticWreck" in diff

    restored = client.post(
        "/api/versions/restore",
        json={"file": entry["file_name"], "version": entry["version"], "source_path": entry["source_path"]},
    ).json()
    assert restored["restored"] is True
    assert "StaticWreck" in path.read_text()


def test_another_files_versions_of_the_same_name_are_not_listed(client, sample_config):
    """A map switch leaves events.xml of another mission in the store."""
    from app.shared.config_versions import snapshot

    other = sample_config.server_dir_path / "mpmissions" / "other.map" / "db" / "events.xml"
    other.parent.mkdir(parents=True)
    other.write_text(EVENTS_XML, encoding="utf-8")
    snapshot(other, trigger="manual", store=sample_config.paths.versions_dir)
    (_mission_db(sample_config) / "events.xml").write_text(EVENTS_XML, encoding="utf-8")

    assert client.get("/api/configs/events/versions").json()["entries"] == []


def test_versions_of_an_editor_without_a_file_answer_404(client):
    assert client.get("/api/configs/runcommand/versions").status_code == 404
    assert client.get("/api/configs/no-such-editor/versions").status_code == 404


def test_the_ignore_list_remembers_removed_names_across_saves(client, sample_config):
    path = _mission_db(sample_config) / "cfgIgnoreList.xml"
    path.write_text(IGNORELIST_XML, encoding="utf-8")
    assert client.get("/api/configs/ignorelist/meta").json()["removed"] is True

    rows = [{"id": "a", "values": {"name": "Land_Something"}}, {"id": "b", "values": {"name": "Bandage"}}]
    assert client.put("/api/configs/ignorelist", json={"rows": rows}).status_code == 200
    assert client.get("/api/configs/ignorelist/removed").json()["items"] == []

    assert client.put("/api/configs/ignorelist", json={"rows": rows[1:]}).status_code == 200
    assert client.put("/api/configs/ignorelist", json={"rows": []}).status_code == 200

    items = client.get("/api/configs/ignorelist/removed").json()["items"]
    assert [i["name"] for i in items] == ["Bandage", "Land_Something"]
    assert all(i["removed_at"] for i in items)

    # Putting one back is a plain save; it leaves the list.
    assert client.put("/api/configs/ignorelist", json={"rows": rows[1:]}).status_code == 200
    names = [i["name"] for i in client.get("/api/configs/ignorelist/removed").json()["items"]]
    assert names == ["Land_Something"]


def test_only_list_editors_keep_a_removed_list(client, sample_config):
    (_mission_db(sample_config) / "events.xml").write_text(EVENTS_XML, encoding="utf-8")
    assert client.get("/api/configs/events/meta").json()["removed"] is False
    assert client.get("/api/configs/events/removed").status_code == 404


def test_a_version_names_the_user_who_saved(client, sample_config):
    """Who did what (the Timeline's blame): a version saved in a request
    records that request's user, never the process default (app/shared/actor.py)."""
    path = _mission_db(sample_config) / "events.xml"
    path.write_text(EVENTS_XML, encoding="utf-8")
    me = client.get("/api/auth/me").json().get("username")

    assert client.put("/api/configs/events", json={"rows": []}).status_code == 200

    (version,) = _versions(sample_config, path)
    assert version.actor and version.actor != "Zedstead" and not version.actor.startswith("CLI")
    if me:
        assert version.actor == me
