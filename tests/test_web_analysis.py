"""Tests for /api/analysis/*, /api/changes*, /api/versions*, /api/metrics/*,
/api/a2s (app/web/routers/analysis.py)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

PASSWORD = "testpass123"

TYPES_XML = """<?xml version="1.0" encoding="UTF-8"?>
<types>
    <type name="AK74">
        <nominal>5</nominal>
        <lifetime>3600</lifetime>
        <restock>0</restock>
        <min>2</min>
        <quantmin>-1</quantmin>
        <quantmax>-1</quantmax>
        <cost>100</cost>
        <flags count_in_cargo="0" count_in_hoarder="0" count_in_map="1" count_in_player="0" crafted="0" deloot="0"/>
        <category name="weapons"/>
        <usage name="Military"/>
        <value name="Tier2"/>
    </type>
</types>
"""

EVENTS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<events>
    <event name="StaticWreck">
        <nominal>5</nominal>
        <min>3</min>
        <max>5</max>
        <lifetime>3600</lifetime>
        <restock>0</restock>
        <saferadius>100</saferadius>
        <distanceradius>500</distanceradius>
        <cleanupradius>1000</cleanupradius>
        <flags deletable="0" init_random="1" remove_damaged="0"/>
        <position>fixed</position>
        <limit>mixed</limit>
        <active>1</active>
    </event>
</events>
"""


@pytest.fixture()
def client(sample_config):
    from app.web import server
    from app.web.auth import hash_password

    sample_config.web_password_hash = hash_password(PASSWORD)
    app = server.create_app(sample_config)
    tclient = TestClient(app)
    tclient.post("/api/auth/login", json={"password": PASSWORD})
    return tclient


def test_requires_auth(sample_config):
    from app.web import server

    app = server.create_app(sample_config)
    tclient = TestClient(app)
    assert tclient.get("/api/analysis/economy").status_code == 401


def test_economy_without_mission_409(client: TestClient):
    resp = client.get("/api/analysis/economy")
    assert resp.status_code == 409


def test_economy_analysis(client: TestClient, tmp_mission_dir):
    (tmp_mission_dir / "db" / "types.xml").write_text(TYPES_XML, encoding="utf-8")

    resp = client.get("/api/analysis/economy")
    assert resp.status_code == 200
    body = resp.json()
    assert body["items_analyzed"] == 1
    assert "item_scores" in body
    assert body["item_scores"][0]["classname"] == "AK74"
    # The file that holds it, for the row's link to the Types page.
    assert body["item_scores"][0]["file"] == str(tmp_mission_dir / "db" / "types.xml")


def test_collision_analysis(client: TestClient, tmp_mission_dir):
    (tmp_mission_dir / "events.xml").write_text(EVENTS_XML, encoding="utf-8")

    resp = client.get("/api/analysis/collisions")
    assert resp.status_code == 200
    body = resp.json()
    assert "collisions" in body


def test_changes_no_snapshot_yet(client: TestClient):
    resp = client.get("/api/changes")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False


def test_changes_descriptions_are_stripped_of_console_markup(client: TestClient, monkeypatch):
    """`changeset_ops` builds descriptions for a Rich console; the browser renders
    them as plain text, so the API must not ship raw `[...]` tags."""
    from app.ops import changeset_ops

    report = changeset_ops.ChangeReport(
        snapshot_time="2026-07-30T00:00:00",
        changes=[
            changeset_ops.ChangeItem("files_new", "  types.xml [green](new, 4.0 KB)[/green]"),
            changeset_ops.ChangeItem(
                "files_detail", "      [dim]... and 37 more — full list: /tmp/views/types-added.txt[/dim]"
            ),
        ],
        unchanged_count=2,
    )
    monkeypatch.setattr(changeset_ops, "compute_changes", lambda config: report)

    body = client.get("/api/changes").json()
    assert body["available"] is True
    descriptions = [c["description"] for c in body["changes"]]
    assert descriptions == [
        "types.xml (new, 4.0 KB)",
        "... and 37 more — full list: /tmp/views/types-added.txt",
    ]
    assert body["unchanged_count"] == 2


def test_baseline_set_and_get(client: TestClient):
    resp = client.post("/api/changes/baseline")
    assert resp.status_code == 200

    resp = client.get("/api/changes/baseline")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["unchanged_count"] >= 0

    resp = client.delete("/api/changes/baseline")
    assert resp.status_code == 200

    resp = client.get("/api/changes/baseline")
    assert resp.json()["available"] is False


