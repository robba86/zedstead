"""Tests for app/commands/doctor.py — _check_port classification."""

from __future__ import annotations

import pytest

from app.commands import doctor


class _FakeSock:
    """UDP socket stand-in. The first `fail_binds` bind() calls raise OSError,
    then subsequent binds succeed (models a transient probe collision)."""

    def __init__(self, fail_binds: int = 0) -> None:
        self._fail_binds = fail_binds

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def settimeout(self, _):
        pass

    def bind(self, _addr):
        if self._fail_binds > 0:
            self._fail_binds -= 1
            raise OSError("EADDRINUSE")


def _patch_socket(monkeypatch, *, fail_binds: int = 0):
    state = {"remaining": fail_binds}

    def factory(*_a, **_k):
        if state["remaining"] > 0:
            state["remaining"] -= 1
            return _FakeSock(fail_binds=1)  # this bind() fails
        return _FakeSock(fail_binds=0)  # this bind() succeeds

    monkeypatch.setattr(doctor.socket, "socket", factory)
    # keep the retry loop fast
    monkeypatch.setattr(doctor.time, "sleep", lambda _s: None)


def test_check_port_free(monkeypatch, sample_config):
    _patch_socket(monkeypatch, fail_binds=0)
    _label, ok, detail = doctor._check_port(sample_config)
    assert ok is True and "is free" in detail


def test_check_port_retries_transient_collision(monkeypatch, sample_config):
    # first attempt fails, second succeeds -> still "free" (cause #2 fix)
    _patch_socket(monkeypatch, fail_binds=1)
    _label, ok, detail = doctor._check_port(sample_config)
    assert ok is True and "is free" in detail


def test_check_port_in_use_by_systemd_server(monkeypatch, sample_config):
    _patch_socket(monkeypatch, fail_binds=999)  # every bind fails
    monkeypatch.setattr("app.server.service_control.is_server_running", lambda config=None: True)
    monkeypatch.setattr("app.server.service_control.is_dayz_process_running", lambda config=None: True)
    _label, ok, detail = doctor._check_port(sample_config)
    assert ok is True and "DayZ server" in detail


def test_check_port_in_use_by_orphan_dayz(monkeypatch, sample_config):
    # cause #1: process alive but not the systemd unit
    _patch_socket(monkeypatch, fail_binds=999)
    monkeypatch.setattr("app.server.service_control.is_server_running", lambda config=None: False)
    monkeypatch.setattr("app.server.service_control.is_dayz_process_running", lambda config=None: True)
    _label, ok, detail = doctor._check_port(sample_config)
    assert ok is True and "not managed by systemd" in detail


def test_check_port_held_by_another_instances_server(monkeypatch, sample_config):
    """A DayZ server from another directory holding this port is a conflict,
    not this instance's orphan (plans/44)."""
    _patch_socket(monkeypatch, fail_binds=999)
    monkeypatch.setattr("app.server.service_control.is_server_running", lambda config=None: False)
    monkeypatch.setattr("app.server.service_control.is_dayz_process_running",
                        lambda config=None: config is None)
    _label, ok, detail = doctor._check_port(sample_config)
    assert ok is False and "another directory" in detail


def test_check_port_in_use_by_foreign_process(monkeypatch, sample_config):
    # cause #3: the only case that should WARN
    _patch_socket(monkeypatch, fail_binds=999)
    monkeypatch.setattr("app.server.service_control.is_server_running", lambda config=None: False)
    monkeypatch.setattr("app.server.service_control.is_dayz_process_running", lambda config=None: False)
    _label, ok, detail = doctor._check_port(sample_config)
    assert ok is False and "another (non-DayZ) process" in detail


# ---------------------------------------------------------------------------
# _check_service_scope
# ---------------------------------------------------------------------------

import dataclasses

from app.server.unit_guard import ZSM_UNIT_MARKER
from tests.conftest import set_test_flags


def test_check_service_scope_system(monkeypatch, sample_config):
    monkeypatch.setattr("app.shared.platform.has_systemd", lambda: True)
    cfg = dataclasses.replace(sample_config, service_scope="system")
    result = doctor._check_service_scope(cfg)
    assert result is not None
    _label, ok, detail = result
    assert ok is True and "system scope" in detail


def test_check_service_scope_user_linger_disabled(monkeypatch, sample_config):
    monkeypatch.setattr("app.shared.platform.has_systemd", lambda: True)
    monkeypatch.setattr("app.shared.systemd_scope.linger_enabled", lambda *a, **k: False)
    monkeypatch.setattr("app.shared.systemd_scope._current_user", lambda: "bob")
    monkeypatch.setattr("app.shared.systemd_scope.SYSTEM_UNIT_DIR", tmp_missing())
    cfg = dataclasses.replace(sample_config, service_scope="user")
    _label, ok, detail = doctor._check_service_scope(cfg)
    assert ok is False
    assert "enable-linger bob" in detail


def test_check_service_scope_user_healthy(monkeypatch, sample_config):
    monkeypatch.setattr("app.shared.platform.has_systemd", lambda: True)
    monkeypatch.setattr("app.shared.systemd_scope.linger_enabled", lambda *a, **k: True)
    monkeypatch.setattr("app.shared.systemd_scope.user_bus_available", lambda: True)
    monkeypatch.setattr("app.shared.systemd_scope.SYSTEM_UNIT_DIR", tmp_missing())
    cfg = dataclasses.replace(sample_config, service_scope="user")
    _label, ok, detail = doctor._check_service_scope(cfg)
    assert ok is True
    assert "user scope" in detail


