"""Tests for `zsm steam-login` and the Steam-login half of `zsm doctor`.

Nothing here runs SteamCMD. The three seams are patched by name in the module
that *calls* them (`app.commands.steam`, `app.shared.steam`), not in the module
they are defined in — a `from x import y` caller holds its own reference, and
patching the definition leaves it untouched. In this repo that is not a slow
test, it is a real SteamCMD process.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from app.commands.steam import steam_login_cmd
from app.shared.steam import LoginDiagnosis, diagnose_login_output


def _flat(output: str) -> str:
    """Rich hard-wraps to the terminal width, so a phrase the test cares about
    can arrive with a newline in the middle of it. Assert against this."""
    return " ".join(output.split()).lower()


def _config(tmp_path: Path, username: str = "bob"):
    """A config whose steamcmd path exists, so the command gets past its gates."""
    steamcmd = tmp_path / "steamcmd.sh"
    steamcmd.write_text("", encoding="utf-8")
    return SimpleNamespace(
        steam_username=username,
        steamcmd_path=steamcmd,
        server_dir_path=tmp_path / "dayz-server",  # nothing installed there
        save=lambda: None,
    )


@pytest.fixture()
def cfg(tmp_path):
    return _config(tmp_path)


# ---------------------------------------------------------------------------
# zsm steam-login
# ---------------------------------------------------------------------------


def test_a_working_cached_login_is_reported_instead_of_replaced(cfg):
    """The commonest reason to run this is a suspicion. Answering it without
    an interactive login also means not spending a Steam Guard code."""
    with patch("app.commands.steam.DayzConfig.load", return_value=cfg), \
         patch("app.commands.steam.check_steam_login", return_value=True) as check, \
         patch("app.commands.steam.steam_login") as login:
        result = CliRunner().invoke(steam_login_cmd, [])

    assert result.exit_code == 0, result.output
    assert check.called
    login.assert_not_called()
    assert "already logged in" in result.output.lower()


def test_force_skips_the_cached_login_check(cfg):
    with patch("app.commands.steam.DayzConfig.load", return_value=cfg), \
         patch("app.commands.steam.check_steam_login") as check, \
         patch("app.commands.steam.steam_login") as login:
        result = CliRunner().invoke(steam_login_cmd, ["--force"])

    assert result.exit_code == 0, result.output
    check.assert_not_called()
    login.assert_called_once()
    assert "successful" in result.output.lower()


def test_a_login_names_the_next_command_while_there_is_no_server(cfg):
    """After a login that failed inside the wizard, this command is where a
    newcomer lands, and nothing said how to get the DayZ server (first-hour
    test, 2026 Q4). Both ways out of the command say it."""
    for cached in (True, False):
        with patch("app.commands.steam.DayzConfig.load", return_value=cfg), \
             patch("app.commands.steam.check_steam_login", return_value=cached), \
             patch("app.commands.steam.steam_login"):
            result = CliRunner().invoke(steam_login_cmd, [])
        assert result.exit_code == 0, result.output
        assert "download it with: zsm setup" in _flat(result.output)


def test_an_installed_server_gets_no_next_command(cfg):
    from app.shared.platform import IS_WINDOWS

    cfg.server_dir_path.mkdir()
    (cfg.server_dir_path / ("DayZServer_x64.exe" if IS_WINDOWS else "DayZServer")).write_text("")
    with patch("app.commands.steam.DayzConfig.load", return_value=cfg), \
         patch("app.commands.steam.check_steam_login", return_value=False), \
         patch("app.commands.steam.steam_login"):
        result = CliRunner().invoke(steam_login_cmd, [])
    assert result.exit_code == 0, result.output
    assert "zsm setup" not in result.output
    assert result.output.lower().count("steam login successful") == 1


def test_a_refused_login_does_not_print_the_command_line(cfg):
    """SteamCMD says why ("ERROR (Invalid Password)") and `steam_login` says
    that it failed. The exception's own text is the argv and "non-zero exit
    status 5", which is what a wrong password used to end in."""
    boom = subprocess.CalledProcessError(5, [str(cfg.steamcmd_path), "+login", "bob", "+quit"])
    with patch("app.commands.steam.DayzConfig.load", return_value=cfg), \
         patch("app.commands.steam.check_steam_login", return_value=False), \
         patch("app.commands.steam.steam_login", side_effect=boom), \
         patch("app.commands.steam.probe_login_failure", return_value=LoginDiagnosis()):
        result = CliRunner().invoke(steam_login_cmd, [])
    assert result.exit_code == 1
    assert "exit status" not in result.output and "+login" not in result.output


