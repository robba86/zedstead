"""SteamCMD's "No subscription": the account does not own DayZ.

Measured on a real Ubuntu 24.04 VPS in 2026 Q3: `zsm init` accepted
`anonymous` as the Steam username, the login "worked", and the server install
then failed with an exit status that explained nothing. The DayZ server (app
223350) and DayZ's Workshop items can only be downloaded by an account that
owns DayZ, so an anonymous login never can.

Nothing here runs SteamCMD. The seam is `app.shared.steam.run_passthrough`,
patched where `update_dayz_server` / `download_mod` look it up; the tests of
`run_passthrough` itself run a Python one-liner instead.
"""

from __future__ import annotations

import io
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from app.shared import steam
from app.shared.platform import IS_WINDOWS
from app.shared.steam import (
    ANONYMOUS_USERNAME_ERROR,
    NoSubscriptionError,
    diagnose_login_output,
    download_mod,
    download_mod_async,
    is_anonymous_username,
    no_subscription_error,
    run_passthrough,
    update_dayz_server,
)

# Verbatim, as `zsm init` printed it on the VPS. The first six lines are
# SteamCMD's; the last three are zsm's own report, which is what this fix
# replaces.
VPS_INIT_OUTPUT = """\
Loading Steam API...OK
Connecting anonymously to Steam Public...OK
Waiting for client config...OK
Waiting for user info...OK
ERROR! Failed to install app '223350' (No subscription)
Unloading Steam API...OK
Error updating DayZ server: Command '['/home/zsm/steamcmd/steamcmd.sh', '+force_install_dir', '/home/zsm/dayz-server', '+login', 'anonymous', '+app_update', '223350', 'validate', '+quit']' returned non-zero exit status 8.
Server installation failed: Command '['/home/zsm/steamcmd/steamcmd.sh', '+force_install_dir', '/home/zsm/dayz-server', '+login', 'anonymous', '+app_update', '223350', 'validate', '+quit']' returned non-zero exit status 8.
You can install later with: zsm setup
"""
STEAMCMD_OUTPUT = "\n".join(VPS_INIT_OUTPUT.splitlines()[:6]) + "\n"

# A failure that is not about ownership, in the same shape.
OTHER_FAILURE = """\
Loading Steam API...OK
Logging in user 'bob' to Steam Public...OK
Waiting for client config...OK
Waiting for user info...OK
ERROR! Failed to install app '223350' (Disk write failure)
Unloading Steam API...OK
"""


def _flat(output: str) -> str:
    """Rich hard-wraps to the terminal width; assert against one line."""
    return " ".join(output.split())


# --- the detector -----------------------------------------------------------


@pytest.mark.parametrize("output", [VPS_INIT_OUTPUT, STEAMCMD_OUTPUT])
def test_the_measured_vps_output_maps_to_the_explanation(output):
    message = no_subscription_error(output, "anonymous")

    assert message is not None
    assert message.startswith("SteamCMD is logged in as 'anonymous', which does not own DayZ")
    assert "(SteamCMD: No subscription)" in message
    assert (
        "The DayZ server and its Workshop mods can only be downloaded by an account "
        "that owns DayZ; an anonymous login cannot." in message
    )
    assert "zsm steam-login --username" in message
    assert "[" not in message  # markup-free: a job log and an HTTP error carry it too


def test_steamcmds_own_anonymous_line_is_believed_without_a_username():
    assert no_subscription_error(STEAMCMD_OUTPUT) == no_subscription_error(
        STEAMCMD_OUTPUT, "anonymous"
    )


def test_a_named_account_is_named():
    output = OTHER_FAILURE.replace("(Disk write failure)", "(No subscription)")

    message = no_subscription_error(output, "bob")

    assert message.startswith("The Steam account 'bob' does not own DayZ (SteamCMD: No subscription).")
    assert "an anonymous login cannot" in message


def test_other_failures_are_not_claimed():
    assert no_subscription_error(OTHER_FAILURE, "bob") is None
    assert no_subscription_error("", "anonymous") is None


def test_the_login_classifier_knows_it_too():
    assert diagnose_login_output(STEAMCMD_OUTPUT).error == no_subscription_error(STEAMCMD_OUTPUT)


@pytest.mark.parametrize("name,expected", [
    ("anonymous", True),
    ("  Anonymous ", True),
    ("ANONYMOUS", True),
    ("anonymous2", False),
    ("bob", False),
    ("", False),
])
def test_is_anonymous_username(name, expected):
    assert is_anonymous_username(name) is expected


@pytest.mark.parametrize("value", ["bob", "bob_99", "a-b", "a+b", "Bob.Smith", "x"])
def test_a_real_account_name_is_one_steamcmd_argument(value):
    from app.shared.steam import steam_username_problem, steamcmd_word_problem

    assert steam_username_problem(value) is None
    assert steamcmd_word_problem("The Steam Guard code", value) is None


