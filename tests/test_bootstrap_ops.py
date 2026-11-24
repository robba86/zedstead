"""Tests for app/ops/bootstrap_ops.py — `zsm bootstrap-vps` (TODO A3).

**No test here runs `useradd`, `sysctl`, `swapon` or `fallocate`.** That is
structural, not discipline: `plan()` returns the argv that *would* run and
never executes a step, and `apply()` takes injected `run`/`write` callables.
If a change here makes a test need a privileged host, the change is wrong.

`plan()` is not side-effect *free* — it shells out to read (`ufw status`,
`timedatectl show`). The guarantee is narrower and is the one that matters:
it never calls the module's own executors.
"""

from __future__ import annotations

import shlex
from pathlib import Path

import pytest

from app.ops import bootstrap_ops
from app.ops.bootstrap_ops import NEEDED, OK, SKIPPED, UNKNOWN


@pytest.fixture()
def host(monkeypatch):
    """A neutral Linux host: everything already satisfied.

    Each test moves one fact and asserts one step, so a failure names the rule
    that broke rather than "the plan changed".
    """
    monkeypatch.setattr(bootstrap_ops, "IS_WINDOWS", False)
    monkeypatch.setattr(bootstrap_ops, "IS_DOCKER", False)
    monkeypatch.setattr(bootstrap_ops, "IS_ROOT", False)
    monkeypatch.setattr(bootstrap_ops, "current_user", lambda: "dayz")
    monkeypatch.setattr(bootstrap_ops.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr("app.shared.systemd_scope.linger_enabled", lambda u=None: True)
    monkeypatch.setattr(bootstrap_ops.vps_readiness, "mem_mb", lambda: (16384, 8000))
    monkeypatch.setattr(bootstrap_ops.vps_readiness, "disk_free_gb", lambda p: 200.0)
    monkeypatch.setattr(bootstrap_ops.vps_readiness, "clock_synced", lambda: True)
    monkeypatch.setattr(
        bootstrap_ops.vps_readiness, "file_handle_limits", lambda: (200000, 1024, 524288)
    )
    monkeypatch.setattr(bootstrap_ops, "_swap_total_kb", lambda: 0)
    return monkeypatch


def _step(plan, key):
    return next(s for s in plan.steps if s.key == key)


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def test_refuses_on_windows(host, sample_config):
    host.setattr(bootstrap_ops, "IS_WINDOWS", True)
    plan = bootstrap_ops.plan(sample_config)
    assert plan.refusal and "Linux" in plan.refusal
    assert plan.steps == []


def test_refuses_inside_a_container(host, sample_config):
    """Swap, sysctl and file limits belong to the Docker host, not the guest."""
    host.setattr(bootstrap_ops, "IS_DOCKER", True)
    plan = bootstrap_ops.plan(sample_config)
    assert plan.refusal and "container" in plan.refusal


def test_apply_on_a_refused_plan_runs_nothing(sample_config):
    ran: list = []
    plan = bootstrap_ops.BootstrapPlan([], refusal="nope")
    code = bootstrap_ops.apply(
        plan, sample_config, log=lambda _l: None, run=lambda a: ran.append(a) or 0
    )
    assert code == 1
    assert ran == []


# ---------------------------------------------------------------------------
# The non-root user
# ---------------------------------------------------------------------------


def test_as_an_ordinary_user_there_is_nothing_to_create(host, sample_config):
    step = _step(bootstrap_ops.plan(sample_config), "user")
    assert step.state == SKIPPED
    assert "dayz" in step.detail


def test_as_root_without_a_username_it_asks_rather_than_guesses(host, sample_config):
    host.setattr(bootstrap_ops, "IS_ROOT", True)
    plan = bootstrap_ops.plan(sample_config)
    assert _step(plan, "user").state == UNKNOWN
    assert any("--user" in n for n in plan.notes)


def test_an_existing_user_is_not_created_again(host, sample_config):
    host.setattr(bootstrap_ops, "IS_ROOT", True)
    host.setattr(bootstrap_ops, "_user_exists", lambda u: True)
    host.setattr(bootstrap_ops.Path, "is_dir", lambda self: False)
    step = _step(bootstrap_ops.plan(sample_config, username="dayz"), "user")
    assert step.state == OK
    assert step.commands == []


def test_root_ssh_keys_are_copied_to_the_new_user(host, sample_config, monkeypatch):
    """Without this a fresh VPS hands you an account you cannot log in as, and
    disabling root SSH then locks the box."""
    host.setattr(bootstrap_ops, "IS_ROOT", True)
    host.setattr(bootstrap_ops, "_user_exists", lambda u: False)
    monkeypatch.setattr(Path, "is_dir", lambda self: False)
    monkeypatch.setattr(Path, "is_file", lambda self: str(self).endswith("authorized_keys"))

    step = _step(bootstrap_ops.plan(sample_config, username="dayz"), "user")
    assert step.state == NEEDED
    flat = [" ".join(c) for c in step.commands]
    assert any(c.startswith("useradd ") for c in flat)
    assert any("/root/.ssh/authorized_keys" in c for c in flat)
    assert any("/home/dayz/.ssh/authorized_keys" in c for c in flat)


def test_no_root_keys_is_called_out_rather_than_passed_over(host, sample_config, monkeypatch):
    host.setattr(bootstrap_ops, "IS_ROOT", True)
    host.setattr(bootstrap_ops, "_user_exists", lambda u: False)
    monkeypatch.setattr(Path, "is_dir", lambda self: False)
    monkeypatch.setattr(Path, "is_file", lambda self: False)

    step = _step(bootstrap_ops.plan(sample_config, username="dayz"), "user")
    assert step.state == NEEDED
    assert "no authorized_keys" in step.detail
    assert not any("authorized_keys" in " ".join(c) for c in step.commands)


def test_redhat_uses_wheel_not_sudo(host, sample_config, monkeypatch):
    host.setattr(bootstrap_ops, "IS_ROOT", True)
    host.setattr(bootstrap_ops, "_user_exists", lambda u: False)
    monkeypatch.setattr(Path, "is_dir", lambda self: False)
    monkeypatch.setattr(Path, "is_file", lambda self: False)
    monkeypatch.setattr(Path, "exists", lambda self: str(self) == "/etc/redhat-release")
    step = _step(bootstrap_ops.plan(sample_config, username="dayz"), "user")
    assert any("wheel" in " ".join(c) for c in step.commands)
    assert not any("sudo --append" in " ".join(c) for c in step.commands)


# ---------------------------------------------------------------------------
# Lingering
# ---------------------------------------------------------------------------


def test_lingering_already_on_is_left_alone(host, sample_config):
    step = _step(bootstrap_ops.plan(sample_config), "linger")
    assert step.state == OK
    assert step.commands == []


def test_lingering_off_is_the_one_root_action_a_user_install_needs(host, sample_config):
    host.setattr("app.shared.systemd_scope.linger_enabled", lambda u=None: False)
    step = _step(bootstrap_ops.plan(sample_config), "linger")
    assert step.state == NEEDED
    assert step.commands[0][-3:] == ["loginctl", "enable-linger", "dayz"]


def test_no_loginctl_means_nothing_to_enable(host, sample_config):
    host.setattr(bootstrap_ops.shutil, "which", lambda name: None)
    assert _step(bootstrap_ops.plan(sample_config), "linger").state == SKIPPED


# ---------------------------------------------------------------------------
# Swap
# ---------------------------------------------------------------------------


def test_a_big_box_needs_no_swap(host, sample_config):
    step = _step(bootstrap_ops.plan(sample_config), "swap")
    assert step.state == OK
    assert "12288" in step.detail


def test_a_small_box_with_no_swap_gets_a_swapfile(host, sample_config, monkeypatch):
    host.setattr(bootstrap_ops.vps_readiness, "mem_mb", lambda: (3814, 2000))
    monkeypatch.setattr(Path, "exists", lambda self: False)

    step = _step(bootstrap_ops.plan(sample_config), "swap")
    assert step.state == NEEDED
    flat = [" ".join(c) for c in step.commands]
    assert any("fallocate -l 4G /swapfile" in c for c in flat)
    assert any("mkswap" in c for c in flat)
    assert any("swapon" in c for c in flat)
    # Survives a reboot, and the fstab line is guarded against a duplicate.
    assert any("/etc/fstab" in c and "grep -q" in c for c in flat)


def test_the_printed_fstab_line_pastes_as_the_argv_apply_runs(host, sample_config, monkeypatch):
    """Measured in 2026 Q3: the report printed `sudo sh -c grep -q '^/swapfile '
    /etc/fstab || echo … >> /etc/fstab`. Pasted, that runs `sh -c grep` and then
    the `>> /etc/fstab` as the unprivileged user."""
    from click.testing import CliRunner

    from app.commands import bootstrap

    host.setattr(bootstrap_ops.vps_readiness, "mem_mb", lambda: (3814, 2000))
    monkeypatch.setattr(Path, "exists", lambda self: False)
    monkeypatch.setattr(bootstrap, "load_context", lambda ctx: (sample_config, False))
    fstab_argv = next(
        c for c in _step(bootstrap_ops.plan(sample_config), "swap").commands
        if "/etc/fstab" in c[-1]
    )

    result = CliRunner().invoke(bootstrap.bootstrap_vps, [], obj={})
    assert result.exit_code == 0, result.output
    printed = [line.strip() for line in result.output.splitlines() if "/etc/fstab" in line]
    assert len(printed) == 1, result.output  # one line, not wrapped at 80 columns
    assert shlex.split(printed[0]) == fstab_argv

    # --apply's log line is the same rendering.
    lines: list[str] = []
    bootstrap_ops.apply(
        bootstrap_ops.plan(sample_config), sample_config,
        log=lines.append, run=lambda argv: 0, write=lambda p, c: None,
    )
    logged = next(line for line in lines if "/etc/fstab" in line)
    assert shlex.split(logged.strip().removeprefix("$ ")) == fstab_argv


def _cli_apply(host, sample_config, monkeypatch, args):
    """Run the CLI with the executors stubbed: nothing on this host changes."""
    from click.testing import CliRunner

    from app.commands import bootstrap

    monkeypatch.setattr(bootstrap, "load_context", lambda ctx: (sample_config, True))
    host.setattr(bootstrap_ops, "_run_command", lambda argv: 0)
    host.setattr(bootstrap_ops, "_write_file", lambda path, content: None)
    return CliRunner().invoke(bootstrap.bootstrap_vps, args, obj={"non_interactive": True})


def test_the_apply_log_does_not_wrap_the_fstab_command(host, sample_config, monkeypatch):
    """Measured on a real VPS (plans/40 F3): cloud-init's log is 80 columns, and
    the apply log split the fstab command over two lines, so it no longer
    pasted. The report's copy was already one line."""
    host.setattr(bootstrap_ops.vps_readiness, "mem_mb", lambda: (3814, 2000))
    monkeypatch.setattr(Path, "exists", lambda self: False)
    fstab_argv = next(
        c for c in _step(bootstrap_ops.plan(sample_config), "swap").commands
        if "/etc/fstab" in c[-1]
    )

    result = _cli_apply(host, sample_config, monkeypatch, ["--apply"])
    assert result.exit_code == 0, result.output
    printed = [line.strip() for line in result.output.splitlines() if "/etc/fstab" in line]
    assert len(printed) == 2, result.output  # the report's line and the log's, one each
    for line in printed:
        assert shlex.split(line.removeprefix("$ ")) == fstab_argv


def _swap_unsettled(host, monkeypatch):
    host.setattr(bootstrap_ops.vps_readiness, "mem_mb", lambda: (1915, 1500))
    host.setattr(bootstrap_ops.vps_readiness, "disk_free_gb", lambda p: 28.0)
    monkeypatch.setattr(Path, "exists", lambda self: False)


def test_the_report_does_not_call_a_host_with_an_unknown_row_bootstrapped(
    host, sample_config, monkeypatch
):
    """Measured on a real VPS (plans/40 F2): no swap, the Swap row `unknown`,
    and the report ended "this host is already bootstrapped"."""
    _swap_unsettled(host, monkeypatch)

    result = _cli_apply(host, sample_config, monkeypatch, [])
    out = " ".join(result.output.split())
    assert result.exit_code == 0, result.output
    assert "already bootstrapped" not in out
    assert "not fully bootstrapped: Swap — see its row above" in out


def test_apply_does_not_call_a_host_with_an_unknown_row_complete(
    host, sample_config, monkeypatch
):
    _swap_unsettled(host, monkeypatch)
    monkeypatch.setattr("app.shared.systemd_scope.linger_enabled", lambda u=None: False)

    result = _cli_apply(host, sample_config, monkeypatch, ["--apply"])
    out = " ".join(result.output.split())
    assert result.exit_code == 0, result.output
    assert "Bootstrap complete" not in out
    assert "Applied the rest, but this host is not fully bootstrapped: Swap" in out


@pytest.mark.parametrize("argv", [
    ["sudo", "fallocate", "-l", "4G", "/swapfile"],
    ["sh", "-c", "echo '$HOME' \"x\""],
    ["echo", "it's", "a b", ""],
])
def test_display_command_round_trips(argv):
    assert shlex.split(bootstrap_ops.display_command(argv)) == argv


def test_existing_swap_is_left_alone(host, sample_config):
    host.setattr(bootstrap_ops.vps_readiness, "mem_mb", lambda: (3814, 2000))
    host.setattr(bootstrap_ops, "_swap_total_kb", lambda: 2097152)
    assert _step(bootstrap_ops.plan(sample_config), "swap").state == OK


def test_a_swapfile_zsm_did_not_create_is_not_overwritten(host, sample_config, monkeypatch):
    host.setattr(bootstrap_ops.vps_readiness, "mem_mb", lambda: (3814, 2000))
    monkeypatch.setattr(Path, "exists", lambda self: str(self) == "/swapfile")

    step = _step(bootstrap_ops.plan(sample_config), "swap")
    assert step.state == UNKNOWN
    assert step.commands == []


def test_a_40_gb_box_gets_its_swapfile(host, sample_config, monkeypatch):
    """Measured on a VPS (plans/40): 33.6 GB free on a 40 GB
    disk, and the step refused swap because 33.6 - 4 is under MIN_DISK_GB. The
    disk check credits the swapfile back (F3), so it cannot fail it."""
    host.setattr(bootstrap_ops.vps_readiness, "mem_mb", lambda: (1915, 1500))
    host.setattr(bootstrap_ops.vps_readiness, "disk_free_gb", lambda p: 33.6)
    monkeypatch.setattr(Path, "exists", lambda self: False)

    step = _step(bootstrap_ops.plan(sample_config), "swap")
    assert step.state == NEEDED
    assert any("fallocate" in " ".join(c) for c in step.commands)


def test_swap_is_refused_on_a_disk_already_short(host, sample_config, monkeypatch):
    """Under MIN_DISK_GB the 4 GB would come out of space the server needs."""
    host.setattr(bootstrap_ops.vps_readiness, "mem_mb", lambda: (3814, 2000))
    host.setattr(bootstrap_ops.vps_readiness, "disk_free_gb", lambda p: 28.0)
    monkeypatch.setattr(Path, "exists", lambda self: False)

    step = _step(bootstrap_ops.plan(sample_config), "swap")
    assert step.state == UNKNOWN
    assert step.commands == []
    assert "30" in step.detail  # MIN_DISK_GB


def test_unreadable_ram_is_unknown_not_needed(host, sample_config):
    host.setattr(bootstrap_ops.vps_readiness, "mem_mb", lambda: (None, None))
    step = _step(bootstrap_ops.plan(sample_config), "swap")
    assert step.state == UNKNOWN


# ---------------------------------------------------------------------------
# File handles
# ---------------------------------------------------------------------------


def test_adequate_limits_are_left_alone(host, sample_config):
    assert _step(bootstrap_ops.plan(sample_config), "file_handles").state == OK


def test_low_limits_write_all_three_mechanisms(host, sample_config):
    """limits.conf does not reach a systemd-started process, so the two
    systemd drop-ins are not redundant with it."""
    host.setattr(
        bootstrap_ops.vps_readiness, "file_handle_limits", lambda: (10000, 1024, 4096)
    )
    step = _step(bootstrap_ops.plan(sample_config), "file_handles")
    assert step.state == NEEDED
    paths = {str(p) for p, _c in step.files}
    assert paths == {
        "/etc/sysctl.d/99-zsm.conf",
        "/etc/security/limits.d/99-zsm.conf",
        "/etc/systemd/system.conf.d/99-zsm.conf",
        "/etc/systemd/user.conf.d/99-zsm.conf",
    }
    contents = dict((str(p), c) for p, c in step.files)
    assert "fs.file-max = 100000" in contents["/etc/sysctl.d/99-zsm.conf"]
    assert "dayz hard nofile 65536" in contents["/etc/security/limits.d/99-zsm.conf"]
    assert "DefaultLimitNOFILE=65536" in contents["/etc/systemd/user.conf.d/99-zsm.conf"]


def test_an_unlimited_file_max_is_not_a_shortfall(host, sample_config):
    """/proc/sys/fs/file-max spells "no limit" as LONG_MAX, not as a small number."""
    host.setattr(
        bootstrap_ops.vps_readiness,
        "file_handle_limits",
        lambda: (bootstrap_ops.vps_readiness.NO_LIMIT, 1024, 524288),
    )
    assert _step(bootstrap_ops.plan(sample_config), "file_handles").state == OK


def test_unreadable_limits_are_unknown(host, sample_config):
    host.setattr(bootstrap_ops.vps_readiness, "file_handle_limits", lambda: (None, None, None))
    assert _step(bootstrap_ops.plan(sample_config), "file_handles").state == UNKNOWN


# ---------------------------------------------------------------------------
# Clock
# ---------------------------------------------------------------------------


def test_an_unsynced_clock_turns_ntp_on(host, sample_config):
    host.setattr(bootstrap_ops.vps_readiness, "clock_synced", lambda: False)
    step = _step(bootstrap_ops.plan(sample_config), "timesync")
    assert step.state == NEEDED
    assert step.commands[0][-3:] == ["timedatectl", "set-ntp", "true"]


def test_an_unanswered_clock_probe_is_unknown(host, sample_config):
    host.setattr(bootstrap_ops.vps_readiness, "clock_synced", lambda: None)
    assert _step(bootstrap_ops.plan(sample_config), "timesync").state == UNKNOWN


# ---------------------------------------------------------------------------
# Firewall — delegated, never duplicated
# ---------------------------------------------------------------------------


def test_the_firewall_is_not_touched_without_the_flag(host, sample_config, monkeypatch):
    from app.ops import firewall_ops

    monkeypatch.setattr(
        firewall_ops, "firewall_status",
        lambda config=None: firewall_ops.FirewallStatus(backend="ufw", active=True),
    )
    step = _step(bootstrap_ops.plan(sample_config), "firewall")
    assert step.state == SKIPPED
    assert "zsm service firewall --apply" in step.detail


@pytest.mark.parametrize("flag", [False, True])
def test_a_firewall_already_in_place_is_ok_and_not_reapplied(
    host, sample_config, monkeypatch, flag
):
    """Measured on a real VPS (plans/40 F5): the re-run of `--firewall --apply`
    showed `to do`, asked both questions again and re-ran every rule."""
    from app.ops import firewall_ops

    monkeypatch.setattr(
        firewall_ops, "firewall_status",
        lambda config=None: firewall_ops.FirewallStatus(backend="ufw", active=True),
    )
    monkeypatch.setattr(firewall_ops, "already_applied", lambda config, status=None: True)

    plan = bootstrap_ops.plan(sample_config, firewall=flag)
    assert _step(plan, "firewall").state == OK
    assert plan.actionable == []


def test_with_the_flag_it_hands_over_to_apply_rules(host, sample_config, monkeypatch):
    """The staged-rule lockout gate has one owner; A3 must not grow a copy."""
    from app.ops import firewall_ops

    monkeypatch.setattr(
        firewall_ops, "firewall_status",
        lambda config=None: firewall_ops.FirewallStatus(backend="ufw", active=True),
    )
    calls: list = []
    monkeypatch.setattr(
        firewall_ops, "apply_rules",
        lambda config, **kw: calls.append(kw) or 0,
    )

    plan = bootstrap_ops.plan(sample_config, firewall=True)
    assert _step(plan, "firewall").state == NEEDED

    ran: list = []
    code = bootstrap_ops.apply(
        plan, sample_config, log=lambda _l: None, run=lambda a: ran.append(a) or 0,
        assume_yes=True,
    )
    assert code == 0
    # It delegated rather than running firewall commands itself.
    assert ran == []
    assert calls and calls[0]["assume_yes"] is True


def test_the_firewalls_own_confirm_is_forwarded_not_bypassed(host, sample_config, monkeypatch):
    """apply_rules refuses when given neither a prompt nor an explicit yes, and
    that safeguard must survive being called from here."""
    from app.ops import firewall_ops

    monkeypatch.setattr(
        firewall_ops, "firewall_status",
        lambda config=None: firewall_ops.FirewallStatus(backend="ufw", active=True),
    )
    calls: list = []
    monkeypatch.setattr(
        firewall_ops, "apply_rules", lambda config, **kw: calls.append(kw) or 0
    )
    asked: list = []

    plan = bootstrap_ops.plan(sample_config, firewall=True)
    bootstrap_ops.apply(
        plan, sample_config, log=lambda _l: None, run=lambda a: 0,
        assume_yes=False, confirm=lambda cmds: asked.append(cmds) or True,
    )
    assert calls[0]["assume_yes"] is False
    assert calls[0]["confirm"] is not None


# ---------------------------------------------------------------------------
# apply()
# ---------------------------------------------------------------------------


def test_apply_writes_files_before_running_commands(host, sample_config):
    host.setattr(
        bootstrap_ops.vps_readiness, "file_handle_limits", lambda: (10000, 1024, 4096)
    )
    plan = bootstrap_ops.plan(sample_config)
    order: list[str] = []
    bootstrap_ops.apply(
        plan, sample_config,
        log=lambda _l: None,
        run=lambda argv: order.append(f"run:{argv[-1]}") or 0,
        write=lambda path, content: order.append(f"write:{path.name}"),
    )
    assert order[0].startswith("write:")
    assert any(o.startswith("run:") for o in order)
    assert order.index("run:--system") > max(
        i for i, o in enumerate(order) if o.startswith("write:")
    )


def test_a_failing_command_stops_that_step_and_reports(host, sample_config):
    host.setattr("app.shared.systemd_scope.linger_enabled", lambda u=None: False)
    host.setattr(bootstrap_ops.vps_readiness, "clock_synced", lambda: False)
    plan = bootstrap_ops.plan(sample_config)

    lines: list[str] = []
    code = bootstrap_ops.apply(
        plan, sample_config, log=lines.append, run=lambda argv: 1, write=lambda p, c: None
    )
    assert code == 1
    assert any("failed" in line for line in lines)
    # Both steps were attempted — one failing step does not abandon the rest.
    assert any("Lingering" in line or "lingering" in line for line in lines)
    assert any("clock" in line.lower() for line in lines)


def test_plan_never_calls_the_executors(host, sample_config, monkeypatch):
    """The guarantee the whole test file rests on.

    Written against `_run_command`/`_write_file` rather than against
    `subprocess`: `plan()` does shell out, but only to *read* (`ufw status`,
    `timedatectl show`). A blanket subprocess ban asserted something that was
    never true and is not what keeps a host safe.
    """

    def _boom(*_a, **_k):
        raise AssertionError("plan() must not execute a step")

    monkeypatch.setattr(bootstrap_ops, "_run_command", _boom)
    monkeypatch.setattr(bootstrap_ops, "_write_file", _boom)
    bootstrap_ops.plan(sample_config, username="dayz", firewall=True)


def test_nothing_to_do_is_an_empty_actionable_list(host, sample_config, monkeypatch):
    from app.ops import firewall_ops

    monkeypatch.setattr(
        firewall_ops, "firewall_status",
        lambda config=None: firewall_ops.FirewallStatus(backend=None, active=None),
    )
    plan = bootstrap_ops.plan(sample_config)
    assert plan.actionable == []
