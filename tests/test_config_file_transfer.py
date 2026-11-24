"""Download / upload per config file (TODO G1c).

The feature exists for the admin whose server is on a host panel with nothing
but FTP: fetch the file there, drag it in here, use the real editor, take it
back. So the tests that matter are the ones about not making that worse than
FTP already is — a rejected upload must write nothing, and the path must never
come from the request.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

PASSWORD = "testpass123"

GOOD_XML = '<?xml version="1.0" encoding="UTF-8"?>\n<variables>\n  <var name="X"/>\n</variables>\n'
BAD_XML = "<variables><var name='X'></variables>"

# The smallest serverDZ.cfg a server starts with: since plans/51 C an upload
# must still parse and still name a mission.
MISSIONS = 'class Missions\n{\n\tclass DayZ\n\t{\n\t\ttemplate = "dayzOffline.chernarusplus";\n\t};\n};\n'


def cfg(first_line: str) -> bytes:
    return (first_line + "\n" + MISSIONS).encode()


@pytest.fixture()
def client(sample_config):
    from app.web import server
    from app.web.auth import hash_password

    sample_config.web_password_hash = hash_password(PASSWORD)
    app = server.create_app(sample_config)
    tclient = TestClient(app)
    tclient.post("/api/auth/login", json={"password": PASSWORD})
    return tclient


@pytest.fixture()
def serverdz(sample_config):
    """`serverdz` is a registry editor with a single raw target that exists in
    the conftest fixture, which makes it the simplest real subject here."""
    return sample_config.server_cfg_path


# ---------------------------------------------------------------------------
# download
# ---------------------------------------------------------------------------


def test_download_needs_a_session(sample_config):
    from app.web import server

    app = server.create_app(sample_config)
    assert TestClient(app).get("/api/configs/serverdz/download").status_code == 401


def test_download_hands_over_the_file_as_an_attachment(client, serverdz):
    resp = client.get("/api/configs/serverdz/download")
    assert resp.status_code == 200
    assert resp.text == serverdz.read_text(encoding="utf-8")
    assert "attachment" in resp.headers["content-disposition"]
    assert serverdz.name in resp.headers["content-disposition"]
    # Re-downloaded after every edit under the same name — a cached copy of
    # yesterday's is worse than a slow download.
    assert resp.headers["cache-control"] == "no-store"


def test_an_editor_with_no_raw_view_has_nothing_to_download(client):
    """`runcommand`'s fields live in zsm's own config.json, not a server
    file, so there is no file to hand over."""
    assert client.get("/api/configs/runcommand/download").status_code == 404
    assert client.get("/api/configs/nope/download").status_code == 404


def test_the_target_is_matched_against_the_listing_not_joined_onto_a_path(client):
    """The rule that comes with downloads.py: a path from the query string is
    the call that gets this wrong."""
    resp = client.get("/api/configs/serverdz/download?target=../../etc/passwd")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# upload
# ---------------------------------------------------------------------------


def test_upload_needs_a_session(sample_config):
    from app.web import server

    app = server.create_app(sample_config)
    resp = TestClient(app).post("/api/configs/serverdz/upload", content=b"x")
    assert resp.status_code == 401


def test_an_upload_replaces_the_file(client, sample_config):
    path = sample_config.server_cfg_path
    resp = client.post("/api/configs/serverdz/upload", content=cfg('hostname = "new";'))
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == path.name
    assert path.read_text(encoding="utf-8") == 'hostname = "new";\n' + MISSIONS


def test_an_upload_target_is_matched_against_the_listing_too(client, sample_config, tmp_path):
    """Same rule as the download: `target` names a listed file, never a path.
    Pinned for plans/57 E1."""
    before = sample_config.server_cfg_path.read_bytes()
    outside = tmp_path / "outside.cfg"
    for target in ("../outside.cfg", str(outside)):
        resp = client.post(
            "/api/configs/serverdz/upload", params={"target": target}, content=cfg('hostname = "x";'),
        )
        assert resp.status_code == 400, target
    assert sample_config.server_cfg_path.read_bytes() == before
    assert not outside.exists()


def test_a_malformed_upload_writes_nothing(client, sample_config, tmp_mission_dir):
    """The whole reason to route an upload through the editor instead of
    treating it as a file drop: it is validated before anything is written."""
    from app.web import editors

    # get_globals_path only resolves an existing file, so it has to be there
    # before the target list is asked for.
    path = tmp_mission_dir / "db" / "globals.xml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(GOOD_XML, encoding="utf-8")

    spec = editors.get_editor("globals")
    assert spec is not None and spec.raw_targets is not None
    assert [t.path for t in spec.raw_targets(sample_config)] == [path]

    resp = client.post("/api/configs/globals/upload", content=BAD_XML.encode())
    assert resp.status_code == 400
    assert path.read_text(encoding="utf-8") == GOOD_XML


def test_the_replaced_version_is_snapshotted_first(client, sample_config, monkeypatch):
    """A bad round trip has to be undoable from Analysis → Change history."""
    from app.web.editors import _raw

    seen = []
    monkeypatch.setattr(_raw, "snapshot", lambda path, trigger="": seen.append(path))

    client.post("/api/configs/serverdz/upload", content=cfg('hostname = "x";'))
    assert seen and seen[0] == sample_config.server_cfg_path


def test_a_byte_order_mark_is_dropped(client, sample_config):
    """The file has probably been through an FTP client and Notepad — which is
    exactly the journey this feature is for, and Notepad adds a BOM."""
    client.post(
        "/api/configs/serverdz/upload", content=b"\xef\xbb\xbf" + cfg('hostname = "bom";')
    )
    text = sample_config.server_cfg_path.read_text(encoding="utf-8")
    assert text.startswith("hostname")


def test_a_binary_file_is_refused_in_words(client, sample_config):
    before = sample_config.server_cfg_path.read_text(encoding="utf-8")
    resp = client.post("/api/configs/serverdz/upload", content=b"\x00\x81\xfe\xff")
    assert resp.status_code == 400
    assert "UTF-8" in resp.json()["detail"]
    assert sample_config.server_cfg_path.read_text(encoding="utf-8") == before


def test_an_empty_upload_is_refused(client, sample_config):
    """Almost always a mis-click or a failed FTP fetch, never an intention —
    and it would silently blank a config file."""
    before = sample_config.server_cfg_path.read_text(encoding="utf-8")
    assert client.post("/api/configs/serverdz/upload", content=b"   \n").status_code == 400
    assert sample_config.server_cfg_path.read_text(encoding="utf-8") == before


def test_an_oversized_upload_is_refused_without_buffering_it(client, monkeypatch):
    from app.web.routers import configs

    monkeypatch.setattr(configs, "MAX_UPLOAD_BYTES", 64)
    resp = client.post("/api/configs/serverdz/upload", content=b"x" * 500)
    assert resp.status_code == 413
    assert "larger than" in resp.json()["detail"]


def test_uploading_to_a_bespoke_page_is_still_refused(client):
    """The raw PUT 404s for those on purpose — writing their files is their
    own page's job — and an upload must not be a way around it."""
    assert client.post("/api/configs/modxml/upload", content=b"x").status_code == 404
    assert client.post("/api/configs/runcommand/upload", content=b"x").status_code == 404


