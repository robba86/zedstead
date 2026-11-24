"""Tests for `zsm service` CLI commands added for user-scope support."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from app.commands import service as service_cmd
from app.commands.service import service as service_group


def _fresh_config():
    from app.shared.config import DayzConfig

    return DayzConfig(server_dir="/srv/dayz", steamcmd_dir="/opt/steamcmd", steam_username="alice")


def test_migrate_scope_system_is_noop(monkeypatch):
    cfg = _fresh_config()
    cfg.save = lambda: (_ for _ in ()).throw(AssertionError("must not save"))
    monkeypatch.setattr(service_cmd, "IS_WINDOWS", False)
    monkeypatch.setattr(service_cmd, "has_systemd", lambda: True)
    monkeypatch.setattr(service_cmd, "load_context", lambda ctx: (cfg, False))
    result = CliRunner().invoke(service_group, ["migrate-scope", "system"])
    assert result.exit_code == 0, result.output
    assert "not needed" in result.output


def test_migrate_scope_user_full_flow(monkeypatch):
    saved = {}
    cfg = _fresh_config()
    cfg.save = lambda: saved.setdefault("scope", cfg.service_scope)

    monkeypatch.setattr(service_cmd, "IS_WINDOWS", False)
    monkeypatch.setattr(service_cmd, "has_systemd", lambda: True)
    monkeypatch.setattr(service_cmd, "load_context", lambda ctx: (cfg, False))
    monkeypatch.setattr(service_cmd, "_ensure_sudo_cli", lambda config=None: True)
    monkeypatch.setattr(
        "app.server.service_control.is_server_running", lambda config=None: False
    )
    monkeypatch.setattr("app.shared.systemd_scope.SYSTEM_UNIT_DIR", __import__("pathlib").Path("/nope"))
    monkeypatch.setattr("app.shared.systemd_scope.linger_enabled", lambda *a, **k: True)

    installed = {}
    monkeypatch.setattr(
        "app.server.service_ops.generate_service_files",
        lambda config: installed.setdefault("gen", config.service_scope),
    )
    monkeypatch.setattr("app.server.scheduler.load_tasks", lambda config: [])

    async def _fake_remove(config):
        return []

    monkeypatch.setattr("app.server.scheduler.remove_timers_async", _fake_remove)

    result = CliRunner().invoke(service_group, ["migrate-scope", "user"])
    assert result.exit_code == 0, result.output
    assert cfg.service_scope == "user"
    assert saved["scope"] == "user"
    assert installed["gen"] == "user"  # reinstalled in user scope
    assert "journalctl --user -u dayz-server" in result.output
    # A raw `systemctl stop` leaves the unit failed, and the restart task
    # restarts a failed unit.
    assert "zsm start|stop|restart" in result.output
    assert "systemctl --user start" not in result.output


@pytest.mark.parametrize("command", ["start", "restart", "update"])
def test_windows_starts_go_through_the_one_launcher(monkeypatch, tmp_path, command):
    monkeypatch.setattr("app.server.service_control.start_refusal", lambda config: None)
    # The launcher keeps the server dir off cmd's command line; a second copy
    # that builds its own argv would bring it back.
    from app.server import service_control

    cfg = _fresh_config()
    cfg.server_dir = str(tmp_path)
    monkeypatch.setattr(service_cmd, "IS_WINDOWS", True)
    monkeypatch.setattr(service_cmd, "load_context", lambda ctx: (cfg, True))
    monkeypatch.setattr("app.ops.changeset_ops.take_start_snapshot", lambda config: None)
    monkeypatch.setattr(service_cmd, "_windows_stop_server", lambda: True)
    launched = []
    monkeypatch.setattr(
        service_cmd, "_windows_launch_start_bat",
        lambda config: launched.append(config) or service_control.ServiceResult(True),
    )
    calls = []
    monkeypatch.setattr(service_cmd.subprocess, "run", lambda *a, **k: calls.append(a))
    result = CliRunner().invoke(service_group, [command])
    assert result.exit_code == 0, result.output
    assert launched == [cfg]
    assert calls == []


@pytest.mark.parametrize("running", [True, False])
def test_windows_restart_task_leaves_a_stopped_server_stopped(monkeypatch, tmp_path, running):
    """The Windows restart task runs `restart --if-running`, as the Linux one
    skips a server the admin stopped."""
    from app.server import service_control

    cfg = _fresh_config()
    cfg.server_dir = str(tmp_path)
    monkeypatch.setattr(service_cmd, "IS_WINDOWS", True)
    monkeypatch.setattr(service_cmd, "load_context", lambda ctx: (cfg, True))
    monkeypatch.setattr(service_cmd, "is_server_running", lambda config=None: running)
    monkeypatch.setattr("app.ops.changeset_ops.take_start_snapshot", lambda config: None)
    stops: list = []
    monkeypatch.setattr(service_cmd, "_windows_stop_server", lambda: stops.append(1) or True)
    launched: list = []
    monkeypatch.setattr(
        service_cmd, "_windows_launch_start_bat",
        lambda config: launched.append(config) or service_control.ServiceResult(True),
    )
    result = CliRunner().invoke(service_group, ["restart", "--if-running"])
    assert result.exit_code == 0, result.output
    assert (stops, launched) == (([1], [cfg]) if running else ([], []))
    if not running:
        assert "nothing to restart" in result.output


@pytest.mark.parametrize("restarts", [0, 3])
def test_service_update_that_leaves_the_server_stopped_clears_failed(monkeypatch, restarts):
    """A server `service update` leaves stopped must stay stopped at the next
    restart task, which restarts a failed unit."""
    from app.server import service_control

    cfg = _fresh_config()
    monkeypatch.setattr(service_cmd, "IS_WINDOWS", False)
    monkeypatch.setattr(service_cmd, "has_systemd", lambda: True)
    monkeypatch.setattr(service_cmd, "load_context", lambda ctx: (cfg, True))
    monkeypatch.setattr(service_cmd, "_ensure_sudo_cli", lambda config=None: True)

    def broken(config):
        raise service_cmd.ServiceUserError("no such user")

    monkeypatch.setattr(service_cmd, "_render_service", broken)
    monkeypatch.setattr(service_control, "restarts_this_run", lambda config=None: restarts)
    calls: list = []
    monkeypatch.setattr(service_cmd, "_systemctl", lambda action, **k: calls.append(action) or True)
    monkeypatch.setattr(
        service_control, "clear_stop_failure",
        lambda config, before: calls.append(("clear", before)),
    )
    result = CliRunner().invoke(service_group, ["update"])
    assert "Service left stopped" in result.output
    assert calls == ["stop", ("clear", restarts)]


def test_enable_linger_already_enabled(monkeypatch):
    monkeypatch.setattr(service_cmd, "IS_WINDOWS", False)
    monkeypatch.setattr("app.shared.systemd_scope.linger_enabled", lambda *a, **k: True)
    monkeypatch.setattr("app.shared.systemd_scope._current_user", lambda: "bob")
    result = CliRunner().invoke(service_group, ["enable-linger"])
    assert result.exit_code == 0, result.output
    assert "already enabled" in result.output


def test_enable_linger_runs_loginctl(monkeypatch):
    monkeypatch.setattr(service_cmd, "IS_WINDOWS", False)
    monkeypatch.setattr("app.shared.systemd_scope.linger_enabled", lambda *a, **k: False)
    monkeypatch.setattr("app.shared.systemd_scope._current_user", lambda: "bob")
    monkeypatch.setattr(service_cmd, "sudo_prefix", lambda: ["sudo"])

    calls = []

    class _R:
        returncode = 0

    def fake_run(cmd, **k):
        calls.append(cmd)
        return _R()

    monkeypatch.setattr(service_cmd.subprocess, "run", fake_run)
    result = CliRunner().invoke(service_group, ["enable-linger"])
    assert result.exit_code == 0, result.output
    # unprivileged attempt tried first, succeeds → no sudo call
    assert calls[0] == ["loginctl", "enable-linger", "bob"]
    assert "enabled for bob" in result.output


def test_enable_linger_falls_back_to_sudo(monkeypatch):
    monkeypatch.setattr(service_cmd, "IS_WINDOWS", False)
    monkeypatch.setattr("app.shared.systemd_scope.linger_enabled", lambda *a, **k: False)
    monkeypatch.setattr("app.shared.systemd_scope._current_user", lambda: "bob")
    monkeypatch.setattr(service_cmd, "sudo_prefix", lambda: ["sudo"])

    calls = []

    def fake_run(cmd, **k):
        calls.append(cmd)

        class _R:
            # unprivileged attempt fails, sudo attempt succeeds
            returncode = 0 if cmd[0] == "sudo" else 1

        return _R()

    monkeypatch.setattr(service_cmd.subprocess, "run", fake_run)
    result = CliRunner().invoke(service_group, ["enable-linger"])
    assert result.exit_code == 0, result.output
    assert calls[0] == ["loginctl", "enable-linger", "bob"]
    assert calls[1] == ["sudo", "loginctl", "enable-linger", "bob"]


# --- service install ----------------------------------------------------------


def _install_setup(monkeypatch, sample_config, tmp_path):
    """User scope under tmp_path, systemctl stubbed. Returns (config, source,
    dest, systemctl calls)."""
    import dataclasses

    from app.server.service_control import SERVICE_NAME

    cfg = dataclasses.replace(sample_config, service_scope="user")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(service_cmd, "IS_WINDOWS", False)
    monkeypatch.setattr(service_cmd, "has_systemd", lambda: True)
    monkeypatch.setattr(service_cmd, "load_context", lambda ctx: (cfg, True))
    calls = []
    monkeypatch.setattr(
        service_cmd, "_systemctl", lambda action, **k: calls.append(action) or True
    )
    source = cfg.server_dir_path / SERVICE_NAME
    dest = tmp_path / "xdg" / "systemd" / "user" / SERVICE_NAME
    return cfg, source, dest, calls


def test_install_copies_a_unit_rendered_for_this_machine(monkeypatch, sample_config, tmp_path):
    from app.server.service_control import _render_service

    cfg, source, dest, calls = _install_setup(monkeypatch, sample_config, tmp_path)
    source.write_text(_render_service(cfg), encoding="utf-8")

    result = CliRunner().invoke(service_group, ["install"], obj={})

    assert result.exit_code == 0, result.output
    assert dest.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")
    assert calls == ["daemon-reload"]


def test_install_refuses_a_unit_rendered_elsewhere(monkeypatch, sample_config, tmp_path):
    """Measured in 2026 Q3: a server folder rsynced from the Docker box carried
    its system-scope unit (another WorkingDirectory, WantedBy=multi-user.target);
    install copied it into user scope, where it could never start."""
    from app.server.service_control import _render_service

    cfg, source, dest, calls = _install_setup(monkeypatch, sample_config, tmp_path)
    here = _render_service(cfg)
    elsewhere = here.replace(str(cfg.server_dir_path), "/home/zsm/server").replace(
        "default.target", "multi-user.target"
    )
    assert elsewhere != here
    source.write_text(elsewhere, encoding="utf-8")

    result = CliRunner().invoke(service_group, ["install"], obj={})

    assert not dest.exists()
    assert calls == []
    assert "zsm service generate" in result.output
    # The file is left for the admin, hand edit and all.
    assert source.read_text(encoding="utf-8") == elsewhere


# --- service web install|remove (TODO A9) ----------------------------------------


def _web_setup(monkeypatch, sample_config, tmp_path, *, password="scrypt$x"):
    """User scope under tmp_path with the install itself stubbed. Returns
    (config, list of install calls)."""
    import dataclasses

    from app.server import panel_unit
    from app.server.service_control import ServiceResult

    cfg = dataclasses.replace(sample_config, service_scope="user", web_password_hash=password)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(service_cmd, "IS_WINDOWS", False)
    monkeypatch.setattr(service_cmd, "has_systemd", lambda: True)
    monkeypatch.setattr(service_cmd, "load_context", lambda ctx: (cfg, True))
    monkeypatch.setattr(panel_unit, "panel_port_taken", lambda config: False)
    monkeypatch.setattr("app.shared.systemd_scope.linger_enabled", lambda *a, **k: True)
    monkeypatch.setattr("app.shared.platform.IS_DOCKER", False)
    # Commands and paths must not wrap mid-line in the captured output.
    monkeypatch.setattr(service_cmd.console, "width", 400)
    monkeypatch.setattr("app.shared.cli_ui.console.width", 400)
    installs = []
    monkeypatch.setattr(
        panel_unit, "install_panel_unit",
        lambda config: installs.append(config) or ServiceResult(True),
    )
    return cfg, installs


def test_web_install_installs_and_says_where_it_listens(monkeypatch, sample_config, tmp_path):
    cfg, installs = _web_setup(monkeypatch, sample_config, tmp_path)

    result = CliRunner().invoke(service_group, ["web", "install"], obj={})

    assert result.exit_code == 0, result.output
    assert installs == [cfg]
    assert "127.0.0.1:8433" in result.output
    assert "web_bind_host" in result.output
    assert "systemctl --user restart zsm-web" in result.output


def test_web_install_refuses_without_a_password(monkeypatch, sample_config, tmp_path):
    """`zsm web` prompts for one; under systemd that prompt is a restart loop."""
    _cfg, installs = _web_setup(monkeypatch, sample_config, tmp_path, password="")

    result = CliRunner().invoke(service_group, ["web", "install"], obj={})

    assert result.exit_code == 1
    assert installs == []
    assert "No web UI password is set, and a service cannot answer the prompt for one." in (
        result.output
    )
    assert "zsm web --set-password" in result.output


def test_web_install_goes_ahead_with_accounts_and_no_single_password(
    monkeypatch, sample_config, tmp_path
):
    """Readable accounts are a login, so `zsm web` does not prompt and a
    service has nothing to answer. Deleting or demoting the account made from
    the single password leaves exactly this state (plans/68)."""
    from app.web.users import UserStore

    UserStore().add("deputy", "password-d", "admin")
    cfg, installs = _web_setup(monkeypatch, sample_config, tmp_path, password="")

    result = CliRunner().invoke(service_group, ["web", "install"], obj={})

    assert result.exit_code == 0, result.output
    assert installs == [cfg]
    assert "No web UI password is set" not in result.output


def test_web_install_refuses_when_the_accounts_cannot_be_read(
    monkeypatch, sample_config, tmp_path
):
    """An unreadable users.json is no accounts, for `zsm web`'s prompt and
    for login alike, so a service would hit the prompt."""
    from app.web.users import users_path

    users_path().parent.mkdir(parents=True, exist_ok=True)
    users_path().write_text("{ truncated", encoding="utf-8")
    _cfg, installs = _web_setup(monkeypatch, sample_config, tmp_path, password="")

    result = CliRunner().invoke(service_group, ["web", "install"], obj={})

    assert result.exit_code == 1
    assert installs == []
    assert "zsm web --set-password" in result.output


def test_web_install_refuses_while_a_hand_started_panel_holds_the_port(
    monkeypatch, sample_config, tmp_path
):
    from app.server import panel_unit

    _cfg, installs = _web_setup(monkeypatch, sample_config, tmp_path)
    monkeypatch.setattr(panel_unit, "panel_port_taken", lambda config: True)

    result = CliRunner().invoke(service_group, ["web", "install"], obj={})

    assert result.exit_code == 1
    assert installs == []
    assert "8433" in result.output


def test_web_install_warns_when_lingering_is_off(monkeypatch, sample_config, tmp_path):
    _web_setup(monkeypatch, sample_config, tmp_path)
    monkeypatch.setattr("app.shared.systemd_scope.linger_enabled", lambda *a, **k: False)

    result = CliRunner().invoke(service_group, ["web", "install"], obj={})

    assert result.exit_code == 0, result.output
    assert "zsm service enable-linger" in result.output


def test_web_remove_without_a_unit_changes_nothing(monkeypatch, sample_config, tmp_path):
    from app.server import panel_unit

    _web_setup(monkeypatch, sample_config, tmp_path)
    monkeypatch.setattr(
        panel_unit, "remove_panel_unit", lambda config: pytest.fail("nothing to remove")
    )

    result = CliRunner().invoke(service_group, ["web", "remove"], obj={})

    assert result.exit_code == 0, result.output
    assert "No app service found" in result.output


# --- service firewall (plans/22) --------------------------------------------


def test_firewall_command_without_apply_runs_nothing(monkeypatch, tmp_path):
    """The report path must be read-only: no ufw command may be executed."""
    import subprocess

    from app.ops import firewall_ops

    cfg = _fresh_config()
    cfg.server_port = 2402
    cfg.server_dir = str(tmp_path)

    read_only = {("ufw", "status", "verbose"), ("firewall-cmd", "--state"),
                 ("firewall-cmd", "--list-all"), ("firewall-cmd", "--get-default-zone")}

    def forbidden(argv, *_a, **_k):
        argv = list(argv)
        firewall_argv = [a for a in argv if a not in ("sudo", "-n")]
        if firewall_argv and firewall_argv[0] in ("ufw", "firewall-cmd"):
            # Reading status is the whole point of the report; anything that
            # could *change* the firewall must not be reachable without --apply.
            assert tuple(firewall_argv) in read_only, f"report path ran {argv}"
        raise FileNotFoundError(argv[0])

    monkeypatch.setattr(service_cmd, "IS_WINDOWS", False)
    monkeypatch.setattr(service_cmd, "load_context", lambda ctx: (cfg, False))
    monkeypatch.setattr(firewall_ops.subprocess, "run", forbidden)
    monkeypatch.setattr(firewall_ops, "detect_backend", lambda: "ufw")
    monkeypatch.setattr(
        firewall_ops, "ssh_detect",
        lambda: firewall_ops.SshDetection(ports=[2222], sources=("ss",), sshd_present=True),
    )
    monkeypatch.setattr(
        firewall_ops, "apply_rules",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not apply")),
    )

    result = CliRunner().invoke(service_group, ["firewall"], obj={})

    assert result.exit_code == 0, result.output
    assert "2402" in result.output  # the port table rendered
    assert "ufw allow 2222/tcp" in result.output  # SSH allowed first
    assert "Nothing was changed" in result.output


def _firewall_before_init(monkeypatch):
    from app.ops import firewall_ops
    from app.shared.config import ConfigNotFound

    def no_config(ctx):
        raise ConfigNotFound("No config found. Run 'zsm init' first.")

    def no_binaries(argv, *_a, **_k):
        raise FileNotFoundError(argv[0])

    monkeypatch.setattr(service_cmd, "IS_WINDOWS", False)
    monkeypatch.setattr(service_cmd, "load_context", no_config)
    monkeypatch.setattr(firewall_ops.subprocess, "run", no_binaries)
    monkeypatch.setattr(firewall_ops, "detect_backend", lambda: "ufw")
    monkeypatch.setattr(
        firewall_ops, "ssh_detect",
        lambda: firewall_ops.SshDetection(ports=[22], sources=("ss",), sshd_present=True),
    )


def test_firewall_reports_the_default_ports_before_init(monkeypatch):
    """Measured on a fresh VPS (plans/40, 2026 Q4): "No config found", exit 1,
    so user-data.yaml's optional firewall line failed on every first boot."""
    _firewall_before_init(monkeypatch)
    monkeypatch.delenv("SUDO_USER", raising=False)

    result = CliRunner().invoke(service_group, ["firewall"], obj={})

    assert result.exit_code == 0, result.output
    assert "2302" in result.output  # DayzConfig()'s game port
    assert "No config yet" in result.output


