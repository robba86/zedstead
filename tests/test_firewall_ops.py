"""Tests for app/ops/firewall_ops.py.

Every backend interaction is subprocess output, so every test replaces
``subprocess.run``. **No test may execute a real ufw/firewall-cmd command** —
`_guard` installs a fake that fails the test if the argv reaches a real
firewall binary, which is what most of these assertions actually check.
See plans/22.
"""

from __future__ import annotations

import subprocess

import pytest

from app.ops import firewall_ops
from app.shared.config import DayzConfig


class _Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _guard(monkeypatch, responses: dict | None = None, *, forbidden=("ufw", "firewall-cmd")):
    """Patch subprocess.run with a canned-response fake.

    `responses` maps a substring of the joined argv to a _Proc. Any argv whose
    first element is in `forbidden` and is not explicitly canned fails the test
    — that is how "this code path must not touch the firewall" is asserted.
    """
    responses = responses or {}
    calls: list[list[str]] = []

    def fake_run(argv, *_a, **_k):
        calls.append(list(argv))
        joined = " ".join(argv)
        for key, proc in responses.items():
            if key in joined:
                return proc
        binary = next((part for part in argv if part not in ("sudo", "-n")), "")
        if binary in forbidden:
            raise AssertionError(f"test ran a real firewall command: {joined}")
        return _Proc(returncode=1, stderr="not stubbed")

    monkeypatch.setattr(firewall_ops.subprocess, "run", fake_run)
    return calls


def _config(tmp_path, **kwargs):
    cfg = DayzConfig(
        server_dir=str(tmp_path / "server"),
        steamcmd_dir=str(tmp_path / "steamcmd"),
        **kwargs,
    )
    (tmp_path / "server").mkdir(parents=True, exist_ok=True)
    return cfg


@pytest.fixture(autouse=True)
def _no_host_ssh_config(monkeypatch, tmp_path_factory):
    """Every source now runs on every `ssh_detect()` call, so an unpinned test
    would read the *host's* /etc/ssh/sshd_config and pass or fail depending on
    whose laptop it runs on. Same trap as `sample_config` not sandboxing
    `workshop_dir` (root CLAUDE.md, Testing). Opt out by re-patching in the
    test. See plans/29 (step 5)."""
    empty = tmp_path_factory.mktemp("no-etc-ssh")
    monkeypatch.setattr(firewall_ops, "SSHD_CONFIG_PATH", empty / "no-sshd-config")
    monkeypatch.setattr(firewall_ops, "SSHD_CONFIG_DIR", empty / "no-etc-ssh")


def _detection(ports=(), source="ss"):
    """An `SshDetection` whose `.source` is `source`.

    Tests patch `ssh_detect`, not `ssh_ports`: detection is a union of four
    sources now and `ssh_ports` is a shim over it, so `apply_rules` and the CLI
    read the object. See plans/29 (step 5).
    """
    if source == "unknown":
        return firewall_ops.SshDetection(sshd_present=True)
    if source == "default":
        return firewall_ops.SshDetection(ports=list(ports))
    return firewall_ops.SshDetection(
        ports=list(ports), sources=(source,), sshd_present=True
    )


# --- required_ports ---------------------------------------------------------


def test_required_ports_derive_from_config(tmp_path):
    cfg = _config(tmp_path, server_port=2402)
    ports = [spec.port for spec in firewall_ops.required_ports(cfg)]
    assert ports[:4] == [2402, 2403, 2404, 2405]
    assert 27016 in ports  # no serverDZ.cfg -> documented fallback
    assert cfg.rcon_port not in ports  # RCon is never a required port here
    assert 2302 not in ports  # nothing hardcoded to the default port


def test_required_ports_uses_servercfg_query_port(tmp_path):
    cfg = _config(tmp_path, server_port=2402)
    cfg.server_cfg_path.write_text('steamQueryPort = 2305;\n', encoding="utf-8")
    ports = [spec.port for spec in firewall_ops.required_ports(cfg)]
    assert 2305 in ports
    assert 27016 not in ports


def test_required_ports_deduplicates(tmp_path):
    """A query port inside the game range collapses instead of listing twice."""
    cfg = _config(tmp_path, server_port=2302)
    cfg.server_cfg_path.write_text('steamQueryPort = 2304;\n', encoding="utf-8")
    ports = [spec.port for spec in firewall_ops.required_ports(cfg)]
    assert ports == sorted(set(ports))
    assert ports == [2302, 2303, 2304, 2305]


def test_required_ports_read_a_port_stored_as_a_string(tmp_path):
    """A bundle import can store the port as a string; the unit renders
    "2402" as 2402, so the firewall must list the same ports (plans/68 B8)."""
    cfg = _config(tmp_path, server_port="2402")
    ports = [spec.port for spec in firewall_ops.required_ports(cfg)]
    assert ports[:4] == [2402, 2403, 2404, 2405]
    assert all(isinstance(p, int) for p in ports)


@pytest.mark.parametrize("bad", ["abc", "70000", "0", " 2302", None])
def test_required_ports_refuse_a_port_the_unit_would_refuse(tmp_path, bad):
    cfg = _config(tmp_path, server_port=bad)
    with pytest.raises(ValueError, match=r"Game port \(-port\) must be a number from 1 to 65535"):
        firewall_ops.required_ports(cfg)


# --- detect_backend ---------------------------------------------------------


def test_detect_backend_prefers_ufw(monkeypatch):
    monkeypatch.setattr(firewall_ops, "IS_WINDOWS", False)
    monkeypatch.setattr(firewall_ops.shutil, "which", lambda name: f"/usr/sbin/{name}")
    assert firewall_ops.detect_backend() == "ufw"


def test_detect_backend_firewalld(monkeypatch):
    monkeypatch.setattr(firewall_ops, "IS_WINDOWS", False)
    monkeypatch.setattr(
        firewall_ops.shutil, "which", lambda name: "/usr/bin/firewall-cmd" if name == "firewall-cmd" else None
    )
    assert firewall_ops.detect_backend() == "firewalld"


def test_detect_backend_none(monkeypatch):
    monkeypatch.setattr(firewall_ops, "IS_WINDOWS", False)
    monkeypatch.setattr(firewall_ops.shutil, "which", lambda name: None)
    assert firewall_ops.detect_backend() is None


# --- no backend at all (plans/30, F17/F18) ----------------------------------
#
# A stock Rocky VPS image can ship neither ufw nor firewalld. Both surfaces
# say the same thing about that box, so the words live in firewall_ops.


@pytest.mark.parametrize(
    "ids, expected",
    [
        (["ubuntu", "debian"], "apt-get install ufw"),
        (["debian"], "apt-get install ufw"),
        (["rocky", "rhel", "centos", "fedora"], "dnf install firewalld"),
        (["opensuse-leap", "suse"], "zypper install firewalld"),
        (["arch"], "pacman -S ufw"),
    ],
)
def test_no_backend_advice_names_this_host_package_manager(monkeypatch, ids, expected):
    monkeypatch.setattr(firewall_ops, "_os_release_ids", lambda: ids)
    assert expected in firewall_ops.no_backend_advice()


@pytest.mark.parametrize("ids", [[], ["plan9"]])
def test_no_backend_advice_guesses_no_package_manager(monkeypatch, ids):
    """A wrong package manager is the same bug as a wrong firewall binary, so
    an unrecognised family names both supported backends and stops."""
    monkeypatch.setattr(firewall_ops, "_os_release_ids", lambda: ids)
    advice = firewall_ops.no_backend_advice()
    assert "ufw or firewalld" in advice
    for manager in ("apt-get", "dnf", "zypper", "pacman"):
        assert manager not in advice


def test_no_backend_advice_runs_nothing(monkeypatch):
    """It reads /etc/os-release and nothing else — no probing a box for which
    package manager answers."""
    calls = _guard(monkeypatch, forbidden=("ufw", "firewall-cmd", "apt-get", "dnf"))
    firewall_ops.no_backend_advice()
    assert calls == []


def test_os_release_ids_reads_id_and_id_like(monkeypatch, tmp_path):
    release = tmp_path / "os-release"
    release.write_text('ID="rocky"\nID_LIKE="rhel centos fedora"\nNAME="Rocky Linux"\n')
    monkeypatch.setattr(firewall_ops, "OS_RELEASE_PATH", release)
    assert firewall_ops._os_release_ids() == ["rocky", "rhel", "centos", "fedora"]


def test_os_release_ids_survives_no_file(monkeypatch, tmp_path):
    monkeypatch.setattr(firewall_ops, "OS_RELEASE_PATH", tmp_path / "absent")
    assert firewall_ops._os_release_ids() == []


# --- ssh_ports --------------------------------------------------------------

_SS_OUTPUT = """State  Recv-Q Send-Q Local Address:Port  Peer Address:Port Process
LISTEN 0      4096         0.0.0.0:2222       0.0.0.0:*     users:(("sshd",pid=800,fd=3))
LISTEN 0      4096            [::]:2222          [::]:*     users:(("sshd",pid=800,fd=4))
LISTEN 0      4096       127.0.0.1:8433       0.0.0.0:*     users:(("python3",pid=900,fd=7))
"""


def test_ssh_ports_from_ss(monkeypatch):
    _guard(monkeypatch, {"ss -tlnp": _Proc(stdout=_SS_OUTPUT)})
    assert firewall_ops.ssh_ports() == ([2222], "ss")


def test_ssh_ports_falls_back_to_sshd_config(monkeypatch, tmp_path):
    _guard(monkeypatch, {})  # ss and sshd -T both fail
    sshd_config = tmp_path / "sshd_config"
    sshd_config.write_text("#Port 22\nPort 2200\nPermitRootLogin no\n", encoding="utf-8")
    monkeypatch.setattr(firewall_ops, "SSHD_CONFIG_PATH", sshd_config)
    assert firewall_ops.ssh_ports() == ([2200], "sshd_config")


def test_ssh_ports_default_when_sshd_not_running(monkeypatch, tmp_path):
    _guard(monkeypatch, {})
    monkeypatch.setattr(firewall_ops, "SSHD_CONFIG_PATH", tmp_path / "missing")
    monkeypatch.setattr(firewall_ops, "_sshd_running", lambda: False)
    assert firewall_ops.ssh_ports() == ([22], "default")


def test_ssh_ports_unknown_when_all_detection_fails(monkeypatch, tmp_path):
    """sshd is up but unreachable by every probe — the one case where guessing
    22 and flipping the default policy would lock the operator out."""
    _guard(monkeypatch, {})
    monkeypatch.setattr(firewall_ops, "SSHD_CONFIG_PATH", tmp_path / "missing")
    monkeypatch.setattr(firewall_ops, "_sshd_running", lambda: True)
    assert firewall_ops.ssh_ports() == ([], "unknown")


# --- build_apply_commands ---------------------------------------------------