def test_the_preamble_is_printed_before_steamcmd_takes_the_terminal(cfg):
    """`zsm init` prints it too, which is why it lives in app/shared/steam.py
    — so this asserts the shared constant reached the output, not a copy."""
    from app.shared import steam as steam_mod

    printed: list[str] = []

    with patch("app.commands.steam.DayzConfig.load", return_value=cfg), \
         patch.object(steam_mod, "_run_interactive_login", lambda cmd, timeout: None), \
         patch.object(steam_mod.console, "print", lambda *a, **k: printed.append(str(a[0]))):
        result = CliRunner().invoke(steam_login_cmd, ["--force"])

    assert result.exit_code == 0, result.output
    blob = " ".join(printed)
    for line in steam_mod.LOGIN_PREAMBLE_LINES:
        assert line in blob, line


def test_a_missing_username_stops_before_steamcmd(tmp_path):
    cfg = _config(tmp_path, username="")
    with patch("app.commands.steam.DayzConfig.load", return_value=cfg), \
         patch("app.commands.steam.steam_login") as login:
        result = CliRunner().invoke(steam_login_cmd, [])

    assert result.exit_code == 1
    login.assert_not_called()
    assert "--username" in result.output


def test_username_is_used_for_this_login_and_saved_only_on_success(tmp_path):
    """Same rule the web login follows: the config records the account that
    actually worked, not the one that was typed."""
    cfg = _config(tmp_path, username="")
    saved: list[str] = []
    cfg.save = lambda: saved.append(cfg.steam_username)

    with patch("app.commands.steam.DayzConfig.load", return_value=cfg), \
         patch("app.commands.steam.steam_login") as login:
        result = CliRunner().invoke(steam_login_cmd, ["--force", "--username", "alice"])

    assert result.exit_code == 0, result.output
    assert login.call_args.args[0].steam_username == "alice"
    assert saved == ["alice"]


@pytest.mark.parametrize("configured,args", [
    ("", ["--username", " Anonymous "]),
    ("anonymous", []),  # saved by an older `zsm init`
])
def test_anonymous_is_refused_before_steamcmd(tmp_path, configured, args):
    """An anonymous login succeeds and then owns nothing: SteamCMD refuses
    the DayZ server with "No subscription"."""
    cfg = _config(tmp_path, username=configured)
    saved: list[str] = []
    cfg.save = lambda: saved.append(cfg.steam_username)

    with patch("app.commands.steam.DayzConfig.load", return_value=cfg), \
         patch("app.commands.steam.check_steam_login") as check, \
         patch("app.commands.steam.steam_login") as login:
        result = CliRunner().invoke(steam_login_cmd, args)

    assert result.exit_code == 1
    check.assert_not_called()
    login.assert_not_called()
    assert saved == []
    assert "an anonymous login cannot" in _flat(result.output)


@pytest.mark.parametrize("configured,args", [
    ("", ["--username", "+quit"]),
    ("", ["--username", "-bob"]),
    ("two words", []),  # put there before the check existed
])
def test_a_username_steamcmd_would_misread_is_refused_before_steamcmd(
    tmp_path, configured, args
):
    cfg = _config(tmp_path, username=configured)
    saved: list[str] = []
    cfg.save = lambda: saved.append(cfg.steam_username)

    with patch("app.commands.steam.DayzConfig.load", return_value=cfg), \
         patch("app.commands.steam.check_steam_login") as check, \
         patch("app.commands.steam.steam_login") as login:
        result = CliRunner().invoke(steam_login_cmd, args)

    assert result.exit_code == 1
    check.assert_not_called()
    login.assert_not_called()
    assert saved == []
    assert "the steam username cannot" in _flat(result.output)


