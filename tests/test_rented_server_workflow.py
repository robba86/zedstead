"""Can zsm be used against a rented server's files alone? (TODO F5, G1c)

The migrating-from-a-host docs page tells someone on Nitrado/GTX to pull their
mission folder down over FTP, point zsm at it and edit it there. That claim
is worth exactly as much as this file: it is the only thing standing between
the guide and an install step nobody can complete.

So the fixture here is **deliberately impoverished** — the files an FTP
download actually gives you and nothing else. No `DayZServer` binary, no
SteamCMD, no workshop cache, no `profiles/`. If a future change starts
requiring any of those to open an editor, the guide becomes wrong and one of
these fails.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

PASSWORD = "testpass123"

TYPES_XML = (
    "<types>\n"
    '  <type name="AKM"><nominal>10</nominal><min>5</min><lifetime>3600</lifetime>'
    "<restock>0</restock><quantmin>-1</quantmin><quantmax>-1</quantmax><cost>100</cost>"
    '<flags count_in_cargo="0" count_in_hoarder="0" count_in_map="1" count_in_player="0"'
    ' crafted="0" deloot="0"/><category name="weapons"/></type>\n'
    "</types>\n"
)
GLOBALS_XML = '<variables><var name="AnimalMaxCount" type="0" value="200"/></variables>\n'


@pytest.fixture()
def rented(tmp_path, monkeypatch):
    """What an FTP client gives you off a rented server, and nothing more."""
    from app.shared.config import DayzConfig

    config_dir = tmp_path / "zsm"
    config_dir.mkdir()
    for mod in ("app.shared.paths", "app.shared.config"):
        monkeypatch.setattr(f"{mod}.CONFIG_DIR", config_dir)
        monkeypatch.setattr(f"{mod}.CONFIG_FILE", config_dir / "config.json")

    root = tmp_path / "from-the-host"
    mission = root / "mpmissions" / "dayzOffline.chernarusplus"
    (mission / "db").mkdir(parents=True)
    (root / "serverDZ.cfg").write_text(
        'hostname = "Rented";\nclass Missions { class DayZ { template = "dayzOffline.chernarusplus"; }; };\n',
        encoding="utf-8",
    )
    (mission / "db" / "types.xml").write_text(TYPES_XML, encoding="utf-8")
    (mission / "db" / "globals.xml").write_text(GLOBALS_XML, encoding="utf-8")
    (mission / "db" / "economy.xml").write_text("<economy/>\n", encoding="utf-8")
    (mission / "cfgeconomycore.xml").write_text(
        '<economycore><classes/><defaults/><economy/>'
        '<ce folder="db"><file name="types.xml" type="types"/></ce></economycore>\n',
        encoding="utf-8",
    )

    return DayzConfig(
        server_dir=str(root),
        config_path="serverDZ.cfg",
        profiles_path="profiles",
        battleye_path="battleye",
    )


def test_there_is_deliberately_no_server_installed(rented):
    """If this ever starts passing by accident the rest proves nothing."""
    from app.commands.doctor import server_is_installed

    assert server_is_installed(rented) is False
    assert not (rented.server_dir_path / "DayZServer").exists()


def test_the_mission_editors_open_without_a_server_binary(rented):
    """The guide's central claim. Named editors rather than a count, so the
    failure says which one stopped working."""
    from app.web import editors

    opens = set()
    for spec in editors.list_editors():
        if not spec.raw_targets:
            continue
        if spec.raw_targets(rented):
            opens.add(spec.id)

    for editor_id in ("serverdz", "globals", "economy", "economycore"):
        assert editor_id in opens, editor_id


def test_a_mission_file_round_trips_through_the_editor(rented):
    """Read it, change it, read it back — with nothing installed."""
    from app.web import server
    from app.web.auth import hash_password

    rented.web_password_hash = hash_password(PASSWORD)
    client = TestClient(server.create_app(rented))
    client.post("/api/auth/login", json={"password": PASSWORD})

    body = client.get("/api/configs/globals/raw").json()
    assert "AnimalMaxCount" in body["content"]

    edited = body["content"].replace("200", "400")
    assert client.put("/api/configs/globals/raw", json={"content": edited}).status_code == 200
    assert "400" in client.get("/api/configs/globals/raw").json()["content"]


def test_the_economy_analysis_reads_a_rented_types_xml(rented):
    """The single best reason to bother: the analysis a host panel has no
    equivalent of, run on files that never left their server."""
    from app.ops.economy_analyzer import analyze_economy
    from app.xml.types_xml import parse_types_file

    parsed = parse_types_file(
        rented.server_dir_path
        / "mpmissions"
        / "dayzOffline.chernarusplus"
        / "db"
        / "types.xml"
    )
    report = analyze_economy(parsed.entries)
    assert report.items_analyzed == 1
    assert report.overall_score >= 0


def test_a_bundle_carries_the_mission_back(rented):
    """`bundle export` is the migration mechanism the guide points at, and it
    has to work from a directory with no server in it."""
    from app.ops.bundle_ops import collect_export_manifest

    manifest = collect_export_manifest(rented)
    names = [f.arc_name for f in manifest.included]
    assert any("serverDZ.cfg" in n for n in names)
    assert any("types.xml" in n for n in names), names