def test_an_unknown_target_is_refused(client, sample_config):
    before = sample_config.server_cfg_path.read_text(encoding="utf-8")
    resp = client.post(
        "/api/configs/serverdz/upload?target=nope.cfg", content=cfg('hostname = "x";')
    )
    assert resp.status_code == 400
    assert sample_config.server_cfg_path.read_text(encoding="utf-8") == before


# ── plans/51 C: a .cfg is checked before it replaces the live file ────────


@pytest.mark.parametrize("content, words", [
    (b"garbage garbage", "Line 1"),                              # N12's own upload
    (b'hostname = "x"\nmaxPlayers = 60;\n' + MISSIONS.encode(), "no ';'"),
    (b'hostname = "x";\nclass Missions { class DayZ { template = "a.b"; };', "never closed"),
    (b'hostname = "x";\n', "No mission is set"),
])
def test_a_broken_server_cfg_upload_is_refused_and_writes_nothing(client, sample_config, content, words):
    before = sample_config.server_cfg_path.read_text(encoding="utf-8")
    resp = client.post("/api/configs/serverdz/upload", content=content)
    assert resp.status_code == 400
    assert words in resp.json()["detail"]
    assert sample_config.server_cfg_path.read_text(encoding="utf-8") == before


def test_the_raw_tab_gets_the_same_check(client, sample_config):
    before = sample_config.server_cfg_path.read_text(encoding="utf-8")
    resp = client.put("/api/configs/serverdz/raw", json={"content": "garbage garbage"})
    assert resp.status_code == 400
    assert sample_config.server_cfg_path.read_text(encoding="utf-8") == before


