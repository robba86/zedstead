"""Tests for /api/modxml (app/web/routers/modxml.py)."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from tests.conftest import manual_dir, set_test_flags, write_xml

PASSWORD = "testpass123"
MISSION = "dayzOffline.chernarusplus"


@pytest.fixture()
def client(sample_config, tmp_mission_dir):
    from app.web import server
    from app.web.auth import hash_password
    from app.xml.xml_economy import merge_economy_core

    write_xml(
        tmp_mission_dir / "cfgeconomycore.xml",
        '<?xml version="1.0"?><economycore><classes/><defaults/></economycore>',
    )
    write_xml(
        sample_config.server_dir_path / "@Bicycles" / "types.xml",
        '<?xml version="1.0"?><types><type name="Old_Bike"/></types>',
    )
    merge_economy_core(sample_config.server_dir_path, MISSION, manual_dir=manual_dir())

    sample_config.web_password_hash = hash_password(PASSWORD)
    tclient = TestClient(server.create_app(sample_config))
    tclient.post("/api/auth/login", json={"password": PASSWORD})
    return tclient


def test_modxml_requires_session(sample_config):
    from app.web import server

    resp = TestClient(server.create_app(sample_config)).get("/api/modxml")
    assert resp.status_code == 401


def test_modxml_report(client):
    body = client.get("/api/modxml").json()

    assert body["mission"] == MISSION
    assert any(f["name"] == "types.xml" and f["status"] == "linked" for f in body["files"])
    assert any(c["name"] == "bicycles-types.xml" for c in body["custom"])


def test_custom_file_is_readable(client):
    body = client.get("/api/modxml/custom/bicycles-types.xml").json()
    assert "Old_Bike" in body["content"]


def test_generated_custom_files_cannot_be_written(client):
    """No PUT counterpart to either GET (2026 Q3). custom/ is wiped and
    rebuilt from the mods every merge, so a save there lasted until the next
    install, toggle or `zsm run` — a fully working editor over content that was
    never durable. 405 rather than 404: the paths still serve GET."""
    for url in (
        "/api/modxml/custom/bicycles-types.xml",
        "/api/modxml/custom/bicycles-types.xml/table",
    ):
        assert client.put(url, json={"content": "<types/>", "rows": []}).status_code == 405, url
        assert client.get(url).status_code == 200, url


def test_custom_file_unknown_name_404(client):
    assert client.get("/api/modxml/custom/nope.xml").status_code == 404


def test_custom_file_name_never_leaves_custom(client, sample_config, tmp_mission_dir):
    """A name with a path in it is refused by the route and by the helper
    under it, even when the file it points at exists (plans/57 E1)."""
    from app.xml.xml_report import custom_file_path

    (tmp_mission_dir / "escape.xml").write_text("<types/>")
    assert client.get("/api/modxml/custom/..%2Fescape.xml").status_code == 404
    assert custom_file_path(sample_config, "../escape.xml") is None
    assert custom_file_path(sample_config, str(tmp_mission_dir / "escape.xml")) is None


def test_custom_table_reads_a_types_file(client):
    body = client.get("/api/modxml/custom/bicycles-types.xml/table").json()
    assert body["ce_kind"] == "types"
    assert body["kind"] == "table"
    assert any(r["values"]["name"] == "Old_Bike" for r in body["rows"])


def test_custom_table_events_file(client, sample_config, tmp_mission_dir):
    write_xml(
        tmp_mission_dir / "custom" / "bicycles-events.xml",
        '<?xml version="1.0"?><events><event name="Bike"><nominal>3</nominal>'
        "<min>1</min><max>5</max><lifetime>300</lifetime><restock>0</restock>"
        "<active>1</active></event></events>",
    )

    body = client.get("/api/modxml/custom/bicycles-events.xml/table").json()
    assert body["ce_kind"] == "events"
    assert body["rows"][0]["values"]["nominal"] == 3


@pytest.mark.parametrize(
    "kind,filename,content,column,value",
    [
        (
            "globals",
            "bicycles-globals.xml",
            '<?xml version="1.0"?><variables>'
            '<var name="ZombieMaxCount" type="0" value="800"/></variables>',
            "value",
            "800",
        ),
        (
            "economy",
            "bicycles-economy.xml",
            '<?xml version="1.0"?><economy>'
            '<dynamic init="1" load="0" respawn="1" save="0"/></economy>',
            "respawn",
            True,
        ),
        (
            "messages",
            "bicycles-messages.xml",
            '<?xml version="1.0"?><messages><message><text>hi</text></message></messages>',
            "text",
            "hi",
        ),
    ],
)
def test_custom_table_reads_the_new_ce_kinds(
    client, tmp_mission_dir, kind, filename, content, column, value,
):
    """globals/economy/messages are full CE types per DayZ's spec; their
    generated custom/ files get the same structured Basic tab as the rest — read
    only, since custom/ is regenerated every merge."""
    write_xml(tmp_mission_dir / "custom" / filename, content)

    body = client.get(f"/api/modxml/custom/{filename}/table").json()
    assert body["ce_kind"] == kind
    assert body["rows"][0]["values"][column] == value


def test_fork_globals_custom_file(client, tmp_mission_dir):
    """Forking used to 400 for anything outside types/events/spawnabletypes."""
    write_xml(
        tmp_mission_dir / "custom" / "bicycles-globals.xml",
        '<?xml version="1.0"?><variables>'
        '<var name="ZombieMaxCount" type="0" value="800"/></variables>',
    )
    link = client.post("/api/modxml/custom/bicycles-globals.xml/fork")
    assert link.status_code == 200, link.text
    assert link.json()["kind"] == "globals"


def test_custom_table_unsupported_kind_422(client, tmp_mission_dir):
    write_xml(
        tmp_mission_dir / "custom" / "bicycles-cfgeventspawns.xml",
        '<?xml version="1.0"?><eventposdef><event name="Bike">'
        '<pos x="1" z="2" a="0"/></event></eventposdef>',
    )
    resp = client.get("/api/modxml/custom/bicycles-cfgeventspawns.xml/table")
    assert resp.status_code == 422


def test_custom_table_unknown_name_404(client):
    assert client.get("/api/modxml/custom/nope.xml/table").status_code == 404


# --- source view (read-only mod XML) ---------------------------------------


def test_source_view_returns_mod_file(client):
    body = client.get("/api/modxml/source", params={"path": "@Bicycles/types.xml"}).json()

    assert body["name"] == "types.xml"
    assert body["language"] == "xml"
    assert "Old_Bike" in body["content"]


def test_source_view_rejects_traversal_and_non_mod_paths(client, sample_config):
    # The mission's own files and anything outside an @mod dir are not sources.
    for bad in (
        "../../../etc/passwd",
        "@Bicycles/../../etc/passwd",
        "mpmissions/dayzOffline.chernarusplus/cfgeconomycore.xml",
        "@Bicycles/types.txt",
        "@Bicycles/missing.xml",
        "",
    ):
        assert client.get("/api/modxml/source", params={"path": bad}).status_code == 404, bad


def test_source_view_rejects_symlink_out_of_server_dir(client, sample_config, tmp_path):
    # tmp_path IS the server dir (tests/conftest.py::tmp_server_dir), so the
    # escape target has to sit one level up to be genuinely outside it.
    outside = tmp_path.parent / "outside.xml"
    outside.write_text("<types/>", encoding="utf-8")
    link = sample_config.server_dir_path / "@Bicycles" / "escape.xml"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):  # pragma: no cover - Windows w/o privilege
        pytest.skip("symlinks unavailable")

    assert client.get("/api/modxml/source", params={"path": "@Bicycles/escape.xml"}).status_code == 404


# --- manual XML links -------------------------------------------------------

LINK_SPAWNS = (
    '<?xml version="1.0"?><eventposdef><event name="StaticHeliCrash">'
    '<pos x="1000" z="2000" a="0"/></event></eventposdef>'
)


def _poll_job(client, job_id, timeout=3.0):
    deadline = time.monotonic() + timeout
    while True:
        body = client.get(f"/api/jobs/{job_id}").json()
        if body["status"] not in ("pending", "running"):
            return body
        assert time.monotonic() < deadline, f"job {job_id} never finished: {body}"
        time.sleep(0.02)


def test_link_crud_round_trip(client):
    resp = client.post(
        "/api/modxml/files",
        json={"id": "heli", "kind": "cfgeventspawns", "content": LINK_SPAWNS, "terrain": "chernarus"},
    )
    assert resp.status_code == 200, resp.text

    body = client.get("/api/modxml/files").json()
    assert [row["id"] for row in body["files"]] == ["heli"]
    assert "StaticHeliCrash" in body["files"][0]["content"]
    assert "cfgeventspawns" in body["kinds"] and "types" in body["kinds"]

    assert client.put("/api/modxml/files/heli", json={"enabled": False}).status_code == 200
    assert client.get("/api/modxml/files").json()["files"][0]["enabled"] is False

    assert client.delete("/api/modxml/files/heli").status_code == 200
    assert client.get("/api/modxml/files").json()["files"] == []


def test_link_form_offers_the_map_catalog(client):
    """One option per map, named as the Maps page names it — and a label for
    every alias, so a scope stored as `livonia` doesn't render as a raw token."""
    from app.maps.registry import known_terrains

    body = client.get("/api/modxml/files").json()
    assert [t["terrain"] for t in body["terrains"]] == known_terrains()
    assert {"terrain": "enoch", "name": "Livonia"} in body["terrains"]
    assert body["terrain_names"]["livonia"] == "Livonia"