def test_ssh_allow_precedes_default_deny(tmp_path):
    cfg = _config(tmp_path, server_port=2402)
    cmds = [" ".join(c) for c in firewall_ops.build_apply_commands(cfg, backend="ufw", ssh=[2222])]
    ssh_idx = cmds.index("ufw allow 2222/tcp")
    assert ssh_idx < cmds.index("ufw default deny incoming")
    assert ssh_idx < cmds.index("ufw --force enable")
    assert cmds.index("ufw default deny incoming") < cmds.index("ufw --force enable")


def test_build_commands_never_include_panel_or_rcon_port(tmp_path):
    cfg = _config(tmp_path, server_port=2402, rcon_port=2305)
    for backend in ("ufw", "firewalld"):
        cmds = firewall_ops.build_apply_commands(cfg, backend=backend, ssh=[22])
        joined = " ".join(" ".join(c) for c in cmds)
        assert "8433" not in joined
        assert str(cfg.rcon_port) not in joined


def test_build_commands_firewalld_shape(monkeypatch, tmp_path):
    _guard(monkeypatch, {"--get-default-zone": _Proc(stdout="home\n")})
    cfg = _config(tmp_path, server_port=2402)
    cmds = [" ".join(c) for c in firewall_ops.build_apply_commands(cfg, backend="firewalld", ssh=[22])]
    assert cmds[0] == "firewall-cmd --permanent --add-port=22/tcp"
    assert "firewall-cmd --permanent --set-target=DROP --zone=home" in cmds
    assert cmds[-1] == "firewall-cmd --reload"
    assert cmds.index("firewall-cmd --permanent --set-target=DROP --zone=home") > 0


def test_rcon_collision_detected_only_on_overlap(tmp_path):
    """Default install: rcon_port 2305 *is* server_port+3, so allowing the
    client/voice port publishes BattlEye's RCon listener."""
    clashing = _config(tmp_path, server_port=2302, rcon_port=2305)
    assert firewall_ops.rcon_collision(clashing).port == 2305
    moved = _config(tmp_path, server_port=2302, rcon_port=2405)
    assert firewall_ops.rcon_collision(moved) is None


# --- firewall_status --------------------------------------------------------

_UFW_ACTIVE = """Status: active

To                         Action      From
--                         ------      ----
22/tcp                     ALLOW IN    Anywhere
2302/udp                   ALLOW IN    Anywhere
2303:2305/udp              ALLOW IN    Anywhere
27016/udp                  ALLOW IN    Anywhere
2302/udp (v6)              ALLOW IN    Anywhere (v6)
"""


_UFW_ADDED = """Added user rules (see 'ufw status' for running firewall):
ufw allow 2222/tcp
ufw allow 2402/udp
ufw allow 2403/udp
ufw allow 2404/udp
ufw allow 2405/udp
ufw allow 27016/udp
"""


def test_status_parses_ufw_ports_and_ranges(monkeypatch, tmp_path):
    monkeypatch.setattr(firewall_ops, "detect_backend", lambda: "ufw")
    _guard(monkeypatch, {"ufw status": _Proc(stdout=_UFW_ACTIVE)})
    status = firewall_ops.firewall_status(_config(tmp_path))
    assert status.active is True
    assert {2302, 2303, 2304, 2305, 27016} <= status.open_udp
    assert 22 not in status.open_udp  # tcp, not udp
    assert status.error is None


def test_status_inactive(monkeypatch, tmp_path):
    monkeypatch.setattr(firewall_ops, "detect_backend", lambda: "ufw")
    _guard(monkeypatch, {"ufw status": _Proc(stdout="Status: inactive\n")})
    status = firewall_ops.firewall_status(_config(tmp_path))
    assert status.active is False
    assert status.open_udp == set()


def test_status_unknown_on_permission_error(monkeypatch, tmp_path):
    """A refused read is 'unknown', never 'closed' — and never an exception."""
    monkeypatch.setattr(firewall_ops, "detect_backend", lambda: "ufw")
    _guard(
        monkeypatch,
        {"ufw status": _Proc(returncode=1, stderr="ERROR: You need to be root to run this script")},
    )
    status = firewall_ops.firewall_status(_config(tmp_path))
    assert status.active is None
    assert "root" in status.error


def test_status_parses_firewalld_list_all(monkeypatch, tmp_path):
    monkeypatch.setattr(firewall_ops, "detect_backend", lambda: "firewalld")
    _guard(
        monkeypatch,
        {
            "firewall-cmd --state": _Proc(stdout="running\n"),
            "--list-all": _Proc(stdout="public (active)\n  target: default\n  ports: 2302/udp 2303-2305/udp\n  protocols:\n"),
        },
    )
    status = firewall_ops.firewall_status(_config(tmp_path))
    assert status.active is True
    assert {2302, 2303, 2304, 2305} <= status.open_udp


# `firewall-cmd --state` exits non-zero both when firewalld is stopped and when
# polkit refuses the caller, and the old expression mapped both to False — so a
# running firewalld on a RHEL box with a cold sudo timestamp read as `inactive`
# (F26, measured on Rocky 10.2). The last two rows are the regression; the
# first two are here to prove the fix did not buy them with the ability to say
# a firewall is genuinely off.
@pytest.mark.parametrize(
    "proc, expected",
    [
        (_Proc(stdout="running\n"), True),
        # firewalld prints this on *stderr* and exits 252 (NOT_RUNNING).
        (_Proc(returncode=252, stderr="not running\n"), False),
        (
            _Proc(
                returncode=252,
                stderr="Authorization failed.\n"
                       "    Make sure polkit agent is running or run the application "
                       "as superuser.\n",
            ),
            None,
        ),
        (_Proc(returncode=1), None),
    ],
    ids=["running", "not-running", "polkit-refused", "empty"],
)
def test_firewalld_state_is_read_as_text_not_as_an_exit_code(
    monkeypatch, tmp_path, proc, expected
):
    monkeypatch.setattr(firewall_ops, "detect_backend", lambda: "firewalld")
    monkeypatch.setattr(firewall_ops, "sudo_prefix", lambda: [])
    _guard(
        monkeypatch,
        {
            "firewall-cmd --state": proc,
            "--list-all": _Proc(stdout="public (active)\n  target: default\n  ports: 2302/udp\n"),
        },
    )
    status = firewall_ops.firewall_status()
    assert status.active is expected


def test_unreadable_firewalld_state_does_not_silence_the_zone_probe(monkeypatch, tmp_path):
    """The knock-on half of F26: with `active=False` the zone-conflict probe was
    skipped, so the box read as off *and* as having nothing to say about it."""
    monkeypatch.setattr(firewall_ops, "detect_backend", lambda: "firewalld")
    monkeypatch.setattr(firewall_ops, "sudo_prefix", lambda: [])
    _guard(
        monkeypatch,
        {
            "firewall-cmd --state": _Proc(returncode=1, stderr="Authorization failed.\n"),
            "--list-all-zones": _Proc(
                stdout="public (active)\n  target: default\n  ports:\n\n"
                       "dmz\n  target: default\n  ports: 2402/udp\n"
            ),
            "--list-all": _Proc(stdout="public (active)\n  target: default\n  ports:\n"),
        },
    )
    status = firewall_ops.firewall_status(_config(tmp_path, server_port=2402))
    assert status.active is None
    assert status.zone_conflict and "dmz" in status.zone_conflict
    # Unknown is still not a complaint list — `missing_ports` takes the same
    # branch for None as it did for False, and that part is correct.
    assert firewall_ops.missing_ports(_config(tmp_path, server_port=2402), status) == []


def test_missing_ports_empty_when_status_unknown(monkeypatch, tmp_path):
    cfg = _config(tmp_path, server_port=2402)
    unknown = firewall_ops.FirewallStatus(backend="ufw", active=None, error="nope")
    assert firewall_ops.missing_ports(cfg, unknown) == []


def test_missing_ports_lists_gaps(tmp_path):
    cfg = _config(tmp_path, server_port=2402)
    status = firewall_ops.FirewallStatus(backend="ufw", active=True, open_udp={2402, 2403})
    assert [s.port for s in firewall_ops.missing_ports(cfg, status)] == [2404, 2405, 27016]


# --- already_applied (plans/40 F5) ------------------------------------------

# Verbatim `ufw status verbose` from a VPS right after
# `bootstrap-vps --firewall --apply`.
_UFW_AFTER_APPLY = """Status: active
Logging: on (low)
Default: deny (incoming), allow (outgoing), disabled (routed)
New profiles: skip

To                         Action      From
--                         ------      ----
22/tcp                     ALLOW IN    Anywhere
2302/udp                   ALLOW IN    Anywhere
2303/udp                   ALLOW IN    Anywhere
2304/udp                   ALLOW IN    Anywhere
2305/udp                   ALLOW IN    Anywhere
27016/udp                  ALLOW IN    Anywhere
22/tcp (v6)                ALLOW IN    Anywhere (v6)
2302/udp (v6)              ALLOW IN    Anywhere (v6)
2303/udp (v6)              ALLOW IN    Anywhere (v6)
2304/udp (v6)              ALLOW IN    Anywhere (v6)
2305/udp (v6)              ALLOW IN    Anywhere (v6)
27016/udp (v6)             ALLOW IN    Anywhere (v6)
"""


def _ufw_status(raw):
    active, udp, tcp = firewall_ops._parse_ufw_status(raw)
    return firewall_ops.FirewallStatus(
        backend="ufw", active=active, open_udp=udp, open_tcp=tcp, raw=raw
    )


def _ssh_on(monkeypatch, *ports):
    monkeypatch.setattr(
        firewall_ops, "ssh_detect",
        lambda: firewall_ops.SshDetection(ports=list(ports), sources=("ss",), sshd_present=True),
    )


def test_a_box_apply_just_configured_reads_as_applied(monkeypatch, tmp_path):
    _ssh_on(monkeypatch, 22)
    assert firewall_ops.already_applied(_config(tmp_path), _ufw_status(_UFW_AFTER_APPLY))


@pytest.mark.parametrize("raw, ssh", [
    (_UFW_AFTER_APPLY.replace("Default: deny (incoming)", "Default: allow (incoming)"), (22,)),
    (_UFW_AFTER_APPLY.replace("27016/udp ", "27017/udp "), (22,)),
    (_UFW_AFTER_APPLY, (2222,)),
    (_UFW_AFTER_APPLY, ()),
    (_UFW_AFTER_APPLY.replace("Status: active", "Status: inactive"), (22,)),
], ids=["default-allow", "port-missing", "ssh-elsewhere", "ssh-unknown", "inactive"])
def test_anything_short_of_what_apply_leaves_is_not_applied(monkeypatch, tmp_path, raw, ssh):
    _ssh_on(monkeypatch, *ssh)
    assert not firewall_ops.already_applied(_config(tmp_path), _ufw_status(raw))


