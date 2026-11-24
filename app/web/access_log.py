"""Who reached the panel, from where, and when (TODO B6).

Four events are recorded: a successful login, a failed one, a failed
*second factor*, and a logout. A failed attempt is the interesting one and is
why this is not simply a session list: repeated failures from an address nobody
recognises is the signal, and before this the only trace was a 401 in uvicorn's
own log, which `zsm web` runs at `warning` so it was never printed at all.

**`login_totp_failed` is deliberately not folded into `login_failed`** (TODO
B5). It means the password was accepted and the code was not, which is a
different statement about how much trouble the panel is in: one says somebody
is guessing, the other says somebody *has a password* and is one phone short.
Merging them would bury the second in the noise of the first, which is exactly
the traffic an exposed panel gets all day.

B6 shipped with the address as the only identity, because there were no
accounts. Since `plans/43` step 6 an entry also carries `user`, and **an entry
written before that has no `user` key at all** — every reader has to tolerate
its absence rather than assume the column.

**The attempted password is never recorded**, nor anything derived from it —
and that promise is what shapes how `user` is filled on a *failed* attempt.
The username box sits next to the password box on the same form, so a name
that matches no account may well be a password typed into the wrong field.
Such an attempt is therefore recorded as **that a name was tried** (`user` is
empty, `unknown_user` is true) and never as the string itself; the caller
decides, see `app/web/routers/auth.py`. What is lost is which made-up names a
scanner guessed; what is kept is the signal worth having — somebody is
hammering an account that really exists.

The path is resolved **at call time**, not bound at import: a module-level
`CONFIG_DIR / …` constant does not follow `tests/conftest.py`'s monkeypatch
and would write into the real `~/.config/zsm` during tests (see the
module-level path rule in `app/web/CLAUDE.md`).
"""

from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path

from app.shared.json_utils import atomic_write_lines
from app.shared.platform import IS_WINDOWS

LOG_NAME = "access_log.jsonl"

# Bounded so an exposed panel under a login flood cannot fill the disk: the
# file is trimmed to the newest MAX_ENTRIES whenever it grows past MAX_BYTES.
# Both are generous for the thing this is read for (the last day or two of
# logins) and small enough that the whole file is cheap to read.
MAX_ENTRIES = 500
MAX_BYTES = 256 * 1024

EVENTS = ("login", "login_failed", "login_totp_failed", "logout")


def access_log_path() -> Path:
    """Resolved per call so a monkeypatched CONFIG_DIR is honoured."""
    from app.shared.paths import CONFIG_DIR

    return CONFIG_DIR / LOG_NAME


def record(
    event: str,
    ip: str,
    user_agent: str = "",
    path: Path | None = None,
    user: str = "",
    unknown_user: bool = False,
) -> None:
    """Append one entry. **Never raises.**

    This sits directly in the login path, so a full disk or an unwritable
    config dir must cost a log line, not the ability to log in.

    *user* must be the name of an account that exists — never a string the
    caller was handed and did not recognise (see the module docstring). Pass
    ``unknown_user=True`` instead for an attempt on a name nobody has.
    """
    dest = path or access_log_path()
    entry = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "event": event,
        "ip": ip or "",
        # Bounded like the User-Agent below, belt and braces: the caller is
        # supposed to hand over a name it looked up, and an account name is
        # already length-checked on creation.
        "user": (user or "")[:64],
        # Bounded: a User-Agent is attacker-controlled and unbounded in
        # principle. It is stored to tell a browser from a script, not to be
        # parsed, so a truncated one is as useful as a whole one.
        "user_agent": (user_agent or "")[:200],
    }
    if unknown_user:
        # Only written when true, so the common line stays the short one.
        entry["unknown_user"] = True
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with _open_owner_only(dest) as fh:
            fh.write(json.dumps(entry) + "\n")
        _trim(dest)
    except OSError:
        return


def _open_owner_only(path: Path):
    """Open *path* for appending, readable by its owner only (plans/68 SEC-8).

    The log holds client addresses, account names and user agents. A new file
    is created 0600; an existing one other users can read loses those bits on
    the next entry, the way `paths.secure_config_dir` tightens the directory.
    A file zsm cannot chmod (someone else's, or a filesystem that refuses) is
    written as it is. On Windows it is a plain append: the config dir's ACL
    already makes it per-user, and chmod there only sets the read-only flag.
    `_trim` rewrites through mkstemp, which creates 0600 as well.
    """
    if IS_WINDOWS:
        return path.open("a", encoding="utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        mode = stat.S_IMODE(os.fstat(fd).st_mode)
        if mode & 0o077:
            os.fchmod(fd, mode & ~0o077)
    except OSError:
        pass
    try:
        return os.fdopen(fd, "a", encoding="utf-8")
    except BaseException:
        os.close(fd)
        raise


def _trim(path: Path) -> None:
    """Keep the newest MAX_ENTRIES once the file passes MAX_BYTES."""
    try:
        if path.stat().st_size <= MAX_BYTES:
            return
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        if len(lines) > MAX_ENTRIES:
            atomic_write_lines(path, lines[-MAX_ENTRIES:], owner_only=True)
    except OSError:
        return


def read_recent(limit: int = 100, path: Path | None = None) -> list[dict]:
    """Newest first. Unreadable or malformed lines are skipped, not raised.

    Entries are returned exactly as they were written, which means a line from
    before `plans/43` step 6 has **no** ``user`` key. Readers must treat that
    as "not recorded" rather than backfilling it with a name.
    """
    src = path or access_log_path()
    if not src.is_file():
        return []
    try:
        raw = src.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    entries: list[dict] = []
    for line in reversed(raw):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
        if len(entries) >= limit:
            break
    return entries


def client_ip(request) -> str:
    """The caller's address as uvicorn resolved it.

    ``request.client`` is already rewritten from ``X-Forwarded-For`` when the
    proxy is trusted (``proxy_headers=True`` + ``web_trusted_proxies``), so
    reading the header here as well would let *any* caller forge the address
    this log is supposed to pin down.
    """
    client = getattr(request, "client", None)
    return getattr(client, "host", "") or ""
