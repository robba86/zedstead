"""Tests for prompt_init_if_missing helper in app/commands/init.py."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import click
import pytest

from app.commands.init import prompt_init_if_missing
from app.shared.config import DayzConfig


@pytest.fixture()
def tmp_config_paths(tmp_path: Path, monkeypatch) -> Path:
    """Patch CONFIG_DIR/CONFIG_FILE to a tmp location (no config written)."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_file = config_dir / "config.json"
    monkeypatch.setattr("app.shared.paths.CONFIG_DIR", config_dir)
    monkeypatch.setattr("app.shared.paths.CONFIG_FILE", config_file)
    monkeypatch.setattr("app.shared.config.CONFIG_DIR", config_dir)
    monkeypatch.setattr("app.shared.config.CONFIG_FILE", config_file)
    return config_file


def _make_ctx(non_interactive: bool = False) -> click.Context:
    cmd = click.Command("test")
    ctx = click.Context(cmd)
    ctx.obj = {"non_interactive": non_interactive}
    return ctx


def test_returns_true_when_config_exists(tmp_config_paths: Path, monkeypatch):
    """When config is already present, no prompt, no invoke — just True."""
    DayzConfig().save()
    assert tmp_config_paths.exists()

    prompted = False

    def fake_confirm(*_args, **_kwargs):
        nonlocal prompted
        prompted = True
        return False

    invoked = False

    def fake_invoke(self, *_args, **_kwargs):
        nonlocal invoked
        invoked = True

    monkeypatch.setattr("app.commands.init.confirm", fake_confirm)
    monkeypatch.setattr(click.Context, "invoke", fake_invoke)

    assert prompt_init_if_missing(_make_ctx()) is True
    assert prompted is False
    assert invoked is False


def test_returns_false_when_user_declines(tmp_config_paths: Path, monkeypatch):
    """Missing config + user says no → False, init not invoked."""
    assert not tmp_config_paths.exists()

    monkeypatch.setattr("app.commands.init.confirm", lambda *a, **k: False)

    invoked = False

    def fake_invoke(self, *_args, **_kwargs):
        nonlocal invoked
        invoked = True

    monkeypatch.setattr(click.Context, "invoke", fake_invoke)

    assert prompt_init_if_missing(_make_ctx()) is False
    assert invoked is False


def test_returns_true_when_user_accepts_and_config_created(tmp_config_paths: Path, monkeypatch):
    """Missing config + user says yes + init writes config → True."""
    assert not tmp_config_paths.exists()

    monkeypatch.setattr("app.commands.init.confirm", lambda *a, **k: True)

    def fake_invoke(self, _cmd, *_args, **_kwargs):
        DayzConfig().save()

    monkeypatch.setattr(click.Context, "invoke", fake_invoke)

    assert prompt_init_if_missing(_make_ctx()) is True
    assert tmp_config_paths.exists()


def test_returns_false_when_user_accepts_but_init_aborts(tmp_config_paths: Path, monkeypatch):
    """Missing config + user says yes but init doesn't write → False."""
    assert not tmp_config_paths.exists()

    monkeypatch.setattr("app.commands.init.confirm", lambda *a, **k: True)
    monkeypatch.setattr(click.Context, "invoke", lambda self, *a, **k: None)

    assert prompt_init_if_missing(_make_ctx()) is False


def test_non_interactive_is_threaded_through(tmp_config_paths: Path, monkeypatch):
    """ctx.obj['non_interactive'] reaches the confirm call."""
    assert not tmp_config_paths.exists()

    seen_non_interactive = {}

    def fake_confirm(prompt, non_interactive=False, **_kwargs):
        seen_non_interactive["value"] = non_interactive
        return False

    monkeypatch.setattr("app.commands.init.confirm", fake_confirm)
    monkeypatch.setattr(click.Context, "invoke", lambda self, *a, **k: None)

    prompt_init_if_missing(_make_ctx(non_interactive=True))
    assert seen_non_interactive["value"] is True


# ── Step 6: the zedstead.com opt-ins (plans/57 B6) ─────────────────────────────


def _answers(monkeypatch, *replies):
    """Replace the wizard's `inquirer.confirm`; record what each was asked."""
    asked = []
    queue = list(replies)

    class _Prompt:
        def __init__(self, reply):
            self._reply = reply

        def execute(self):
            return self._reply

    def fake_confirm(message, default=None, **_kwargs):
        asked.append({"message": message, "default": default})
        return _Prompt(queue.pop(0))

    monkeypatch.setattr("app.commands.init.inquirer.confirm", fake_confirm)
    return asked