def _user_scope_doctor(monkeypatch, sample_config, tmp_path, *, installed: bool):
    """A user-scope config whose unit dir is under tmp_path, with every
    systemctl read answered by a stub. Returns (config, unit path)."""
    from app.shared.systemd_scope import SERVICE_NAME, SystemdScope

    monkeypatch.setattr(doctor, "IS_WINDOWS", False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(SystemdScope, "query", lambda self, *a, **k: "disabled\n")
    unit = tmp_path / "xdg" / "systemd" / "user" / SERVICE_NAME
    if installed:
        unit.parent.mkdir(parents=True)
        unit.write_text(f"[Unit]\nDescription=DayZ\n{ZSM_UNIT_MARKER}\n", encoding="utf-8")
    return dataclasses.replace(sample_config, service_scope="user"), unit


def test_the_service_row_names_a_unit_zsm_did_not_write(monkeypatch, sample_config, tmp_path):
    """An adopted server's hand-written unit under zsm's name (plans/78 step
    9): the row said "not enabled at boot. Start it at boot: zsm service
    enable", as if the unit were zsm's."""
    cfg, unit = _user_scope_doctor(monkeypatch, sample_config, tmp_path, installed=True)
    unit.write_text("[Unit]\nDescription=My DayZ\n", encoding="utf-8")

    _label, ok, detail = doctor._check_service(cfg)
    assert ok
    assert "your own unit, not Zedstead's" in detail
    assert "zsm service install --replace" in detail
    assert "service enable" not in detail


@pytest.mark.parametrize("installed", [True, False])
def test_the_service_row_and_the_unit_printout_agree_in_user_scope(
    monkeypatch, sample_config, tmp_path, installed
):
    """Measured on a VPS (2026 Q3): the Service row said "Installed (user)"
    and the same run ended with "dayz-server.service: not installed", because
    the printout still read /etc/systemd/system."""
    cfg, unit = _user_scope_doctor(monkeypatch, sample_config, tmp_path, installed=installed)

    _label, _ok, detail = doctor._check_service(cfg)
    monkeypatch.setattr(doctor.console, "width", 400)  # no wrap inside the tmp path
    with doctor.console.capture() as cap:
        doctor._print_service_files(cfg)
    printed = cap.get()

    if installed:
        assert detail == (
            "Installed (user) — not enabled at boot. Start it at boot: zsm service enable"
        )
        assert str(unit) in printed and "Description=DayZ" in printed
        assert f"{unit.name}: not installed" not in printed
    else:
        assert detail == f"Not installed: {unit}"
        assert f"not installed ({unit})" in printed


def _panel_doctor(monkeypatch, sample_config, tmp_path, *, active: str | None, enabled=True):
    """User scope under tmp_path; *active* None means no panel unit installed."""
    from app.server import panel_unit
    from app.shared.systemd_scope import SystemdScope

    monkeypatch.setattr(panel_unit, "IS_WINDOWS", False)
    monkeypatch.setattr(panel_unit, "has_systemd", lambda: True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    answers = {"is-enabled": "enabled" if enabled else "disabled", "is-active": active or ""}
    monkeypatch.setattr(SystemdScope, "query", lambda self, verb, *a, **k: answers[verb] + "\n")
    unit = tmp_path / "xdg" / "systemd" / "user" / panel_unit.PANEL_SERVICE_NAME
    if active is not None:
        unit.parent.mkdir(parents=True)
        unit.write_text("[Unit]\nDescription=zsm web panel\n", encoding="utf-8")
    return dataclasses.replace(sample_config, service_scope="user"), unit


def test_no_panel_unit_is_a_hint_not_a_warning(monkeypatch, sample_config, tmp_path):
    cfg, _unit = _panel_doctor(monkeypatch, sample_config, tmp_path, active=None)

    label, ok, detail = doctor._check_panel_service(cfg)

    assert label == "App service" and ok is True
    assert "zsm service web install" in detail


def test_a_running_panel_unit_reads_as_the_server_row_does(monkeypatch, sample_config, tmp_path):
    cfg, _unit = _panel_doctor(monkeypatch, sample_config, tmp_path, active="active")

    assert doctor._check_panel_service(cfg) == (
        "App service", True, "Installed (user) — running, enabled at boot",
    )


def test_a_failed_panel_unit_warns_and_names_its_log(monkeypatch, sample_config, tmp_path):
    cfg, _unit = _panel_doctor(monkeypatch, sample_config, tmp_path, active="failed", enabled=False)

    _label, ok, detail = doctor._check_panel_service(cfg)

    assert ok is False
    assert "journalctl --user -u zsm-web -f" in detail


def test_the_panel_unit_is_printed_beside_the_servers(monkeypatch, sample_config, tmp_path):
    cfg, unit = _panel_doctor(monkeypatch, sample_config, tmp_path, active="active")
    monkeypatch.setattr(doctor, "IS_WINDOWS", False)
    monkeypatch.setattr(doctor.console, "width", 400)

    with doctor.console.capture() as cap:
        doctor._print_service_files(cfg)
    printed = cap.get()

    assert str(unit) in printed and "Description=zsm web panel" in printed
    assert "dayz-server.service: not installed" in printed


def tmp_missing():
    """A path that does not contain the system unit (so no dual-install warning)."""
    from pathlib import Path
    return Path("/nonexistent-zsm-system-unit-dir")


def test_xml_check_reports_the_shared_friendly_hint(sample_config, tmp_mission_dir):
    """The hint table moved to `xml_text`; doctor's wording must not have."""
    (tmp_mission_dir / "db").mkdir(exist_ok=True)
    (tmp_mission_dir / "db" / "types.xml").write_text(
        "<types>\n<type>\n</typo>\n</types>", encoding="utf-8",
    )

    label, ok, detail = doctor._check_xml_files(sample_config)

    assert label == "XML files" and ok is False
    assert "types.xml:3 — closing tag doesn't match opening tag" in detail


# --- _check_os_deps ----------------------------------------------------------


def test_check_os_deps_windows_returns_none_before_the_server_is_installed(monkeypatch, sample_config):
    monkeypatch.setattr(doctor, "IS_WINDOWS", True)
    assert doctor._check_os_deps(sample_config) is None


def _installed_windows_server(monkeypatch, sample_config):
    from app.server import windows_runtime

    monkeypatch.setattr(doctor, "IS_WINDOWS", True)
    (sample_config.server_dir_path / "DayZServer_x64.exe").write_bytes(b"")
    return windows_runtime


def test_doctor_fails_on_windows_while_a_runtime_is_missing(monkeypatch, sample_config):
    """A fresh Windows 11 has neither runtime, the server exits the moment it
    starts, and doctor printed fifteen OK rows and exit 0 for it."""
    windows_runtime = _installed_windows_server(monkeypatch, sample_config)
    problem = "DayZServer_x64.exe cannot start on this computer: MSVCP140.dll is missing."
    monkeypatch.setattr(windows_runtime, "runtime_problem", lambda config: problem)

    assert doctor._check_os_deps(sample_config) == ("OS dependencies", False, problem)


def test_doctor_names_the_runtimes_it_found_on_windows(monkeypatch, sample_config):
    windows_runtime = _installed_windows_server(monkeypatch, sample_config)
    monkeypatch.setattr(windows_runtime, "runtime_problem", lambda config: None)

    label, ok, detail = doctor._check_os_deps(sample_config)

    assert (label, ok) == ("OS dependencies", True)
    assert "MSVCP140.dll" in detail and "XAPOFX1_5.dll" in detail


def test_missing_windows_scripts_pass_with_the_command_that_writes_them(monkeypatch, sample_config):
    """A start writes them itself, so their absence is not a failure."""
    monkeypatch.setattr(doctor, "IS_WINDOWS", True)

    label, ok, detail = doctor._check_service(sample_config)

    assert (label, ok) == ("Service", True)
    assert "start-server.bat" in detail and "zsm service generate" in detail


def test_a_missing_stop_script_alone_still_fails_on_windows(monkeypatch, sample_config):
    monkeypatch.setattr(doctor, "IS_WINDOWS", True)
    (sample_config.server_dir_path / "start-server.bat").write_text("echo hi", encoding="utf-8")

    assert doctor._check_service(sample_config) == ("Service", False, "Missing: stop-server.bat")


def test_check_os_deps_missing_binary_returns_none(monkeypatch, sample_config, tmp_path):
    """Nothing installed yet is not a finding."""
    monkeypatch.setattr(doctor, "IS_WINDOWS", False)
    import dataclasses

    cfg = dataclasses.replace(sample_config, server_dir=str(tmp_path / "empty"))
    assert doctor._check_os_deps(cfg) is None


def test_check_os_deps_reports_missing_lib(monkeypatch, sample_config):
    """ldd is the strong signal: a missing soname fails and names its package."""
    monkeypatch.setattr(doctor, "IS_WINDOWS", False)
    (sample_config.server_dir_path / "DayZServer").write_text("", encoding="utf-8")
    monkeypatch.setattr(
        "app.shared.os_packages.missing_shared_libs", lambda _b: ["libcurl.so.4"]
    )

    result = doctor._check_os_deps(sample_config)

    assert result is not None
    label, ok, detail = result
    assert (label, ok) == ("OS dependencies", False)
    assert "libcurl.so.4" in detail and "libcurl4" in detail


def test_check_os_deps_falls_back_to_packages(monkeypatch, sample_config):
    """With every soname resolved, the weaker package check still reports."""
    monkeypatch.setattr(doctor, "IS_WINDOWS", False)
    (sample_config.server_dir_path / "DayZServer").write_text("", encoding="utf-8")
    monkeypatch.setattr("app.shared.os_packages.missing_shared_libs", lambda _b: [])
    monkeypatch.setattr("app.shared.os_packages.missing_packages", lambda _n: ["libcurl4"])

    result = doctor._check_os_deps(sample_config)

    assert result is not None
    label, ok, detail = result
    assert (label, ok) == ("OS dependencies", False)
    assert "libcurl4" in detail


def test_check_os_deps_passes_when_clean(monkeypatch, sample_config):
    monkeypatch.setattr(doctor, "IS_WINDOWS", False)
    (sample_config.server_dir_path / "DayZServer").write_text("", encoding="utf-8")
    monkeypatch.setattr("app.shared.os_packages.missing_shared_libs", lambda _b: [])
    monkeypatch.setattr("app.shared.os_packages.missing_packages", lambda _n: [])
    monkeypatch.setattr(
        "app.shared.os_packages.effective_utf8_locale", lambda: "en_US.UTF-8"
    )

    result = doctor._check_os_deps(sample_config)

    assert result == (
        "OS dependencies",
        True,
        "all runtime libraries resolve; locale en_US.UTF-8",
    )


def test_check_os_deps_reports_a_missing_locale_without_failing(
    monkeypatch, sample_config
):
    """Plan 21 decision 4: report the locale, never assert on it.

    Whether DayZ needs en_US.UTF-8 specifically or merely *a* UTF-8 locale
    cannot be settled without server files, so a box with none must still pass
    — it just says so. See plans/28 C1.
    """
    monkeypatch.setattr(doctor, "IS_WINDOWS", False)
    (sample_config.server_dir_path / "DayZServer").write_text("", encoding="utf-8")
    monkeypatch.setattr("app.shared.os_packages.missing_shared_libs", lambda _b: [])
    monkeypatch.setattr("app.shared.os_packages.missing_packages", lambda _n: [])
    monkeypatch.setattr("app.shared.os_packages.effective_utf8_locale", lambda: None)

    label, ok, detail = doctor._check_os_deps(sample_config)

    assert (label, ok) == ("OS dependencies", True)
    assert "no UTF-8 locale found (C/POSIX only)" in detail


# --- _check_firewall (plans/22) ---------------------------------------------


def _fw_status(**kwargs):
    from app.ops.firewall_ops import FirewallStatus

    kwargs.setdefault("backend", "ufw")
    return FirewallStatus(**kwargs)


def test_check_firewall_zone_conflict_is_not_reported_as_needing_root(
    monkeypatch, sample_config
):
    """A port open only in a non-default zone sets `error`, and that branch used
    to render as "could not read firewalld status (needs root)" — untrue twice
    over: the table was read fine, and root is not the obstacle. `zone_conflict`
    carries the real reason; `error` stays set so every decision path is still
    conservative."""
    conflict = (
        "port(s) 2302/udp are open in zone trusted, not the default zone "
        "— cannot tell whether that zone covers the server's interface"
    )
    monkeypatch.setattr("app.ops.firewall_ops.detect_backend", lambda: "firewalld")
    monkeypatch.setattr(
        "app.ops.firewall_ops.firewall_status",
        lambda _c: _fw_status(
            backend="firewalld", active=True, error=conflict, zone_conflict=conflict
        ),
    )

    label, ok, detail = doctor._check_firewall(sample_config)

    assert (label, ok) == ("Firewall", True)
    assert "needs root" not in detail
    assert "zone trusted" in detail
    assert "get-active-zones" in detail


def test_check_firewall_unreadable_still_says_needs_root(monkeypatch, sample_config):
    """The other error class is unchanged: a refused read really is a root
    problem, and must not inherit the zone wording."""
    monkeypatch.setattr("app.ops.firewall_ops.detect_backend", lambda: "ufw")
    monkeypatch.setattr(
        "app.ops.firewall_ops.firewall_status",
        lambda _c: _fw_status(active=None, error="ERROR: You need to be root"),
    )

    label, ok, detail = doctor._check_firewall(sample_config)

    assert (label, ok) == ("Firewall", True)
    assert "needs root" in detail
    # The command it names has to be one that can answer: `zsm service
    # firewall --dry-run` reads the same table and says "unknown" too.
    assert detail.endswith("run: sudo ufw status verbose")


def test_check_firewall_warns_without_a_backend(monkeypatch, sample_config):
    """No backend used to return None — no row at all. Measured on a
    stock Rocky VPS image, which ships neither ufw nor firewalld: the
    box with a public IP and nothing filtering got silence, while the Ubuntu
    box beside it with an *inactive* ufw got a green "all ports reachable".
    The most exposed box was the one doctor said least about."""
    monkeypatch.setattr(doctor, "IS_WINDOWS", False)
    monkeypatch.setattr(doctor, "IS_DOCKER", False)
    monkeypatch.setattr("app.ops.firewall_ops.detect_backend", lambda: None)

    label, ok, detail = doctor._check_firewall(sample_config)

    assert (label, ok) == ("Firewall", False)  # False renders as WARN
    assert "No managed firewall is installed" in detail
    assert "reachable from the internet" in detail
    # ...and one actionable line, not a bare complaint.
    assert "install" in detail.lower()


def test_check_firewall_none_without_backend_in_a_container(monkeypatch, sample_config):
    """In a container the host owns the firewall, so the row is pure noise —
    the operator has nothing to act on inside."""
    monkeypatch.setattr(doctor, "IS_WINDOWS", False)
    monkeypatch.setattr(doctor, "IS_DOCKER", True)
    monkeypatch.setattr("app.ops.firewall_ops.detect_backend", lambda: None)

    assert doctor._check_firewall(sample_config) is None


def test_check_firewall_none_on_windows(monkeypatch, sample_config):
    monkeypatch.setattr(doctor, "IS_WINDOWS", True)

    assert doctor._check_firewall(sample_config) is None


def test_check_firewall_passes_when_inactive(monkeypatch, sample_config):
    monkeypatch.setattr(doctor, "IS_WINDOWS", False)
    monkeypatch.setattr("app.ops.firewall_ops.detect_backend", lambda: "ufw")
    monkeypatch.setattr(
        "app.ops.firewall_ops.firewall_status", lambda _c: _fw_status(active=False)
    )

    label, ok, detail = doctor._check_firewall(sample_config)

    assert (label, ok) == ("Firewall", True)
    assert "inactive" in detail


def test_check_firewall_passes_when_firewalld_output_unparseable(monkeypatch, sample_config):
    """plans/27 (D5): a firewalld `--list-all` with no `ports:` line now reports
    unknown instead of "nothing is open". Doctor must still pass on it — the row
    that used to FAIL on a correctly configured box is what trained users to
    ignore doctor."""
    monkeypatch.setattr(doctor, "IS_WINDOWS", False)
    monkeypatch.setattr("app.ops.firewall_ops.detect_backend", lambda: "firewalld")
    monkeypatch.setattr(
        "app.ops.firewall_ops.subprocess.run",
        lambda argv, *a, **k: type("P", (), {
            "returncode": 0,
            "stdout": "running\n" if "--state" in argv else "public (active)\n  target: default\n",
            "stderr": "",
        })(),
    )

    label, ok, detail = doctor._check_firewall(sample_config)

    assert (label, ok) == ("Firewall", True)
    assert "could not read" in detail


def test_check_firewall_passes_when_status_unreadable(monkeypatch, sample_config):
    """`ufw status` needs root; a refused read is not a closed port."""
    monkeypatch.setattr(doctor, "IS_WINDOWS", False)
    monkeypatch.setattr("app.ops.firewall_ops.detect_backend", lambda: "ufw")
    monkeypatch.setattr(
        "app.ops.firewall_ops.firewall_status",
        lambda _c: _fw_status(active=None, error="You need to be root"),
    )

    label, ok, detail = doctor._check_firewall(sample_config)

    assert (label, ok) == ("Firewall", True)
    assert "could not read" in detail


def test_check_firewall_fails_on_missing_game_port(monkeypatch, sample_config):
    monkeypatch.setattr(doctor, "IS_WINDOWS", False)
    sample_config.server_port = 2402
    monkeypatch.setattr("app.ops.firewall_ops.detect_backend", lambda: "ufw")
    monkeypatch.setattr(
        "app.ops.firewall_ops.firewall_status",
        lambda _c: _fw_status(active=True, open_udp={2403, 2404, 2405, 27016}),
    )

    label, ok, detail = doctor._check_firewall(sample_config)

    assert (label, ok) == ("Firewall", False)
    assert "2402/udp (game)" in detail
    assert "zsm service firewall" in detail


def test_check_firewall_passes_when_all_ports_open(monkeypatch, sample_config):
    monkeypatch.setattr(doctor, "IS_WINDOWS", False)
    sample_config.server_port = 2402
    monkeypatch.setattr("app.ops.firewall_ops.detect_backend", lambda: "ufw")
    monkeypatch.setattr(
        "app.ops.firewall_ops.firewall_status",
        lambda _c: _fw_status(active=True, open_udp={2402, 2403, 2404, 2405, 27016}),
    )

    label, ok, detail = doctor._check_firewall(sample_config)

    assert (label, ok) == ("Firewall", True)
    assert "2402/udp" in detail


# The RCon line (plans/68 NET-14): the fixture's beserver_x64.cfg sets an
# RConPassword and no RConIP, which a running server was measured to bind to
# every interface.

_RCON_LINE = "RCon port 2305 is reachable too"


def _no_backend(monkeypatch):
    monkeypatch.setattr(doctor, "IS_WINDOWS", False)
    monkeypatch.setattr(doctor, "IS_DOCKER", False)
    monkeypatch.setattr("app.ops.firewall_ops.detect_backend", lambda: None)


def test_check_firewall_without_a_backend_says_rcon_is_reachable(monkeypatch, sample_config):
    _no_backend(monkeypatch)

    label, ok, detail = doctor._check_firewall(sample_config)

    assert (label, ok) == ("Firewall", False)  # the verdict is the row's own
    assert _RCON_LINE in detail
    assert "every interface" in detail
    assert "RConIP 127.0.0.1" in detail
    # zsm says it; it never writes the key.
    assert "RConIP" not in sample_config.beserver_cfg_path.read_text()


def test_check_firewall_inactive_says_rcon_is_reachable_and_still_passes(
    monkeypatch, sample_config
):
    monkeypatch.setattr(doctor, "IS_WINDOWS", False)
    monkeypatch.setattr("app.ops.firewall_ops.detect_backend", lambda: "ufw")
    monkeypatch.setattr(
        "app.ops.firewall_ops.firewall_status", lambda _c: _fw_status(active=False)
    )

    label, ok, detail = doctor._check_firewall(sample_config)

    assert (label, ok) == ("Firewall", True)
    assert _RCON_LINE in detail


def test_check_firewall_active_names_rcon_only_when_its_port_is_allowed(
    monkeypatch, sample_config
):
    monkeypatch.setattr(doctor, "IS_WINDOWS", False)
    monkeypatch.setattr("app.ops.firewall_ops.detect_backend", lambda: "ufw")
    allowed = {2302, 2303, 2304, 2305, 27016}
    monkeypatch.setattr(
        "app.ops.firewall_ops.firewall_status",
        lambda _c: _fw_status(active=True, open_udp=set(allowed)),
    )
    label, ok, detail = doctor._check_firewall(sample_config)
    assert (label, ok) == ("Firewall", True)
    assert _RCON_LINE in detail

    sample_config.rcon_port = 2310  # not allowed by the firewall
    label, ok, detail = doctor._check_firewall(sample_config)
    assert (label, ok) == ("Firewall", True)
    assert "reachable too" not in detail


@pytest.mark.parametrize(
    "be_text",
    [
        "RConPassword mypass\nRConPort 2305\nRConIP 127.0.0.1\n",
        "RConPort 2305\n",  # no password: not the measured case, say nothing
    ],
)
def test_check_firewall_says_nothing_about_rcon_it_cannot_stand_behind(
    monkeypatch, sample_config, be_text
):
    _no_backend(monkeypatch)
    sample_config.beserver_cfg_path.write_text(be_text)

    _label, _ok, detail = doctor._check_firewall(sample_config)

    assert "reachable too" not in detail


def test_check_firewall_skips_the_rcon_line_for_a_remote_rcon_host(monkeypatch, sample_config):
    """Against a rented server this machine's BattlEye cfg is not that
    server's (root CLAUDE.md, `is_remote_rcon`)."""
    _no_backend(monkeypatch)
    sample_config.rcon_host = "203.0.113.7"

    _label, _ok, detail = doctor._check_firewall(sample_config)

    assert "reachable too" not in detail


def test_check_firewall_skips_the_rcon_line_when_battleye_is_off(monkeypatch, sample_config):
    _no_backend(monkeypatch)
    with sample_config.server_cfg_path.open("a") as f:
        f.write("BattlEye = 0;\n")

    _label, _ok, detail = doctor._check_firewall(sample_config)

    assert "reachable too" not in detail


def test_check_firewall_reads_battleyes_active_copy(monkeypatch, sample_config, battleye_running):
    """While the server runs only BattlEye's renamed copy exists, so a reader
    of beserver_x64.cfg alone would find nothing and say nothing."""
    _no_backend(monkeypatch)

    _label, _ok, detail = doctor._check_firewall(sample_config)

    assert _RCON_LINE in detail


# --- _check_tls_cert (plans/28 E2) ------------------------------------------


@pytest.fixture()
def tls_home(tmp_path, monkeypatch):
    monkeypatch.setattr("app.shared.paths.CONFIG_DIR", tmp_path)
    return tmp_path


def test_check_tls_cert_silent_on_a_default_install(sample_config, tls_home):
    """No TLS configured and nothing generated — most installs. Nothing to say,
    and a row saying "not configured" on every doctor run is noise."""
    assert doctor._check_tls_cert(sample_config) is None


def test_check_tls_cert_reports_a_healthy_cert(sample_config, tls_home):
    from app.web import tls

    cert, _, _ = tls.ensure_self_signed("127.0.0.1")

    label, ok, detail = doctor._check_tls_cert(sample_config)

    assert (label, ok) == ("App certificate", True)
    assert tls.fingerprint_sha256(cert) in detail
    assert "expires" in detail


def test_check_tls_cert_warns_inside_the_renewal_window(
    sample_config, tls_home, monkeypatch
):
    from app.web import tls

    monkeypatch.setattr(tls, "VALIDITY_DAYS", tls.RENEW_WHEN_DAYS_LEFT - 1)
    tls.ensure_self_signed("127.0.0.1")

    label, ok, detail = doctor._check_tls_cert(sample_config)

    assert (label, ok) == ("App certificate", False)
    assert "Expires in" in detail


def test_check_tls_cert_warns_on_expiry(sample_config, tls_home):
    """The check runs only at `zsm web` startup, so a panel alive past expiry
    serves an expired certificate indefinitely — nothing else notices."""
    from app.web import tls

    cert, key = tls.cert_paths()
    _write_expired_cert(cert, key)

    label, ok, detail = doctor._check_tls_cert(sample_config)

    assert (label, ok) == ("App certificate", False)
    assert "Expired on" in detail
    assert "never re-checks" in detail


def test_check_tls_cert_reports_a_missing_configured_path(
    sample_config, tls_home, tmp_path
):
    sample_config.web_tls_cert = str(tmp_path / "gone.pem")

    label, ok, detail = doctor._check_tls_cert(sample_config)

    assert (label, ok) == ("App certificate", False)
    assert "missing file" in detail


def test_check_tls_cert_is_a_server_check():
    assert doctor.section_for("App certificate") == doctor.SECTION_SERVER


def _write_expired_cert(cert_path, key_path) -> None:
    """A syntactically valid certificate whose validity ended yesterday."""
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "expired")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=10))
        .not_valid_after(now - datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    cert_path.parent.mkdir(parents=True, exist_ok=True)
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )


# --- VPS readiness section (plans/23) -------------------------------------


def test_section_for_defaults_to_server():
    assert doctor.section_for("Anything not listed") == doctor.SECTION_SERVER
    assert doctor.section_for("Swap") == doctor.SECTION_VPS
    assert doctor.section_for("Service scope") == doctor.SECTION_VPS


def test_every_vps_probe_label_is_sectioned(monkeypatch):
    """A probe whose label is missing from CHECK_SECTIONS would silently show
    up under Server — the one way this mapping can rot."""
    from app.ops import vps_readiness

    monkeypatch.setattr(vps_readiness, "IS_WINDOWS", False)
    for probe in vps_readiness.probes():
        assert doctor.section_for(probe.label) == doctor.SECTION_VPS, probe.label


def test_vps_checks_appear_in_run_all_checks(monkeypatch, sample_config):
    monkeypatch.setattr(doctor, "IS_WINDOWS", False)
    results, _ = doctor._run_all_checks(sample_config)
    labels = {label for label, _ok, _detail in results}
    assert {"CPU", "RAM", "Swap", "Disk space", "File handles", "Clock"} <= labels


def test_check_result_is_still_a_three_tuple(monkeypatch, sample_config):
    """Sections are a label mapping precisely so this stays true — a fourth
    element would break get_failed_checks and build_doctor_report."""
    monkeypatch.setattr(doctor, "IS_WINDOWS", False)
    results, _ = doctor._run_all_checks(sample_config)
    assert all(len(row) == 3 for row in results)