@pytest.mark.parametrize("body", [{}, {"content": None}, {"content": 7}])
def test_a_raw_save_without_content_writes_nothing(client, sample_config, server_stopped, body):
    """beserver_x64.cfg has no line a missing body breaks, so a request
    without content wrote it empty."""
    be = sample_config.beserver_cfg_path
    before = be.read_bytes()
    resp = client.put("/api/configs/battleye/raw", json={"target": be.name, **body})
    assert resp.status_code == 422
    assert be.read_bytes() == before


def test_raw_text_that_cannot_be_encoded_leaves_the_file_as_it_was(client, sample_config):
    """The write used to empty the file first, so the 400 came with a serverDZ.cfg of 0 bytes."""
    before = sample_config.server_cfg_path.read_text(encoding="utf-8")
    body = json.dumps({"content": before + "// half an emoji: \ud83d\n"})  # escaped, as a browser sends it
    resp = client.put("/api/configs/serverdz/raw", content=body, headers={"Content-Type": "application/json"})
    assert resp.status_code == 400
    assert sample_config.server_cfg_path.read_text(encoding="utf-8") == before


def test_server_cfg_is_checked_through_the_battleye_editor_too(client, sample_config):
    """serverDZ.cfg is a raw target of two editors; the check is on the file."""
    before = sample_config.server_cfg_path.read_text(encoding="utf-8")
    resp = client.post(
        f"/api/configs/battleye/upload?target={sample_config.server_cfg_path.name}",
        content=b"garbage garbage",
    )
    assert resp.status_code == 400
    assert sample_config.server_cfg_path.read_text(encoding="utf-8") == before


def test_a_server_cfg_uploaded_as_the_battleye_file_is_refused(client, sample_config):
    be = sample_config.beserver_cfg_path
    before = be.read_text(encoding="utf-8")
    resp = client.post(
        f"/api/configs/battleye/upload?target={be.name}", content=cfg('hostname = "x";')
    )
    assert resp.status_code == 400
    assert "Key value" in resp.json()["detail"]
    assert be.read_text(encoding="utf-8") == before



# ---------------------------------------------------------------------------
# beserver_x64.cfg while the server runs (measured in 2026 Q3): BattlEye has
# renamed it to its own active copy and puts that back at the next stop, so a
# file written now would be lost. `battleye_running` is that shape.
# ---------------------------------------------------------------------------


def _assert_untouched(sample_config, active, before: str) -> None:
    from app.shared.config_versions import load_index

    assert not sample_config.beserver_cfg_path.exists()
    assert active.read_text() == before
    assert load_index(sample_config.paths.versions_dir).entries == []


def test_a_battleye_upload_is_refused_while_running(client, sample_config, battleye_running):
    before = battleye_running.read_text()
    resp = client.post(
        f"/api/configs/battleye/upload?target={sample_config.beserver_cfg_path.name}",
        content=b"RConPassword uploaded\nRConPort 2310\n",
    )
    assert resp.status_code == 409
    assert "Stop the server first." in resp.json()["detail"]
    _assert_untouched(sample_config, battleye_running, before)


def test_a_battleye_raw_save_is_refused_while_running(client, sample_config, battleye_running):
    before = battleye_running.read_text()
    resp = client.put(
        "/api/configs/battleye/raw",
        json={"target": sample_config.beserver_cfg_path.name, "content": "RConPassword x\n"},
    )
    assert resp.status_code == 409
    _assert_untouched(sample_config, battleye_running, before)


def test_the_refusal_comes_before_the_content_check(client, sample_config, battleye_running):
    """A bad file gets the answer that applies whatever it holds."""
    resp = client.post(
        f"/api/configs/battleye/upload?target={sample_config.beserver_cfg_path.name}",
        content=b"garbage = 1;\n",
    )
    assert resp.status_code == 409


def test_serverdz_through_the_battleye_editor_is_not_refused(client, sample_config, battleye_running):
    """Only BattlEye's file is swapped out while running; serverDZ.cfg is not."""
    new = cfg('hostname = "Renamed";')
    resp = client.post(
        f"/api/configs/battleye/upload?target={sample_config.server_cfg_path.name}", content=new
    )
    assert resp.status_code == 200, resp.text
    assert sample_config.server_cfg_path.read_bytes() == new


