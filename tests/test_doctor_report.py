"""Tests for app/ops/doctor_report.py — structured Doctor report builder."""

from __future__ import annotations

from app.ops.doctor_report import build_doctor_report
from tests.conftest import write_test_ids, write_test_mods

_KEYS = {"checks", "summary", "conflicts", "server_info", "disc_usage",
         "log_analysis", "service_file"}


def test_build_doctor_report_shape(sample_config):
    report = build_doctor_report(sample_config)
    assert isinstance(report, dict)
    assert _KEYS <= report.keys()
    assert isinstance(report["checks"], list)
    assert report["summary"]["total"] == len(report["checks"])


def test_build_doctor_report_server_info_reflects_config(sample_config):
    report = build_doctor_report(sample_config)
    assert report["server_info"]["game_port"] == sample_config.server_port
    assert report["server_info"]["rcon_port"] == sample_config.rcon_port


def test_server_info_shows_the_query_port_an_instance_was_given(sample_config, capsys):
    """`instances add` gave exp 27116, which lives only in its config until
    `zsm setup` writes serverDZ.cfg. Doctor showed 27016 instead, in the CLI
    and the web, while the Instances page said 27116."""
    from app.commands import doctor

    sample_config.server_cfg_path.unlink(missing_ok=True)  # not installed yet
    sample_config.steam_query_port = 27116

    assert build_doctor_report(sample_config)["server_info"]["steam_query_port"] == 27116
    doctor._print_server_info(sample_config)
    assert "27116" in capsys.readouterr().out


def test_build_doctor_report_none_config_is_well_formed():
    # None resolves to whatever config the environment has (or none) — either
    # way the report must be a well-formed, empty-safe dict that never raises.
    report = build_doctor_report(None)
    assert isinstance(report, dict)
    assert _KEYS <= report.keys()
    assert isinstance(report["conflicts"]["issues"], list)
    assert isinstance(report["conflicts"]["dependencies"]["edges"], list)


def _check(report, label):
    return next(c for c in report["checks"] if c["label"] == label)


def _install_server_binary(config):
    """Mission + XML rows are only reported once the server is installed."""
    from app.shared.platform import IS_WINDOWS

    binary = "DayZServer_x64.exe" if IS_WINDOWS else "DayZServer"
    (config.server_dir_path / binary).write_text("", encoding="utf-8")


def test_every_check_carries_link_fields(sample_config):
    for check in build_doctor_report(sample_config)["checks"]:
        assert isinstance(check["links"], list)
        assert isinstance(check["link_phrases"], list)
        assert check["tooltip"] is None or isinstance(check["tooltip"], str)
        # A phrase link only works if the phrase is really in the detail text.
        for phrase in check["link_phrases"]:
            assert phrase["phrase"] in check["detail"]
            assert phrase["to"].startswith("/")


def test_config_check_names_the_loaded_file_and_links_to_settings(sample_config):
    from app.shared.paths import CONFIG_FILE

    check = _check(build_doctor_report(sample_config), "Apps config file")
    assert str(CONFIG_FILE) in check["detail"]
    assert {"label": "Settings", "to": "/settings"} in check["links"]


def test_mod_list_count_links_to_the_installed_tab(sample_config, tmp_path):
    write_test_mods(sample_config, ['@CF', '@TraderPlus'])
    check = _check(build_doctor_report(sample_config), "Mod list")
    assert check["link_phrases"] == [{"phrase": "2 mod(s) listed", "to": "/mods?tab=Installed"}]


def test_mission_folder_name_links_to_maps(sample_config):
    _install_server_binary(sample_config)
    check = _check(build_doctor_report(sample_config), "Mission folder")
    if check["ok"]:
        assert check["link_phrases"] == [{"phrase": check["detail"], "to": "/maps"}]
    else:
        assert {"label": "Change map", "to": "/maps"} in check["links"]


_TRADERPLUS_DETAIL = (
    "@CF dependency missing — add & install it; "
    "custom/traderplus-TraderPlus_types.xml missing — reset XML files; "
    "types XML not registered in cfgeconomycore.xml — reset XML files"
)