def test_preflight_runs_without_config(monkeypatch):
    """--preflight is the "before you own it" surface: it must not go through
    the init wizard and must survive an unloadable config."""
    from click.testing import CliRunner

    class _NoConfig:
        @staticmethod
        def load():
            raise FileNotFoundError("no config here")

    monkeypatch.setattr(doctor, "DayzConfig", _NoConfig)
    monkeypatch.setattr(doctor, "IS_WINDOWS", False)

    def _boom(_ctx):
        raise AssertionError("--preflight must not prompt for init")

    monkeypatch.setattr("app.commands.init.prompt_init_if_missing", _boom)

    result = CliRunner().invoke(doctor.doctor_cmd, ["--preflight"])

    assert result.exit_code in (0, 1), result.output
    assert "Verdict:" in result.output
    for server_word in ("mission", "mod list", "SteamCMD"):
        assert server_word.lower() not in result.output.lower()


# --- the exit code (F8) ----------------------------------------------------


def _doctor_run(monkeypatch, checks):
    """`zsm doctor` over a canned check list. Everything after the tables is
    stubbed out — this test is about the exit code, not the renderers."""
    from click.testing import CliRunner

    monkeypatch.setattr(doctor, "IS_WINDOWS", False)
    monkeypatch.setattr(
        doctor, "_run_all_checks", lambda _c, probe_steam=False: (checks, None)
    )
    monkeypatch.setattr(doctor, "_print_conflict_scan", lambda _s: None)
    monkeypatch.setattr(doctor, "_print_server_info", lambda _c: None)
    monkeypatch.setattr(doctor, "_print_service_files", lambda _c: None)
    monkeypatch.setattr("app.commands.init.prompt_init_if_missing", lambda _ctx: True)
    return CliRunner().invoke(doctor.doctor_cmd, [])