def test_a_battleye_download_hands_over_battleyes_copy(client, sample_config, battleye_running):
    resp = client.get(
        f"/api/configs/battleye/download?target={sample_config.beserver_cfg_path.name}"
    )
    assert resp.status_code == 200
    assert resp.text == battleye_running.read_text()
    assert sample_config.beserver_cfg_path.name in resp.headers["content-disposition"]


def test_a_battleye_upload_when_stopped_is_unchanged(client, sample_config, server_stopped):
    new = b"RConPassword uploaded\nRConPort 2310\n"
    resp = client.post(
        f"/api/configs/battleye/upload?target={sample_config.beserver_cfg_path.name}", content=new
    )
    assert resp.status_code == 200, resp.text
    assert sample_config.beserver_cfg_path.read_bytes() == new


def _battleye_raw_save(client, sample_config, how: str, text: str):
    name = sample_config.beserver_cfg_path.name
    if how == "upload":
        return client.post(f"/api/configs/battleye/upload?target={name}", content=text.encode())
    return client.put("/api/configs/battleye/raw", json={"target": name, "content": text})


@pytest.mark.parametrize("how", ["upload", "raw"])
def test_a_battleye_raw_save_sets_the_password_zsm_dials_with(client, sample_config, server_stopped, how):
    """As the BattlEye form does: a file that changed only on disk left zsm
    dialling with the password it replaced."""
    from app.shared.config import DayzConfig

    resp = _battleye_raw_save(client, sample_config, how, "RConPassword uploaded\nRConPort 2310\n")
    assert resp.status_code == 200, resp.text
    live = client.app.state.ctx.config
    assert (live.rcon_password, live.rcon_port) == ("uploaded", 2310)
    saved = DayzConfig.load()
    assert (saved.rcon_password, saved.rcon_port) == ("uploaded", 2310)


@pytest.mark.parametrize("how", ["upload", "raw"])
def test_a_refused_battleye_raw_save_keeps_zsms_password(client, sample_config, server_stopped, how):
    resp = _battleye_raw_save(client, sample_config, how, 'hostname = "x";\nRConPassword nope\n')
    assert resp.status_code == 400
    assert client.app.state.ctx.config.rcon_password == ""


@pytest.mark.parametrize("how", ["upload", "raw"])
def test_a_battleye_raw_save_keeps_a_remote_hosts_password(client, sample_config, server_stopped, how):
    state = client.app.state.ctx
    state.config.rcon_host = "203.0.113.7"
    state.config.rcon_password = "the-remote-password"
    state.config.save()

    resp = _battleye_raw_save(client, sample_config, how, "RConPassword local\n")
    assert resp.status_code == 200, resp.text
    assert state.config.rcon_password == "the-remote-password"


def test_a_cfg_saved_in_another_encoding_does_not_take_the_panel_down(client, sample_config):
    """An ANSI editor on Windows, then SFTP. Every route that reads one value
    from serverDZ.cfg answered 500, the status poll among them."""
    path = sample_config.server_cfg_path
    data = path.read_text(encoding="utf-8").replace("Test Server", "Café des Survivants").encode("cp1252")
    path.write_bytes(data)

    for url in ("/api/status", "/api/setup-state", "/api/configs", "/api/maps", "/api/configs/serverdz"):
        assert client.get(url).status_code == 200, url
    raw = client.get("/api/configs/serverdz/raw")
    assert raw.status_code == 200 and "Caf\ufffd des Survivants" in raw.json()["content"]

    resp = client.put("/api/configs/serverdz", json={"values": {"hostname": "New name"}})
    assert resp.status_code == 400
    assert resp.json()["detail"].startswith("serverDZ.cfg is not saved as UTF-8")
    assert path.read_bytes() == data

    from app.commands import doctor

    label, ok, detail = doctor._check_mission(sample_config)
    assert (label, ok) == ("Mission folder", False) and "not saved as UTF-8" in detail
    sample_config.paths.mods_json.parent.mkdir(parents=True, exist_ok=True)
    sample_config.paths.mods_json.write_bytes('{"version": 1, "mods": [{"name": "@Café"}]}'.encode("cp1252"))
    label, ok, detail = doctor._check_mod_list(sample_config)
    assert (label, ok) == ("Mod list", False) and "is not UTF-8" in detail