def test_an_unreadable_firewall_is_not_applied(monkeypatch, tmp_path):
    _ssh_on(monkeypatch, 22)
    status = firewall_ops.FirewallStatus(backend="ufw", active=None, error="needs root")
    assert not firewall_ops.already_applied(_config(tmp_path), status)


@pytest.mark.parametrize("target, applied", [("DROP", True), ("default", False)])
def test_firewalld_counts_as_applied_only_with_the_drop_target(
    monkeypatch, tmp_path, target, applied
):
    _ssh_on(monkeypatch, 22)
    raw = f"public (active)\n  target: {target}\n  ports: 22/tcp 2302-2305/udp 27016/udp\n"
    status = firewall_ops.FirewallStatus(
        backend="firewalld", active=True, open_udp={2302, 2303, 2304, 2305, 27016},
        open_tcp={22}, raw=raw,
    )
    assert firewall_ops.already_applied(_config(tmp_path), status) is applied


# --- apply_rules: the refusals ---------------------------------------------


def test_apply_refuses_when_ssh_unknown(monkeypatch, tmp_path):
    """The lockout test. Nothing may reach ufw when SSH detection failed."""
    monkeypatch.setattr(firewall_ops, "IS_WINDOWS", False)
    monkeypatch.setattr(firewall_ops, "IS_DOCKER", False)
    monkeypatch.setattr(firewall_ops, "detect_backend", lambda: "ufw")
    monkeypatch.setattr(firewall_ops, "ssh_detect", lambda: _detection([], "unknown"))
    calls = _guard(monkeypatch, {})
    logged: list[str] = []

    code = firewall_ops.apply_rules(
        _config(tmp_path), assume_yes=True, log=logged.append,
    )

    assert code != 0
    assert not any("ufw" in " ".join(call) for call in calls)
    assert any("lock you out" in line for line in logged)


def test_apply_refuses_in_container(monkeypatch, tmp_path):
    monkeypatch.setattr(firewall_ops, "IS_WINDOWS", False)
    monkeypatch.setattr(firewall_ops, "IS_DOCKER", True)
    calls = _guard(monkeypatch, {})
    logged: list[str] = []

    code = firewall_ops.apply_rules(_config(tmp_path), assume_yes=True, log=logged.append)

    assert code != 0
    assert calls == []
    assert any("container" in line for line in logged)


def test_apply_refuses_without_backend(monkeypatch, tmp_path):
    """It used to print nine `sudo ufw …` lines here "so it still shows what it
    would have run" — for a binary that is not installed, under `apt-get` on a
    box whose package manager is `dnf`, with SSH hardcoded to 22. That was the
    assertion this test carried until F24; the honest answer is the advice for
    *this* host and no command list at all, which is what `--dry-run` and doctor
    have printed since plan 30 package C."""
    monkeypatch.setattr(firewall_ops, "IS_WINDOWS", False)
    monkeypatch.setattr(firewall_ops, "IS_DOCKER", False)
    monkeypatch.setattr(firewall_ops, "detect_backend", lambda: None)
    monkeypatch.setattr(firewall_ops, "_os_release_ids", lambda: ["rocky", "rhel"])
    calls = _guard(monkeypatch, {})
    logged: list[str] = []

    code = firewall_ops.apply_rules(_config(tmp_path), assume_yes=True, log=logged.append)

    assert code != 0
    assert not any("ufw" in " ".join(call) for call in calls)
    text = "\n".join(logged)
    assert "dnf install firewalld" in text
    assert "ufw allow" not in text and "apt-get" not in text
    # No allow rule, no policy line, and above all no guessed SSH port.
    assert "22/tcp" not in text


def test_no_apply_rules_message_names_one_backend_as_if_it_were_the_only_one():
    """The tripwire for F24/F25's whole class. Both were a message inside
    `apply_rules` that said "ufw" on a box running firewalld, and a third
    survived plans/31 B: the Docker refusal said Docker bypasses *ufw's*
    chains, which is equally true of firewalld and was reachable on RHEL.

    A message may name both — "Linux-only (ufw/firewalld)" is correct — and
    comments may say whatever is historically accurate. What must not happen
    again is user-facing output picking one.

    Walks the AST rather than the lines: the Docker message spans three source
    lines and the word `ufw` was on the second of them, so a line-by-line
    version of this test passed while the bug was in front of it.
    """
    import ast
    from pathlib import Path

    tree = ast.parse(Path(firewall_ops.__file__).read_text(encoding="utf-8"))
    func = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "apply_rules"
    )

    offenders = []
    for node in ast.walk(func):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "log"):
            continue
        text = " ".join(
            part.value for part in ast.walk(node)
            if isinstance(part, ast.Constant) and isinstance(part.value, str)
        )
        named = {b for b in ("ufw", "firewall-cmd", "firewalld") if b in text}
        if named == {"ufw"}:
            offenders.append(f"line {node.lineno}: {text[:90]}")
    assert not offenders, (
        "apply_rules output names ufw without naming firewalld:\n" + "\n".join(offenders)
    )


@pytest.mark.parametrize(
    "backend, expected, forbidden",
    [
        ("ufw", "ufw allow <port>/tcp", "firewall-cmd"),
        ("firewalld", "firewall-cmd --permanent --add-port=<port>/tcp", "ufw allow"),
    ],
)
def test_apply_refusal_names_the_backend_this_box_has(
    monkeypatch, tmp_path, backend, expected, forbidden
):
    """F25. This is the branch a RHEL box takes from a cold sudo timestamp —
    every SSH source there needs root, so the union is empty — and since F29 the
    refusal is the only thing on screen telling the operator what to type."""
    monkeypatch.setattr(firewall_ops, "IS_WINDOWS", False)
    monkeypatch.setattr(firewall_ops, "IS_DOCKER", False)
    monkeypatch.setattr(firewall_ops, "detect_backend", lambda: backend)
    monkeypatch.setattr(firewall_ops, "ssh_detect", lambda: _detection([], "unknown"))
    calls = _guard(monkeypatch, {})
    logged: list[str] = []

    code = firewall_ops.apply_rules(_config(tmp_path), assume_yes=True, log=logged.append)

    assert code != 0
    assert calls == []
    text = "\n".join(logged)
    assert expected in text
    assert forbidden not in text


def test_apply_refuses_without_confirmation(monkeypatch, tmp_path):
    """No assume_yes and no confirm callback = refusal, so a caller that
    forgets to wire up a prompt cannot apply by accident."""
    monkeypatch.setattr(firewall_ops, "IS_WINDOWS", False)
    monkeypatch.setattr(firewall_ops, "IS_DOCKER", False)
    monkeypatch.setattr(firewall_ops, "detect_backend", lambda: "ufw")
    monkeypatch.setattr(firewall_ops, "ssh_detect", lambda: _detection([22], "default"))
    calls = _guard(monkeypatch, {})

    code = firewall_ops.apply_rules(_config(tmp_path), log=lambda _l: None)

    assert code != 0
    assert not any("ufw" in " ".join(call) for call in calls)


def test_apply_declined_confirmation_changes_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(firewall_ops, "IS_WINDOWS", False)
    monkeypatch.setattr(firewall_ops, "IS_DOCKER", False)
    monkeypatch.setattr(firewall_ops, "detect_backend", lambda: "ufw")
    monkeypatch.setattr(firewall_ops, "ssh_detect", lambda: _detection([22], "default"))
    calls = _guard(monkeypatch, {})

    code = firewall_ops.apply_rules(_config(tmp_path), confirm=lambda _lines: False, log=lambda _l: None)

    assert code != 0
    assert not any("ufw" in " ".join(call) for call in calls)


# --- apply_rules: the happy path and the half-applied path ------------------


def test_apply_runs_commands_in_order(monkeypatch, tmp_path):
    monkeypatch.setattr(firewall_ops, "IS_WINDOWS", False)
    monkeypatch.setattr(firewall_ops, "IS_DOCKER", False)
    monkeypatch.setattr(firewall_ops, "detect_backend", lambda: "ufw")
    monkeypatch.setattr(firewall_ops, "ssh_detect", lambda: _detection([2222], "ss"))
    monkeypatch.setattr(firewall_ops, "sudo_prefix", lambda: [])
    calls = _guard(
        monkeypatch,
        {"ufw show added": _Proc(stdout=_UFW_ADDED), "ufw": _Proc(stdout="Rule added")},
    )

    code = firewall_ops.apply_rules(
        _config(tmp_path, server_port=2402), assume_yes=True, log=lambda _l: None
    )

    assert code == 0
    joined = [" ".join(c) for c in calls]
    assert joined[0] == "ufw allow 2222/tcp"
    assert joined[-1] == "ufw --force enable"
    assert joined.index("ufw allow 2222/tcp") < joined.index("ufw default deny incoming")


def test_apply_stops_and_names_the_failed_step(monkeypatch, tmp_path):
    """A half-applied firewall is the dangerous state, so the failure has to
    name the step and how to undo it."""
    monkeypatch.setattr(firewall_ops, "IS_WINDOWS", False)
    monkeypatch.setattr(firewall_ops, "IS_DOCKER", False)
    monkeypatch.setattr(firewall_ops, "detect_backend", lambda: "ufw")
    monkeypatch.setattr(firewall_ops, "ssh_detect", lambda: _detection([22], "default"))
    monkeypatch.setattr(firewall_ops, "sudo_prefix", lambda: [])
    calls = _guard(
        monkeypatch,
        {
            "ufw allow 22/tcp": _Proc(stdout="Rule added"),
            "ufw allow 2402/udp": _Proc(returncode=1, stderr="ERROR: bad port"),
            "ufw": _Proc(stdout="ok"),
        },
    )
    logged: list[str] = []

    code = firewall_ops.apply_rules(
        _config(tmp_path, server_port=2402), assume_yes=True, log=logged.append
    )

    assert code != 0
    assert any("FAILED at: ufw allow 2402/udp" in line for line in logged)
    assert any("ufw disable" in line for line in logged)
    # stopped at the failure — the deny policy was never set
    assert not any("default deny" in " ".join(c) for c in calls)


def test_apply_warns_on_rcon_port_collision(monkeypatch, tmp_path):
    monkeypatch.setattr(firewall_ops, "IS_WINDOWS", False)
    monkeypatch.setattr(firewall_ops, "IS_DOCKER", False)
    monkeypatch.setattr(firewall_ops, "detect_backend", lambda: "ufw")
    monkeypatch.setattr(firewall_ops, "ssh_detect", lambda: _detection([22], "default"))
    monkeypatch.setattr(firewall_ops, "sudo_prefix", lambda: [])
    _guard(monkeypatch, {"ufw": _Proc(stdout="ok")})
    logged: list[str] = []

    firewall_ops.apply_rules(
        _config(tmp_path, server_port=2302, rcon_port=2305),
        assume_yes=True,
        log=logged.append,
    )

    assert any("RCon port 2305" in line for line in logged)