def test_doctor_exits_one_when_a_check_warns(monkeypatch):
    """F8. It printed "Some checks failed" and exited **0**, while
    `--preflight` exited 1 on a warning and said so in its own --help — so the
    *narrower* command was the only one usable as a gate and `zsm doctor` was
    useless in a cron job or a cloud-init check. Measured on the VPS test: five
    WARNs and exit 0."""
    result = _doctor_run(
        monkeypatch,
        [("Server directory", False, "Missing: /nope"), ("SteamCMD", True, "ok")],
    )
    assert result.exit_code == 1, result.output
    # Everything still prints — the exit is the last thing that happens.
    assert "Server directory" in result.output
    assert "Some checks failed" in result.output


def test_doctor_exits_zero_when_everything_passes(monkeypatch):
    """The other half: a healthy box must stay usable in an `&&` chain."""
    result = _doctor_run(
        monkeypatch, [("Server directory", True, "ok"), ("SteamCMD", True, "ok")]
    )
    assert result.exit_code == 0, result.output
    assert "Some checks failed" not in result.output


def test_doctor_help_documents_the_exit_code(monkeypatch):
    """`--preflight`'s help promised its exit code and the full form's did not,
    which is half of why the mismatch survived."""
    from click.testing import CliRunner

    result = CliRunner().invoke(doctor.doctor_cmd, ["--help"])
    assert result.exit_code == 0
    assert "Exits 1" in result.output