def test_firewall_under_sudo_without_a_config_still_refuses(monkeypatch):
    """Under sudo the missing config is root's; defaults would stand in for the
    caller's real ports."""
    from app.shared.config import ConfigNotFound

    _firewall_before_init(monkeypatch)
    monkeypatch.setenv("SUDO_USER", "dayz")

    result = CliRunner().invoke(service_group, ["firewall"], obj={})

    assert isinstance(result.exception, ConfigNotFound)


def test_firewall_without_a_backend_prints_no_commands(monkeypatch, tmp_path):
    """It used to fall back to `backend or "ufw"` and print nine `sudo ufw …`
    commands under a header correctly saying ufw is not installed — measured on
    a stock Rocky VPS image, which ships neither backend. A user pasting
    them got `command not found` nine times.

    The assertions look for the *rule* commands rather than the substring
    "ufw": the replacement names ufw on purpose, in the one line that says how
    to install a firewall zsm can manage."""
    from app.ops import firewall_ops

    cfg = _fresh_config()
    cfg.server_dir = str(tmp_path)

    monkeypatch.setattr(service_cmd, "IS_WINDOWS", False)
    monkeypatch.setattr(service_cmd, "load_context", lambda ctx: (cfg, False))
    monkeypatch.setattr(firewall_ops, "detect_backend", lambda: None)
    monkeypatch.setattr(
        firewall_ops, "firewall_status",
        lambda _c: firewall_ops.FirewallStatus(
            backend=None, active=None, error="no managed firewall found"
        ),
    )
    monkeypatch.setattr(
        firewall_ops, "ssh_detect", lambda: firewall_ops.SshDetection(ports=[22])
    )
    monkeypatch.setattr(
        firewall_ops, "apply_rules",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not apply")),
    )

    result = CliRunner().invoke(service_group, ["firewall"], obj={})

    assert result.exit_code == 0, result.output
    assert "No managed firewall is installed" in result.output
    for command in ("ufw allow", "ufw default", "ufw enable", "--add-port"):
        assert command not in result.output, f"printed {command!r} with no backend"
    assert "No commands to show" in result.output