def test_link_create_rejects_bad_input(client):
    bad = [
        {"id": "../escape", "kind": "types", "content": "<types/>"},
        {"id": "ok", "kind": "nonsense", "content": "<types/>"},
        {"id": "ok", "kind": "types", "content": "<types>"},
        {"id": "ok", "kind": "types", "content": "<types/>", "terrain": "atlantis"},
    ]
    for body in bad:
        assert client.post("/api/modxml/files", json=body).status_code == 400, body


def test_delete_unknown_link_404(client):
    assert client.delete("/api/modxml/files/nope").status_code == 404


def test_report_lists_links_with_status(client):
    client.post(
        "/api/modxml/files",
        json={"id": "heli", "kind": "cfgeventspawns", "content": LINK_SPAWNS},
    )
    links = client.get("/api/modxml").json()["manual"]

    assert len(links) == 1
    # Stored but not merged yet — the button exists for exactly this state.
    assert links[0]["status"] == "no_target"
    assert links[0]["target"] == "cfgeventspawns.xml"


def test_apply_links_job_merges_them(sample_config, tmp_mission_dir):
    from app.web import server
    from app.web.auth import hash_password
    from tests.conftest import write_xml

    write_xml(
        tmp_mission_dir / "cfgeconomycore.xml",
        '<?xml version="1.0"?><economycore><classes/><defaults/></economycore>',
    )
    write_xml(
        tmp_mission_dir / "cfgeventspawns.xml",
        '<?xml version="1.0"?><eventposdef><event name="Vanilla"/></eventposdef>',
    )
    sample_config.web_password_hash = hash_password(PASSWORD)

    with TestClient(server.create_app(sample_config)) as client:
        client.post("/api/auth/login", json={"password": PASSWORD})
        client.post(
            "/api/modxml/files",
            json={"id": "heli", "kind": "cfgeventspawns", "content": LINK_SPAWNS},
        )
        job_id = client.post("/api/modxml/files/apply").json()["job_id"]
        assert _poll_job(client, job_id)["status"] == "success"

        assert "@user:heli" in (tmp_mission_dir / "cfgeventspawns.xml").read_text()
        assert client.get("/api/modxml").json()["manual"][0]["status"] == "merged"