# --- the Server Info IP label (F10) ----------------------------------------


def test_server_info_does_not_call_the_default_route_address_a_local_ip(
    capsys, sample_config
):
    """`_get_local_ip()` returns the source address of the *default route* — the
    **public** IPv4 on a VPS. Measured on a VPS, doctor printed
    `Local IP | 203.0.113.10` (F10): right value, wrong label, on the first
    screen a new operator reads.

    Asserted on rendered output, because the label was duplicated across
    surfaces. The React page has its own test below.
    """
    doctor._print_server_info(sample_config)
    table = capsys.readouterr().out
    assert "Primary IP" in table
    assert "Local IP" not in table
    assert "default route" in table  # the line that says which address it is


def test_the_react_doctor_page_uses_the_same_label():
    """The web surface. `app/web/static/` is built from this file, so a
    mismatch here is what a user actually sees in the browser."""
    from pathlib import Path

    page = (
        Path(__file__).resolve().parents[1]
        / "webui" / "src" / "pages" / "DoctorPage.jsx"
    )
    rows = [
        line for line in page.read_text(encoding="utf-8").splitlines()
        if "Local IP" in line and not line.lstrip().startswith("//")
    ]
    assert not rows, f"DoctorPage.jsx still labels it Local IP: {rows}"
    assert '"Primary IP"' in page.read_text(encoding="utf-8")