def test_traderplus_phrases_link_to_their_fix(sample_config):
    from app.ops.doctor_report import _links_for

    links, phrases = _links_for("TraderPlus", False, _TRADERPLUS_DETAIL)
    routes = {p["phrase"]: p["to"] for p in phrases}
    assert routes["cfgeconomycore.xml"] == "/configs/economycore"
    # Both of these are instructions, not destinations — they are buttons now.
    assert "reset XML files" not in routes
    assert "add & install it" not in routes
    assert {
        "label": "Re-configure XML",
        "to": "/mods?tab=Installed#reconfigure-xml",
    } in links


def test_traderplus_missing_xml_gets_a_reset_button():
    from app.ops.doctor_report import _actions_for

    actions = _actions_for("TraderPlus", False, _TRADERPLUS_DETAIL, None)
    assert actions == [{"kind": "reset_traderplus_xml", "label": "Reset XML files"}]
    # No missing XML in the detail ⇒ nothing to reset.
    assert _actions_for("TraderPlus", False, "no JSON configs in TraderPlusConfig", None) == []


def test_missing_server_gets_an_install_button(sample_config):
    """The Doctor row that says the server isn't there has to be able to put it
    there — the admin otherwise has to find the Update page and tick the steps."""
    report = build_doctor_report(sample_config)
    check = _check(report, "Server directory")

    assert check["ok"] is False
    assert {"kind": "install_server", "label": "Install server"} in check["actions"]
    # The rows that only restate "no server installed" are not reported at all.
    labels = [c["label"] for c in report["checks"]]
    assert "Mission folder" not in labels and "XML files" not in labels


def test_installed_server_has_no_install_button_and_reports_mission(sample_config):
    _install_server_binary(sample_config)
    report = build_doctor_report(sample_config)

    assert _check(report, "Server directory")["actions"] == []
    assert [c["label"] for c in report["checks"]].count("Mission folder") == 1


def test_missing_service_gets_an_install_button():
    from app.ops.doctor_report import _actions_for

    actions = _actions_for("Service", False, "Not installed: /etc/systemd/system/dayz-server.service", None)
    assert actions == [{"kind": "install_service", "label": "Install service"}]
    assert _actions_for("Service", True, "Installed (system) — enabled at boot", None) == []


def test_missing_dependency_check_links_to_conflicts_tab():
    from app.ops.doctor_report import _links_for

    links, phrases = _links_for("Mod dependencies", False, "1 missing")
    # The chip already goes where the count is itemised — don't link it twice.
    assert phrases == []
    assert {"label": "Check mods", "to": "/mods?tab=Conflicts"} in links


def test_missing_dependency_check_offers_install_buttons():
    from app.modmgr.mod_conflict import ConflictItem, ScanResult
    from app.ops.doctor_report import _actions_for

    scan = ScanResult(
        items=[
            ConflictItem(
                severity="error",
                category="missing_dep",
                message="@CF required by @TraderPlus",
                dep_id="1559212036",
                dep_name="@CF",
            ),
            ConflictItem(severity="error", category="missing_dep", message="x", dep_name="@Dabs"),
            # Duplicates collapse; other categories are ignored.
            ConflictItem(
                severity="error",
                category="missing_dep",
                message="@CF again",
                dep_id="1559212036",
                dep_name="@CF",
            ),
            ConflictItem(severity="warning", category="missing_bikey", message="y"),
        ]
    )
    actions = _actions_for("Mod dependencies", False, "2 missing", scan)
    assert [a["label"] for a in actions] == ["Install @CF", "Install @Dabs", "Install all"]
    assert actions[0]["mods"] == [{"name": "@CF", "id": "1559212036"}]
    assert actions[-1]["mods"] == [
        {"name": "@CF", "id": "1559212036"},
        {"name": "@Dabs", "id": ""},
    ]


def test_single_missing_dependency_has_no_install_all():
    from app.modmgr.mod_conflict import ConflictItem, ScanResult
    from app.ops.doctor_report import _actions_for

    scan = ScanResult(
        items=[
            ConflictItem(severity="error", category="missing_dep", message="x", dep_name="@CF")
        ]
    )
    assert [a["label"] for a in _actions_for("Mod dependencies", False, "1 missing", scan)] == [
        "Install @CF"
    ]


