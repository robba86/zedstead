"""User accounts and roles — the store only (TODO B4, ``plans/43`` step 1).

This module knows *who exists* and *what they may be*. It does **not** gate a
single route: wiring it into login is step 2 and enforcing roles is step 4, and
keeping those apart is what makes each diff reviewable on an auth surface.

Three decisions are baked in here and each has a reason that is not style:

* **Accounts live in their own file, ``<CONFIG_DIR>/users.json``, mode 0600 —
  not in ``config.json``.** ``bundle export`` puts ``config.json`` into the zip
  verbatim (measured in 2026 Q3, see ``app/ops/CLAUDE.md``), so every hash put
  there travels to whoever is handed a bundle. ``offsite_ops`` set the
  precedent in the other direction and stated it: credentials get their own
  0600 file.
* **An unreadable file must not lock anyone out.** It falls back to the single
  ``config.web_password_hash`` the panel had before accounts existed, and says
  so through ``degraded``. The alternative is a panel nobody can enter on a
  box whose disk hiccuped, which is worse than the downgrade — and an attacker
  who can corrupt this file already has the filesystem.
* **Authentication always pays for a hash**, even for a name that does not
  exist. scrypt is deliberately slow, so returning early on an unknown user
  makes "which names are real" readable off a stopwatch.
* **The account made out of the single password stays tied to it**
  (plans/68). `migrate_from_config` marks that row ``migrated``. Every
  password change for it is mirrored into ``config.web_password_hash`` while
  it is an admin, and deleting or demoting it clears that field
  (`linked_fallback_hash`). So the fallback login above follows that admin's
  password, or is gone, instead of staying a password its owner retired or a
  way in as admin for someone who is no longer one.

Roles are an **ordered triple**, not a permission bitmap. TODO B4 asks for
three, and a bitmap invites per-route fiddling — which is the failure mode the
whole design in ``plans/43`` §3.2 is built to avoid.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from dataclasses import dataclass, replace
from functools import wraps
from pathlib import Path

from app.shared.platform import IS_WINDOWS
from app.web.auth import check_new_password, hash_password, verify_password

log = logging.getLogger(__name__)

__all__ = [
    "ROLES",
    "User",
    "UserStore",
    "highest_role",
    "role_at_least",
    "users_path",
]

# Ascending. `role_at_least` is the only thing that should read the order.
ROLES = ("viewer", "moderator", "admin")

USERS_FILENAME = "users.json"

# The name the single pre-accounts password becomes on migration.
DEFAULT_ADMIN = "admin"

# Cost paid for a name that does not exist, so an unknown user takes about as
# long as a wrong password. Any valid hash does; this one is for the literal
# string below and matches nothing anyone would type by accident.
_DUMMY_HASH = hash_password("zsm-no-such-user-" + "x" * 16)


def users_path() -> Path:
    """``<CONFIG_DIR>/users.json``.

    ``CONFIG_DIR`` is resolved **per call**, never captured at import: a
    module-level constant does not follow ``monkeypatch`` and every test would
    read the developer's real account file.
    """
    from app.shared import paths

    return paths.CONFIG_DIR / USERS_FILENAME


def role_at_least(role: str, minimum: str) -> bool:
    """Whether *role* is at least as privileged as *minimum*.

    An unknown role is **not** privileged. That direction matters: a typo in a
    hand-edited users.json should cost someone their access, never grant it.
    """
    try:
        return ROLES.index(role) >= ROLES.index(minimum)
    except ValueError:
        return False


def highest_role(roles: list[str]) -> str:
    """The most privileged of *roles*, or ``viewer`` for an empty/unknown set."""
    known = [r for r in roles if r in ROLES]
    return max(known, key=ROLES.index) if known else ROLES[0]


@dataclass(frozen=True)
class User:
    name: str
    role: str
    password_hash: str
    # A base32 TOTP secret, or "" for an account that signs in with a password
    # alone (TODO B5). Off by default and per account, never panel-wide: one
    # admin turning it on must not lock out colleagues who have not enrolled.
    totp_secret: str = ""
    # True only on the admin `UserStore.authenticate` makes out of
    # ``config.web_password_hash`` when there are no readable accounts. Never
    # stored: no row in users.json is a fallback. The login route hands it to
    # the session, which is how the role gate tells that session apart.
    fallback: bool = False
    # The row `migrate_from_config` made out of ``config.web_password_hash``
    # (plans/68). Stored as ``"migrated": true``, only on that row.
    migrated: bool = False

    @property
    def totp_enabled(self) -> bool:
        return bool(self.totp_secret)

    def as_json(self) -> dict:
        """The stored row. The TOTP key is omitted when there is none, so an
        install that never touches B5 keeps the file it always had."""
        data = {"name": self.name, "role": self.role, "password_hash": self.password_hash}
        if self.totp_secret:
            data["totp_secret"] = self.totp_secret
        if self.migrated:
            data["migrated"] = True
        return data

    def public(self) -> dict:
        """What a surface may see. Never the hash, and never the TOTP secret —
        that secret *is* the second factor, so handing it to a browser would
        make the factor exactly as strong as the session that asked."""
        return {"name": self.name, "role": self.role, "totp": self.totp_enabled}


# Every account write loads the whole file, edits it and saves it back, and the
# routes run them in the thread pool, so two at once would both save what they
# read and one change would be lost. One lock for every store in the process:
# they all write the same users.json. Reentrant, as migrate_from_config calls
# _mark_migrated.
_WRITE_LOCK = threading.RLock()


def _serialized(method):
    @wraps(method)
    def locked(self, *args, **kwargs):
        with _WRITE_LOCK:
            return method(self, *args, **kwargs)

    return locked


class UserStore:
    """Reads and writes ``users.json``.

    Built per ``WebState`` rather than as a module global, for the same reason
    ``WebState`` itself is: tests run several apps at once against different
    config dirs.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        # Set by `load` when the file exists but could not be understood. A
        # surface may report it; nothing here changes behaviour on it except
        # the fallback in `authenticate`.
        self.degraded = False

    @property
    def path(self) -> Path:
        return self._path if self._path is not None else users_path()

    # ── reading ─────────────────────────────────────────────────────────

    def exists(self) -> bool:
        return self.path.is_file()

    def load(self) -> list[User]:
        """Every account, or ``[]`` when the file is missing or unusable.

        ``degraded`` distinguishes the two: missing is the normal pre-migration
        state, unusable is a problem worth showing someone.
        """
        path = self.path
        if not path.is_file():
            self.degraded = False
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            entries = raw["users"] if isinstance(raw, dict) else raw
            users = [
                User(
                    name=str(e["name"]),
                    role=str(e.get("role", "viewer")),
                    password_hash=str(e.get("password_hash", "")),
                    totp_secret=str(e.get("totp_secret", "")),
                    migrated=e.get("migrated") is True,
                )
                for e in entries
                if isinstance(e, dict) and str(e.get("name", "")).strip()
            ]
        except (OSError, ValueError, KeyError, TypeError):
            self.degraded = True
            return []
        self.degraded = False
        return users

    def has_accounts(self) -> bool:
        """Whether any usable account exists.

        ``False`` also covers a file that exists but could not be read, on
        purpose. Every caller of this is deciding whether to fall back to the
        pre-accounts single password, and an unreadable file is exactly when
        that fallback is needed — the same rule ``authenticate`` follows, and
        the reason a corrupt ``users.json`` does not lock anyone out.
        """
        return bool(self.load())

    def get(self, name: str) -> User | None:
        """Look up by name, case-insensitively — the login form is typed."""
        lowered = (name or "").strip().lower()
        if not lowered:
            return None
        for user in self.load():
            if user.name.lower() == lowered:
                return user
        return None

    # ── writing ─────────────────────────────────────────────────────────

    def save(self, users: list[User]) -> Path:
        """Write the whole set atomically, 0600 from creation.

        0600 **at open time**, not by a later chmod: between a default-mode
        write and a chmod there is a window where the hashes are
        world-readable. Same rule as ``offsite_ops``' rclone.conf.
        """
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"users": [u.as_json() for u in users]}, indent=2)

        tmp = path.with_name(path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload + "\n")
        os.replace(tmp, path)
        if not IS_WINDOWS:
            path.chmod(0o600)
        return path

    def _loaded_for_writing(self) -> list[User]:
        """Every account, or a refusal if the file could not be read.

        A write starts by loading, edits the list and saves the whole set back,
        so a ``load`` that quietly returned ``[]`` for an unreadable file would
        **replace** whatever that file holds with a single new row. Reading is
        allowed to shrug and fall back to the old single password (that is what
        keeps a bad disk from locking anyone out); writing is not, because the
        two failures are not symmetric — one costs a downgrade, the other
        costs the accounts.
        """
        users = self.load()
        if self.degraded:
            raise ValueError(
                f"{self.path} exists but could not be read. Fix or move it "
                "before changing accounts — writing now would replace "
                "whatever it holds."
            )
        return users

    @_serialized
    def add(self, name: str, password: str, role: str) -> User:
        """Create an account.

        **The first account must be an admin.** A store whose only account is a
        viewer is a panel nobody can administer — the same end state `remove`
        and `set_role` refuse to reach, through the one door they did not
        cover. On an install that still has the single pre-accounts password,
        call `migrate_from_config` first and that admin already exists.

        **The name goes into a URL path** (``/api/users/<name>/role``), so a
        name the router can never match back is refused here, for the CLI and
        the web alike. A ``/`` arrives decoded and splits the path, and the
        browser folds ``.`` and ``..`` away before sending, so such an account
        could be created and then never changed or deleted from the panel.
        """
        name = (name or "").strip()
        if not name:
            raise ValueError("A user needs a name.")
        if (
            "/" in name
            or name in (".", "..")
            or any(ord(c) < 32 or ord(c) == 127 for c in name)
        ):
            raise ValueError(
                "A name cannot contain a slash or a control character, and "
                "cannot be . or .., because the app puts it in a web address."
            )
        if role not in ROLES:
            raise ValueError(f"Unknown role: {role!r}. One of: {', '.join(ROLES)}.")
        check_new_password(password)
        users = self._loaded_for_writing()
        if any(u.name.lower() == name.lower() for u in users):
            raise ValueError(f"{name} already exists.")
        if role != "admin" and not any(u.role == "admin" for u in users):
            raise ValueError("The first account has to be an admin.")
        user = User(name=name, role=role, password_hash=hash_password(password))
        self.save(users + [user])
        return user

    @_serialized
    def remove(self, name: str) -> User | None:
        """Delete an account and return it, or ``None`` if there is no such
        account. Refuses to remove the last admin.

        A panel with no admin cannot be administered and cannot be repaired
        from inside itself — the one irreversible mistake this store can make.

        The removed row is returned rather than ``True`` so a caller can see
        whether it was the ``migrated`` one (`linked_fallback_hash`).
        """
        users = self._loaded_for_writing()
        target = next((u for u in users if u.name.lower() == (name or "").lower()), None)
        if target is None:
            return None
        remaining = [u for u in users if u is not target]
        if target.role == "admin" and not any(u.role == "admin" for u in remaining):
            raise ValueError("That is the only admin — promote someone else first.")
        self.save(remaining)
        return target

    @_serialized
    def set_role(self, name: str, role: str) -> User:
        if role not in ROLES:
            raise ValueError(f"Unknown role: {role!r}. One of: {', '.join(ROLES)}.")
        users = self._loaded_for_writing()
        target = next((u for u in users if u.name.lower() == (name or "").lower()), None)
        if target is None:
            raise ValueError(f"No such user: {name}")
        if target.role == "admin" and role != "admin":
            if not any(u.role == "admin" and u is not target for u in users):
                raise ValueError("That is the only admin — promote someone else first.")
        updated = replace(target, role=role)
        self.save([updated if u is target else u for u in users])
        return updated

    @_serialized
    def set_password(self, name: str, password: str) -> User:
        """Set *name*'s password and return the updated row.

        For the ``migrated`` row the caller also writes the new hash into
        ``config.web_password_hash`` (`linked_fallback_hash`); this store
        holds no config.
        """
        check_new_password(password)
        users = self._loaded_for_writing()
        target = next((u for u in users if u.name.lower() == (name or "").lower()), None)
        if target is None:
            raise ValueError(f"No such user: {name}")
        # Rotating a password does not un-enroll a phone. They are two
        # independent factors, and dropping one because the other changed is
        # a downgrade nobody asked for.
        updated = replace(target, password_hash=hash_password(password))
        self.save([updated if u is target else u for u in users])
        return updated

    @_serialized
    def set_totp(self, name: str, secret: str) -> User:
        """Turn two-factor sign-in on (a secret) or off (``""``) for *name*.

        **The secret is decoded before it is stored.** A secret that cannot be
        decoded verifies no code at all, which reads to the person holding the
        phone exactly like a wrong clock — and it is unrecoverable from inside
        the panel, because the way back in needs a code. `totp._decode` raising
        here is the only place that failure is cheap.
        """
        from app.web import totp  # late: totp imports nothing from here, but
                                  # keeping the module pair acyclic is cheaper
                                  # than remembering which way round it went.

        cleaned = totp.normalise_secret(secret)
        if cleaned:
            totp._decode(cleaned)  # raises ValueError on anything unusable

        users = self._loaded_for_writing()
        target = next((u for u in users if u.name.lower() == (name or "").lower()), None)
        if target is None:
            raise ValueError(f"No such user: {name}")
        updated = replace(target, totp_secret=cleaned)
        self.save([updated if u is target else u for u in users])
        return updated

    # ── login ───────────────────────────────────────────────────────────

    def authenticate(self, name: str, password: str, fallback_hash: str = "") -> User | None:
        """The account for these credentials, or ``None``.

        *fallback_hash* is ``config.web_password_hash`` and is consulted **only**
        when there are no usable accounts — a fresh install that has not been
        migrated yet, or a ``users.json`` that could not be read. It logs the
        caller in as an admin, which is exactly what the panel did before
        accounts existed.
        """
        users = self.load()
        if not users:
            if fallback_hash and verify_password(password, fallback_hash):
                return User(
                    name=DEFAULT_ADMIN, role="admin", password_hash=fallback_hash, fallback=True
                )
            verify_password(password, _DUMMY_HASH)
            return None

        wanted = (name or "").strip().lower()
        if not wanted:
            # The login form has no username field yet (it grows one in
            # plans/43 step 5), and an install that has just been migrated has
            # exactly one account. Accepting a blank name against that single
            # account is what keeps the existing form working through the
            # migration. With two accounts a blank name is ambiguous, and
            # picking one would be a guess about who is at the keyboard.
            target = users[0] if len(users) == 1 else None
        else:
            target = next((u for u in users if u.name.lower() == wanted), None)
        if target is None:
            # Pay the hash anyway: scrypt is slow on purpose, so an early
            # return here is a timing oracle for "which names exist".
            verify_password(password, _DUMMY_HASH)
            return None
        if not verify_password(password, target.password_hash):
            return None
        return target

    # ── migration ───────────────────────────────────────────────────────

    @_serialized
    def migrate_from_config(self, config) -> User | None:
        """Turn the single pre-accounts password into the first admin.

        Runs once: it does nothing when ``users.json`` already exists, and
        nothing when there is no password to migrate (a fresh install, where
        ``zsm web --set-password`` still creates the first one).

        ``config.web_password_hash`` is **left in place**, not cleared. It stays
        the fallback in ``authenticate`` while this is young, and removing a
        working credential is not something a migration should do on its own.

        The admin it makes is marked ``migrated`` (plans/68), which ties its
        password to that fallback from then on. A ``users.json`` written
        before the marker existed gets it here too: the one row whose hash is
        still the config's is marked and the file saved. No match, or more than
        one, marks nothing, because the link would be a guess.
        """
        existing = str(getattr(config, "web_password_hash", "") or "")
        if self.exists():
            self._mark_migrated(existing)
            return None
        if not existing:
            return None
        admin = User(name=DEFAULT_ADMIN, role="admin", password_hash=existing, migrated=True)
        self.save([admin])
        return admin

    @_serialized
    def _mark_migrated(self, config_hash: str) -> None:
        """Mark the row made from *config_hash*, on a file that predates the
        marker. Does nothing once any row carries it, or when the file cannot
        be read (a write would replace it; see `_loaded_for_writing`)."""
        if not config_hash:
            return
        users = self.load()
        if self.degraded or any(u.migrated for u in users):
            return
        matches = [u for u in users if u.password_hash == config_hash]
        if len(matches) != 1:
            return
        self.save([replace(u, migrated=True) if u is matches[0] else u for u in users])