def test_apply_refuses_when_sudo_needs_a_password_without_a_tty(monkeypatch, tmp_path):
    """A password prompt with no tty hangs forever — same reasoning as
    systemd_scope.query's sudo -n."""
    monkeypatch.setattr(firewall_ops, "IS_WINDOWS", False)
    monkeypatch.setattr(firewall_ops, "IS_DOCKER", False)
    monkeypatch.setattr(firewall_ops, "detect_backend", lambda: "ufw")
    monkeypatch.setattr(firewall_ops, "ssh_detect", lambda: _detection([22], "default"))
    monkeypatch.setattr(firewall_ops, "sudo_prefix", lambda: ["sudo"])
    monkeypatch.setattr("app.shared.platform.sudo_available_noninteractive", lambda: False)
    calls = _guard(monkeypatch, {})
    logged: list[str] = []

    code = firewall_ops.apply_rules(
        _config(tmp_path), assume_yes=True, log=logged.append, interactive=False
    )

    assert code != 0
    assert not any("ufw" in " ".join(call) for call in calls)
    assert any("no terminal" in line for line in logged)


# --- sshd_config Include (plans/27, D1) -------------------------------------


def _dropin(tmp_path, main_text: str, dropin_text: str = "Port 2222\n"):
    dropin_dir = tmp_path / "sshd_config.d"
    dropin_dir.mkdir()
    (dropin_dir / "50-cloud-init.conf").write_text(dropin_text, encoding="utf-8")
    main = tmp_path / "sshd_config"
    main.write_text(main_text.format(dir=dropin_dir), encoding="utf-8")
    return main


def test_sshd_config_follows_include(tmp_path):
    """Ubuntu 22.04+/Debian 12 cloud images put the real port in a drop-in."""
    main = _dropin(tmp_path, "Include {dir}/*.conf\n#Port 22\nPermitRootLogin no\n")
    assert firewall_ops._ports_from_sshd_config(main) == [2222]


def test_drop_in_port_wins_over_main_file_port(tmp_path):
    """The lockout case: an *uncommented* `Port 22` in the main file while a
    drop-in moved sshd to 2222. Reporting only 22 and then denying by default
    cuts the live session. Both ports must come back — the union is the safe
    shape, since an extra allow rule costs nothing and a missing one costs the
    box."""
    main = _dropin(tmp_path, "Include {dir}/*.conf\nPort 22\n")
    assert firewall_ops._ports_from_sshd_config(main) == [22, 2222]


def test_sshd_config_include_relative_to_etc_ssh(monkeypatch, tmp_path):
    """sshd resolves a relative Include against its config dir, not the CWD."""
    monkeypatch.setattr(firewall_ops, "SSHD_CONFIG_DIR", tmp_path)
    (tmp_path / "sshd_config.d").mkdir()
    (tmp_path / "sshd_config.d" / "10-port.conf").write_text("Port 2200\n", encoding="utf-8")
    main = tmp_path / "sshd_config"
    main.write_text("Include sshd_config.d/*.conf\n", encoding="utf-8")
    assert firewall_ops._ports_from_sshd_config(main) == [2200]


def test_sshd_config_include_cycle_terminates(tmp_path):
    """Two files including each other must return, not recurse forever."""
    a, b = tmp_path / "a.conf", tmp_path / "b.conf"
    a.write_text(f"Port 2201\nInclude {b}\n", encoding="utf-8")
    b.write_text(f"Port 2202\nInclude {a}\n", encoding="utf-8")
    assert firewall_ops._ports_from_sshd_config(a) == [2201, 2202]


def test_sshd_config_unreadable_include_is_skipped(tmp_path):
    """A glob that matches nothing and a path that does not exist are skipped,
    never raised — this runs on the way to a firewall write."""
    main = tmp_path / "sshd_config"
    main.write_text(
        f"Port 2203\nInclude {tmp_path}/nope.d/*.conf\nInclude {tmp_path}/gone.conf\n",
        encoding="utf-8",
    )
    assert firewall_ops._ports_from_sshd_config(main) == [2203]


def test_sshd_config_include_multiple_patterns_on_one_line(tmp_path):
    other = tmp_path / "other.conf"
    other.write_text("Port 2204\n", encoding="utf-8")
    main = _dropin(tmp_path, f"Include {{dir}}/*.conf {other}\n")
    assert firewall_ops._ports_from_sshd_config(main) == [2204, 2222]


# --- _sshd_running fails safe (plans/27, D4) --------------------------------


def _no_proc(monkeypatch):
    def boom(_self):
        raise OSError("permission denied")

    monkeypatch.setattr(firewall_ops.Path, "iterdir", boom)


def test_sshd_running_assumes_true_when_proc_unreadable(monkeypatch):
    """No pgrep *and* an unreadable /proc is "we could not look", not "sshd is
    not running" — the caller turns False into "assume 22 and deny the rest"."""
    monkeypatch.setattr(firewall_ops, "_run", lambda *_a, **_k: (127, "", "pgrep: not found"))
    _no_proc(monkeypatch)
    assert firewall_ops._sshd_running() is True


def test_sshd_running_false_when_pgrep_answers(monkeypatch):
    """pgrep ran and matched nothing is a real answer and stays False — but
    only once systemd has confirmed there is no ssh socket unit either. Under
    socket activation there is no process to find at all (plans/29, D7)."""
    monkeypatch.setattr(
        firewall_ops, "_run",
        lambda argv, **_k: (0, "LoadState=not-found\n", "") if argv[0] == "systemctl" else (1, "", ""),
    )
    assert firewall_ops._sshd_running() is False


def test_ssh_ports_refuses_rather_than_guessing_22_when_undetectable(monkeypatch, tmp_path):
    """The end-to-end consequence of D4: nothing could be established, so the
    answer is 'unknown' (which apply_rules refuses on), never 22."""
    _guard(monkeypatch, {"pgrep": _Proc(returncode=127, stderr="pgrep: not found")})
    monkeypatch.setattr(firewall_ops, "SSHD_CONFIG_PATH", tmp_path / "missing")
    _no_proc(monkeypatch)
    assert firewall_ops.ssh_ports() == ([], "unknown")


# --- ss under sudo -n (plans/27, D3) ----------------------------------------


def test_ports_from_ss_retries_under_sudo_n(monkeypatch):
    """`ss -p` names processes only for root, so the unprivileged pass finds
    nothing on a stock VPS and detection would fall through to a config file."""
    calls = _guard(monkeypatch, {"sudo -n ss -tlnp": _Proc(stdout=_SS_OUTPUT)})
    monkeypatch.setattr(firewall_ops, "sudo_prefix", lambda: ["sudo"])

    assert firewall_ops._ports_from_ss().ssh == {2222}

    ss_calls = [c for c in calls if c[-1] == "-tlnp"]
    assert ss_calls[0] == ["ss", "-tlnp"]
    assert ss_calls[1][:2] == ["sudo", "-n"]


def test_ss_retry_does_not_depend_on_the_sudo_timestamp(monkeypatch):
    """D8. The retry used to be gated on `sudo_available_noninteractive()`,
    which probes `sudo -n true` — so whether the authoritative detector ran at
    all was decided by how recently the operator typed a password, and the same
    command gave different answers minutes apart on the same box.

    `sudo -n` cannot prompt and cannot hang; that is what `-n` means and it is
    why plan 27 decision 3 mandates it. The gate bought no safety over just
    running the command, so it is gone. What survives is the half that matters:
    **never a bare `sudo`** (plans/29, D8)."""
    calls = _guard(monkeypatch, {"sudo -n ss -tlnp": _Proc(stdout=_SS_OUTPUT)})
    monkeypatch.setattr(firewall_ops, "sudo_prefix", lambda: ["sudo"])
    monkeypatch.setattr("app.shared.platform.sudo_available_noninteractive", lambda: False)

    scan = firewall_ops._ports_from_ss()

    assert ["sudo", "-n", "ss", "-tlnp"] in calls  # the retry ran despite the cold probe
    assert not any(c[:1] == ["sudo"] and c[1] != "-n" for c in calls)
    assert scan.ssh == {2222}


def test_ports_from_ss_never_retries_with_a_bare_sudo(monkeypatch):
    """A bare `sudo` on a tty-less web poll hangs on the password prompt
    forever — same rule as `systemd_scope.query`."""
    calls = _guard(monkeypatch, {})
    monkeypatch.setattr(firewall_ops, "sudo_prefix", lambda: ["sudo"])

    assert firewall_ops._ports_from_ss().ssh == set()

    assert calls == [["ss", "-tlnp"], ["sudo", "-n", "ss", "-tlnp"]]


def test_ssh_source_is_confident_only_for_the_running_daemon():
    assert firewall_ops.ssh_source_is_confident("ss")
    assert firewall_ops.ssh_source_is_confident("sshd -T")
    assert not firewall_ops.ssh_source_is_confident("sshd_config")
    assert not firewall_ops.ssh_source_is_confident("default")


# --- the verification gate (plans/27, D2) -----------------------------------


def _apply_env(monkeypatch, backend="ufw", ssh=(2222, )):
    monkeypatch.setattr(firewall_ops, "IS_WINDOWS", False)
    monkeypatch.setattr(firewall_ops, "IS_DOCKER", False)
    monkeypatch.setattr(firewall_ops, "detect_backend", lambda: backend)
    monkeypatch.setattr(firewall_ops, "ssh_detect", lambda: _detection(list(ssh), "ss"))
    monkeypatch.setattr(firewall_ops, "sudo_prefix", lambda: [])


def test_split_at_policy_boundary(monkeypatch, tmp_path):
    _guard(monkeypatch, {"--get-default-zone": _Proc(stdout="public\n")})
    cfg = _config(tmp_path, server_port=2402)
    expected = {
        "ufw": "ufw default deny incoming",
        "firewalld": "firewall-cmd --permanent --set-target=DROP --zone=public",
    }
    for backend, first_policy in expected.items():
        cmds = firewall_ops.build_apply_commands(cfg, backend=backend, ssh=[2222])
        allow, policy = firewall_ops.split_at_policy(cmds, backend=backend)
        assert allow + policy == [list(c) for c in cmds]
        assert " ".join(policy[0]) == first_policy
        assert any("2222/tcp" in " ".join(c) for c in allow)
        joined_allow = " ".join(" ".join(c) for c in allow)
        assert "default" not in joined_allow and "set-target" not in joined_allow


def test_apply_aborts_when_ssh_rule_not_in_table(monkeypatch, tmp_path):
    """The central test of plans/27. `ufw allow 2222/tcp` exiting 0 is not
    proof the rule is in the table, and the very next command denies everything
    else — so the table is read back *before* the policy flips."""
    _apply_env(monkeypatch)
    calls = _guard(
        monkeypatch,
        {
            "ufw show added": _Proc(
                stdout="Added user rules (see 'ufw status' for running firewall):\n"
                       "ufw allow 2402/udp\n"
            ),
            "ufw": _Proc(stdout="Rule added"),
        },
    )
    logged: list[str] = []

    code = firewall_ops.apply_rules(
        _config(tmp_path, server_port=2402), assume_yes=True, log=logged.append
    )

    assert code != 0
    joined = [" ".join(c) for c in calls]
    assert not any("default deny" in line for line in joined)
    assert not any("--force enable" in line for line in joined)
    assert any("2222/tcp is not in the firewall table" in line for line in logged)
    assert any("Nothing has been locked down" in line for line in logged)