def test_versions_empty(client: TestClient):
    resp = client.get("/api/versions")
    assert resp.status_code == 200
    assert resp.json() == {"files": []}


def _save(path: Path, text: str, encoding: str = "utf-8") -> None:
    """A write as Zedstead makes it (atomic_write_text), so config_versions
    records it and never reads it as a change made outside Zedstead."""
    from app.shared.json_utils import atomic_write_text

    atomic_write_text(path, text)


def test_versions_for_file_after_snapshot(client: TestClient, tmp_mission_dir):
    from app.shared.config_versions import snapshot

    globals_path = tmp_mission_dir / "globals.xml"
    _save(globals_path, "<globals/>", encoding="utf-8")
    snapshot(globals_path, trigger="test")
    _save(globals_path, "<globals><var name=\"x\" type=\"0\" value=\"1\"/></globals>", encoding="utf-8")
    snapshot(globals_path, trigger="test")

    resp = client.get("/api/versions", params={"file": "globals.xml"})
    assert resp.status_code == 200
    entries = resp.json()["entries"]
    assert len(entries) == 2

    # `identical` drives the UI's "hide zero-diff versions" filter: the newest
    # version matches the live file, the older one doesn't.
    by_version = {e["version"]: e for e in entries}
    assert by_version[2]["identical"] is True
    assert by_version[1]["identical"] is False

    resp = client.post(
        "/api/versions/restore",
        json={"file": "globals.xml", "version": entries[-1]["version"], "source_path": entries[-1]["source_path"]},
    )
    assert resp.status_code == 200
    assert resp.json()["restored"] is True


def test_restore_unknown_version_400(client: TestClient):
    resp = client.post("/api/versions/restore", json={"file": "globals.xml", "version": 99})
    assert resp.status_code == 400


def _beserver_version(sample_config) -> dict:
    """A stored version of beserver_x64.cfg, taken while the server was stopped."""
    from app.shared.config_versions import snapshot

    be = sample_config.beserver_cfg_path
    entry = snapshot(be, trigger="test")
    return {"file": be.name, "version": entry.version, "source_path": entry.source_path}


def test_restoring_the_battleye_cfg_is_refused_while_running(client, sample_config, monkeypatch):
    """BattlEye renames its active copy back over the cfg at the next stop
    (measured in 2026 Q3), so a restore made while it runs would be lost."""
    from tests.conftest import ACTIVE_BESERVER_CFG

    query = _beserver_version(sample_config)
    # Start the server: BattlEye takes the cfg as its active copy.
    active = sample_config.battleye_dir / ACTIVE_BESERVER_CFG
    sample_config.beserver_cfg_path.rename(active)
    monkeypatch.setattr(
        "app.server.service_control.is_server_process_alive", lambda config=None: True
    )

    resp = client.post("/api/versions/restore", json=query)
    assert resp.status_code == 409
    assert "Stop the server first." in resp.json()["detail"]
    assert not sample_config.beserver_cfg_path.exists()


def test_restoring_the_battleye_cfg_when_stopped_is_unchanged(client, sample_config, server_stopped):
    query = _beserver_version(sample_config)
    sample_config.beserver_cfg_path.write_text("RConPassword changed\n")

    resp = client.post("/api/versions/restore", json=query)
    assert resp.status_code == 200, resp.text
    assert "RConPassword mypass" in sample_config.beserver_cfg_path.read_text()


@pytest.fixture()
def beserver_version_then_running(sample_config, request) -> dict:
    """A version of beserver_x64.cfg taken while stopped (RConPort 2305), then
    the server started: ``battleye_running`` (RConPort 2306), requested here so
    it comes after the snapshot."""
    query = _beserver_version(sample_config)
    request.getfixturevalue("battleye_running")
    return query


def test_versions_list_compares_against_battleyes_copy_while_running(
    client, beserver_version_then_running
):
    """The cfg is missing while the server runs, which read as "identical" and
    hid every version of it from the list."""
    entries = client.get("/api/versions", params={"file": "beserver_x64.cfg"}).json()["entries"]
    assert [e["identical"] for e in entries] == [False]


def test_versions_diff_compares_against_battleyes_copy_while_running(
    client, beserver_version_then_running
):
    resp = client.get("/api/versions/diff", params=beserver_version_then_running)
    assert resp.status_code == 200, resp.text
    diff = resp.json()["diff"]
    assert "-RConPort 2305" in diff
    assert "+RConPort 2306" in diff
    # Labelled with the cfg's own name, not the active copy's.
    assert "+++ beserver_x64.cfg (current)" in diff