def test_a_failed_login_with_that_username_saves_nothing(tmp_path):
    cfg = _config(tmp_path, username="")
    saved: list[str] = []
    cfg.save = lambda: saved.append(cfg.steam_username)

    with patch("app.commands.steam.DayzConfig.load", return_value=cfg), \
         patch("app.commands.steam.steam_login", side_effect=RuntimeError("nope")), \
         patch("app.commands.steam.probe_login_failure", return_value=LoginDiagnosis()):
        result = CliRunner().invoke(steam_login_cmd, ["--force", "--username", "alice"])

    assert result.exit_code == 1
    assert saved == []


def test_a_missing_steamcmd_stops_before_anything_else(tmp_path):
    cfg = SimpleNamespace(
        steam_username="bob", steamcmd_path=tmp_path / "nope.sh", save=lambda: None
    )
    with patch("app.commands.steam.DayzConfig.load", return_value=cfg), \
         patch("app.commands.steam.check_steam_login") as check:
        result = CliRunner().invoke(steam_login_cmd, [])

    assert result.exit_code == 1
    check.assert_not_called()
    assert "zsm setup" in _flat(result.output)


def test_a_failure_is_explained_with_the_shared_classifier(cfg):
    """A6's rule: reuse diagnose_login_output, do not write new diagnostics.
    The rate limit is the one that earns it — Steam sends no new code while
    throttled, so retyping codes for half an hour is the failure mode."""
    rate_limited = diagnose_login_output("FAILED (Rate Limit Exceeded)")

    with patch("app.commands.steam.DayzConfig.load", return_value=cfg), \
         patch("app.commands.steam.steam_login", side_effect=RuntimeError("exit 5")), \
         patch("app.commands.steam.probe_login_failure", return_value=rate_limited):
        result = CliRunner().invoke(steam_login_cmd, ["--force"])

    assert result.exit_code == 1
    assert "rate-limiting logins" in _flat(result.output)
    assert "30 minutes" in _flat(result.output)


def test_a_mobile_account_is_told_no_email_is_coming(cfg):
    with patch("app.commands.steam.DayzConfig.load", return_value=cfg), \
         patch("app.commands.steam.steam_login", side_effect=RuntimeError("exit 5")), \
         patch("app.commands.steam.probe_login_failure",
               return_value=LoginDiagnosis(factor="mobile")):
        result = CliRunner().invoke(steam_login_cmd, ["--force"])

    assert result.exit_code == 1
    assert "no email is sent for those accounts" in _flat(result.output)


def test_an_unclassifiable_failure_adds_no_guess(cfg):
    """Both fields empty means "unknown". A confident-sounding guess there is
    worse than the plain failure it would replace."""
    with patch("app.commands.steam.DayzConfig.load", return_value=cfg), \
         patch("app.commands.steam.steam_login", side_effect=RuntimeError("exit 5")), \
         patch("app.commands.steam.probe_login_failure", return_value=LoginDiagnosis()):
        result = CliRunner().invoke(steam_login_cmd, ["--force"])

    assert result.exit_code == 1
    assert "exit 5" in _flat(result.output)
    assert "steam wants" not in _flat(result.output)


def test_a_broken_diagnosis_never_masks_the_real_failure(cfg):
    with patch("app.commands.steam.DayzConfig.load", return_value=cfg), \
         patch("app.commands.steam.steam_login", side_effect=RuntimeError("exit 5")), \
         patch("app.commands.steam.probe_login_failure", side_effect=OSError("boom")):
        result = CliRunner().invoke(steam_login_cmd, ["--force"])

    assert result.exit_code == 1
    assert "exit 5" in result.output


def test_a_timeout_says_what_to_do_next(cfg):
    from app.shared.steam import SteamLoginTimeout

    with patch("app.commands.steam.DayzConfig.load", return_value=cfg), \
         patch("app.commands.steam.steam_login",
               side_effect=SteamLoginTimeout("No answer for 900s — run it again")), \
         patch("app.commands.steam.probe_login_failure") as probe:
        result = CliRunner().invoke(steam_login_cmd, ["--force"])

    assert result.exit_code == 1
    assert "no answer for 900s" in _flat(result.output)
    # Nobody typed anything; there is no account problem to diagnose.
    probe.assert_not_called()


# ---------------------------------------------------------------------------
# the interactive run itself
# ---------------------------------------------------------------------------