def test_firewall_prints_no_commands_when_the_ssh_port_is_unknown(monkeypatch, tmp_path):
    """An empty union used to fall back to `ssh=ports or [22]` for the *display*
    — the one assumption `ssh_detect()` refuses to make.

    Measured on Rocky 10.2 with sshd on 2222 (F29): a warning saying the port
    could not be determined, and four lines below it a runnable recipe allowing
    22, denying 2222 and setting the target to DROP. `--apply` refuses on this
    detection, so nothing zsm *ran* was wrong; the lockout was in what the
    screen told a human to type — and the refusal message tells them to allow
    the port by hand, on that same screen."""
    from app.ops import firewall_ops

    cfg = _fresh_config()
    cfg.server_dir = str(tmp_path)

    monkeypatch.setattr(service_cmd, "IS_WINDOWS", False)
    monkeypatch.setattr(service_cmd, "load_context", lambda ctx: (cfg, False))
    monkeypatch.setattr(firewall_ops, "detect_backend", lambda: "firewalld")
    monkeypatch.setattr(
        firewall_ops, "firewall_status",
        lambda _c: firewall_ops.FirewallStatus(backend="firewalld", active=True),
    )
    monkeypatch.setattr(
        firewall_ops, "ssh_detect",
        lambda: firewall_ops.SshDetection(
            ports=[], sources=(), sshd_present=True,
            incomplete=("`ss -tlnp` would not name the owner of port(s) 2222",),
        ),
    )
    monkeypatch.setattr(
        firewall_ops, "apply_rules",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not apply")),
    )

    result = CliRunner().invoke(service_group, ["firewall", "--dry-run"], obj={})

    assert result.exit_code == 0, result.output
    assert "could not be determined" in result.output
    # The port number is what locks you out, so assert on it directly rather
    # than on the surrounding wording.
    assert "22/tcp" not in result.output, "printed an allow rule for a guessed port"
    assert "set-target=DROP" not in result.output, "printed a deny policy to paste"
    assert "No commands to show" in result.output
    # "Re-run with --apply" would be advice to run a command that refuses.
    assert "Re-run with --apply" not in result.output