def test_metrics_summary_empty(client: TestClient):
    resp = client.get("/api/metrics/summary")
    assert resp.status_code == 200
    assert resp.json()["samples"] == 0


def test_metrics_recent_and_hourly_and_peaks(client: TestClient, sample_config):
    from app.monitoring.metrics_db import MetricSnapshot, init_db, record_snapshot

    db_path = sample_config.paths.metrics_db
    init_db(db_path)
    record_snapshot(
        MetricSnapshot(timestamp=__import__("time").time(), fps=30.0, memory_mb=4000.0, players=5, cpu_pct=20.0, uptime_hours=1.0),
        db_path=db_path,
    )

    resp = client.get("/api/metrics/recent")
    assert resp.status_code == 200
    assert len(resp.json()["snapshots"]) == 1

    resp = client.get("/api/metrics/hourly")
    assert resp.status_code == 200
    assert "hourly" in resp.json()

    resp = client.get("/api/metrics/peaks")
    assert resp.status_code == 200
    assert resp.json()["players_peak"] == 5


def test_a2s_query_offline(client: TestClient, monkeypatch):
    async def _fake_query_server(ip, port, timeout=3.0):
        from app.monitoring.a2s_monitor import ServerQueryResult

        return ServerQueryResult(online=False, error="Timeout - server not responding to queries")

    monkeypatch.setattr("app.monitoring.a2s_monitor.query_server", _fake_query_server)

    resp = client.get("/api/a2s")
    assert resp.status_code == 200
    assert resp.json()["online"] is False


# ---------------------------------------------------------------------------
# Versions: diff / candidates / manual snapshot / cleanup
# ---------------------------------------------------------------------------


def test_version_snapshot_and_diff(client, sample_config):
    from app.shared.config_versions import snapshot

    m_dir = (
        sample_config.server_dir_path / "mpmissions" / "dayzOffline.chernarusplus"
    )
    target = m_dir / "db" / "globals.xml"
    target.parent.mkdir(parents=True, exist_ok=True)
    _save(target, "<globals><var name='x' value='1'/></globals>")
    snapshot(target, trigger="test")
    _save(target, "<globals><var name='x' value='2'/></globals>")

    resp = client.get("/api/versions/diff", params={"file": "globals.xml", "version": 1})
    assert resp.status_code == 200
    assert "value='2'" in resp.json()["diff"] or 'value="2"' in resp.json()["diff"]

    # A non-empty diff must never be reported as identical (the UI hides those).
    entry = client.get("/api/versions", params={"file": "globals.xml"}).json()["entries"][0]
    assert entry["identical"] is False

    # Put the original content back — now it is identical again.
    _save(target, "<globals><var name='x' value='1'/></globals>")
    entry = client.get("/api/versions", params={"file": "globals.xml"}).json()["entries"][0]
    assert entry["identical"] is True


def test_version_candidates_and_manual_snapshot(client, sample_config):
    m_dir = (
        sample_config.server_dir_path / "mpmissions" / "dayzOffline.chernarusplus"
    )
    target = m_dir / "db" / "globals.xml"
    target.parent.mkdir(parents=True, exist_ok=True)
    _save(target, "<globals/>")

    files = client.get("/api/versions/candidates").json()["files"]
    rel = "db/globals.xml"
    assert rel in files

    resp = client.post("/api/versions/snapshot", json={"file": rel})
    assert resp.status_code == 200
    assert resp.json()["created"] is True

    # Unchanged file -> no new snapshot.
    resp = client.post("/api/versions/snapshot", json={"file": rel})
    assert resp.json()["created"] is False


def test_version_snapshot_unknown_file_400(client):
    resp = client.post("/api/versions/snapshot", json={"file": "../../etc/passwd"})
    assert resp.status_code == 400


def test_version_source_path_filters_the_index_and_is_never_a_path(client, sample_config, tmp_path):
    """Diff and restore read and write the path the index stored at snapshot
    time; the request's `source_path` only picks among entries (plans/57 E1)."""
    m_dir = sample_config.server_dir_path / "mpmissions" / "dayzOffline.chernarusplus"
    target = m_dir / "db" / "globals.xml"
    target.parent.mkdir(parents=True, exist_ok=True)
    _save(target, "<globals/>")
    assert client.post("/api/versions/snapshot", json={"file": "db/globals.xml"}).json()["created"]

    outside = tmp_path / "outside" / "globals.xml"
    outside.parent.mkdir()
    outside.write_text("<outside/>")
    query = {"file": "globals.xml", "version": 1, "source_path": str(outside)}

    assert client.get("/api/versions/diff", params={**query, "source_path": str(target)}).status_code == 200
    assert client.get("/api/versions/diff", params=query).status_code == 400
    assert client.post("/api/versions/restore", json=query).status_code == 400
    assert outside.read_text() == "<outside/>"