def test_link_from_event_captures_hand_placed_positions(client, tmp_mission_dir):
    write_xml(
        tmp_mission_dir / "cfgeventspawns.xml",
        '<?xml version="1.0"?><eventposdef><event name="StaticHeliCrash">'
        '<pos x="7" z="8" a="0"/></event></eventposdef>',
    )

    link = client.post("/api/modxml/files/from-event", json={"event": "StaticHeliCrash"}).json()
    assert link["kind"] == "cfgeventspawns"
    assert link["terrain"] == "chernarusplus"

    content = client.get("/api/modxml/files").json()["files"][0]["content"]
    assert 'x="7"' in content


def test_link_from_event_unknown_event_400(client, tmp_mission_dir):
    write_xml(
        tmp_mission_dir / "cfgeventspawns.xml",
        '<?xml version="1.0"?><eventposdef/>',
    )
    resp = client.post("/api/modxml/files/from-event", json={"event": "Nope"})
    assert resp.status_code == 400


def test_file_from_mission_file_moves_the_admins_entries(client, tmp_mission_dir):
    write_xml(
        tmp_mission_dir / "cfgeventgroups.xml",
        '<?xml version="1.0"?><eventgroupdef><group name="Mine">'
        '<child type="Barrel"/></group></eventgroupdef>',
    )

    link = client.post(
        "/api/modxml/files/from-file", json={"target": "cfgeventgroups.xml"}
    ).json()
    assert link["kind"] == "cfgeventgroups" and link["enabled"] is True

    text = (tmp_mission_dir / "cfgeventgroups.xml").read_text()
    assert text.count('name="Mine"') == 1
    assert f"START MOD: @user:{link['id']}" in text


@pytest.mark.parametrize(
    "target",
    [
        "cfgeconomycore.xml",  # generated <ce> block — a competing mechanism
        "db/types.xml",  # inside the mission, but no merge config owns it
        "../../etc/passwd",  # outside it entirely
        "",
    ],
)
def test_file_from_mission_file_rejects_a_target_no_merge_owns(client, target):
    resp = client.post("/api/modxml/files/from-file", json={"target": target})
    assert resp.status_code == 400, target


def test_editor_meta_names_the_file_that_can_be_captured(client, tmp_mission_dir):
    """The button is driven off the server's own kind_for_target map, so the
    webui can't disagree about which files a mod merges into."""
    write_xml(tmp_mission_dir / "cfgeventgroups.xml", '<?xml version="1.0"?><eventgroupdef/>')
    write_xml(tmp_mission_dir / "cfgspawnabletypes.xml", '<?xml version="1.0"?><spawnabletypes/>')

    assert client.get("/api/configs/eventgroups/meta").json()["capture_target"] == (
        "cfgeventgroups.xml"
    )
    # cfgeconomycore.xml is out by design; a CE file reaches the mission through
    # custom/, so there is no marker block in it to separate.
    assert client.get("/api/configs/economycore/meta").json()["capture_target"] == ""
    assert client.get("/api/configs/spawnabletypes/meta").json()["capture_target"] == ""


# --- disabling / forking generated custom/ files ----------------------------


def test_custom_state_toggle_disables_and_restores(client, tmp_mission_dir):
    assert (tmp_mission_dir / "custom" / "bicycles-types.xml").is_file()

    resp = client.put("/api/modxml/custom/bicycles-types.xml/state", json={"excluded": True})
    assert resp.status_code == 200
    assert resp.json()["mod"] == "@Bicycles"
    assert not (tmp_mission_dir / "custom" / "bicycles-types.xml").exists()
    assert 'name="bicycles-types.xml"' not in (tmp_mission_dir / "cfgeconomycore.xml").read_text()

    # Still listed while disabled, or it could never be switched back on.
    entry = next(c for c in client.get("/api/modxml").json()["custom"] if c["name"] == "bicycles-types.xml")
    assert entry["excluded"] is True and entry["missing"] is True

    assert client.put(
        "/api/modxml/custom/bicycles-types.xml/state", json={"excluded": False}
    ).status_code == 200
    assert (tmp_mission_dir / "custom" / "bicycles-types.xml").is_file()
    assert 'name="bicycles-types.xml"' in (tmp_mission_dir / "cfgeconomycore.xml").read_text()


def test_custom_state_rejects_anything_that_isnt_a_bare_xml_name(client):
    # The toggle can't require the file to exist (a disabled one is deleted),
    # so the name shape is the only guard between it and the exclusion list.
    # ".." is left out: the URL is normalized away before it ever reaches a route.
    for bad in ("notxml", "sub\\dir.xml", "trailing.xml.bak"):
        resp = client.put(f"/api/modxml/custom/{bad}/state", json={"excluded": True})
        assert resp.status_code == 404, bad


def test_fork_custom_file_creates_link_and_disables_original(client, tmp_mission_dir):
    link = client.post("/api/modxml/custom/bicycles-types.xml/fork").json()

    assert link["kind"] == "types"
    assert link["mod"] == "@Bicycles"
    assert "Old_Bike" in client.get("/api/modxml/files").json()["files"][0]["content"]
    assert not (tmp_mission_dir / "custom" / "bicycles-types.xml").exists()


