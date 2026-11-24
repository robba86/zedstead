"""Tests for `zsm users` — the only way to create an account (plans/43 step 5).

Two things here are worth more than the happy paths, and both are ways this
command could quietly lock somebody out of their own panel:

1. **The migration has to run before the first `add`.** Adding a moderator to
   an install that still has the single password would otherwise make that
   moderator the *only* account — and the single password stops working the
   moment accounts exist.
2. **`list` must not create the file it is reporting on.** It is the command
   someone runs to find out whether they have accounts yet.

Every test pins `CONFIG_DIR` at tmp: `users_path()` resolves it per call, and
a test that forgot would read and rewrite the developer's real account file.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from app.commands.users import users_group
from app.shared.config import DayzConfig
from app.web.auth import hash_password, verify_password
from app.web.users import UserStore


@pytest.fixture()
def config_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("app.shared.paths.CONFIG_DIR", tmp_path)
    return tmp_path


@pytest.fixture()
def with_password(config_dir, monkeypatch):
    """An install that still has the single pre-accounts password."""
    config = DayzConfig(server_dir="/srv/dayz")
    config.web_password_hash = hash_password("old-password")
    monkeypatch.setattr("app.commands.users.DayzConfig.load", lambda: config)
    return config


@pytest.fixture()
def no_password(config_dir, monkeypatch):
    config = DayzConfig(server_dir="/srv/dayz")
    monkeypatch.setattr("app.commands.users.DayzConfig.load", lambda: config)
    return config


def _run(*args, **kw):
    return CliRunner().invoke(users_group, list(args), **kw)


def _users(config_dir):
    return json.loads((config_dir / "users.json").read_text(encoding="utf-8"))["users"]


# ── list ────────────────────────────────────────────────────────────────


def test_list_on_a_fresh_install_says_so_without_creating_anything(
    config_dir, with_password
):
    """The command someone runs to ask "do I have accounts yet?". A report
    that creates the file cannot answer that."""
    result = _run("list")
    assert result.exit_code == 0, result.output
    assert "No accounts yet" in result.output
    assert not (config_dir / "users.json").exists()


def test_list_shows_names_and_roles(config_dir, with_password):
    UserStore().add("boss", "password-a", "admin")
    UserStore().add("mandy", "password-b", "moderator")

    result = _run("list")
    assert result.exit_code == 0, result.output
    assert "boss" in result.output
    assert "mandy" in result.output
    assert "moderator" in result.output


def test_list_reports_an_unreadable_file_rather_than_an_empty_one(
    config_dir, with_password
):
    """"No accounts" and "cannot read the accounts" are different problems and
    the fix for one is not the fix for the other."""
    (config_dir / "users.json").write_text("{ truncated", encoding="utf-8")
    result = _run("list")
    assert result.exit_code == 1
    assert "could not be read" in result.output


# ── add ─────────────────────────────────────────────────────────────────


def test_the_first_add_migrates_the_old_password_into_an_admin(
    config_dir, with_password
):
    """The lockout this command exists to avoid: without the migration, the
    moderator below would be the only account, and the single password stops
    working the moment accounts exist."""
    result = _run("add", "mandy", "--role", "moderator", "--password", "modpass-1")
    assert result.exit_code == 0, result.output

    rows = _users(config_dir)
    assert [(r["name"], r["role"]) for r in rows] == [
        ("admin", "admin"),
        ("mandy", "moderator"),
    ]
    # And the migrated admin is the *same* credential, not a new one.
    assert verify_password("old-password", rows[0]["password_hash"])


def test_a_first_account_that_is_not_an_admin_is_refused(config_dir, no_password):
    """No password to migrate, so there is no admin to inherit — creating a
    lone viewer here would be a panel nobody can administer."""
    result = _run("add", "looker", "--role", "viewer", "--password", "password-x")
    assert result.exit_code == 1
    assert "has to be an admin" in result.output
    assert not (config_dir / "users.json").exists()


def test_the_default_role_is_the_least_privileged(config_dir, with_password):
    _run("add", "looker", "--password", "password-x")
    assert _users(config_dir)[-1]["role"] == "viewer"


def test_the_password_is_prompted_when_not_given(config_dir, with_password):
    result = _run("add", "mandy", "--role", "moderator", input="s3cret-pw\ns3cret-pw\n")
    assert result.exit_code == 0, result.output
    assert verify_password("s3cret-pw", _users(config_dir)[-1]["password_hash"])
    assert "s3cret-pw" not in result.output, "a typed password must not be echoed"


def test_a_short_password_is_asked_for_again_at_the_prompt(config_dir, with_password):
    """A typed short password costs a retry, not the command."""
    result = _run(
        "add", "mandy", "--role", "moderator", input="short\ns3cret-pw\ns3cret-pw\n"
    )
    assert result.exit_code == 0, result.output
    assert "at least 8 characters" in result.output
    assert "short" not in result.output, "a typed password must not be echoed"
    assert verify_password("s3cret-pw", _users(config_dir)[-1]["password_hash"])


def test_a_short_password_flag_is_an_error_on_add_and_passwd(config_dir, with_password):
    result = _run("add", "mandy", "--password", "short")
    assert result.exit_code == 1
    assert "at least 8 characters" in result.output
    assert [u["name"] for u in _users(config_dir)] == ["admin"]

    _run("add", "mandy", "--password", "long-enough")
    result = _run("passwd", "mandy", "--password", "short")
    assert result.exit_code == 1
    assert "at least 8 characters" in result.output
    assert verify_password("long-enough", _users(config_dir)[-1]["password_hash"])


def test_a_duplicate_name_is_an_error_not_a_traceback(config_dir, with_password):
    _run("add", "mandy", "--password", "password-x")
    result = _run("add", "mandy", "--password", "password-y")
    assert result.exit_code == 1
    assert "already exists" in result.output


def test_an_unknown_role_is_rejected_by_click(config_dir, with_password):
    result = _run("add", "mandy", "--role", "superuser", "--password", "password-x")
    assert result.exit_code != 0
    assert "superuser" in result.output


# ── remove / set-role / passwd ──────────────────────────────────────────


@pytest.fixture()
def say_yes(monkeypatch):
    """Answer the delete prompt.

    Patched on `app.commands.users`, not on `cli_ui` — the command did
    `from ... import confirm`, so it holds its own reference and patching the
    source module would leave the real InquirerPy prompt in place, which under
    CliRunner has no tty and silently returns the default.
    """
    monkeypatch.setattr("app.commands.users.confirm", lambda *a, **k: True)


def test_remove_asks_first(config_dir, with_password, monkeypatch):
    """The default is no. Walking away from a delete prompt must not delete."""
    seen = {}

    def _record(prompt, **kw):
        seen.update(prompt=prompt, default=kw.get("default"))
        return False

    monkeypatch.setattr("app.commands.users.confirm", _record)
    _run("add", "mandy", "--password", "password-x")
    result = _run("remove", "mandy")

    assert result.exit_code == 0, result.output
    assert "mandy" in seen["prompt"]
    assert seen["default"] is False
    assert UserStore().get("mandy") is not None


def test_remove_deletes_when_confirmed(config_dir, with_password, say_yes):
    _run("add", "mandy", "--password", "password-x")
    result = _run("remove", "mandy")
    assert result.exit_code == 0, result.output
    assert UserStore().get("mandy") is None


def test_removing_the_last_admin_is_refused_with_a_reason(
    config_dir, with_password, say_yes
):
    """The store refuses; this asserts the refusal reaches the operator as a
    message rather than as a traceback."""
    _run("add", "mandy", "--password", "password-x")
    result = _run("remove", "admin")
    assert result.exit_code == 1
    assert "only admin" in result.output
    assert UserStore().get("admin") is not None


def test_removing_somebody_who_is_not_there_is_an_error(
    config_dir, with_password, say_yes
):
    result = _run("remove", "ghost")
    assert result.exit_code == 1
    assert "No such account" in result.output


def test_set_role_changes_it(config_dir, with_password):
    _run("add", "mandy", "--password", "password-x")
    result = _run("set-role", "mandy", "moderator")
    assert result.exit_code == 0, result.output
    assert UserStore().get("mandy").role == "moderator"


def test_demoting_the_last_admin_is_refused_with_a_reason(config_dir, with_password):
    _run("add", "mandy", "--password", "password-x")
    result = _run("set-role", "admin", "viewer")
    assert result.exit_code == 1
    assert "only admin" in result.output


def test_passwd_replaces_the_hash_and_keeps_the_role(config_dir, with_password):
    _run("add", "mandy", "--role", "moderator", "--password", "old-password")
    result = _run("passwd", "mandy", "--password", "new-password")
    assert result.exit_code == 0, result.output

    user = UserStore().get("mandy")
    assert user.role == "moderator"
    assert verify_password("new-password", user.password_hash)
    assert not verify_password("old-password", user.password_hash)


def _saved_fallback() -> str:
    """``web_password_hash`` as written to disk (the autouse fixture points
    ``CONFIG_FILE`` at a throwaway dir)."""
    from app.shared import config as config_module

    return json.loads(config_module.CONFIG_FILE.read_text(encoding="utf-8"))["web_password_hash"]


def test_passwd_on_the_migrated_admin_moves_the_fallback(config_dir, with_password):
    """plans/68: the account the migration made keeps the single password in
    step with its own, so the fallback is never a password its owner retired."""
    result = _run("passwd", "admin", "--password", "new-password")
    assert result.exit_code == 0, result.output

    assert verify_password("new-password", with_password.web_password_hash)
    assert verify_password("new-password", _saved_fallback())


def test_passwd_on_another_account_leaves_the_fallback(config_dir, with_password):
    before = with_password.web_password_hash
    _run("add", "mandy", "--role", "moderator", "--password", "old-password")
    result = _run("passwd", "mandy", "--password", "new-password")
    assert result.exit_code == 0, result.output
    assert with_password.web_password_hash == before


def test_removing_the_migrated_admin_clears_the_fallback(
    config_dir, with_password, say_yes
):
    _run("add", "deputy", "--role", "admin", "--password", "password-d")
    result = _run("remove", "admin")
    assert result.exit_code == 0, result.output

    assert with_password.web_password_hash == ""
    assert _saved_fallback() == ""
    assert "zsm web --set-password" in result.output


def test_demoting_the_migrated_admin_clears_the_fallback(config_dir, with_password):
    _run("add", "deputy", "--role", "admin", "--password", "password-d")
    result = _run("set-role", "admin", "viewer")
    assert result.exit_code == 0, result.output

    assert with_password.web_password_hash == ""
    assert _saved_fallback() == ""
    assert "zsm web --set-password" in result.output


def test_promoting_it_back_restores_nothing_until_its_next_password(
    config_dir, with_password
):
    _run("add", "deputy", "--role", "admin", "--password", "password-d")
    _run("set-role", "admin", "viewer")
    result = _run("set-role", "admin", "admin")
    assert result.exit_code == 0, result.output
    assert with_password.web_password_hash == ""
    assert "zsm web --set-password" not in result.output

    assert _run("passwd", "admin", "--password", "new-password").exit_code == 0
    assert verify_password("new-password", _saved_fallback())


def test_changing_another_role_leaves_the_fallback(config_dir, with_password):
    before = with_password.web_password_hash
    _run("add", "mandy", "--role", "moderator", "--password", "password-m")
    assert _run("set-role", "mandy", "viewer").exit_code == 0
    assert with_password.web_password_hash == before


@pytest.fixture()
def cloned(config_dir, tmp_path):
    """`main` with the single password; `pvp` cloned from it before any
    account existed, so it holds the same hash; `pve` with a fallback of its
    own. `DayzConfig.load()` reads the registry's default, `main`."""
    from app.server import instances

    instances.save_new_config(DayzConfig(
        server_dir=str(tmp_path / "srv-main"), web_password_hash=hash_password("old-password")))
    instances.create_instance(
        "pvp", server_dir=str(tmp_path / "srv-pvp"), source=DayzConfig.for_instance("main"))
    pve = instances.create_instance("pve", server_dir=str(tmp_path / "srv-pve")).config
    pve.web_password_hash = hash_password("pve-own-pass")
    pve.save()