def test_version_cleanup(client, sample_config):
    from app.shared.config_versions import snapshot

    m_dir = (
        sample_config.server_dir_path / "mpmissions" / "dayzOffline.chernarusplus"
    )
    target = m_dir / "db" / "globals.xml"
    target.parent.mkdir(parents=True, exist_ok=True)
    for i in range(3):
        _save(target, f"<globals>{i}</globals>")
        snapshot(target, trigger="test")

    resp = client.post("/api/versions/cleanup", json={"keep": 1})
    assert resp.status_code == 200
    assert resp.json()["removed"] == 2

    assert client.post("/api/versions/cleanup", json={"keep": 0}).status_code == 422


def test_metrics_cleanup(client, monkeypatch):
    monkeypatch.setattr("app.monitoring.metrics_db.cleanup_old", lambda days, db_path: 7)
    resp = client.post("/api/metrics/cleanup", json={"retention_days": 14})
    assert resp.status_code == 200
    assert resp.json() == {"removed": 7, "retention_days": 14}

    assert client.post("/api/metrics/cleanup", json={"retention_days": 0}).status_code == 422


# ---------------------------------------------------------------------------
# Selective collision scans
# ---------------------------------------------------------------------------

EVENTSPAWNS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<eventposdef>
    <event name="StaticHeliCrash">
        <pos x="1000" z="2000" a="0"/>
        <pos x="1000" z="2000" a="0"/>
    </event>
    <event name="VehicleOffroad">
        <pos x="5000" z="6000" a="0"/>
    </event>
</eventposdef>
"""


def _mission_root_dir(sample_config):
    d = sample_config.server_dir_path / "mpmissions" / "dayzOffline.chernarusplus"
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_collision_scan_eventspawns_kind(client, sample_config):
    root = _mission_root_dir(sample_config)
    (root / "cfgeventspawns.xml").write_text(EVENTSPAWNS_XML)

    resp = client.get("/api/analysis/collisions", params={"kind": "eventspawns"})
    assert resp.status_code == 200
    body = resp.json()
    # The two identical StaticHeliCrash positions are an exact duplicate.
    assert any(c["kind"] == "exact_duplicate" for c in body["collisions"])
    assert body["map_editor_query"].startswith("collisions=eventspawns")

    # Type filter excludes the colliding event -> no collisions.
    resp = client.get(
        "/api/analysis/collisions",
        params={"kind": "eventspawns", "types": "VehicleOffroad"},
    )
    assert resp.json()["collisions"] == []
    assert "types=VehicleOffroad" in resp.json()["map_editor_query"]


def test_collision_scan_unknown_kind_422(client):
    assert client.get("/api/analysis/collisions", params={"kind": "bogus"}).status_code == 422


def test_collision_event_types(client, sample_config):
    root = _mission_root_dir(sample_config)
    (root / "cfgeventspawns.xml").write_text(EVENTSPAWNS_XML)
    resp = client.get("/api/analysis/collision-event-types")
    assert resp.status_code == 200
    assert resp.json()["types"] == ["StaticHeliCrash", "VehicleOffroad"]


# ── /api/analysis/logs ───────────────────────────────────────────────────


def _rpt_body(version: str = "1.29.163451", *extra: str) -> str:
    """A minimal but shaped-like-the-real-thing .RPT.

    The header block (Version / Exe timestamp / Current time) is written by the
    engine *before* it starts timestamping lines — the analyzer anchors on that,
    so a fixture that timestamps those lines would be testing a file DayZ never
    writes.
    """
    head = f"Exe timestamp: 2026/07/16 19:27:36\nCurrent time:  2026/07/16 20:14:08\nVersion {version}\n"
    return head + "".join(line if line.endswith("\n") else line + "\n" for line in extra)


def _write_rpt(sample_config, name: str, body: str | None = None):
    path = sample_config.server_dir_path / sample_config.profiles_path / name
    path.write_text(body if body is not None else _rpt_body(), encoding="utf-8")
    return path


def test_logs_lists_rpt_files_and_the_directory(client, sample_config):
    _write_rpt(sample_config, "DayZServer_2026-07-16.RPT")
    body = client.get("/api/analysis/logs").json()
    assert body["profiles_found"] is True
    assert body["dir"].endswith("profiles")
    assert [entry["name"] for entry in body["logs"]] == ["DayZServer_2026-07-16.RPT"]


def test_logs_report_defaults_to_the_newest_log(client, sample_config):
    _write_rpt(
        sample_config,
        "DayZServer_2026-07-16.RPT",
        _rpt_body(
            "1.29.163451",
            "20:14:29 !!! [CE][offlineDB] :: Type 'Nope' will be ignored. (Type does not exist. (Typo?))",
        ),
    )
    body = client.get("/api/analysis/logs/report").json()
    assert body["latest_log"] == "DayZServer_2026-07-16.RPT"
    assert body["run"]["version"] == "1.29.163451"
    assert [f["id"] for f in body["findings"] if f["id"] == "ce_type_missing"]


def test_logs_report_can_analyse_an_older_log(client, sample_config):
    _write_rpt(sample_config, "new.RPT")
    _write_rpt(sample_config, "old.RPT", _rpt_body("1.28.999"))
    body = client.get("/api/analysis/logs/report", params={"file": "old.RPT"}).json()
    assert body["log"]["name"] == "old.RPT"
    assert body["run"]["version"] == "1.28.999"


def test_an_older_log_is_compared_with_the_run_before_it(client, sample_config):
    """TraderPlus's first boot drops every vehicle once (plans/48 D); picked
    from the log list later, that boot must still read as the mod's."""
    from tests.test_log_analysis import before_traderplus_rpt, server_log

    _write_rpt(sample_config, "DayZServer_2026-09-24_12-24-47.RPT", before_traderplus_rpt())
    _write_rpt(sample_config, "DayZServer_2026-09-24_20-04-15.RPT", server_log("traderplus-1.29"))
    _write_rpt(sample_config, "DayZServer_2026-09-25_09-00-00.RPT")
    body = client.get(
        "/api/analysis/logs/report", params={"file": "DayZServer_2026-09-24_20-04-15.RPT"}
    ).json()
    lost = [f for f in body["findings"] if f["id"] == "storage_scripted_vars"]
    assert [(f["origin"], f["mod"], f["count"]) for f in lost] == [("mod", "@TraderPlus", 3)]