def linked_fallback_hash(
    user: User, *, removed: bool = False, previous_role: str | None = None
) -> str | None:
    """What ``config.web_password_hash`` must become after *user* changed,
    or ``None`` to leave it alone (plans/68).

    *user* is the row after the change. *removed* says it was deleted;
    *previous_role* is given for a role change and is the role it had before.
    Anything else is a password change.

    Only the ``migrated`` account is tied to it: a new password is copied
    across, and deleting the account, or demoting it from admin, clears it.
    So the fallback login is never a password its owner retired, and never an
    admin login for someone who is no longer an admin. The web writes the
    answer through ``mutate_config`` (`sync_linked_fallback`), the CLI with
    ``config.save()``.

    **A new password is copied only while that account is an admin.** The
    fallback signs in as admin, and every role may change its own password
    (`permissions.SESSION_WRITES`), so copying a demoted account's new
    password would hand a viewer the panel's admin fallback.

    **Promoting it back restores nothing.** The fallback stays empty until
    the account's next password change as an admin, which writes it through
    the rule above. The marker stays on the row through a demotion for that
    reason: it is what ties the account to the fallback again.
    """
    if not user.migrated:
        return None
    if removed:
        return ""
    if previous_role is not None:
        # Only leaving admin changes anything; a promotion or a move between
        # the other roles leaves whatever the fallback holds now.
        return "" if previous_role == "admin" and user.role != "admin" else None
    return user.password_hash if user.role == "admin" else None