def test_the_fallback_follows_the_account_on_every_instance_holding_it(cloned, say_yes):
    """2P-W2: a copy left on `pvp` would sign in with the retired password once
    the panel starts there. The web's sync follows the same rule."""
    result = _run("passwd", "admin", "--password", "new-password")
    assert result.exit_code == 0, result.output
    for ident in ("main", "pvp"):
        assert verify_password("new-password", DayzConfig.for_instance(ident).web_password_hash)
    assert verify_password("pve-own-pass", DayzConfig.for_instance("pve").web_password_hash)
    assert "pvp" in result.output

    _run("add", "deputy", "--role", "admin", "--password", "password-d")
    result = _run("remove", "admin")
    assert result.exit_code == 0, result.output
    assert DayzConfig.for_instance("main").web_password_hash == ""
    assert DayzConfig.for_instance("pvp").web_password_hash == ""
    assert verify_password("pve-own-pass", DayzConfig.for_instance("pve").web_password_hash)


def test_passwd_for_somebody_who_is_not_there_is_an_error(config_dir, with_password):
    result = _run("passwd", "ghost", "--password", "password-x")
    assert result.exit_code == 1
    assert "No such user" in result.output


# ── the file itself ─────────────────────────────────────────────────────


def test_the_typed_password_never_reaches_the_file(config_dir, with_password):
    _run("add", "mandy", "--password", "correct-horse-battery-staple")
    raw = (config_dir / "users.json").read_text(encoding="utf-8")
    assert "correct-horse-battery-staple" not in raw