def test_xml_files_tooltip_lists_the_mission_files(sample_config, tmp_mission_dir):
    _install_server_binary(sample_config)
    (tmp_mission_dir / "types.xml").write_text("<types/>", encoding="utf-8")
    (tmp_mission_dir / "custom").mkdir()
    (tmp_mission_dir / "custom" / "mod-types.xml").write_text("<types/>", encoding="utf-8")

    check = _check(build_doctor_report(sample_config), "XML files")
    assert check["detail"] == "2 files OK"
    assert check["tooltip"].splitlines() == ["custom/mod-types.xml", "types.xml"]


def test_conflict_issues_expose_the_missing_dep(sample_config):
    # The web Doctor page links the dep name in the message to its mod page, so
    # the serialized issue has to name the dep as data, not only in prose.
    write_test_mods(sample_config, ['@TraderPlus'])
    write_test_ids({'@TraderPlus': '2458896948'})
    issues = build_doctor_report(sample_config)["conflicts"]["issues"]
    dep_issues = [i for i in issues if i["category"] == "missing_dep"]
    assert dep_issues
    assert dep_issues[0]["dep_name"] == "@CF"
    assert dep_issues[0]["dep_id"] == "1559212036"


def test_execstart_no_mods_warning_offers_the_install_action():
    from app.ops.doctor_report import _annotate_mod_validation

    items = [{"level": "warning", "message": "No mods found in ExecStart."}]
    _annotate_mod_validation(items, mods_listed=3)
    assert items[0]["action"] == "install_mods"
    assert "3 mod(s) in the mod list" in items[0]["message"]
    assert "links" not in items[0]


def test_execstart_no_mods_with_nothing_installed_links_to_mods():
    from app.ops.doctor_report import _annotate_mod_validation

    items = [{"level": "warning", "message": "No mods found in ExecStart."}]
    _annotate_mod_validation(items, mods_listed=0)
    assert "action" not in items[0]
    assert items[0]["message"].endswith("No mods installed yet.")
    assert items[0]["links"] == [{"label": "Manage mods", "to": "/mods?tab=Installed"}]


def test_other_mod_validation_messages_are_untouched():
    from app.ops.doctor_report import _annotate_mod_validation

    items = [{"level": "error", "message": "Missing mod folder: @CF"}]
    _annotate_mod_validation(items, mods_listed=2)
    assert items == [{"level": "error", "message": "Missing mod folder: @CF"}]


def test_log_analysis_reports_the_mods_txt_count(sample_config):
    write_test_mods(sample_config, ['@CF', '@TraderPlus'])
    assert build_doctor_report(sample_config)["log_analysis"]["mods_listed"] == 2


def test_build_doctor_report_conflicts_empty_safe(sample_config):
    conflicts = build_doctor_report(sample_config)["conflicts"]
    assert isinstance(conflicts["issues"], list)
    assert isinstance(conflicts["error_count"], int)
    assert isinstance(conflicts["dependencies"]["pbos_scanned"], int)


def test_conflict_issues_carry_a_fix(sample_config, monkeypatch):
    # Every rendered finding gets the same remediation payload the Mods page's
    # Conflicts tab attaches (app/ops/fix_hints.py) — the two tables share one
    # renderer, so a missing key there is a blank cell on both.
    from app.modmgr.mod_conflict import ConflictItem, ScanResult
    from app.ops import doctor_report

    scan = ScanResult(
        items=[
            ConflictItem(
                severity="warning",
                category="missing_bikey",
                message="Missing bikey(s): tp.bikey",
                mod_name="@TraderPlus",
            ),
            ConflictItem(severity="info", category="not_a_real_category", message="x"),
        ]
    )
    monkeypatch.setattr(
        "app.commands.doctor._run_all_checks", lambda config: ([("Mission folder", True, "")], scan)
    )
    issues = doctor_report.build_doctor_report(sample_config)["conflicts"]["issues"]
    assert [a["kind"] for a in issues[0]["fix"]["actions"]] == ["copy_bikeys", "install_mods"]
    # An unknown category renders as it always did rather than inventing advice.
    assert issues[1]["fix"] is None