def test_firewall_prints_the_zone_conflict(monkeypatch, tmp_path):
    """The explanation was doctor-only. `service firewall --dry-run` rendered
    "firewalld — active" and then five `unknown`s with no reason anywhere —
    self-contradictory, on the command the tool tells users to run first."""
    from app.ops import firewall_ops

    cfg = _fresh_config()
    cfg.server_dir = str(tmp_path)
    conflict = (
        "port(s) 2302 are open in zone zsmtest, not the default zone "
        "— cannot tell whether that zone covers the server's interface"
    )

    monkeypatch.setattr(service_cmd, "IS_WINDOWS", False)
    monkeypatch.setattr(service_cmd, "load_context", lambda ctx: (cfg, False))
    monkeypatch.setattr(firewall_ops, "detect_backend", lambda: "firewalld")
    monkeypatch.setattr(
        firewall_ops, "firewall_status",
        lambda _c: firewall_ops.FirewallStatus(
            backend="firewalld", active=True, error=conflict, zone_conflict=conflict
        ),
    )
    monkeypatch.setattr(
        firewall_ops, "ssh_detect", lambda: firewall_ops.SshDetection(ports=[22])
    )

    result = CliRunner().invoke(service_group, ["firewall", "--dry-run"], obj={})

    assert result.exit_code == 0, result.output
    assert "zsmtest" in result.output
    assert "get-active-zones" in result.output

    # `error` is still set on this path, so the table stays conservative — the
    # wording improved, not what the command trusts.
    #
    # **Every** row reads `unknown`, and that is F21, reviewed and deliberately
    # kept in 2026 Q3. It looks like a free win to render the ports that are
    # in the default zone as `yes` and the rest as `no`, since the tool has just
    # enumerated every zone. It is not: `_firewalld_other_zone_with` returns the
    # *first* non-default zone with any overlap, so with 2302 in `zsmtest` and
    # 2303 in `dmz` only the first comes back and 2303 would print `no` while
    # genuinely open — "could not tell" rendering as "closed", which is the one
    # thing this module's stated invariant forbids. The reasoning, and what a
    # real fix would need, are in `app/ops/CLAUDE.md`. Read that before changing
    # this assertion.
    from app.ops.firewall_ops import required_ports

    assert result.output.count("unknown") >= len(required_ports(cfg))