@pytest.mark.parametrize(
    "value", ["two words", "bob\nx", "bob\rx", "bob\tx", "+quit", "-bob", "+", "-"]
)
def test_a_value_steamcmd_would_split_or_read_as_a_command_is_refused(value):
    # SteamCMD takes an argument starting with + as its next command.
    from app.shared.steam import steam_username_problem, steamcmd_word_problem

    assert steam_username_problem(value)
    problem = steamcmd_word_problem("The Steam Guard code", value)
    assert problem and problem.startswith("The Steam Guard code")


# --- update_dayz_server: init, setup, `run update-and-copy` -----------------


def test_server_update_raises_the_explanation(sample_config):
    sample_config.steam_username = "anonymous"
    lines: list[str] = []

    with patch("app.shared.steam.run_passthrough", return_value=(8, STEAMCMD_OUTPUT)):
        with pytest.raises(NoSubscriptionError) as info:
            update_dayz_server(sample_config, log=lines.append)

    expected = no_subscription_error(STEAMCMD_OUTPUT, "anonymous")
    assert str(info.value) == expected
    assert isinstance(info.value, subprocess.CalledProcessError)  # old handlers still catch it
    assert info.value.returncode == 8
    blob = "\n".join(lines)
    assert expected in blob
    assert "returned non-zero exit status" not in blob


def test_a_normal_server_update_failure_reads_as_it_did(sample_config):
    lines: list[str] = []

    with patch("app.shared.steam.run_passthrough", return_value=(8, OTHER_FAILURE)):
        with pytest.raises(subprocess.CalledProcessError) as info:
            update_dayz_server(sample_config, log=lines.append)

    assert not isinstance(info.value, NoSubscriptionError)
    assert "returned non-zero exit status 8." in str(info.value)
    assert any(
        line.startswith("[red]Error updating DayZ server: Command '[") for line in lines
    )


def test_init_reports_the_explanation_instead_of_the_exit_status(sample_config, monkeypatch, capsys):
    from app.commands.init import _offer_server_install

    sample_config.steam_username = "anonymous"
    monkeypatch.setattr(
        "app.commands.init.inquirer.confirm",
        lambda **_kw: SimpleNamespace(execute=lambda: True),
    )

    with patch("app.shared.steam.run_passthrough", return_value=(8, STEAMCMD_OUTPUT)):
        _offer_server_install(sample_config, login_ok=True)

    out = _flat(capsys.readouterr().out)
    assert "Server installation failed: SteamCMD is logged in as 'anonymous'" in out
    assert "an anonymous login cannot" in out
    assert "returned non-zero exit status" not in out
    assert "You can install later with: zsm setup" in out


def test_setup_reports_the_explanation(sample_config):
    from app.commands.setup import setup

    sample_config.steam_username = "anonymous"
    sample_config.save()

    with patch("app.commands.setup.check_steam_login", return_value=True), \
         patch("app.shared.steam.run_passthrough", return_value=(8, STEAMCMD_OUTPUT)):
        result = CliRunner().invoke(setup, obj={"non_interactive": True})

    out = _flat(result.output)
    assert "Server install failed: SteamCMD is logged in as 'anonymous'" in out
    assert "returned non-zero exit status" not in out


# --- the update pipeline: `zsm run all`, `run update`, web Update ----------


class _Runner:
    def __init__(self, returncode: int, last_output: str = "") -> None:
        self.returncode = returncode
        self.last_output = last_output
        self.calls = 0
        self.lines: list[str] = []

    async def run_command(self, cmd: list[str]) -> int:
        self.calls += 1
        return self.returncode

    def write(self, text: str) -> None:
        self.lines.append(text)


async def test_the_pipelines_update_step_fails_with_the_explanation(sample_config, monkeypatch):
    from app.ops.pipeline import PipelineContext, build_update_steps, run_pipeline

    monkeypatch.setattr("app.shared.steam.build_server_update_cmd", lambda config: ["steamcmd"])
    step = next(s for s in build_update_steps(sample_config) if s.key == "update_server")
    ctx = PipelineContext(
        config=sample_config,
        emit=lambda s: None,
        runner=_Runner(8, STEAMCMD_OUTPUT),
        should_run=lambda key: True,
    )

    results, _ = await run_pipeline([step], ctx)

    assert results[0].status == "failed"
    assert results[0].error == no_subscription_error(STEAMCMD_OUTPUT, "testuser")


# --- Workshop downloads -----------------------------------------------------


@pytest.fixture()
def steam_config(sample_config, tmp_path):
    # Pinned: workshop_dir otherwise resolves to the host's real Steam install.
    sample_config.workshop_content_dir = str(tmp_path / "workshop" / "content" / "221100")
    sample_config.workshop_dir.mkdir(parents=True, exist_ok=True)
    return sample_config


def _workshop_output(result: str) -> str:
    return STEAMCMD_OUTPUT.replace(
        "ERROR! Failed to install app '223350' (No subscription)",
        f"ERROR! Download item 123456 failed ({result}).",
    )


async def test_async_download_stops_at_no_subscription(steam_config, monkeypatch):
    monkeypatch.setattr("app.shared.steam.STEAM_RATE_LIMIT_SECONDS", 0)
    runner = _Runner(8, _workshop_output("No subscription"))

    ok = await download_mod_async(runner, steam_config, "123456", "@CF", max_retries=5)

    assert ok is False
    assert runner.calls == 1  # retrying cannot fix the account
    assert any("does not own DayZ" in line for line in runner.lines)