def test_logs_report_404s_on_an_unknown_file(client, sample_config):
    _write_rpt(sample_config, "new.RPT")
    assert client.get("/api/analysis/logs/report", params={"file": "../../etc/passwd"}).status_code == 404


def test_logs_report_is_well_formed_with_no_logs(client):
    body = client.get("/api/analysis/logs/report").json()
    assert body["latest_log"] is None
    assert body["findings"] == []


def test_economy_uses_the_stock_copy_as_baseline(client: TestClient, tmp_mission_dir, sample_config):
    """With the stock types.xml held (mission_stock), the report compares the
    live file with it, and an unchanged file is 100% like vanilla."""
    (tmp_mission_dir / "db" / "types.xml").write_text(TYPES_XML, encoding="utf-8")
    stock = sample_config.paths.mission_stock_dir / "stock" / tmp_mission_dir.name / "db" / "types.xml"
    stock.parent.mkdir(parents=True)
    stock.write_text(TYPES_XML, encoding="utf-8")

    body = client.get("/api/analysis/economy").json()
    assert body["vanilla"]["closeness"] == 1.0
    assert body["vanilla"]["vanilla_items"] == 1
    assert body["items_analyzed"] == 0  # AK74 is vanilla: no rule checks it


def test_economy_without_a_stock_copy_has_no_baseline(client: TestClient, tmp_mission_dir):
    (tmp_mission_dir / "db" / "types.xml").write_text(TYPES_XML, encoding="utf-8")
    assert client.get("/api/analysis/economy").json()["vanilla"] is None