def test_apply_aborts_when_status_unverifiable(monkeypatch, tmp_path):
    """Could not read the table at all is not 'the rule is missing' and not
    'the rule is there' — it is no evidence, so the policy must not flip."""
    _apply_env(monkeypatch)
    refused = _Proc(returncode=1, stderr="ERROR: You need to be root to run this script")
    calls = _guard(
        monkeypatch,
        {
            "ufw show added": refused,
            "ufw status": refused,
            "ufw": _Proc(stdout="Rule added"),
        },
    )
    logged: list[str] = []

    code = firewall_ops.apply_rules(
        _config(tmp_path, server_port=2402), assume_yes=True, log=logged.append
    )

    assert code != 0
    assert not any("default deny" in " ".join(c) for c in calls)
    assert any("could not be read back" in line for line in logged)


def test_apply_proceeds_when_ssh_rule_present(monkeypatch, tmp_path):
    _apply_env(monkeypatch)
    calls = _guard(
        monkeypatch,
        {"ufw show added": _Proc(stdout=_UFW_ADDED), "ufw": _Proc(stdout="Rule added")},
    )
    logged: list[str] = []

    code = firewall_ops.apply_rules(
        _config(tmp_path, server_port=2402), assume_yes=True, log=logged.append
    )

    assert code == 0
    joined = [" ".join(c) for c in calls]
    assert joined.index("ufw allow 2222/tcp") < joined.index("ufw show added")
    assert joined.index("ufw show added") < joined.index("ufw default deny incoming")
    assert any("Verified: SSH port(s) 2222" in line for line in logged)


def test_apply_gate_reads_permanent_zone_for_firewalld(monkeypatch, tmp_path):
    """firewalld's allow rules are `--permanent`, so they are absent from the
    runtime zone until `--reload` — reading the live view would abort every
    run."""
    _apply_env(monkeypatch, backend="firewalld")
    calls = _guard(
        monkeypatch,
        {
            "--get-default-zone": _Proc(stdout="public\n"),
            "--permanent --list-all": _Proc(
                stdout="public\n  target: default\n  services: ssh\n  ports: 2222/tcp 2402/udp\n"
            ),
            "firewall-cmd": _Proc(stdout="success"),
        },
    )

    code = firewall_ops.apply_rules(
        _config(tmp_path, server_port=2402), assume_yes=True, log=lambda _l: None
    )

    assert code == 0
    joined = [" ".join(c) for c in calls]
    gate = next(i for i, line in enumerate(joined) if "--permanent --list-all" in line)
    assert gate < next(i for i, line in enumerate(joined) if "--set-target=DROP" in line)


_ROCKY_PUBLIC_ZONE = (
    "public\n"
    "  target: default\n"
    "  services: cockpit dhcpv6-client ssh\n"
    "  ports: 2222/tcp 2402/udp\n"
)


def test_apply_names_the_services_drop_left_open(monkeypatch, tmp_path):
    """F28. `--set-target=DROP` is the zone's *fallthrough*; services already in
    the zone survive it, so it is not the promise `ufw default deny incoming`
    is. Measured on Rocky after a successful apply: 8433 timed out from outside
    while cockpit's 9090 answered `Connection refused`."""
    _apply_env(monkeypatch, backend="firewalld")
    calls = _guard(
        monkeypatch,
        {
            "--get-default-zone": _Proc(stdout="public\n"),
            "--permanent --list-all": _Proc(stdout=_ROCKY_PUBLIC_ZONE),
            "firewall-cmd": _Proc(stdout="success"),
        },
    )
    logged: list[str] = []

    code = firewall_ops.apply_rules(
        _config(tmp_path, server_port=2402), assume_yes=True, log=logged.append
    )

    assert code == 0
    text = "\n".join(logged)
    assert "cockpit" in text and "dhcpv6-client" in text
    # ssh is the one service `_firewalld_service_tcp` knows, so it gets a port;
    # the other two are the coverage gap and must survive as bare names rather
    # than being dropped silently.
    assert "ssh  (22/tcp)" in text
    assert "--remove-service=<name>" in text

    # Output only: this package must not close anything. The written rule set is
    # exactly what `build_apply_commands` produces, in order.
    written = [
        c for c in calls
        if c[:1] == ["firewall-cmd"]
        and "--list-all" not in c and "--get-default-zone" not in c
    ]
    expected = firewall_ops.build_apply_commands(
        _config(tmp_path, server_port=2402), backend="firewalld", ssh=[2222]
    )
    assert written == [list(c) for c in expected]
    assert not any("--remove-service" in " ".join(c) for c in calls)


def test_zone_services_note_is_silent_without_a_services_line(monkeypatch):
    """No `services:` line means the zone listing could not be read that far —
    same rule as `_parse_firewalld_ports`, an absent line is not an empty set."""
    assert firewall_ops._zone_services_note("public\n  target: DROP\n  ports:\n") == []


def test_ufw_added_ignores_source_restricted_rule():
    """`ufw allow from 1.2.3.4 to any port 22` leaves the box reachable from a
    single address. Counting it as a general SSH allow would let the gate pass
    a run that is about to lock everyone else out."""
    open_udp, open_tcp = firewall_ops._parse_ufw_added(
        "Added user rules (see 'ufw status' for running firewall):\n"
        "ufw allow from 1.2.3.4 to any port 22 proto tcp\n"
        "ufw allow 2402/udp\n"
    )
    assert 22 not in open_tcp
    assert open_udp == {2402}


def test_ufw_added_reads_a_protocol_less_rule_as_both():
    """`ufw allow 22` really does cover tcp, so reading it as neither would
    make the gate refuse a box whose SSH rule is fine."""
    open_udp, open_tcp = firewall_ops._parse_ufw_added("ufw allow 22\n")
    assert 22 in open_tcp and 22 in open_udp


# --- firewalld "unknown" vs "closed" (plans/27, D5) -------------------------


def test_firewalld_no_ports_line_is_unknown_not_empty(monkeypatch, tmp_path):
    """No `ports:` line at all means we could not read it. A zone with nothing
    open still prints an empty `ports:`, so an empty set is a real answer and
    this is not — and 'could not tell' must never render as 'closed'."""
    monkeypatch.setattr(firewall_ops, "detect_backend", lambda: "firewalld")
    _guard(
        monkeypatch,
        {
            "firewall-cmd --state": _Proc(stdout="running\n"),
            "--list-all": _Proc(stdout="public (active)\n  target: default\n"),
        },
    )

    status = firewall_ops.firewall_status(_config(tmp_path))

    assert status.active is None
    assert status.error
    assert status.open_udp == set()
    # ...and therefore nothing is reported as missing
    assert firewall_ops.missing_ports(_config(tmp_path), status) == []


def test_firewalld_empty_ports_line_is_a_real_answer(monkeypatch, tmp_path):
    monkeypatch.setattr(firewall_ops, "detect_backend", lambda: "firewalld")
    _guard(
        monkeypatch,
        {
            "firewall-cmd --state": _Proc(stdout="running\n"),
            "--list-all": _Proc(stdout="public (active)\n  services:\n  ports:\n  protocols:\n"),
        },
    )

    status = firewall_ops.firewall_status(_config(tmp_path))

    assert status.active is True
    assert status.error is None
    assert status.open_udp == set()


def test_firewalld_ssh_service_counts_as_open(monkeypatch, tmp_path):
    """A port opened as a service never appears in `ports:`."""
    monkeypatch.setattr(firewall_ops, "detect_backend", lambda: "firewalld")
    _guard(
        monkeypatch,
        {
            "firewall-cmd --state": _Proc(stdout="running\n"),
            "--list-all": _Proc(
                stdout="public (active)\n  services: dhcpv6-client ssh\n  ports: 2302/udp\n"
            ),
        },
    )

    status = firewall_ops.firewall_status(_config(tmp_path))

    assert 22 in status.open_tcp
    assert status.open_udp == {2302}


def test_ufw_status_separates_tcp_from_udp(monkeypatch, tmp_path):
    monkeypatch.setattr(firewall_ops, "detect_backend", lambda: "ufw")
    _guard(monkeypatch, {"ufw status": _Proc(stdout=_UFW_ACTIVE)})

    status = firewall_ops.firewall_status(_config(tmp_path))

    assert 22 in status.open_tcp
    assert 22 not in status.open_udp
    assert 2302 in status.open_udp


# --- the RCon claim is retracted, not restated (plans/27, D6) ---------------


def test_rcon_warning_names_the_measured_exposure_and_the_fix(monkeypatch, tmp_path):
    """Measured in 2026 Q3 on a running server: `ss -ulnp` shows the DayZ
    process holding 0.0.0.0:2305, so one allow rule really does publish RCon.

    The warning must therefore state the exposure (not hedge it, as it did
    while the question was open) *and* name the fix — RConIP 127.0.0.1 in
    beserver_x64.cfg. Naming the exposure without the fix is what makes a
    warning unactionable. See plans/27 (D6)."""
    _apply_env(monkeypatch, ssh=(22,))
    _guard(monkeypatch, {"ufw": _Proc(stdout="ok")})
    logged: list[str] = []

    firewall_ops.apply_rules(
        _config(tmp_path, server_port=2302, rcon_port=2305),
        assume_yes=True,
        log=logged.append,
    )

    joined = " ".join(logged)
    warning = next(line for line in logged if "RCon port 2305" in line)
    assert "0.0.0.0" in warning
    assert "RConIP 127.0.0.1" in joined
    assert "ss -ulnp" in joined


# --- socket-activated sshd (plans/29, D7) -----------------------------------

# Measured on plan 27's test box: under `ssh.socket` activation systemd (pid 1)
# holds the listening socket and there is no process named sshd at all until the
# first connection. Both live detectors looked for that process, so both went
# blind.
_SS_SOCKET_ACTIVATED = """State  Recv-Q Send-Q Local Address:Port  Peer Address:Port Process
LISTEN 0      4096         0.0.0.0:2222       0.0.0.0:*     users:(("systemd",pid=1,fd=159))
LISTEN 0      4096            [::]:2222          [::]:*     users:(("systemd",pid=1,fd=160))
"""

_SOCKET_UNIT_2222 = "LoadState=loaded\nActiveState=active\nListen=[::]:2222 (Stream)\n"
_SOCKET_UNIT_ABSENT = "LoadState=not-found\nActiveState=inactive\n"