# --- FPS logging (test pass 1, F52) -----------------------------------------

def _fps_check(config, cfg_line: str | None, server_args: str):
    cfg = config.server_cfg_path
    text = cfg.read_text() if cfg.exists() else ""
    if cfg_line is not None:
        text += cfg_line + "\n"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(text)
    config.server_args = server_args
    return doctor._check_fps_logging(config)


def test_fps_logging_that_is_off_passes_and_says_how_to_turn_it_on(sample_config, tmp_server_dir):
    """Bohemia's stock serverDZ.cfg has no logAverageFps. As a warning this row
    made `zsm doctor` exit 1 on every new install (first-hour test, 2026 Q4)."""
    sample_config.server_dir = str(tmp_server_dir)
    label, ok, detail = _fps_check(sample_config, None, "-adminLog -doLogs")
    assert (label, ok) == ("FPS logging", True)
    assert detail.startswith("Off: logAverageFps is not set in serverDZ.cfg")
    assert "To turn it on, set logAverageFps (seconds between log lines, for example 60)" in detail
    assert "-doLogs" not in detail


def test_fps_logging_that_is_off_names_dologs_when_that_is_missing_too(sample_config, tmp_server_dir):
    sample_config.server_dir = str(tmp_server_dir)
    _label, ok, detail = _fps_check(sample_config, None, "-adminLog")
    assert ok
    assert "-doLogs is not in the Run command" in detail and detail.endswith("and add -doLogs")


def test_fps_logging_zero_counts_as_off(sample_config, tmp_server_dir):
    sample_config.server_dir = str(tmp_server_dir)
    _label, ok, detail = _fps_check(sample_config, "logAverageFps = 0;", "-doLogs")
    assert ok and detail.startswith("Off: logAverageFps is not set")


def test_fps_logging_needs_dologs_too(sample_config, tmp_server_dir):
    """An interval that is set and never logged is a mistake, so this one warns."""
    sample_config.server_dir = str(tmp_server_dir)
    _label, ok, detail = _fps_check(sample_config, "logAverageFps = 30;", "-adminLog")
    assert not ok
    assert detail.startswith("-doLogs is not in the Run command")


def test_fps_logging_ok_with_both(sample_config, tmp_server_dir):
    sample_config.server_dir = str(tmp_server_dir)
    assert _fps_check(sample_config, "logAverageFps = 30;", "-adminLog -dologs") == (
        "FPS logging", True, "Every 30s"
    )


def test_fps_logging_has_no_opinion_without_serverdz(sample_config, tmp_path):
    sample_config.server_dir = str(tmp_path / "nothing-here")
    assert doctor._check_fps_logging(sample_config) is None