def test_both_site_questions_default_to_no_ping_first(
    tmp_config_paths: Path, monkeypatch, capsys
):
    from app.commands.init import _offer_site

    config = DayzConfig()
    config.save()
    asked = _answers(monkeypatch, False, False)

    _offer_site(config)

    assert [a["default"] for a in asked] == [False, False]
    assert "install count" in asked[0]["message"]
    assert "publicly" in asked[1]["message"]
    for a in asked:
        assert "later" in a["message"]
    saved = DayzConfig.load()
    assert not saved.site_ping and not saved.site_listing
    assert saved.site_install_id == "" and saved.site_secret == ""


def test_a_yes_turns_that_switch_on_and_creates_the_ids(tmp_config_paths: Path, monkeypatch):
    from app.commands.init import _offer_site

    config = DayzConfig()
    config.save()
    _answers(monkeypatch, True, False)

    _offer_site(config)

    saved = DayzConfig.load()
    assert saved.site_ping is True and saved.site_listing is False
    assert saved.site_install_id and saved.site_secret


# ── Step 3: the Steam username ──────────────────────────────────────────────


def _typed(monkeypatch, *replies):
    """Replace the wizard's `inquirer.text`; record the default each ask showed."""
    defaults = []
    queue = list(replies)

    def fake_text(message, default="", validate=None, **_kwargs):
        defaults.append(default)
        reply = queue.pop(0)
        assert validate(reply)  # the wizard's own "required" check lets it through
        return SimpleNamespace(execute=lambda: reply)

    monkeypatch.setattr("app.commands.init.inquirer.text", fake_text)
    return defaults


def test_the_username_prompt_refuses_anonymous_and_asks_again(monkeypatch, capsys):
    """An anonymous login owns nothing, so SteamCMD refuses the DayZ server
    with "No subscription" (measured on a VPS, 2026 Q3)."""
    from app.commands.init import _prompt_steam_username
    from app.shared.steam import ANONYMOUS_USERNAME_ERROR

    defaults = _typed(monkeypatch, "anonymous", "  Anonymous ", "realowner")

    assert _prompt_steam_username("anonymous") == "realowner"

    assert defaults == ["anonymous", "", ""]  # a refused default is not offered again
    out = " ".join(capsys.readouterr().out.split())
    assert out.count("'anonymous' can't be used as the Steam username") == 2
    assert " ".join(ANONYMOUS_USERNAME_ERROR.split()) in out


def test_the_username_prompt_refuses_a_value_steamcmd_would_misread(monkeypatch, capsys):
    from app.commands.init import _prompt_steam_username

    defaults = _typed(monkeypatch, "+quit", "two words", "realowner")

    assert _prompt_steam_username("") == "realowner"
    assert defaults == ["", "", ""]
    assert " ".join(capsys.readouterr().out.split()).count("The Steam username cannot") == 2


def test_the_username_prompt_takes_a_real_account_at_once(monkeypatch, capsys):
    from app.commands.init import _prompt_steam_username

    defaults = _typed(monkeypatch, " bob ")

    assert _prompt_steam_username("bob") == "bob"
    assert defaults == ["bob"]
    assert "anonymous" not in capsys.readouterr().out


# --- where the wizard saves (plans/44 step 4) -----------------------------------


def _run_wizard(monkeypatch, tmp_path: Path, typed_first: tuple[str, ...] = ()):
    """`zsm init` with every question answered, and nothing spawned.
    *typed_first* are server dirs answered at step 2 before the real one."""
    from click.testing import CliRunner

    from app.commands import init as init_mod

    server = tmp_path / "server"
    server.mkdir(exist_ok=True)
    answers = [*typed_first, str(server)]
    yes = SimpleNamespace(execute=lambda: True)
    monkeypatch.setattr(init_mod, "_setup_steamcmd", lambda existing: str(tmp_path / "steamcmd"))
    monkeypatch.setattr(init_mod, "_setup_server", lambda existing: (answers.pop(0), True))
    monkeypatch.setattr(init_mod, "detect_workshop_content_dir", lambda: None)
    monkeypatch.setattr(init_mod, "_migrate_env_file", lambda server_dir, existing: (None, ""))
    monkeypatch.setattr(init_mod, "_prompt_steam_username", lambda default: "someone")
    monkeypatch.setattr(init_mod.inquirer, "confirm", lambda **_k: yes)
    monkeypatch.setattr(init_mod, "_perform_steam_login", lambda config: False)
    monkeypatch.setattr(init_mod, "_offer_site", lambda config: None)
    result = CliRunner().invoke(init_mod.init, [], obj={})
    assert result.exit_code == 0, result.output
    return result