def _socket_env(monkeypatch, responses):
    """`_guard` plus the two things every D7 test needs pinned: no sudo (so the
    `ss` retry is deterministic) and `sshd.socket` absent unless said otherwise."""
    monkeypatch.setattr(firewall_ops, "sudo_prefix", lambda: [])
    canned = {"systemctl show sshd.socket": _Proc(stdout=_SOCKET_UNIT_ABSENT)}
    canned.update(responses)
    return _guard(monkeypatch, canned)


def test_socket_activated_ssh_on_nondefault_port_is_detected(monkeypatch):
    """**The central test of plans/29.** SSH on 2222 via `ssh.socket`, no
    process named sshd anywhere, nothing in the config file. Detection used to
    fall all the way through to `([22], "default")` — and `--apply` would then
    allow 22, pass its own gate (22 really is in the table), flip to deny, and
    cut the live session on 2222."""
    _socket_env(
        monkeypatch,
        {
            "ss -tlnp": _Proc(stdout=_SS_SOCKET_ACTIVATED),
            "systemctl show ssh.socket": _Proc(stdout=_SOCKET_UNIT_2222),
            "pgrep": _Proc(returncode=1),
        },
    )

    ports, source = firewall_ops.ssh_ports()

    assert ports == [2222]
    assert source == "ssh.socket"
    assert 22 not in ports
    assert firewall_ops.ssh_source_is_confident(source)


def test_socket_unit_union_beats_a_confident_but_stale_sshd_t(monkeypatch):
    """D7's second variant, and the one first-match-wins cannot see. Under
    socket activation the daemon ignores `Port` in sshd_config entirely —
    `ssh.socket`'s ListenStream is what binds — yet `sshd -T` still reports it,
    and "sshd -T" is a *confident* source. A chain stops there and never looks
    further; a union sees the contradiction and allows both."""
    _socket_env(
        monkeypatch,
        {
            "sshd -T": _Proc(stdout="port 22\npermitrootlogin no\n"),
            "systemctl show ssh.socket": _Proc(stdout=_SOCKET_UNIT_2222),
            "pgrep": _Proc(returncode=1),
        },
    )

    ports, source = firewall_ops.ssh_ports()

    assert ports == [22, 2222]  # first-match-wins stopped at `sshd -T` and returned [22]
    assert set(firewall_ops.ssh_detect().sources) == {"ssh.socket", "sshd -T"}
    # `ssh.socket` outranks `sshd -T` in the single-label view: under socket
    # activation it is the thing that actually binds, and -T is reporting a
    # `Port` the daemon ignores.
    assert source == "ssh.socket"


def test_socket_unit_not_found_is_a_real_negative(monkeypatch):
    """`LoadState=not-found` with rc 0 means the unit is not installed — an
    answer, not a failure. A box with no sshd must still reach the `default`
    branch rather than the new refusal."""
    _socket_env(
        monkeypatch,
        {
            "systemctl show ssh.socket": _Proc(stdout=_SOCKET_UNIT_ABSENT),
            "pgrep": _Proc(returncode=1),
        },
    )

    assert firewall_ops._socket_unit_ports() == (set(), True)
    detection = firewall_ops.ssh_detect()
    assert (detection.ports, detection.source) == ([22], "default")
    assert detection.incomplete == ()


# Rocky Linux 10.2, measured on a real VPS in 2026 Q3. RHEL-family images ship
# `sshd.socket` installed but *disabled*, so it is LoadState=loaded and systemd
# still reports the Listen= it would bind if it were ever started — while the
# daemon that is actually running is sshd.service, on whatever `sshd_config`
# says.
_SOCKET_UNIT_DISABLED_22 = (
    "LoadState=loaded\nActiveState=inactive\n"
    "Listen=0.0.0.0:22 (Stream)\nListen=[::]:22 (Stream)\n"
)

# The same box's unprivileged `ss`: the port is visible, the owner is not.
_SS_RHEL_UNATTRIBUTED = """State  Recv-Q Send-Q Local Address:Port Peer Address:Port Process
LISTEN 0      128          0.0.0.0:2222       0.0.0.0:*
LISTEN 0      128             [::]:2222          [::]:*
"""


def test_loaded_but_inactive_socket_unit_contributes_nothing(monkeypatch):
    """A disabled socket unit holds no listener, so its `Listen=` is config, not
    fact. Reporting it is how a dead unit ends up naming the firewall's SSH
    port."""
    _socket_env(
        monkeypatch,
        {
            "systemctl show ssh.socket": _Proc(stdout=_SOCKET_UNIT_ABSENT),
            "systemctl show sshd.socket": _Proc(stdout=_SOCKET_UNIT_DISABLED_22),
        },
    )

    assert firewall_ops._socket_unit_ports() == (set(), True)


def test_disabled_socket_unit_never_becomes_the_only_source(monkeypatch, tmp_path):
    """**The RHEL lockout, measured on Rocky 10.2.** sshd really listens on 2222
    via sshd.service; `ss` runs unprivileged so it sees the port but not its
    owner; `sshd -T` needs root; and RHEL ships `sshd_config` mode 0600 so the
    config scan gets nothing. That leaves a *disabled* `sshd.socket` as the only
    source that answers — and it answers 22.

    Before the ActiveState check this returned `([22], "ssh.socket")`, a
    **confident** source, so `apply_rules` proceeded: allow 22, deny the rest,
    and plan 27's gate passed because 22/tcp really was in the table. The live
    session on 2222 dies. Afterwards the union is empty, the source is the
    unconfident `default`, and apply refuses instead.
    """
    monkeypatch.setattr(firewall_ops, "SSHD_CONFIG_PATH", tmp_path / "none" / "sshd_config")
    monkeypatch.setattr(firewall_ops, "SSHD_CONFIG_DIR", tmp_path / "none")
    _socket_env(
        monkeypatch,
        {
            "ss -tlnp": _Proc(stdout=_SS_RHEL_UNATTRIBUTED),
            "systemctl show ssh.socket": _Proc(stdout=_SOCKET_UNIT_ABSENT),
            "systemctl show sshd.socket": _Proc(stdout=_SOCKET_UNIT_DISABLED_22),
            "pgrep": _Proc(stdout="2669\n"),
        },
    )

    detection = firewall_ops.ssh_detect()

    assert detection.source != "ssh.socket"
    assert not firewall_ops.ssh_source_is_confident(detection.source)


def test_socket_unit_missing_systemctl_is_a_real_negative(monkeypatch):
    """Socket activation requires systemd by definition, so "there is no
    systemd here" answers the question rather than failing to. Without this
    every non-systemd container would land in the refusal (plans/29, decision
    5)."""
    _socket_env(
        monkeypatch,
        {
            "systemctl": _Proc(returncode=127, stderr="systemctl: not found"),
            "pgrep": _Proc(returncode=1),
        },
    )

    assert firewall_ops._socket_unit_ports() == (set(), True)
    assert firewall_ops.ssh_ports() == ([22], "default")


def test_socket_unit_error_is_incomplete_not_absent(monkeypatch):
    """A systemctl that *exists and errors* established nothing, so it is an
    unknown — and an unknown has to be visible."""
    _socket_env(
        monkeypatch,
        {"systemctl": _Proc(returncode=1, stderr="Failed to connect to bus")},
    )

    assert firewall_ops._socket_unit_ports() == (set(), False)
    assert any("systemd could not be asked" in r for r in firewall_ops.ssh_detect().incomplete)


def test_socket_unit_ignores_unix_and_datagram_listens(monkeypatch):
    """The TCP `Listen=` shape is the one thing plans/29 could not verify on a
    dev box, so the parser must ignore what it does not recognise: a Unix path
    or a Datagram socket yields *nothing*, never a guessed port."""
    _socket_env(
        monkeypatch,
        {
            "systemctl show ssh.socket": _Proc(
                stdout="LoadState=loaded\nListen=/run/foo.sock (Stream)\n"
                       "Listen=[::]:69 (Datagram)\n"
            ),
        },
    )

    assert firewall_ops._socket_unit_ports() == (set(), True)


def test_sshd_running_true_for_an_active_socket_unit(monkeypatch):
    """Pins step 3's ordering. `pgrep` exits 1 and /proc has no sshd, but
    systemd is holding the socket — without this the box falls through to
    `([22], "default")`, which is the lockout."""
    _socket_env(
        monkeypatch,
        {
            "systemctl show ssh.socket": _Proc(stdout=_SOCKET_UNIT_2222),
            "pgrep": _Proc(returncode=1),
        },
    )
    _no_proc(monkeypatch)

    assert firewall_ops._sshd_running() is True


def test_systemd_owned_listener_is_never_taken_as_an_ssh_port(monkeypatch, tmp_path):
    """The STOP condition against the naive fix. systemd owns plenty of
    unrelated sockets — snapd, cups, docker, avahi — so "accept systemd as the
    listener owner" would emit `ufw allow` for ports nobody asked to publish.
    An unattributable systemd listener makes detection *incomplete*; it never
    becomes a port."""
    calls = _socket_env(
        monkeypatch,
        {
            "ss -tlnp": _Proc(
                stdout="State Recv-Q Send-Q Local Address:Port Peer Address:Port Process\n"
                       'LISTEN 0 4096 0.0.0.0:9090 0.0.0.0:* users:(("systemd",pid=1,fd=3))\n'
            ),
            "systemctl show ssh.socket": _Proc(stdout=_SOCKET_UNIT_ABSENT),
            "pgrep": _Proc(returncode=0),  # sshd is up; we just cannot see where
        },
    )
    ports, source = firewall_ops.ssh_ports()

    assert 9090 not in ports
    assert source == "unknown"  # refuse, rather than publish a port nobody asked for
    cmds = firewall_ops.build_apply_commands(
        _config(tmp_path, server_port=2402), backend="ufw", ssh=ports
    )
    assert not any("9090" in " ".join(cmd) for cmd in cmds)
    assert not any("9090" in " ".join(call) for call in calls)


def test_systemd_owned_listener_makes_detection_incomplete(monkeypatch):
    """The other half of the same rule: the listener is not silently ignored
    either. It is why we could not answer, and `apply_rules` prints it."""
    _socket_env(
        monkeypatch,
        {
            "ss -tlnp": _Proc(
                stdout='LISTEN 0 4096 0.0.0.0:9090 0.0.0.0:* users:(("systemd",pid=1,fd=3))\n'
            ),
            "systemctl show ssh.socket": _Proc(stdout=_SOCKET_UNIT_ABSENT),
            "pgrep": _Proc(returncode=0),
        },
    )

    detection = firewall_ops.ssh_detect()

    assert detection.incomplete
    assert any("systemd itself owns the listener" in r for r in detection.incomplete)


# --- detection is a union, and it says what it could not see (plans/29, D8) --