def test_an_abandoned_prompt_is_asked_to_stop_before_it_is_killed():
    """The process being stopped is the one *creating* the cached login token,
    and SteamCMD killed mid-rewrite of config.vdf loses it. So terminate
    first; SIGKILL only if it ignores that."""
    from app.shared.steam import SteamLoginTimeout, _run_interactive_login

    calls: list[str] = []

    class _Proc:
        """Ignores terminate; only SIGKILL stops it."""

        def __init__(self):
            self.killed = False

        def wait(self, timeout=None):
            if self.killed:
                return -9
            raise subprocess.TimeoutExpired(cmd="steamcmd", timeout=timeout or 0)

        def terminate(self):
            calls.append("terminate")

        def kill(self):
            calls.append("kill")
            self.killed = True

    with patch("app.shared.steam.subprocess.Popen", return_value=_Proc()):
        with pytest.raises(SteamLoginTimeout):
            _run_interactive_login(["steamcmd"], 900)

    assert calls.index("terminate") < calls.index("kill")


def test_a_process_that_stops_when_asked_is_not_killed():
    from app.shared.steam import SteamLoginTimeout, _run_interactive_login

    calls: list[str] = []

    class _Proc:
        def __init__(self):
            self.waits = 0

        def wait(self, timeout=None):
            self.waits += 1
            if self.waits == 1:
                raise subprocess.TimeoutExpired(cmd="steamcmd", timeout=timeout or 0)
            calls.append("reaped")
            return -15

        def terminate(self):
            calls.append("terminate")

        def kill(self):  # pragma: no cover - the point is that it is not called
            calls.append("kill")

    with patch("app.shared.steam.subprocess.Popen", return_value=_Proc()):
        with pytest.raises(SteamLoginTimeout):
            _run_interactive_login(["steamcmd"], 900)

    assert "kill" not in calls


def test_a_non_zero_exit_still_raises_calledprocesserror():
    """Unchanged on purpose — zsm init catches it and keeps going."""
    from app.shared.steam import _run_interactive_login

    class _Proc:
        def wait(self, timeout=None):
            return 5

    with patch("app.shared.steam.subprocess.Popen", return_value=_Proc()):
        with pytest.raises(subprocess.CalledProcessError):
            _run_interactive_login(["steamcmd"], 900)


def test_probe_login_failure_never_runs_steamcmd_without_a_username(tmp_path):
    from app.shared.steam import probe_login_failure

    cfg = _config(tmp_path, username="")
    with patch("app.shared.steamcmd_lock.run_to_end") as run:
        assert probe_login_failure(cfg) == LoginDiagnosis()
    run.assert_not_called()


def test_probe_login_failure_classifies_both_streams(tmp_path):
    from app.shared.steam import probe_login_failure

    cfg = _config(tmp_path)
    completed = SimpleNamespace(returncode=5, stdout=b"", stderr=b"FAILED (Rate Limit Exceeded)")
    with patch("app.shared.steamcmd_lock.run_to_end", return_value=completed) as run:
        diag = probe_login_failure(cfg)

    assert diag.error and "rate-limiting" in diag.error
    # Captured, and non-interactive: run_to_end gives SteamCMD stdin=DEVNULL,
    # so nothing may read from the terminal the user is on.
    assert run.call_args.kwargs["capture"] is True


def test_probe_login_failure_says_nothing_when_steamcmd_will_not_run(tmp_path):
    from app.shared.steam import probe_login_failure

    cfg = _config(tmp_path)
    with patch("app.shared.steamcmd_lock.run_to_end", side_effect=OSError("boom")):
        assert probe_login_failure(cfg) == LoginDiagnosis()


# ---------------------------------------------------------------------------
# zsm doctor --steam
# ---------------------------------------------------------------------------


def test_doctor_does_not_probe_steam_unless_asked(sample_config):
    """`zsm doctor` gates cron jobs, and the web notifier polls the same
    checks. A probe on either would put a second SteamCMD next to a scheduled
    update."""
    from app.commands import doctor

    with patch("app.shared.steam.check_steam_login") as check:
        labels = [c[0] for c in doctor._run_all_checks(sample_config)[0]]

    check.assert_not_called()
    assert "Steam login" not in labels