def test_timeline_lists_changes_and_what_each_did(client, sample_config):
    """Analysis → Timeline: each saved version is one change, newest first,
    and its change-diff runs to the next version, not to the live file."""
    from app.shared.config_versions import snapshot

    m_dir = sample_config.server_dir_path / "mpmissions" / "dayzOffline.chernarusplus"
    target = m_dir / "db" / "globals.xml"
    target.parent.mkdir(parents=True, exist_ok=True)
    _save(target, "<globals><var name='x' value='1'/></globals>")
    snapshot(target, trigger="editor")  # v1: 1, before the write to 2
    _save(target, "<globals><var name='x' value='2'/></globals>")
    snapshot(target, trigger="preset_apply:custom:night")  # v2: 2, before the write to 3
    _save(target, "<globals><var name='x' value='3'/></globals>")

    body = client.get("/api/versions/timeline").json()
    assert [(e["version"], e["trigger"]) for e in body["events"]] == [(2, "preset_apply:custom:night"), (1, "editor")]
    assert "markers" in body

    first = client.get("/api/versions/change-diff", params={"file": "globals.xml", "version": 1}).json()["diff"]
    assert "value='1'" in first.replace('"', "'") and "value='2'" in first.replace('"', "'")
    assert "value='3'" not in first.replace('"', "'")
    newest = client.get("/api/versions/change-diff", params={"file": "globals.xml", "version": 2}).json()["diff"]
    assert "value='3'" in newest.replace('"', "'")


def test_snapshot_times_reach_the_browser_with_an_offset():
    """A start or baseline snapshot is stamped in naive local time. A browser
    reads a naive ISO time as its own local time, so the Change history drew
    "Server started" an hour off from a UTC container. The route adds the
    machine's offset; an aware or empty value passes through."""
    from datetime import datetime

    from app.web.routers.analysis import _with_offset

    sent = _with_offset("2026-11-22T22:59:02")
    assert datetime.fromisoformat(sent).tzinfo is not None
    assert datetime.fromisoformat(sent) == datetime(2026, 11, 22, 22, 59, 2).astimezone()
    assert _with_offset("2026-11-22T22:59:02+00:00") == "2026-11-22T22:59:02+00:00"
    assert _with_offset(None) is None
    assert _with_offset("not a time") == "not a time"


def test_timeline_pages_back_and_reads_one_file(client, sample_config):
    """Change history loads a page at a time ("more" says an older page
    exists), and one file's whole line on its own, so no saved version is out
    of reach behind the page size."""
    from app.shared.config_versions import snapshot

    m_dir = sample_config.server_dir_path / "mpmissions" / "dayzOffline.chernarusplus"
    a, b = m_dir / "db" / "globals.xml", m_dir / "db" / "events.xml"
    a.parent.mkdir(parents=True, exist_ok=True)
    for n in range(3):
        for f in (a, b):
            _save(f, f"<x n='{n}'/>")
            snapshot(f, trigger="editor")
    for f in (a, b):
        _save(f, "<x n='last'/>")

    first = client.get("/api/versions/timeline", params={"limit": 4}).json()
    older = client.get("/api/versions/timeline", params={"limit": 4, "offset": 4}).json()
    assert (len(first["events"]), first["more"], len(older["events"]), older["more"]) == (4, True, 2, False)
    seen = [(e["source_path"], e["version"]) for e in first["events"] + older["events"]]
    assert len(set(seen)) == 6
    one = client.get("/api/versions/timeline", params={"source_path": str(b)}).json()["events"]
    assert {e["source_path"] for e in one} == {str(b)} and len(one) == 3


def test_the_whole_log_downloads_by_name_and_newest_by_default(client, sample_config):
    import os
    import time

    _write_rpt(sample_config, "old.RPT", "old run\n")
    os.utime(_write_rpt(sample_config, "new.RPT", "new run\n"), (time.time() + 5,) * 2)
    newest = client.get("/api/analysis/logs/download")
    assert newest.status_code == 200 and newest.text == "new run\n"
    assert "new.RPT" in newest.headers["content-disposition"]
    assert client.get("/api/analysis/logs/download", params={"file": "old.RPT"}).text == "old run\n"


@pytest.mark.parametrize("name", ["nope.RPT", "../../config.json", "/etc/passwd"])
def test_a_log_download_only_serves_a_discovered_log(client, sample_config, name):
    _write_rpt(sample_config, "new.RPT")
    assert client.get("/api/analysis/logs/download", params={"file": name}).status_code == 404


from tests.test_web_players import MODERATOR, VIEWER, _as, roles_app  # noqa: E402,F401


def test_the_log_download_is_for_moderators(roles_app, sample_config):  # noqa: F811
    _write_rpt(sample_config, "new.RPT", "a player line\n")
    app = roles_app
    assert _as(app, *VIEWER).get("/api/analysis/logs/download").status_code == 403
    assert _as(app, *MODERATOR).get("/api/analysis/logs/download").text == "a player line\n"