# ── totp (TODO B5) ──────────────────────────────────────────────────────
#
# `disable` is the one that matters most here. It is the **recovery path**: an
# account whose phone is lost cannot sign in to fix itself, and there is
# deliberately no web route that strips somebody else's second factor — one
# would make every admin session as strong as the weakest admin's password.


def test_enable_saves_nothing_until_a_code_checks_out(config_dir, with_password):
    """A key the phone never took is an account nobody can sign in to, and
    the only way back is this command with `disable`."""
    _run("add", "mandy", "--role", "moderator", "--password", "modpass-1")

    result = _run("totp", "enable", "mandy", input="000000\n")

    assert result.exit_code == 1
    assert "not right" in result.output
    assert all("totp_secret" not in u for u in _users(config_dir))


def test_enable_prints_a_key_that_can_be_typed(config_dir, with_password):
    """There is no QR code, so the printed key is the whole setup path — and
    an unbroken run of 32 characters is where typing it goes wrong."""
    from app.web import totp

    _run("add", "mandy", "--role", "moderator", "--password", "modpass-1")
    result = _run("totp", "enable", "mandy", "--no-verify")

    assert result.exit_code == 0, result.output
    stored = UserStore().get("mandy").totp_secret
    assert totp.format_secret(stored) in result.output
    assert totp.provisioning_uri(stored, "mandy") in result.output