def test_init_on_a_fresh_box_writes_the_first_instance(tmp_config_paths: Path, monkeypatch, tmp_path):
    from app.server.instances import load_registry

    _run_wizard(monkeypatch, tmp_path)

    assert not tmp_config_paths.exists(), "nothing goes to the legacy path"
    config = DayzConfig.load()
    assert (config.instance_id, config.steam_username) == ("main", "someone")
    assert load_registry().default == "main"


def test_init_asks_again_for_a_server_dir_the_unit_could_not_render(
    tmp_config_paths: Path, monkeypatch, tmp_path
):
    result = _run_wizard(monkeypatch, tmp_path, typed_first=("/srv/da'yz", "/srv/%h"))

    assert " ".join(result.output.split()).count("Server directory contains") == 2
    assert DayzConfig.load().server_dir == str(tmp_path / "server")


def test_init_over_a_legacy_config_keeps_writing_it(tmp_config_paths: Path, monkeypatch, tmp_path):
    """The migration has not run (a panel was still up): the legacy file is
    the live one, and a new instance beside it would be read instead."""
    DayzConfig(steam_username="before").save()

    _run_wizard(monkeypatch, tmp_path)

    config = DayzConfig.load()
    assert (config.instance_id, config.steam_username) == ("", "someone")
    assert not (tmp_config_paths.parent / "instances").exists()


# --- the wizard after a Steam login that failed (first-hour test, 2026 Q4) ---


def _wizard_config(tmp_path: Path) -> SimpleNamespace:
    steamcmd = tmp_path / "steamcmd.sh"
    steamcmd.write_text("", encoding="utf-8")
    return SimpleNamespace(
        steam_username="bob",
        steamcmd_path=steamcmd,
        server_dir_path=tmp_path / "dayz-server",
    )


def test_a_wrong_password_ends_in_one_plain_line(tmp_path: Path, monkeypatch):
    """The wizard printed the exception: the SteamCMD argv and "returned
    non-zero exit status 5". SteamCMD's own line already says why."""
    import subprocess

    from app.commands import init as init_mod

    config = _wizard_config(tmp_path)

    def refuse(_config):
        raise subprocess.CalledProcessError(5, [str(config.steamcmd_path), "+login", "bob", "+quit"])

    monkeypatch.setattr(init_mod, "steam_login", refuse)
    with init_mod.console.capture() as cap:
        assert init_mod._perform_steam_login(config) is False
    out = cap.get()
    assert "exit status" not in out and "+login" not in out
    assert "zsm steam-login" in out


def test_any_other_login_failure_still_says_what_it_was(tmp_path: Path, monkeypatch):
    from app.commands import init as init_mod

    def busy(_config):
        raise RuntimeError("SteamCMD is busy in another zsm")

    monkeypatch.setattr(init_mod, "steam_login", busy)
    with init_mod.console.capture() as cap:
        assert init_mod._perform_steam_login(_wizard_config(tmp_path)) is False
    assert "SteamCMD is busy in another zsm" in cap.get()


def test_a_skipped_server_download_is_said_with_the_two_commands(tmp_path: Path, capsys):
    """Without the login the download step returned without a word, and the
    wizard ended "Setup complete!" on a machine with no DayZ server."""
    from app.commands import init as init_mod

    with init_mod.console.capture() as cap:
        init_mod._offer_server_install(_wizard_config(tmp_path), login_ok=False)
    out = cap.get() + capsys.readouterr().out
    flat = " ".join(out.split())
    assert "cannot be downloaded without the Steam login" in flat
    assert "zsm steam-login" in flat and "zsm setup" in flat


def test_setup_complete_is_said_only_when_there_is_a_server(tmp_path: Path):
    from app.commands import init as init_mod
    from app.shared.platform import IS_WINDOWS

    config = _wizard_config(tmp_path)
    with init_mod.console.capture() as cap:
        init_mod._say_done(config)
    flat = " ".join(cap.get().split())
    assert "Setup complete" not in flat
    assert "The DayZ server is not installed yet" in flat
    assert "zsm steam-login" in flat and "zsm setup" in flat

    config.server_dir_path.mkdir()
    (config.server_dir_path / ("DayZServer_x64.exe" if IS_WINDOWS else "DayZServer")).write_text("")
    with init_mod.console.capture() as cap:
        init_mod._say_done(config)
    assert "Setup complete!" in cap.get() and "not installed" not in cap.get()