def test_ss_hidden_owner_is_recorded_as_incomplete(monkeypatch):
    """`ss -p` shows process info for *your own* PIDs and hides everyone
    else's, so a listener with an empty owner column is one we were not allowed
    to see — not one that does not exist. Returning a bare list conflated
    those."""
    _socket_env(
        monkeypatch,
        {
            "ss -tlnp": _Proc(
                stdout="LISTEN 0 4096 127.0.0.53%lo:53 0.0.0.0:*\n"
                       "LISTEN 0 4096 127.0.0.1:631 0.0.0.0:*\n"
                       'LISTEN 0 511 127.0.0.1:40496 0.0.0.0:* users:(("code",pid=229847,fd=36))\n'
            ),
            "systemctl show ssh.socket": _Proc(stdout=_SOCKET_UNIT_ABSENT),
            "pgrep": _Proc(returncode=0),
        },
    )

    scan = firewall_ops._ports_from_ss()
    assert scan.hidden == {53, 631}
    assert scan.ssh == set()

    detection = firewall_ops.ssh_detect()
    assert any("would not name the owner" in r for r in detection.incomplete)


def test_ss_retry_resolves_the_hidden_owners_it_named(monkeypatch):
    """F14. `hidden` used to union, so a port the `sudo -n` retry attributed to
    sshd stayed on the "could not name the owner" list — printing
    `SSH: port(s) 2222 (detected via ss)` and, one line below,
    "would not name the owner of port(s) 53, 2222". Measured on Ubuntu 26.04
    and again on Rocky 10.2."""
    _socket_env(
        monkeypatch,
        {
            # Unprivileged: every owner column redacted, including sshd's.
            "sudo -n ss -tlnp": _Proc(
                stdout="LISTEN 0 4096 127.0.0.53%lo:53 0.0.0.0:*"
                       ' users:(("systemd-resolve",pid=700,fd=13))\n'
                       'LISTEN 0 4096 0.0.0.0:2222 0.0.0.0:* users:(("sshd",pid=800,fd=3))\n'
            ),
            "ss -tlnp": _Proc(
                stdout="LISTEN 0 4096 127.0.0.53%lo:53 0.0.0.0:*\n"
                       "LISTEN 0 4096 0.0.0.0:2222 0.0.0.0:*\n"
            ),
            "pgrep": _Proc(returncode=0),
        },
    )
    # _socket_env pins `sudo_prefix` to [] itself, so this comes after it.
    monkeypatch.setattr(firewall_ops, "sudo_prefix", lambda: ["sudo"])

    scan = firewall_ops._ports_from_ss()
    assert scan.ssh == {2222}
    assert scan.hidden == set()  # both were named by the retry

    detection = firewall_ops.ssh_detect()
    assert detection.ports == [2222]
    assert not any("would not name the owner" in r for r in detection.incomplete)


def test_ss_retry_keeps_the_owners_it_still_could_not_name(monkeypatch):
    """The other half, so the fix above cannot be a blanket "drop `hidden`":
    when the privileged pass ran and *still* could not attribute a listener,
    that is a real answer and the note has to survive. `ss` lists every LISTEN
    line whatever your privileges, so the privileged pass is the authority on
    which of them it could not see."""
    _socket_env(
        monkeypatch,
        {
            "sudo -n ss -tlnp": _Proc(
                stdout="LISTEN 0 4096 0.0.0.0:9999 0.0.0.0:*\n"
                       'LISTEN 0 4096 0.0.0.0:2222 0.0.0.0:* users:(("sshd",pid=800,fd=3))\n'
            ),
            "ss -tlnp": _Proc(
                stdout="LISTEN 0 4096 0.0.0.0:9999 0.0.0.0:*\n"
                       "LISTEN 0 4096 0.0.0.0:2222 0.0.0.0:*\n"
            ),
            "pgrep": _Proc(returncode=0),
        },
    )
    # _socket_env pins `sudo_prefix` to [] itself, so this comes after it.
    monkeypatch.setattr(firewall_ops, "sudo_prefix", lambda: ["sudo"])

    scan = firewall_ops._ports_from_ss()
    assert scan.ssh == {2222}
    assert scan.hidden == {9999}

    detection = firewall_ops.ssh_detect()
    assert any("9999" in r for r in detection.incomplete)
    assert not any("2222" in r for r in detection.incomplete)


def test_ss_retry_that_did_not_run_changes_nothing(monkeypatch):
    """A failed `sudo -n` is not evidence that the listeners were attributable.
    Its empty `hidden` must not replace what the unprivileged pass saw — that
    would turn "I was not allowed to look" into "there is nothing there", which
    is the conflation `_SsScan` exists to prevent."""
    _socket_env(
        monkeypatch,
        {
            "ss -tlnp": _Proc(stdout="LISTEN 0 4096 0.0.0.0:9999 0.0.0.0:*\n"),
            "sudo -n ss": _Proc(returncode=1, stderr="sudo: a password is required"),
            "pgrep": _Proc(returncode=0),
        },
    )
    # _socket_env pins `sudo_prefix` to [] itself, so this comes after it.
    monkeypatch.setattr(firewall_ops, "sudo_prefix", lambda: ["sudo"])

    scan = firewall_ops._ports_from_ss()
    assert scan.hidden == {9999}
    assert scan.ran is True


def test_detection_is_a_union_not_first_match(monkeypatch, tmp_path):
    """`ss` sees 22 and the config file says 2222 — both are allowed, and the
    single-label `(ports, source)` contract still reports the strongest."""
    sshd_config = tmp_path / "sshd_config"
    sshd_config.write_text("Port 2222\n", encoding="utf-8")
    monkeypatch.setattr(firewall_ops, "SSHD_CONFIG_PATH", sshd_config)
    _socket_env(
        monkeypatch,
        {
            "ss -tlnp": _Proc(
                stdout='LISTEN 0 4096 0.0.0.0:22 0.0.0.0:* users:(("sshd",pid=800,fd=3))\n'
            ),
            "systemctl show ssh.socket": _Proc(stdout=_SOCKET_UNIT_ABSENT),
        },
    )

    assert firewall_ops.ssh_ports() == ([22, 2222], "ss")


def _incomplete_apply_env(monkeypatch, responses):
    monkeypatch.setattr(firewall_ops, "IS_WINDOWS", False)
    monkeypatch.setattr(firewall_ops, "IS_DOCKER", False)
    monkeypatch.setattr(firewall_ops, "detect_backend", lambda: "ufw")
    return _socket_env(monkeypatch, responses)


def test_apply_refuses_when_incomplete_and_nothing_confident(monkeypatch, tmp_path):
    """Ports came only from a config file while part of the check could not be
    completed. That is the D9 lockout shape, so nothing may reach ufw."""
    sshd_config = tmp_path / "sshd_config"
    sshd_config.write_text("Port 22\n", encoding="utf-8")
    monkeypatch.setattr(firewall_ops, "SSHD_CONFIG_PATH", sshd_config)
    calls = _incomplete_apply_env(
        monkeypatch,
        {
            "ss -tlnp": _Proc(stdout="LISTEN 0 4096 0.0.0.0:53 0.0.0.0:*\n"),
            "systemctl": _Proc(returncode=1, stderr="Failed to connect to bus"),
            "pgrep": _Proc(returncode=0),
        },
    )
    logged: list[str] = []

    code = firewall_ops.apply_rules(
        _config(tmp_path, server_port=2402), assume_yes=True, log=logged.append
    )

    assert code != 0
    assert not any("ufw" in " ".join(call) for call in calls)
    assert any("part of the check could not be completed" in line for line in logged)


def test_apply_proceeds_when_incomplete_but_a_confident_source_answered(monkeypatch, tmp_path):
    """Decision 4's second row, and the constraint that keeps the new refusal
    narrow: a 0600 drop-in nobody could read does not block a box whose live
    SSH port `ss` just read. The reasons are printed, not fatal."""
    calls = _incomplete_apply_env(
        monkeypatch,
        {
            "ufw show added": _Proc(stdout=_UFW_ADDED),
            "ufw": _Proc(stdout="Rule added"),
            "ss -tlnp": _Proc(
                stdout="LISTEN 0 4096 127.0.0.1:631 0.0.0.0:*\n"
                       'LISTEN 0 4096 0.0.0.0:2222 0.0.0.0:* users:(("sshd",pid=800,fd=3))\n'
            ),
            "systemctl": _Proc(returncode=1, stderr="Failed to connect to bus"),
            "pgrep": _Proc(returncode=0),
        },
    )
    logged: list[str] = []

    code = firewall_ops.apply_rules(
        _config(tmp_path, server_port=2402), assume_yes=True, log=logged.append
    )

    assert code == 0
    assert any(line.startswith("NOTE: ") for line in logged)
    assert any("ufw allow 2222/tcp" == " ".join(call) for call in calls)


def test_apply_unions_ports_found_after_acquiring_privileges(monkeypatch, tmp_path):
    """The first detection runs before sudo is warm — precisely when `ss`
    cannot see other users' listeners — because `_ensure_privileges` must stay
    *below* the confirmation prompt. So detect again afterwards and take the
    union: the allowlist can only grow (plans/29, decision 6)."""
    _apply_env(monkeypatch)
    detections = iter([_detection([22], "ss"), _detection([22, 2222], "ss")])
    monkeypatch.setattr(firewall_ops, "ssh_detect", lambda: next(detections))
    calls = _guard(
        monkeypatch,
        {
            "ufw show added": _Proc(stdout="ufw allow 22/tcp\nufw allow 2222/tcp\n"),
            "ufw": _Proc(stdout="Rule added"),
        },
    )
    logged: list[str] = []

    code = firewall_ops.apply_rules(
        _config(tmp_path, server_port=2402), assume_yes=True, log=logged.append
    )

    assert code == 0
    joined = [" ".join(call) for call in calls]
    assert "ufw allow 2222/tcp" in joined
    assert joined.index("ufw allow 2222/tcp") < joined.index("ufw default deny incoming")
    assert any("additional SSH port(s) after acquiring privileges: 2222" in l for l in logged)


def test_apply_never_shrinks_the_allowlist_on_redetect(monkeypatch, tmp_path):
    """A second detection that finds *fewer* ports is ignored. Shrinking the
    list the operator already confirmed is the direction that locks people
    out."""
    _apply_env(monkeypatch)
    detections = iter([_detection([22, 2222], "ss"), _detection([22], "ss")])
    monkeypatch.setattr(firewall_ops, "ssh_detect", lambda: next(detections))
    calls = _guard(
        monkeypatch,
        {
            "ufw show added": _Proc(stdout="ufw allow 22/tcp\nufw allow 2222/tcp\n"),
            "ufw": _Proc(stdout="Rule added"),
        },
    )

    code = firewall_ops.apply_rules(
        _config(tmp_path, server_port=2402), assume_yes=True, log=lambda _l: None
    )

    assert code == 0
    joined = [" ".join(call) for call in calls]
    assert "ufw allow 2222/tcp" in joined
    assert "ufw allow 22/tcp" in joined


# --- an unreadable Include is reported, not dropped (plans/29, D9) ----------