def test_firewall_command_dry_run_beats_apply(monkeypatch, tmp_path):
    """--dry-run with --apply reports; writing rules is never the default."""
    from app.ops import firewall_ops

    cfg = _fresh_config()
    cfg.server_dir = str(tmp_path)

    monkeypatch.setattr(service_cmd, "IS_WINDOWS", False)
    monkeypatch.setattr(service_cmd, "load_context", lambda ctx: (cfg, False))
    monkeypatch.setattr(firewall_ops, "detect_backend", lambda: "ufw")
    monkeypatch.setattr(firewall_ops, "firewall_status", lambda _c: firewall_ops.FirewallStatus(
        backend="ufw", active=False))
    monkeypatch.setattr(
        firewall_ops, "ssh_detect", lambda: firewall_ops.SshDetection(ports=[22])
    )
    monkeypatch.setattr(
        firewall_ops, "apply_rules",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not apply")),
    )

    result = CliRunner().invoke(service_group, ["firewall", "--apply", "--dry-run"], obj={})

    assert result.exit_code == 0, result.output
    assert "Nothing was changed" in result.output


def test_firewall_command_apply_propagates_refusal_exit_code(monkeypatch, tmp_path):
    from app.ops import firewall_ops

    cfg = _fresh_config()
    cfg.server_dir = str(tmp_path)

    monkeypatch.setattr(service_cmd, "IS_WINDOWS", False)
    monkeypatch.setattr(service_cmd, "load_context", lambda ctx: (cfg, False))
    monkeypatch.setattr(firewall_ops, "detect_backend", lambda: "ufw")
    monkeypatch.setattr(firewall_ops, "firewall_status", lambda _c: firewall_ops.FirewallStatus(
        backend="ufw", active=False))
    monkeypatch.setattr(
        firewall_ops, "ssh_detect",
        lambda: firewall_ops.SshDetection(sshd_present=True),
    )
    monkeypatch.setattr(firewall_ops, "apply_rules", lambda *a, **k: 1)

    result = CliRunner().invoke(service_group, ["firewall", "--apply", "--yes"], obj={})

    assert result.exit_code == 1
    assert "could not be determined" in result.output