def test_enable_with_the_right_code_turns_it_on(config_dir, with_password, monkeypatch):
    """The code has to be for the key *this run* generates, and `enable` never
    reuses one — so the secret is pinned rather than read back out of a
    previous run's output."""
    from app.web import totp

    secret = totp.generate_secret()
    monkeypatch.setattr("app.web.totp.generate_secret", lambda: secret)
    _run("add", "mandy", "--role", "moderator", "--password", "modpass-1")

    result = _run("totp", "enable", "mandy", input=f"{totp.now_code(secret)}\n")

    assert result.exit_code == 0, result.output
    assert UserStore().get("mandy").totp_secret == secret


def test_no_verify_says_what_it_skipped(config_dir, with_password):
    """It can lock the account out, so it names the way back in."""
    _run("add", "mandy", "--role", "moderator", "--password", "modpass-1")
    result = _run("totp", "enable", "mandy", "--no-verify")

    # Rich wraps, so the recovery command can land across two lines.
    flat = " ".join(result.output.split())
    assert "No code was checked" in flat
    assert "zsm users totp disable mandy" in flat


def test_enabling_twice_asks_before_replacing_the_key(config_dir, with_password, monkeypatch):
    """Replacing it stops the old key working, and the old key is what the
    account is currently signing in with — so a second `enable` is a
    destructive command wearing a harmless name."""
    _run("add", "mandy", "--role", "moderator", "--password", "modpass-1")
    _run("totp", "enable", "mandy", "--no-verify")
    first = UserStore().get("mandy").totp_secret

    answers = {"value": False}
    monkeypatch.setattr("app.commands.users.confirm", lambda *a, **k: answers["value"])

    declined = _run("totp", "enable", "mandy", "--no-verify")
    assert "already has two-factor" in declined.output
    assert "Cancelled" in declined.output
    assert UserStore().get("mandy").totp_secret == first

    answers["value"] = True
    accepted = _run("totp", "enable", "mandy", "--no-verify")
    assert accepted.exit_code == 0
    assert UserStore().get("mandy").totp_secret != first