def fallback_copies(old_hash: str, own_id: str) -> list:
    """The other instances whose ``web_password_hash`` is *old_hash*, each
    config as read from disk (2P-W2).

    Every instance has a ``web_password_hash`` of its own, and a clone starts
    with its source's. The panel reads only the one it was started on, so a
    copy left behind elsewhere signs in again once the panel starts on that
    instance. So when the fallback changes, every copy of the value it
    replaces changes with it, and both sync paths ask this. Matching the old
    value leaves alone an instance whose fallback was set to something else
    on purpose; an empty old value matches nothing, since nothing is retired.
    """
    if not old_hash:
        return []
    from app.server import instances
    from app.shared.config import DayzConfig

    try:
        ids = instances.load_registry().ids()
    except (OSError, ValueError, KeyError, TypeError):
        log.warning("instances.json could not be read; the fallback changed on this instance only")
        return []
    copies = []
    for ident in ids:
        if ident == own_id:
            continue
        try:
            config = DayzConfig.for_instance(ident)
        except FileNotFoundError:
            continue
        except Exception:  # noqa: BLE001 - one broken config must not stop the change
            log.warning("instance %r could not be read; its fallback password was left as it is",
                        ident, exc_info=True)
            continue
        if config.web_password_hash == old_hash:
            copies.append(config)
    return copies