# --- Mod load order (plans/49 D2) -------------------------------------------


def test_the_load_order_row_appears_only_in_manual_mode(sample_config):
    assert doctor._check_load_order(sample_config) is None


def test_a_manual_order_that_breaks_a_dependency_warns(sample_config, monkeypatch):
    from app.modmgr import mods
    from app.ops.doctor_report import _links_for
    from app.ops.fix_hints import MODS_INSTALLED

    set_test_flags(sample_config, manual=True)
    monkeypatch.setattr(mods, "load_order_breaks", lambda config: [("@Trader", "@CF")])
    label, ok, detail = doctor._check_load_order(sample_config)
    assert (label, ok) == ("Mod load order", False)
    assert detail == "Manual: @Trader loads before @CF, which it requires"
    links, _phrases = _links_for(label, ok, detail)
    assert links == [{"label": "Mods", "to": MODS_INSTALLED}]

    monkeypatch.setattr(mods, "load_order_breaks", lambda config: [])
    assert doctor._check_load_order(sample_config)[1] is True


def test_a_long_path_or_link_in_a_row_is_folded_not_cut():
    """Rich ends a word that does not fit its column in "…". On a Windows 11
    terminal the runtime row lost the end of the installer's path and of the
    download link, the two things the row is there to give."""
    from io import StringIO

    from rich.console import Console

    from app.commands.doctor import _checks_table
    from app.server import windows_runtime

    detail = (
        "DayZServer_x64.exe cannot start on this computer: MSVCP140.dll and XAPOFX1_5.dll are missing. "
        "Install the Microsoft Visual C++ 2015-2019 Redistributable (x64): run "
        r"C:\Users\name\dayz-server\_CommonRedist\vcredist\2019\VC_redist.x64.exe; "
        f"the DirectX End-User Runtimes (June 2010): {windows_runtime.DIRECTX_URL}. Then start the server again."
    )
    table, any_fail = _checks_table("Server", [("OS dependencies", False, detail)])
    out = StringIO()
    Console(file=out, width=84, force_terminal=False).print(table)

    assert any_fail
    assert "…" not in out.getvalue()
    cells = "".join(line.split("│")[3].strip() for line in out.getvalue().splitlines() if line.count("│") == 4)
    assert r"vcredist\2019\VC_redist.x64.exe" in cells
    assert windows_runtime.DIRECTX_URL in cells


def test_a_fingerprint_detail_prints_its_colons(monkeypatch):
    """Rich read the `:AB:` in a SHA-256 fingerprint
    as the emoji code :ab: and printed 🆎, so the fingerprint an admin
    compares before trusting the certificate matched nothing."""
    fp = "3D:1B:6C:AB:4D:22"
    table, _ = doctor._checks_table("Panel", [("App certificate", True, f"SHA-256 {fp}")])
    monkeypatch.setattr(doctor.console, "width", 200)
    with doctor.console.capture() as cap:
        doctor.console.print(table)
    assert fp in cap.get()


def test_a_server_dir_on_no_volume_gets_its_own_doctor_row(sample_config, container_mounts):
    sample_config.server_dir = str(container_mounts / "server-exp")
    label, ok, detail = doctor._check_server_volume(sample_config)
    assert (label, ok) == ("Server volume", False) and "--force-recreate" in detail
    sample_config.server_dir = str(container_mounts / "server")
    assert doctor._check_server_volume(sample_config) is None


def test_a_traderplus_types_file_turned_off_or_taken_over_is_no_issue(
    sample_config, tmp_mission_dir, monkeypatch
):
    """After Take over (or Off) of TraderPlus_types.xml,
    doctor said the file was missing and not registered and sent the admin
    to "reset XML files" on every run."""
    from app.xml.xml_manual import set_excluded

    monkeypatch.setattr("app.traderplus.trader_common.is_mod_active", lambda config, names: True)
    _traderplus_setup(sample_config, ships_types=True)

    label, ok, detail = doctor._check_traderplus(sample_config)
    assert not ok and "traderplus-TraderPlus_types.xml missing" in detail

    set_excluded("traderplus-TraderPlus_types.xml", True, manual_dir=sample_config.paths.custom_xml_dir)
    label, ok, detail = doctor._check_traderplus(sample_config)
    assert ok and detail.startswith("Off:"), detail


def _traderplus_setup(config, *, ships_types: bool) -> None:
    """An installed @TraderPlus with @CF and one valid profiles JSON. With
    *ships_types* the mod folder holds an invented TraderPlus_Types.xml."""
    mod = config.server_dir_path / "@TraderPlus"
    mod.mkdir()
    (config.server_dir_path / "@CF").mkdir()
    if ships_types:
        (mod / "Centraleconomy").mkdir()
        (mod / "Centraleconomy" / "TraderPlus_Types.xml").write_text("<types/>")
    tp = config.server_dir_path / config.profiles_path / "TraderPlus" / "TraderPlusConfig"
    tp.mkdir(parents=True)
    (tp / "TraderPlusGeneralConfig.json").write_text("{}")


def test_a_traderplus_that_ships_no_types_xml_is_no_issue(sample_config, tmp_mission_dir, monkeypatch):
    """The 2026-11-09 Workshop version of TraderPlus has no Centraleconomy
    folder. Doctor said the types file was missing and sent the admin to
    "reset XML files", which has nothing to copy."""
    monkeypatch.setattr("app.traderplus.trader_common.is_mod_active", lambda config, names: True)
    _traderplus_setup(sample_config, ships_types=False)

    label, ok, detail = doctor._check_traderplus(sample_config)
    assert ok and detail.startswith("Off: this TraderPlus version ships no types XML"), detail