def test_fork_pairs_the_link_with_the_original_in_the_report(client):
    """The page's only join between the greyed original and its replacement."""
    link = client.post("/api/modxml/custom/bicycles-types.xml/fork").json()

    report = client.get("/api/modxml").json()
    row = next(r for r in report["manual"] if r["id"] == link["id"])
    assert row["forked_from"] == "bicycles-types.xml"
    assert row["custom_file"] == f"user-{link['id']}.xml"
    # The original is still listed (disabled, not deleted) for the pairing to
    # have something to point at.
    original = next(c for c in report["custom"] if c["name"] == "bicycles-types.xml")
    assert original["excluded"] is True


def test_fork_non_ce_file_400(client, tmp_mission_dir):
    write_xml(
        tmp_mission_dir / "custom" / "bicycles-cfgeventspawns.xml",
        '<?xml version="1.0"?><eventposdef><event name="X"/></eventposdef>',
    )
    assert client.post("/api/modxml/custom/bicycles-cfgeventspawns.xml/fork").status_code == 400


# --- disabling / deleting / forking a mod's own XML file --------------------

MOD_SPAWNS = (
    '<?xml version="1.0"?><eventposdef><event name="ModHeli">'
    '<pos x="1" z="2" a="0"/></event></eventposdef>'
)


@pytest.fixture()
def merged_client(client, sample_config, tmp_mission_dir):
    """A mod whose cfgeventspawns.xml is spliced into the mission (a table row).

    The `client` fixture only runs the economy half, which produces a `custom/`
    tile; the actions under test belong to the files that never become one.
    """
    from app.xml.xml_merge import merge_all_direct_configs

    write_xml(
        tmp_mission_dir / "cfgeventspawns.xml",
        '<?xml version="1.0"?><eventposdef><event name="Vanilla"/></eventposdef>',
    )
    write_xml(sample_config.server_dir_path / "@Bicycles" / "cfgeventspawns.xml", MOD_SPAWNS)
    merge_all_direct_configs(sample_config.server_dir_path, MISSION, manual_dir=manual_dir())
    assert "@Bicycles" in (tmp_mission_dir / "cfgeventspawns.xml").read_text()
    return client


SPAWNS_SOURCE = "@Bicycles/cfgeventspawns.xml"


def _row(client, source):
    return next(f for f in client.get("/api/modxml").json()["files"] if f["source"] == source)


def _tile(client, name):
    return next(c for c in client.get("/api/modxml").json()["custom"] if c["name"] == name)