def replace_fallback_copies(old_hash: str, new_hash: str, own_id: str) -> list[str]:
    """Write *new_hash* into every `fallback_copies` config and return their
    ids. The CLI's path; the web writes an instance it serves through that
    instance's own bundle (`sync_linked_fallback`)."""
    changed = []
    for config in fallback_copies(old_hash, own_id):
        config.web_password_hash = new_hash
        config.save()
        changed.append(config.instance_id)
    return changed


async def sync_linked_fallback(
    state, user: User, *, removed: bool = False, previous_role: str | None = None
) -> None:
    """`linked_fallback_hash`, written to the panel's config (*state* is
    `deps.get_panel_state`'s bundle: the fallback is a panel setting), and
    to every other instance holding the value it replaced (`fallback_copies`).
    An instance the panel serves is written through its own bundle's
    `mutate_config`, so the copy that bundle holds in memory changes too."""
    new_hash = linked_fallback_hash(user, removed=removed, previous_role=previous_role)
    if new_hash is None:
        return
    replaced: list[str] = []

    def _set(cfg) -> None:
        replaced.append(cfg.web_password_hash)
        cfg.web_password_hash = new_hash

    await state.mutate_config(_set)
    old_hash = replaced[0]

    def _replace(cfg) -> None:
        if cfg.web_password_hash == old_hash:
            cfg.web_password_hash = new_hash

    for config in await asyncio.to_thread(fallback_copies, old_hash, state.config.instance_id):
        bundle = state.shared.bundles.get(config.instance_id)
        if bundle is not None:
            await bundle.mutate_config(_replace)
        else:
            config.web_password_hash = new_hash
            await asyncio.to_thread(config.save)


def has_no_login(config) -> bool:
    """Whether nobody could sign in: no single password and no readable account.

    `zsm web` asks for a first password exactly then, and `zsm service web
    install` refuses exactly then, because a service has no terminal to answer
    that prompt. Accounts are machine-wide (users.json beside every instance),
    so an instance with no password of its own still has a login while they
    exist. An unreadable users.json counts as no accounts, the same rule
    `authenticate` falls back on.
    """
    if str(getattr(config, "web_password_hash", "") or ""):
        return False
    return not UserStore().has_accounts()