async def test_async_download_still_retries_other_failures(steam_config, monkeypatch):
    monkeypatch.setattr("app.shared.steam.STEAM_RATE_LIMIT_SECONDS", 0)
    runner = _Runner(8, _workshop_output("Failure"))

    ok = await download_mod_async(runner, steam_config, "123456", "@CF", max_retries=3)

    assert ok is False
    assert runner.calls == 3
    assert not any("does not own DayZ" in line for line in runner.lines)


def test_sync_download_stops_at_no_subscription(steam_config, monkeypatch):
    monkeypatch.setattr("app.shared.steam.time.sleep", lambda _s: None)
    calls: list[list[str]] = []

    def fake_run(cmd, **_kw):
        calls.append(cmd)
        return 0, _workshop_output("No subscription")  # exit 0 proves nothing

    monkeypatch.setattr("app.shared.steam.run_passthrough", fake_run)

    with pytest.raises(NoSubscriptionError, match="does not own DayZ"):
        download_mod(steam_config, "123456", "@CF", max_retries=5, log=lambda _s: None)
    assert len(calls) == 1


# --- run_passthrough: output on the terminal, tail kept ---------------------

_PRINT_AND_FAIL = [
    sys.executable,
    "-c",
    "import sys; print('Loading Steam API...OK'); "
    "print(\"ERROR! Failed to install app '223350' (No subscription)\"); sys.exit(8)",
]


def test_passthrough_copies_the_output_and_keeps_it(capsys):
    returncode, output = run_passthrough(_PRINT_AND_FAIL)

    assert returncode == 8
    assert "(No subscription)" in output
    assert "(No subscription)" in capsys.readouterr().out  # the user still sees it


class _FakeTerminal:
    """A stdout that says it is a terminal, so run_passthrough uses a pty."""

    def __init__(self) -> None:
        self.buffer = io.BytesIO()

    def isatty(self) -> bool:
        return True

    def write(self, text: str) -> None:
        self.buffer.write(text.encode())

    def flush(self) -> None:
        pass


@pytest.mark.skipif(IS_WINDOWS, reason="pseudo-terminals are POSIX-only")
def test_passthrough_on_a_terminal_gives_the_child_a_terminal(monkeypatch):
    terminal = _FakeTerminal()
    monkeypatch.setattr(sys, "stdout", terminal)
    probe = [
        sys.executable,
        "-c",
        "import sys; print('tty' if sys.stdout.isatty() else 'pipe'); "
        "print(\"ERROR! Failed to install app '223350' (No subscription)\"); sys.exit(8)",
    ]

    returncode, output = run_passthrough(probe)

    assert returncode == 8
    assert output.splitlines()[0] == "tty"  # line-buffered, as when it inherited the terminal
    assert "(No subscription)" in output
    assert b"(No subscription)" in terminal.buffer.getvalue()


def test_passthrough_timeout_kills_and_keeps_the_output():
    slow = [sys.executable, "-c", "import time; print('started', flush=True); time.sleep(30)"]

    with pytest.raises(subprocess.TimeoutExpired) as info:
        run_passthrough(slow, timeout=1)

    assert "started" in info.value.output


def test_a_quiet_run_says_steamcmd_is_still_working(monkeypatch, capsys):
    """On Windows SteamCMD block-buffers its pipe and prints nothing for the
    whole server download, under its banner "-- type 'quit' to exit --"."""
    monkeypatch.setattr(steam, "_QUIET_SECONDS", 0.5)
    monkeypatch.setattr(steam, "_QUIET_POLL_SECONDS", 0.1)
    quiet = [sys.executable, "-c", "import sys, time; sys.stdout.write('-- type quit --'); sys.stdout.flush(); time.sleep(1.5)"]

    returncode, output = run_passthrough(quiet)

    shown = capsys.readouterr().out
    assert returncode == 0
    assert "-- type quit --\nZedstead: SteamCMD is still working (" in shown
    assert "there is nothing to type" in shown
    assert "Zedstead:" not in output  # the tail a diagnosis reads stays SteamCMD's own


def test_a_run_that_keeps_printing_gets_no_notice(monkeypatch, capsys):
    monkeypatch.setattr(steam, "_QUIET_SECONDS", 0.5)
    monkeypatch.setattr(steam, "_QUIET_POLL_SECONDS", 0.1)
    busy = [sys.executable, "-c", "import time\nfor i in range(6):\n    print(i, flush=True); time.sleep(0.2)"]

    run_passthrough(busy)

    assert "still working" not in capsys.readouterr().out


# --- usernames: refused wherever one is typed in ----------------------------


def test_the_refusal_says_why():
    assert "'anonymous' can't be used as the Steam username" in ANONYMOUS_USERNAME_ERROR
    assert "an anonymous login cannot" in ANONYMOUS_USERNAME_ERROR
    assert "[" not in ANONYMOUS_USERNAME_ERROR