def test_source_state_disables_a_merged_file_and_restores_it(merged_client, sample_config, tmp_mission_dir):
    from app.xml.xml_merge import merge_all_direct_configs

    resp = merged_client.put(
        "/api/modxml/source/state", json={"path": SPAWNS_SOURCE, "excluded": True},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["mod"] == "@Bicycles" and resp.json()["applied"] is True
    # Out of the mission now, not at some later merge.
    assert "@Bicycles" not in (tmp_mission_dir / "cfgeventspawns.xml").read_text()

    row = _row(merged_client, SPAWNS_SOURCE)
    assert row["status"] == "excluded" and row["excluded"] is True

    # And durable: a full merge must not bring it back.
    merge_all_direct_configs(sample_config.server_dir_path, MISSION, manual_dir=manual_dir())
    assert "@Bicycles" not in (tmp_mission_dir / "cfgeventspawns.xml").read_text()

    assert merged_client.put(
        "/api/modxml/source/state", json={"path": SPAWNS_SOURCE, "excluded": False},
    ).status_code == 200
    assert "@Bicycles" in (tmp_mission_dir / "cfgeventspawns.xml").read_text()
    assert _row(merged_client, SPAWNS_SOURCE)["status"] == "merged"


def test_source_state_disables_a_ce_file_before_it_is_ever_linked(client, sample_config, tmp_mission_dir):
    """The CE half honours the same list, keyed by the mod's file."""
    from app.xml.xml_economy import merge_economy_core

    resp = client.put(
        "/api/modxml/source/state", json={"path": "@Bicycles/types.xml", "excluded": True},
    )
    assert resp.status_code == 200, resp.text
    assert not (tmp_mission_dir / "custom" / "bicycles-types.xml").exists()

    merge_economy_core(sample_config.server_dir_path, MISSION, manual_dir=manual_dir())
    assert not (tmp_mission_dir / "custom" / "bicycles-types.xml").exists()
    assert 'name="bicycles-types.xml"' not in (tmp_mission_dir / "cfgeconomycore.xml").read_text()


def test_a_ce_file_off_by_its_source_is_turned_on_by_its_source(client, tmp_mission_dir):
    """mods-11: after Adopt's "Keep mine" the Mod XML
    page showed the file "Off" with its switch at "On". The record is the
    source's, not the tile's, so the page reads the row's flag, and its On
    clears the source state: the tile's own route leaves the file off."""
    from app.xml import xml_manual

    source = "@Bicycles/types.xml"
    # What adopt.keep_pasted writes. It leaves the mission alone.
    xml_manual.set_source_state(source, "excluded", manual_dir=manual_dir())
    assert _row(client, source)["excluded"] is True
    assert _tile(client, "bicycles-types.xml")["excluded"] is False

    client.put("/api/modxml/custom/bicycles-types.xml/state", json={"state": ""})
    assert _row(client, source)["status"] == "excluded"

    resp = client.put("/api/modxml/source/state", json={"path": source, "state": ""})
    assert resp.status_code == 200, resp.text
    assert resp.json()["applied"] is True
    assert (tmp_mission_dir / "custom" / "bicycles-types.xml").is_file()
    assert _row(client, source)["status"] == "linked"


def test_delete_source_is_permanent(merged_client, sample_config, tmp_mission_dir):
    """The mod file is SteamCMD's — only a recorded delete survives a merge."""
    from app.xml.xml_manual import load_deleted_sources, load_source_exclusions
    from app.xml.xml_merge import merge_all_direct_configs

    resp = merged_client.delete(f"/api/modxml/source?path={SPAWNS_SOURCE}")
    assert resp.status_code == 200 and resp.json()["permanent"] is True
    assert "@Bicycles" not in (tmp_mission_dir / "cfgeventspawns.xml").read_text()
    assert load_deleted_sources(manual_dir=manual_dir()) == {SPAWNS_SOURCE}
    # Deleted and disabled suppress identically — the merge reads one list.
    assert load_source_exclusions(manual_dir=manual_dir()) == {SPAWNS_SOURCE}

    merge_all_direct_configs(sample_config.server_dir_path, MISSION, manual_dir=manual_dir())
    assert "@Bicycles" not in (tmp_mission_dir / "cfgeventspawns.xml").read_text()
    assert _row(merged_client, SPAWNS_SOURCE)["status"] == "deleted"


def test_deleted_source_is_restorable(merged_client, sample_config, tmp_mission_dir):
    """Delete drops the row from the page, not the ability to bring it back."""
    from app.xml.xml_merge import merge_all_direct_configs

    merged_client.delete(f"/api/modxml/source?path={SPAWNS_SOURCE}")
    resp = merged_client.put(
        "/api/modxml/source/state", json={"path": SPAWNS_SOURCE, "state": ""},
    )
    assert resp.status_code == 200, resp.text

    merge_all_direct_configs(sample_config.server_dir_path, MISSION, manual_dir=manual_dir())
    assert "@Bicycles" in (tmp_mission_dir / "cfgeventspawns.xml").read_text()
    assert _row(merged_client, SPAWNS_SOURCE)["status"] == "merged"


def test_delete_source_keeps_the_mods_other_files_merged(merged_client, sample_config, tmp_mission_dir):
    """The mod's files share one START/END block — dropping it isn't the fix."""
    from app.xml.xml_merge import merge_all_direct_configs

    write_xml(
        tmp_mission_dir / "cfgeventgroups.xml",
        '<?xml version="1.0"?><eventgroupdef><group name="Vanilla"/></eventgroupdef>',
    )
    write_xml(
        sample_config.server_dir_path / "@Bicycles" / "cfgeventgroups.xml",
        '<?xml version="1.0"?><eventgroupdef><group name="ModGroup"/></eventgroupdef>',
    )
    merge_all_direct_configs(sample_config.server_dir_path, MISSION, manual_dir=manual_dir())

    assert merged_client.delete(f"/api/modxml/source?path={SPAWNS_SOURCE}").status_code == 200
    assert "ModGroup" in (tmp_mission_dir / "cfgeventgroups.xml").read_text()


def test_delete_source_refuses_a_ce_file(client):
    resp = client.delete("/api/modxml/source?path=@Bicycles/types.xml")
    assert resp.status_code == 400
    assert "custom/" in resp.json()["detail"]


def test_fork_source_creates_a_link_and_disables_the_mod_file(merged_client, tmp_mission_dir):
    link = merged_client.post("/api/modxml/source/fork", json={"path": SPAWNS_SOURCE}).json()

    assert link["kind"] == "cfgeventspawns"
    assert link["mod"] == "@Bicycles"
    assert link["forked_from"] == SPAWNS_SOURCE
    assert "ModHeli" in merged_client.get("/api/modxml/files").json()["files"][0]["content"]
    # Both live would merge the same positions twice.
    assert "@Bicycles" not in (tmp_mission_dir / "cfgeventspawns.xml").read_text()
    assert _row(merged_client, SPAWNS_SOURCE)["status"] == "excluded"
    # Enabled, like a custom-file fork: the same "Set as manual" button must not
    # mean "take it over" on a tile and "switch it off" on a table row.
    assert link["enabled"] is True
    # And applied in the same step — the exclusion above already took the mod's
    # positions out, so an unapplied copy would leave the mission with neither.
    assert link["applied"] is True
    assert f"@user:{link['id']}" in (tmp_mission_dir / "cfgeventspawns.xml").read_text()


def test_fork_source_for_a_disabled_mod_is_recorded_but_not_applied(
    merged_client, sample_config,
):
    """Same refusal the state routes make — its CE entries are parked in
    DISABLED wrappers a scoped merge would delete."""
    set_test_flags(sample_config, disabled=["@Bicycles"])

    body = merged_client.post("/api/modxml/source/fork", json={"path": SPAWNS_SOURCE}).json()

    assert body["enabled"] is True
    assert body["applied"] is False and body["reason"] == "mod disabled"


def test_source_actions_reject_a_path_outside_the_mods(client):
    for call in (
        lambda p: client.put("/api/modxml/source/state", json={"path": p, "excluded": True}),
        lambda p: client.delete(f"/api/modxml/source?path={p}"),
        lambda p: client.post("/api/modxml/source/fork", json={"path": p}),
    ):
        assert call("mpmissions/x.xml").status_code == 404
        assert call("@Bicycles/nope.xml").status_code == 404


def test_merge_link_targets_a_named_mission_file(client, tmp_mission_dir):
    write_xml(tmp_mission_dir / "db" / "globals.xml", '<?xml version="1.0"?><variables/>')

    body = client.get("/api/modxml/files").json()
    assert "db/globals.xml" in body["targets"]
    assert body["merge_kind"] == "merge"

    resp = client.post(
        "/api/modxml/files",
        json={
            "id": "extra-globals",
            "kind": "merge",
            "target": "db/globals.xml",
            "content": '<variables><var name="MyVar"/></variables>',
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["target"] == "db/globals.xml"

    row = client.get("/api/modxml").json()["manual"][0]
    assert row["target"] == "db/globals.xml" and row["status"] == "no_target"


def test_merge_link_without_a_target_400(client):
    resp = client.post(
        "/api/modxml/files", json={"id": "nope", "kind": "merge", "content": "<x/>"},
    )
    assert resp.status_code == 400


def test_delete_custom_file_is_permanent(client, sample_config, tmp_mission_dir):
    """custom/ is regenerated on every merge, so a delete has to be recorded."""
    from app.xml.xml_economy import merge_economy_core
    from app.xml.xml_manual import load_deleted, load_exclusions

    resp = client.delete("/api/modxml/custom/bicycles-types.xml")
    assert resp.status_code == 200 and resp.json()["permanent"] is True
    assert not (tmp_mission_dir / "custom" / "bicycles-types.xml").exists()
    assert 'name="bicycles-types.xml"' not in (tmp_mission_dir / "cfgeconomycore.xml").read_text()
    assert load_deleted(manual_dir=manual_dir()) == {"bicycles-types.xml"}
    assert load_exclusions(manual_dir=manual_dir()) == {"bicycles-types.xml"}

    merge_economy_core(sample_config.server_dir_path, MISSION, manual_dir=manual_dir())
    assert not (tmp_mission_dir / "custom" / "bicycles-types.xml").exists()


def test_deleted_custom_file_keeps_a_restorable_tile(client, sample_config, tmp_mission_dir):
    from app.xml.xml_economy import merge_economy_core

    client.delete("/api/modxml/custom/bicycles-types.xml")
    tile = _tile(client, "bicycles-types.xml")
    assert tile["deleted"] is True and tile["missing"] is True
    # It still knows where it came from, so Restore and "Set as manual" work.
    assert tile["mod"] == "@Bicycles" and tile["source"] == "@Bicycles/types.xml"

    resp = client.put("/api/modxml/custom/bicycles-types.xml/state", json={"state": ""})
    assert resp.status_code == 200, resp.text
    merge_economy_core(sample_config.server_dir_path, MISSION, manual_dir=manual_dir())
    assert (tmp_mission_dir / "custom" / "bicycles-types.xml").is_file()


def test_delete_custom_file_works_when_nothing_generated_it(client):
    """A tile for a mod-disabled or never-merged file must still be removable."""
    from app.xml.xml_manual import load_deleted

    assert client.delete("/api/modxml/custom/bikes-types.xml").status_code == 200
    assert "bikes-types.xml" in load_deleted(manual_dir=manual_dir())


def test_fork_custom_file_makes_an_enabled_applied_file(client, tmp_mission_dir):
    """Taking a generated file over is now the whole act, not the first half of
    one — custom/ is read-only, so a disabled result would silently drop that
    file's content out of the mission until the admin found a second switch."""
    link = client.post("/api/modxml/custom/bicycles-types.xml/fork", json={}).json()

    assert link["enabled"] is True
    assert link["applied"] is True
    assert link["forked_from"] == "bicycles-types.xml"

    # The swap is invisible to the running economy: the mod's copy is gone, the
    # admin's is registered in its place, same content.
    core = (tmp_mission_dir / "cfgeconomycore.xml").read_text()
    assert 'name="bicycles-types.xml"' not in core
    assert f'name="user-{link["id"]}.xml" type="types"' in core
    assert "Old_Bike" in (tmp_mission_dir / "custom" / f"user-{link['id']}.xml").read_text()


def _fork(client) -> dict:
    return client.post("/api/modxml/custom/bicycles-types.xml/fork", json={}).json()


def _file_row(client, file_id: str) -> dict:
    return next(f for f in client.get("/api/modxml/files").json()["files"] if f["id"] == file_id)


def _bump_mod_file(sample_config, body: str) -> None:
    write_xml(sample_config.server_dir_path / "@Bicycles" / "types.xml", body)


NEW_MOD_TYPES = '<?xml version="1.0"?><types><type name="New_Bike"/></types>'


def test_upstream_is_current_until_the_mod_file_changes(client, sample_config):
    link = _fork(client)

    body = client.get(f"/api/modxml/files/{link['id']}/upstream").json()
    assert body["status"] == "current"
    assert body["source"] == "@Bicycles/types.xml"
    assert body["captured_at"]

    _bump_mod_file(sample_config, NEW_MOD_TYPES)

    body = client.get(f"/api/modxml/files/{link['id']}/upstream").json()
    assert body["status"] == "changed"
    assert "New_Bike" in body["diff"]


def test_files_listing_carries_the_upstream_status(client, sample_config):
    """Hash-only on the listing — a diff per row would parse two XML files each."""
    link = _fork(client)
    assert _file_row(client, link["id"])["upstream"] == "current"

    _bump_mod_file(sample_config, NEW_MOD_TYPES)
    assert _file_row(client, link["id"])["upstream"] == "changed"


def test_upstream_ack_clears_the_flag_without_touching_the_file(client, sample_config):
    link = _fork(client)
    client.put(f"/api/modxml/files/{link['id']}", json={"content": "<types><type name='Mine'/></types>"})
    _bump_mod_file(sample_config, NEW_MOD_TYPES)

    assert client.post(f"/api/modxml/files/{link['id']}/upstream/ack").json()["status"] == "current"

    row = _file_row(client, link["id"])
    assert row["upstream"] == "current"
    assert "Mine" in row["content"]


def test_upstream_adopt_replaces_the_file(client, sample_config):
    link = _fork(client)
    client.put(f"/api/modxml/files/{link['id']}", json={"content": "<types><type name='Mine'/></types>"})
    _bump_mod_file(sample_config, NEW_MOD_TYPES)

    assert client.post(f"/api/modxml/files/{link['id']}/upstream/adopt").json()["status"] == "current"

    row = _file_row(client, link["id"])
    assert row["upstream"] == "current"
    assert "New_Bike" in row["content"] and "Mine" not in row["content"]


def test_upstream_routes_404_for_an_unknown_file(client):
    assert client.get("/api/modxml/files/nope/upstream").status_code == 404
    assert client.post("/api/modxml/files/nope/upstream/ack").status_code == 404
    assert client.post("/api/modxml/files/nope/upstream/adopt").status_code == 404


def test_upstream_400s_when_the_mod_file_is_gone(client, sample_config):
    import shutil

    link = _fork(client)
    shutil.rmtree(sample_config.server_dir_path / "@Bicycles")

    assert client.get(f"/api/modxml/files/{link['id']}/upstream").json()["status"] == "missing"
    assert client.post(f"/api/modxml/files/{link['id']}/upstream/adopt").status_code == 400


def test_fork_for_a_disabled_mod_is_recorded_but_not_applied(client, sample_config):
    """Nothing of a disabled mod is in the mission to keep serving, and its CE
    entries are parked in DISABLED wrappers — same refusal the state routes make."""
    set_test_flags(sample_config, disabled=["@Bicycles"])

    body = client.post("/api/modxml/custom/bicycles-types.xml/fork", json={}).json()

    assert body["enabled"] is True
    assert body["applied"] is False and body["reason"] == "mod disabled"


def test_fork_custom_file_falls_back_to_the_mod_file(client, sample_config, tmp_mission_dir):
    """A file the user turned off is gone from custom/ — fork the mod's own copy."""
    client.put("/api/modxml/custom/bicycles-types.xml/state", json={"state": "excluded"})
    assert not (tmp_mission_dir / "custom" / "bicycles-types.xml").exists()

    resp = client.post(
        "/api/modxml/custom/bicycles-types.xml/fork", json={"source": "@Bicycles/types.xml"},
    )
    assert resp.status_code == 200, resp.text
    links = client.get("/api/modxml/files").json()["files"]
    assert "Old_Bike" in next(link for link in links if link["id"] == resp.json()["id"])["content"]


def test_fork_custom_file_rejects_a_bad_fallback(client):
    resp = client.post(
        "/api/modxml/custom/bicycles-types.xml/fork", json={"source": "../../etc/passwd"},
    )
    assert resp.status_code == 404


def test_reenabling_a_disabled_mods_file_is_recorded_not_applied(client, sample_config, tmp_mission_dir):
    """A scoped CE merge would delete the mod's parked DISABLED wrappers."""
    from app.xml.xml_economy import disable_mod_economy_entries

    client.put("/api/modxml/custom/bicycles-types.xml/state", json={"state": "excluded"})
    set_test_flags(sample_config, disabled=["@Bicycles"])
    disable_mod_economy_entries(sample_config.server_dir_path, MISSION, "@Bicycles")
    before = (tmp_mission_dir / "cfgeconomycore.xml").read_text()

    body = client.put("/api/modxml/custom/bicycles-types.xml/state", json={"state": ""}).json()
    assert body["applied"] is False and body["reason"] == "mod disabled"
    assert (tmp_mission_dir / "cfgeconomycore.xml").read_text() == before


def test_custom_actions_reject_a_bad_name(client):
    # `sub\dir.xml` is the one that matters: it is a bare name to PurePosixPath
    # and only becomes a path once it reaches a Windows server.
    for name in ("nope.txt", "sub%5Cdir.xml"):
        assert client.delete(f"/api/modxml/custom/{name}").status_code == 404
        assert client.put(f"/api/modxml/custom/{name}/state", json={}).status_code == 404


def test_links_payload_carries_the_two_modes_vocabulary(client):
    body = client.get("/api/modxml/files").json()

    # Link mode offers exactly the six `type=` values DayZ accepts.
    assert sorted(body["ce_kinds"]) == [
        "economy", "events", "globals", "messages", "spawnabletypes", "types",
    ]
    # The table names the file a link lands in without re-deriving it.
    assert body["kind_targets"]["types"] == "cfgeconomycore.xml"
    assert body["kind_targets"]["cfgeventspawns"] == "cfgeventspawns.xml"
    assert "merge" not in body["kind_targets"]


def test_merge_at_a_known_file_is_upgraded_to_its_kind(client):
    resp = client.post(
        "/api/modxml/files",
        json={
            "id": "heli",
            "kind": "merge",
            "target": "cfgeventspawns.xml",
            "content": LINK_SPAWNS,
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["kind"] == "cfgeventspawns"
    assert resp.json()["target"] == ""


@pytest.mark.parametrize(
    "content,kind,mode,target",
    [
        ('<type name="X"><nominal>1</nominal></type>', "types", "link", "cfgeconomycore.xml"),
        (
            '<event name="E"><pos x="1" z="2" a="0"/></event>',
            "cfgeventspawns",
            "merge",
            "cfgeventspawns.xml",
        ),
        (
            '<variables><var name="X" type="0" value="1"/></variables>',
            "globals",
            "link",
            "cfgeconomycore.xml",
        ),
        ('<messages><message><text>hi</text></message></messages>',
         "messages", "link", "cfgeconomycore.xml"),
        # <variables> with no <var> is somebody's own settings file.
        ('<variables><setting name="X"/></variables>', None, None, ""),
    ],
)
def test_classify_proposes_a_mode_for_pasted_xml(client, content, kind, mode, target):
    body = client.post("/api/modxml/files/classify", json={"content": content}).json()

    assert body["kind"] == kind
    assert body["mode"] == mode
    assert body["target"] == target


def test_classify_does_not_shadow_the_link_id_routes(client):
    """POST /links/classify must not be reachable as a link named "classify"."""
    client.post("/api/modxml/files", json={"id": "classify", "kind": "types", "content": "<types/>"})
    assert client.get("/api/modxml/files").json()["files"][0]["id"] == "classify"
    assert client.delete("/api/modxml/files/classify").status_code == 200


# ---------------------------------------------------------------------------
# GET /links/starters — the builder's "Start from:" buttons
# ---------------------------------------------------------------------------


def test_starters_for_a_ce_kind_returns_its_templates(client):
    body = client.get("/api/modxml/files/starters?kind=types").json()

    assert body["kind"] == "types"
    assert body["root_tag"] == "types"
    assert body["target"] == "cfgeconomycore.xml"
    assert {t["id"] for t in body["templates"]} == {
        "types-item", "types-disable", "types-vehicle",
    }
    # A kind knows its own file; there is nothing to sample.
    assert body["sample"] is None and body["empty"] is None


def test_starters_for_a_known_merge_file_resolves_to_that_kind(client):
    """Same resolution the save path uses, so the buttons can't offer a starter
    the save would then reclassify."""
    body = client.get("/api/modxml/files/starters?target=cfgeventspawns.xml").json()

    assert body["kind"] == "cfgeventspawns"
    assert [t["id"] for t in body["templates"]] == ["cfgeventspawns-positions"]


def test_starters_for_a_free_form_target_samples_the_real_file(client, tmp_mission_dir):
    write_xml(
        tmp_mission_dir / "db" / "globals.xml",
        '<?xml version="1.0"?><variables>'
        '<!-- START MOD: @Bikes --><var name="ModVar" type="0" value="1"/>'
        '<!-- END MOD: @Bikes -->'
        '<var name="ZombieMaxCount" type="0" value="800"/></variables>',
    )
    body = client.get("/api/modxml/files/starters?target=db/globals.xml").json()

    assert body["kind"] == "merge"
    assert [t["id"] for t in body["templates"]] == ["globals-var"]
    assert "ZombieMaxCount" in body["sample"]["content"]
    assert "ModVar" not in body["sample"]["content"]
    assert body["sample"]["skipped_mod_blocks"] == 1
    assert body["empty"]["content"].startswith('<?xml version="1.0" encoding="UTF-8"?>\n<variables>')


def test_starters_offers_an_empty_root_when_nothing_can_be_sampled(client, tmp_mission_dir):
    write_xml(tmp_mission_dir / "cfgweather.xml", '<?xml version="1.0"?><weather/>')
    body = client.get("/api/modxml/files/starters?target=cfgweather.xml").json()

    assert body["templates"] == [] and body["sample"] is None
    assert body["empty"]["label"] == "Empty <weather>"
    assert "nothing of yours" in body["reason"]


def test_starters_without_a_target_is_empty_not_an_error(client):
    body = client.get("/api/modxml/files/starters").json()
    assert body == {
        "kind": "merge", "target": "", "root_tag": "",
        "templates": [], "sample": None, "empty": None, "reason": "",
    }


def test_starters_rejects_an_unsafe_target(client):
    assert client.get("/api/modxml/files/starters?target=db/sub/deep.xml").status_code == 400
    assert client.get("/api/modxml/files/starters?target=../../etc/passwd").status_code == 400


def test_starters_does_not_shadow_the_link_id_routes(client):
    client.post("/api/modxml/files", json={"id": "starters", "kind": "types", "content": "<types/>"})
    assert client.get("/api/modxml/files").json()["files"][0]["id"] == "starters"
    assert client.delete("/api/modxml/files/starters").status_code == 200


def test_a_starter_creates_a_working_link(sample_config, tmp_mission_dir):
    """End to end: the button's XML goes in unedited and comes out merged."""
    from app.web import server
    from app.web.auth import hash_password

    write_xml(
        tmp_mission_dir / "cfgeconomycore.xml",
        '<?xml version="1.0"?><economycore><classes/><defaults/></economycore>',
    )
    write_xml(
        tmp_mission_dir / "cfgeventspawns.xml",
        '<?xml version="1.0"?><eventposdef><event name="Vanilla"/></eventposdef>',
    )
    sample_config.web_password_hash = hash_password(PASSWORD)

    with TestClient(server.create_app(sample_config)) as client:
        client.post("/api/auth/login", json={"password": PASSWORD})
        starter = client.get("/api/modxml/files/starters?target=cfgeventspawns.xml").json()
        content = starter["templates"][0]["content"]

        resp = client.post(
            "/api/modxml/files",
            json={"id": "from-starter", "kind": "merge",
                  "target": "cfgeventspawns.xml", "content": content},
        )
        assert resp.status_code == 200, resp.text

        job_id = client.post("/api/modxml/files/apply").json()["job_id"]
        assert _poll_job(client, job_id)["status"] == "success"
        assert "@user:from-starter" in (tmp_mission_dir / "cfgeventspawns.xml").read_text()