def test_disable_is_the_way_back_in(config_dir, with_password, say_yes):
    _run("add", "mandy", "--role", "moderator", "--password", "modpass-1")
    _run("totp", "enable", "mandy", "--no-verify")

    result = _run("totp", "disable", "mandy")

    assert result.exit_code == 0, result.output
    assert UserStore().get("mandy").totp_enabled is False
    assert all("totp_secret" not in u for u in _users(config_dir))


def test_disable_asks_first(config_dir, with_password, monkeypatch):
    """The default is no, like every other destructive prompt here — and this
    one takes a protection off rather than putting one on."""
    seen = {}

    def _record(prompt, **kw):
        seen.update(prompt=prompt, default=kw.get("default"))
        return False

    monkeypatch.setattr("app.commands.users.confirm", _record)
    _run("add", "mandy", "--role", "moderator", "--password", "modpass-1")
    _run("totp", "enable", "mandy", "--no-verify")

    result = _run("totp", "disable", "mandy")

    assert seen["default"] is False
    assert "Cancelled" in result.output
    assert UserStore().get("mandy").totp_enabled is True


def test_disable_on_an_account_without_one_says_so(config_dir, with_password):
    _run("add", "mandy", "--role", "moderator", "--password", "modpass-1")
    result = _run("totp", "disable", "mandy")

    assert result.exit_code == 0
    assert "does not have" in result.output


def test_totp_on_an_unknown_account_is_an_error(config_dir, with_password):
    for cmd in ("enable", "disable"):
        result = _run("totp", cmd, "nobody")
        assert result.exit_code == 1
        assert "No such account" in result.output


def test_list_shows_who_is_not_protected(config_dir, with_password):
    """"Who has no second factor" is the question an admin asks, and this is
    the only place the answer is visible at a glance."""
    _run("add", "mandy", "--role", "moderator", "--password", "modpass-1")
    _run("totp", "enable", "mandy", "--no-verify")

    result = _run("list")

    assert result.exit_code == 0, result.output
    assert "2FA" in result.output
    # `admin` came from the migration and has none; mandy just enrolled.
    lines = [ln for ln in result.output.splitlines() if "mandy" in ln or "admin" in ln]
    assert any("mandy" in ln and "on" in ln for ln in lines)
    assert any("admin" in ln and "on" not in ln for ln in lines)


def test_a_totp_write_migrates_first(config_dir, with_password, say_yes):
    """Same rule as every other write here: without the migration the first
    one on a single-password install leaves the file holding one row, and that
    password stops working the moment accounts exist."""
    assert not (config_dir / "users.json").exists()

    _run("totp", "disable", "admin")

    assert [u["name"] for u in _users(config_dir)] == ["admin"]