def _unreadable_dropin(tmp_path):
    dropin_dir = tmp_path / "sshd_config.d"
    dropin_dir.mkdir()
    dropin = dropin_dir / "50-cloud-init.conf"
    dropin.write_text("Port 2222\n", encoding="utf-8")
    dropin.chmod(0o000)
    main = tmp_path / "sshd_config"
    main.write_text(f"Include {dropin_dir}/*.conf\nPort 22\n", encoding="utf-8")
    return main, dropin


_ROOT_READS_EVERYTHING = pytest.mark.skipif(
    hasattr(__import__("os"), "geteuid") and __import__("os").geteuid() == 0,
    reason="root can read a 0000 file, so the permission gap cannot be staged",
)


@_ROOT_READS_EVERYTHING
def test_unreadable_drop_in_is_reported_not_silently_dropped(monkeypatch, tmp_path):
    """`/etc/ssh/sshd_config.d/50-cloud-init.conf` is mode 0600 on Ubuntu cloud
    images — exactly where an operator is told to put a port override. Main
    file says `Port 22`, the drop-in says `Port 2222`, and detection used to
    return a confident-looking `([22], "sshd_config")`. `--apply` then cuts the
    session on 2222."""
    monkeypatch.setattr(firewall_ops, "sudo_prefix", lambda: [])
    main, dropin = _unreadable_dropin(tmp_path)

    scan = firewall_ops._scan_sshd_config(main)

    assert scan.ports == {22}
    assert any(str(dropin) in skipped for skipped in scan.skipped)


@_ROOT_READS_EVERYTHING
def test_unreadable_drop_in_is_reported_through_ssh_detect(monkeypatch, tmp_path):
    """The behavioural sibling of the test above — phrased against the public
    entry point so it pins the *defect*, not the name of a new helper."""
    main, dropin = _unreadable_dropin(tmp_path)
    monkeypatch.setattr(firewall_ops, "SSHD_CONFIG_PATH", main)
    _socket_env(monkeypatch, {"pgrep": _Proc(returncode=0)})

    detection = firewall_ops.ssh_detect()

    assert detection.incomplete != ()
    assert any(str(dropin) in reason for reason in detection.incomplete)
    assert not detection.confident


@_ROOT_READS_EVERYTHING
def test_unreadable_drop_in_is_retried_under_sudo_n(monkeypatch, tmp_path):
    """Same rule as every other read path in the module: retry once under
    `sudo -n` (never bare `sudo`) before giving up. On a cloud image that turns
    the 0600 drop-in from a silent hole into a read."""
    main, dropin = _unreadable_dropin(tmp_path)
    calls = _guard(monkeypatch, {"cat": _Proc(stdout="Port 2222\n")})
    monkeypatch.setattr(firewall_ops, "sudo_prefix", lambda: ["sudo"])

    scan = firewall_ops._scan_sshd_config(main)

    assert scan.ports == {22, 2222}
    assert scan.skipped == ()
    assert ["sudo", "-n", "cat", str(dropin)] in calls


def test_unreadable_include_directory_is_reported(monkeypatch, tmp_path):
    """The other skip point: `_glob_include` swallowed every OSError, so a
    drop-in directory we could not list contributed nothing and said nothing."""
    main = tmp_path / "sshd_config"
    main.write_text(f"Port 22\nInclude {tmp_path}/locked.d/*.conf\n", encoding="utf-8")

    def boom(_self, _pattern):
        raise OSError("permission denied")

    monkeypatch.setattr(firewall_ops.Path, "glob", boom)

    scan = firewall_ops._scan_sshd_config(main)

    assert scan.ports == {22}
    assert any("locked.d" in skipped for skipped in scan.skipped)


@_ROOT_READS_EVERYTHING
def test_apply_refuses_when_a_config_file_was_skipped_and_nothing_confident(
    monkeypatch, tmp_path
):
    """D9 end to end: the only port we found came from the main config file,
    and a drop-in that could set a different one was unreadable. Allowing 22
    and denying the rest is the lockout."""
    main, _dropin = _unreadable_dropin(tmp_path)
    monkeypatch.setattr(firewall_ops, "SSHD_CONFIG_PATH", main)
    calls = _incomplete_apply_env(
        monkeypatch,
        {
            "systemctl show ssh.socket": _Proc(stdout=_SOCKET_UNIT_ABSENT),
            "pgrep": _Proc(returncode=0),
        },
    )
    logged: list[str] = []

    code = firewall_ops.apply_rules(
        _config(tmp_path, server_port=2402), assume_yes=True, log=logged.append
    )

    assert code != 0
    assert not any("ufw" in " ".join(call) for call in calls)
    assert any("could not read" in line for line in logged)


# --- firewalld rich rules and non-default zones (plans/29, D10) -------------

_FIREWALLD_RICH = """public (active)
  target: default
  services: dhcpv6-client ssh
  ports: 2402-2405/udp
  protocols:
  rich rules:
\trule family="ipv4" port port="27016" protocol="udp" accept
"""


def _firewalld_doctor(monkeypatch, responses):
    """doctor's `_check_firewall` over a canned firewalld. Asserting through
    doctor and not just the parser is the point: the FAIL is what trains users
    to ignore the tool."""
    from app.commands import doctor as doctor_cmd

    monkeypatch.setattr(doctor_cmd, "IS_WINDOWS", False)
    monkeypatch.setattr(firewall_ops, "detect_backend", lambda: "firewalld")
    monkeypatch.setattr(firewall_ops, "sudo_prefix", lambda: [])
    canned = {"firewall-cmd --state": _Proc(stdout="running\n")}
    canned.update(responses)
    _guard(monkeypatch, canned)
    return doctor_cmd._check_firewall


def test_firewalld_rich_rule_port_counts_as_open_in_status(monkeypatch, tmp_path):
    """A port opened by a rich rule never appears in `ports:`, so it parsed as
    absent and doctor FAILed a correctly configured box — the same outcome D5
    existed to prevent, through a different door."""
    check = _firewalld_doctor(
        monkeypatch,
        {"--list-all-zones": _Proc(stdout=_FIREWALLD_RICH), "--list-all": _Proc(stdout=_FIREWALLD_RICH)},
    )
    cfg = _config(tmp_path, server_port=2402)

    status = firewall_ops.firewall_status(cfg)
    assert 27016 in status.open_udp
    assert firewall_ops.missing_ports(cfg, status) == []

    name, ok, _detail = check(cfg)
    assert (name, ok) == ("Firewall", True)

    # Same parser, two questions: the status view counts a source-restricted
    # rule (the port really is reachable, from somewhere), the gate does not.
    restricted = (
        '  rich rules:\n\trule family="ipv4" source address="1.2.3.4" '
        'port port="22" protocol="tcp" accept\n'
    )
    assert firewall_ops._parse_firewalld_rich(restricted, restricted_ok=True)[1] == {22}
    assert firewall_ops._parse_firewalld_rich(restricted, restricted_ok=False)[1] == set()


def test_firewalld_rich_rule_reject_is_not_open(monkeypatch, tmp_path):
    """`reject`/`drop` count for neither view."""
    rejected = _FIREWALLD_RICH.replace("accept", 'reject type="icmp-port-unreachable"')
    check = _firewalld_doctor(
        monkeypatch,
        {"--list-all-zones": _Proc(stdout=rejected), "--list-all": _Proc(stdout=rejected)},
    )
    cfg = _config(tmp_path, server_port=2402)

    status = firewall_ops.firewall_status(cfg)
    assert 27016 not in status.open_udp

    _name, ok, _detail = check(cfg)
    assert ok is False


def test_firewalld_source_restricted_rich_rule_does_not_satisfy_the_gate(monkeypatch, tmp_path):
    """The firewalld twin of `test_ufw_added_ignores_source_restricted_rule`.
    A rule that only lets 1.2.3.4 in leaves the box reachable from one address;
    counting it as a general SSH allow would let the gate flip the policy on
    everyone else. The status view still counts it — the port really is open,
    from somewhere — which is why the two call sites differ by one flag."""
    listing = (
        "public (active)\n  target: default\n  ports: 2402/udp\n  rich rules:\n"
        '\trule family="ipv4" source address="1.2.3.4" port port="22" '
        'protocol="tcp" accept\n'
    )
    _apply_env(monkeypatch, backend="firewalld", ssh=(22,))
    calls = _guard(
        monkeypatch,
        {
            "--get-default-zone": _Proc(stdout="public\n"),
            "--permanent --list-all": _Proc(stdout=listing),
            "firewall-cmd": _Proc(stdout="success"),
        },
    )
    logged: list[str] = []

    staged = firewall_ops.staged_rules(_config(tmp_path), backend="firewalld")
    assert 22 not in staged.tcp

    code = firewall_ops.apply_rules(
        _config(tmp_path, server_port=2402), assume_yes=True, log=logged.append
    )

    assert code != 0
    assert not any("--set-target" in " ".join(call) for call in calls)


def test_firewalld_port_only_in_another_zone_is_unknown_not_closed(monkeypatch, tmp_path):
    """A zone bound to a different interface may or may not be the one the game
    traffic arrives on, and this module cannot tell. So the honest answer is
    unknown — never closed, and never unioned into `open_udp`, which would
    assert reachability we have not established."""
    default_zone = "public (active)\n  target: default\n  ports: 2402-2405/udp\n"
    all_zones = default_zone + "internal\n  target: default\n  ports: 27016/udp\n"
    check = _firewalld_doctor(
        monkeypatch,
        {"--list-all-zones": _Proc(stdout=all_zones), "--list-all": _Proc(stdout=default_zone)},
    )
    cfg = _config(tmp_path, server_port=2402)

    status = firewall_ops.firewall_status(cfg)

    assert status.error and "internal" in status.error
    assert 27016 not in status.open_udp
    assert firewall_ops.missing_ports(cfg, status) == []

    _name, ok, _detail = check(cfg)
    assert ok is True


def test_firewalld_list_all_retries_under_sudo_n(monkeypatch, tmp_path):
    """Every sibling read path retried and this one did not, so doctor's
    firewalld row read "needs root" on boxes where a `sudo -n` would have
    worked — while the ufw row beside it did not. See plans/29 (D10c)."""
    monkeypatch.setattr(firewall_ops, "detect_backend", lambda: "firewalld")
    monkeypatch.setattr(firewall_ops, "sudo_prefix", lambda: ["sudo"])
    calls = _guard(
        monkeypatch,
        {
            "sudo -n firewall-cmd --state": _Proc(stdout="running\n"),
            "sudo -n firewall-cmd --list-all": _Proc(stdout=_FIREWALLD_RICH),
            "firewall-cmd": _Proc(returncode=1, stderr="Authorization failed."),
        },
    )

    status = firewall_ops.firewall_status(_config(tmp_path, server_port=2402))

    assert status.active is True
    assert 2402 in status.open_udp
    assert ["sudo", "-n", "firewall-cmd", "--list-all"] in calls
    assert not any(c[:1] == ["sudo"] and c[1] != "-n" for c in calls)
