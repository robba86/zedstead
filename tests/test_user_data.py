"""Tests for `user-data.yaml` — the cloud-init template (TODO A8).

Nothing here boots a VM. What can be checked without one is exactly what A8's
rule is about: that the file stays a **thin caller** and does not grow a second
copy of the provisioning logic, and that the flags it passes still exist.

PyYAML comes in with `uvicorn[standard]`, which is a declared dependency — so
it is present in any environment that can run zsm, and a hard import here
fails loudly rather than skipping quietly if that ever stops being true.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from click.testing import CliRunner

USER_DATA = Path(__file__).resolve().parents[1] / "user-data.yaml"


def _text() -> str:
    return USER_DATA.read_text(encoding="utf-8")


def _doc() -> dict:
    return yaml.safe_load(_text())


def _runcmds() -> list[list[str]]:
    return [c for c in _doc()["runcmd"] if isinstance(c, list)]


def _cmd_running(needle: str) -> list[str]:
    """The `su` line that RUNS *needle*, not the curl line that downloads it.

    Matching on the bare filename picks the curl line first, which has no `--`
    flags at all — so a flag-checking test written that way passes while
    asserting nothing. It did, until this helper existed.
    """
    return next(c for c in _runcmds() if c[0] == "su" and needle in c[-1])


def test_it_starts_with_the_cloud_config_marker():
    """cloud-init ignores the file outright without this exact first line."""
    assert _text().splitlines()[0] == "#cloud-config"


def test_it_is_valid_yaml_with_the_keys_cloud_init_reads():
    doc = _doc()
    assert isinstance(doc, dict)
    assert {"users", "runcmd"} <= set(doc)


def test_the_user_is_created_by_cloud_init_with_a_key_and_passwordless_sudo():
    """The key is cloud-init's job, not bootstrap-vps': it comes from the
    operator, and cloud-init has a first-class field for it."""
    user = _doc()["users"][0]
    assert user["name"]
    assert user["ssh_authorized_keys"]
    # install.sh --yes and the bootstrap's sysctl/swap steps use
    # `sudo -n`; without NOPASSWD first boot stops at a prompt nobody answers.
    assert "NOPASSWD" in user["sudo"]


def test_the_ssh_key_is_an_obvious_placeholder():
    """A real-looking key here would be pasted unchanged by somebody."""
    key = _doc()["users"][0]["ssh_authorized_keys"][0]
    assert "REPLACE" in key.upper()


def test_every_username_mention_agrees():
    """The name appears in several places and YAML has no variables, so the
    only thing keeping them in step is that they are checked."""
    name = _doc()["users"][0]["name"]
    for cmd in _runcmds():
        if cmd[0] == "su":
            assert name in cmd, cmd


def test_it_carries_no_provisioning_logic_of_its_own():
    """A8's rule: all of it lives in `zsm bootstrap-vps` and `install.sh`.

    A package or a kernel setting added here would be the copy nobody updates.
    """
    banned = (
        "apt-get", "apt ", "dpkg", "sysctl", "swapon", "mkswap", "fallocate",
        "useradd", "usermod", "locale-gen", "ufw", "firewall-cmd",
    )
    flat = " ".join(" ".join(c) for c in _runcmds())
    for word in banned:
        assert word not in flat, f"{word!r} belongs in bootstrap-vps, not in the YAML"


def test_the_only_things_it_runs_are_the_installer_and_the_bootstrap():
    flat = [" ".join(c) for c in _runcmds()]
    assert any("install.sh" in c for c in flat)
    assert any("bootstrap-vps" in c for c in flat)
    # Two su invocations (fetch-and-install, bootstrap), and nothing else.
    assert len(flat) == 2, flat


def test_the_installer_is_fetched_by_the_user_into_their_own_home():
    """Not as root under a fixed name in the shared /tmp (plans/68 SUP-9).

    The download and the run are one `&&` line, so a failed download runs
    nothing.
    """
    install = _cmd_running("install.sh")
    script = install[-1]
    assert "/tmp/" not in script, script
    assert not any(c[0] == "curl" for c in _runcmds())
    fetch, sep, run = script.partition(" && ")
    assert sep, script
    assert fetch.startswith("curl ") and "-o $HOME/zsm-install.sh" in fetch
    assert run.startswith("sh $HOME/zsm-install.sh ")


def test_the_installer_is_fetched_from_a_url_that_resolves():
    """The site prints the short `https://zedstead.com/install.sh`, and zedstead.com
    has no DNS record at all (TODO F1, measured in 2026 Q3).

    Everywhere else that is a 404 a reader works around. Here it is the
    first runcmd of an unattended first boot: the box comes up with no zsm
    on it and the operator finds out from a log. The deploy-on-a-vps docs page
    refuses the same URL for the same reason, so the rule is pinned twice.
    """
    install = _cmd_running("install.sh")
    url = next(a for a in install[-1].split() if a.startswith("http"))
    assert "zedstead.com" not in url, url
    readme = (USER_DATA.parent / "README.md").read_text(encoding="utf-8")
    assert url in readme, url


def test_the_installer_runs_as_the_user_not_as_root():
    """install.sh installs into $HOME — as root it would install zsm for root."""
    install = _cmd_running("install.sh")
    assert "-l" in install  # a login shell, so $HOME is the user's


def test_the_flags_it_passes_to_bootstrap_vps_exist():
    """The drift that matters: a flag renamed in the CLI and not here."""
    from app.commands.bootstrap import bootstrap_vps

    cmd = _cmd_running("bootstrap-vps")
    flags = [w for w in cmd[-1].split() if w.startswith("--")]
    assert flags, cmd

    known = set()
    for param in bootstrap_vps.params:
        known.update(param.opts)
    from app.__main__ import main as root_group

    for param in root_group.params:
        known.update(param.opts)
    for flag in flags:
        assert flag in known, flag


def test_the_whole_invocation_parses_through_the_root_group():
    """Parse the real argv, in the real order, against the real CLI.

    Written against `main` rather than the subcommand, and without dropping
    anything first: `--yes` belongs to the **root group**, so it has to precede
    the subcommand. An earlier version of this test stripped it and then
    asserted the rest parsed — which is how the YAML shipped a moment with
    `zsm bootstrap-vps ... --yes`, an argv Click rejects with
    "No such option '--yes'".
    """
    from app.__main__ import main

    cmd = _cmd_running("bootstrap-vps")
    args = cmd[-1].split()
    args = args[args.index("zsm") + 1:] if "zsm" in args else args
    args = [a.split("/")[-1] if a.endswith("/zsm") else a for a in args]
    args = [a for a in args if a != "zsm"]

    result = CliRunner().invoke(main, [*args, "--help"])
    assert result.exit_code == 0, result.output
    assert "No such option" not in result.output


def test_the_installer_flags_exist():
    """install.sh is shell, so this greps its own --help text rather than
    guessing — the two are in the same repo and must agree."""
    install_sh = (USER_DATA.parent / "install.sh").read_text(encoding="utf-8")
    cmd = _cmd_running("install.sh")
    flags = [w for w in cmd[-1].split() if w.startswith("--")]
    assert flags, cmd
    for flag in flags:
        assert flag in install_sh, flag


def test_the_firewall_step_is_commented_out():
    """The one step that can end the session is not run unattended."""
    text = _text()
    assert "# - [su" in text and "service firewall --apply" in text
    assert not any("firewall" in " ".join(c) for c in _runcmds())


def test_root_login_is_left_enabled_with_a_reason():
    """Locking root out before the operator has proved the new key works is a
    lockout, not hardening."""
    assert _doc()["disable_root"] is False
    assert "disable_root" in _text()


def test_it_says_what_is_left_for_a_human():
    """Steam Guard needs a person; claiming otherwise is the promise that
    would make this file feel broken."""
    text = _text().lower()
    assert "zsm init" in text
    assert "steam guard" in text