def test_new_vps_checks_render_in_web_report(monkeypatch, sample_config):
    """The VPS probes (plans/23) reach the web Doctor page's flat list without
    a frontend change, and none of them trips the label-keyed helpers.

    `_links_for`, `_actions_for` and `_check_tooltips` are all keyed on the
    check label; the first two fall through their if/elif chains for an unknown
    label and the third is a `.get`, so a new label is inert by construction —
    asserted here rather than assumed.
    """
    from app.ops import vps_readiness

    monkeypatch.setattr(vps_readiness, "IS_WINDOWS", False)
    monkeypatch.setattr("app.commands.doctor.IS_WINDOWS", False)

    report = build_doctor_report(sample_config)
    by_label = {c["label"]: c for c in report["checks"]}

    assert {"CPU", "CPU steal", "RAM", "Swap", "Disk space", "Clock"} <= by_label.keys()
    for label in ("CPU", "CPU steal", "RAM", "Swap", "Disk space", "Clock", "Host"):
        row = by_label[label]
        assert row["links"] == []
        assert row["link_phrases"] == []
        assert row["actions"] == []
        assert row["tooltip"] is None
        assert row["severity"] in ("ok", "warning")


# --- the latest log's counts beside the checks' (test pass 1, F48) ----------

def test_log_summary_carries_the_latest_logs_own_counts(sample_config, monkeypatch):
    from app.ops import doctor_report

    monkeypatch.setattr(
        doctor_report,
        "_log_analysis",
        lambda config: {
            "latest_log": "DayZServer_2026-09-24_20-17-01.RPT",
            "summary": {"error": 1, "warning": 4, "notice": 1, "vanilla": 7, "mod": 2},
        },
    )
    report = build_doctor_report(sample_config)
    assert report["log_summary"] == {
        "error": 1,
        "warning": 4,
        "notice": 1,
        "latest_log": "DayZServer_2026-09-24_20-17-01.RPT",
    }
    # The health-check counts are untouched by the log.
    assert report["summary"]["error"] == sum(1 for c in report["checks"] if c["severity"] == "error")


def test_no_log_means_no_log_summary(sample_config, monkeypatch):
    from app.ops import doctor_report

    monkeypatch.setattr(doctor_report, "_log_analysis", lambda config: None)
    assert build_doctor_report(sample_config)["log_summary"] is None
    monkeypatch.setattr(
        doctor_report, "_log_analysis", lambda config: {"latest_log": None, "summary": {}}
    )
    assert build_doctor_report(sample_config)["log_summary"] is None


def test_fps_logging_phrases_link_to_the_fields():
    from app.ops.doctor_report import _links_for

    detail = (
        "Off: logAverageFps is not set in serverDZ.cfg and -doLogs is not in the Run command, "
        "so the server logs no FPS and the FPS history stays empty. "
        "To turn it on, set logAverageFps (seconds between log lines, for example 60) and add -doLogs"
    )
    # The row passes when the logging is off, and its fields are linked all the same.
    for ok in (True, False):
        _links, phrases = _links_for("FPS logging", ok, detail)
        routes = {p["phrase"]: p["to"] for p in phrases}
        assert routes == {
            "logAverageFps": "/configs/serverdz?q=logAverageFps",
            "-doLogs": "/configs/runcommand?q=doLogs",
        }
    assert _links_for("FPS logging", True, "Every 30s") == ([], [])


def test_install_service_waits_for_the_server(sample_config, monkeypatch):
    """With no server the Service row offered Install
    service, which answered "[Errno 2]". Install server installs the unit too."""
    monkeypatch.setattr(
        "app.commands.doctor._check_service",
        lambda config: ("Service", False, "Not installed: /etc/systemd/system/dayz-server.service"),
    )
    assert _check(build_doctor_report(sample_config), "Service")["actions"] == []
    _install_server_binary(sample_config)
    assert _check(build_doctor_report(sample_config), "Service")["actions"] == [
        {"kind": "install_service", "label": "Install service"}
    ]