def test_doctor_steam_reports_a_working_cached_login(sample_config):
    from app.commands import doctor

    sample_config.steamcmd_path.parent.mkdir(parents=True, exist_ok=True)
    sample_config.steamcmd_path.write_text("", encoding="utf-8")

    with patch("app.shared.steam.check_steam_login", return_value=True):
        rows = doctor._run_all_checks(sample_config, probe_steam=True)[0]

    row = next(r for r in rows if r[0] == "Steam login")
    assert row[1] is True
    assert sample_config.steam_username in row[2]


def test_doctor_steam_names_the_fix_when_the_login_is_gone(sample_config):
    from app.commands import doctor

    sample_config.steamcmd_path.parent.mkdir(parents=True, exist_ok=True)
    sample_config.steamcmd_path.write_text("", encoding="utf-8")

    with patch("app.shared.steam.check_steam_login", return_value=False):
        rows = doctor._run_all_checks(sample_config, probe_steam=True)[0]

    row = next(r for r in rows if r[0] == "Steam login")
    assert row[1] is False
    assert "zsm steam-login" in row[2]


# ---------------------------------------------------------------------------
# diagnose_login_output — a non-zero SteamCMD exit says nothing on its own
# ---------------------------------------------------------------------------


def test_diagnose_email_guard():
    out = "Logging in user 'bob' to Steam Public...\nSteam Guard code:"
    diag = diagnose_login_output(out)
    assert diag.factor == "email"
    assert diag.error is None


def test_diagnose_mobile_authenticator():
    """An account on the mobile authenticator is never emailed a code — the
    old code called every failure an email and sent the user hunting for one."""
    diag = diagnose_login_output("Two-factor code:")
    assert diag.factor == "mobile"
    assert diag.error is None


def test_diagnose_account_login_denied_need_two_factor():
    diag = diagnose_login_output(
        "FAILED (Account Login Denied Need Two Factor)"
    )
    assert diag.factor == "mobile"


def test_diagnose_invalid_password_is_a_hard_error():
    diag = diagnose_login_output("FAILED login with result code Invalid Password")
    assert diag.factor is None
    assert diag.error and "password" in diag.error.lower()


def test_diagnose_rate_limit_is_a_hard_error():
    diag = diagnose_login_output("FAILED (Rate Limit Exceeded)")
    assert diag.error and "rate-limiting" in diag.error


def test_diagnose_rejected_code_beats_the_generic_two_factor_words():
    """The mismatch message also contains 'Two Factor', so ordering matters."""
    diag = diagnose_login_output("FAILED (Two Factor Code Mismatch)")
    assert diag.factor is None
    assert diag.error and "rejected" in diag.error


def test_diagnose_unknown_output_asks_for_nothing():
    diag = diagnose_login_output("FAILED (Unknown Failure)")
    assert diag == LoginDiagnosis()


def test_the_windows_retry_names_no_cause_it_cannot_know(cfg, monkeypatch):
    """SteamCMD owns the terminal, so zsm does not know why a login failed.
    The retry line used to blame "a pending firewall prompt" right under
    SteamCMD's own "ERROR (Invalid Password)" (first-hour test, Windows 11)."""
    import subprocess

    from app.shared import steam as steam_mod

    printed: list[str] = []
    attempts: list[int] = []

    def _refused(cmd, timeout):
        attempts.append(1)
        raise subprocess.CalledProcessError(5, cmd)

    monkeypatch.setattr(steam_mod, "IS_WINDOWS", True)
    monkeypatch.setattr(steam_mod, "_run_interactive_login", _refused)
    monkeypatch.setattr(steam_mod.time, "sleep", lambda s: None)
    monkeypatch.setattr(steam_mod.console, "print", lambda *a, **k: printed.append(str(a[0])))

    with pytest.raises(subprocess.CalledProcessError):
        steam_mod.steam_login(cfg)

    blob = " ".join(printed)
    assert len(attempts) == 2, "the retry itself stays"
    assert "may be caused by" not in blob
    assert "SteamCMD's reason is in the lines above" in blob
    assert "can fail while a Windows Firewall prompt is open" in blob
    assert blob.count("Steam login failed.") == 1