def test_cli_start_and_restart_take_the_start_snapshot(monkeypatch):
    """Test pass 1, F51: `zsm start` / `restart` never took one, so "Since
    last start" compared against a snapshot days old."""
    monkeypatch.setattr("app.server.service_control.start_refusal", lambda config: None)
    cfg = _fresh_config()
    snaps: list = []
    monkeypatch.setattr(service_cmd, "IS_WINDOWS", False)
    monkeypatch.setattr(service_cmd, "load_context", lambda ctx: (cfg, False))
    monkeypatch.setattr(service_cmd, "_systemctl", lambda action, **kw: True)
    monkeypatch.setattr("app.ops.changeset_ops.take_start_snapshot", snaps.append)
    for command in ("start", "restart"):
        result = CliRunner().invoke(service_group, [command])
        assert result.exit_code == 0, result.output
    assert snaps == [cfg, cfg]


def test_cli_start_with_a_runtime_missing_names_the_way_round_it(monkeypatch):
    """`zsm start` refuses with the runtime's sentence and names --anyway,
    which starts it all the same."""
    monkeypatch.setattr("app.server.service_control.start_refusal", lambda config: None)
    from app.server import windows_runtime

    cfg = _fresh_config()
    problem = "DayZServer_x64.exe cannot start on this computer: X.dll is missing."
    monkeypatch.setattr(windows_runtime, "runtime_problem", lambda config: problem)
    monkeypatch.setattr(service_cmd, "IS_WINDOWS", False)
    monkeypatch.setattr(service_cmd, "load_context", lambda ctx: (cfg, True))
    monkeypatch.setattr("app.ops.changeset_ops.take_start_snapshot", lambda config: None)
    started = []
    monkeypatch.setattr(service_cmd, "_systemctl", lambda action, **kw: started.append(action) or True)

    refused = CliRunner().invoke(service_group, ["start"])
    assert refused.exit_code == 1
    assert "X.dll is missing" in refused.output
    assert "start --anyway" in refused.output
    assert started == []

    result = CliRunner().invoke(service_group, ["start", "--anyway"])
    assert result.exit_code == 0, result.output
    assert started == ["start"]
