"""Which role a request needs (``plans/43`` steps 3 and 4).

`required_role` answers the question; `deps.require_session` is the single
place that acts on the answer, **with the §2 decisions in hand**.
`audit_app` enumerates the whole app so a test can assert every route has an
answer.

The design, and why it is not 288 route annotations:

* **Default deny, by method.** A write is `admin` unless it is named in
  `MODERATOR_WRITES`. Forgetting to classify a new `POST` therefore costs a
  moderator a 403 — a bug report — where forgetting an entry in an allowlist
  would leave it open. Same discipline as ``firewall_ops``' rule that "could
  not tell" must never render as "closed", with the sign flipped to match the
  stakes.
* **A read is `viewer` unless it is named.** This is the direction that needs
  care, and `NON_VIEWER_READS` is the result of actually walking the 133 GET
  routes rather than assuming (see below).

**GET is not automatically safe in this codebase.** The root ``CLAUDE.md`` says
it outright — commands that read like reports still write. Measured on the real
app, 2026 Q3:

* ``GET /api/settings`` serves ``steam_api_key`` and ``discord_webhook_url``.
  A webhook URL *is* the credential.
* ``GET /api/bundle/download`` and ``/api/backups/download`` hand over a zip
  containing ``config.json``, which carries the RCon password, that webhook
  URL, the Steam API key and the panel password hash in cleartext.
* ``GET /api/configs/serverdz/raw`` (and ``/download``) is ``serverDZ.cfg``,
  which holds ``password`` and ``passwordAdmin``.
* ``GET /api/security/access-log`` is a security log.
* ``GET /api/steam/status?check=1`` reaches ``WebState.steam_probe``, which
  spawns SteamCMD. It stays viewer: the probe is serialised behind a lock and
  never runs beside a job. Plain ``/api/status``, ``/api/steam/status`` and
  ``/api/setup-state`` serve the cached answer since plans/51 D, and touch
  nothing. The route is listed below as a *known* side-effecting read rather
  than an overlooked one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.web.users import ROLES, role_at_least

__all__ = [
    "MODERATOR_WRITES",
    "NON_VIEWER_READS",
    "SESSION_WRITES",
    "RouteRule",
    "audit_app",
    "enumerate_routes",
    "refusal_message",
    "required_role",
    "would_refuse",
]

VIEWER, MODERATOR, ADMIN = ROLES


@dataclass(frozen=True)
class RouteRule:
    method: str
    pattern: str
    role: str
    why: str


def _rx(pattern: str) -> re.Pattern:
    """A route path with ``{params}`` turned into a full-match regex."""
    return re.compile("^" + re.sub(r"\{[^}]+\}", r"[^/]+", re.escape(pattern)
                                   .replace(r"\{", "{").replace(r"\}", "}")) + "$")


# ── writes a moderator may make ─────────────────────────────────────────
#
# TODO B4's own list: kick, ban, broadcast, restart. Everything else that
# writes is admin by omission, which is the point.
MODERATOR_WRITES: tuple[RouteRule, ...] = (
    RouteRule("POST", "/api/players/kick", MODERATOR, "kick a player"),
    RouteRule("POST", "/api/players/ban", MODERATOR, "ban a player"),
    RouteRule("POST", "/api/players/message", MODERATOR, "broadcast to players"),
    RouteRule("POST", "/api/bans", MODERATOR, "add a ban"),
    RouteRule("DELETE", "/api/bans", MODERATOR, "lift a ban"),
    RouteRule("DELETE", "/api/bans/rcon/{index}", MODERATOR, "lift a live ban"),
    RouteRule("POST", "/api/bans/cleanup-expired", MODERATOR, "tidy expired bans"),
    RouteRule("POST", "/api/rcon/command", MODERATOR, "the RCon console"),
    RouteRule("POST", "/api/service/start", MODERATOR, "start the server"),
    RouteRule("POST", "/api/service/stop", MODERATOR, "stop the server"),
    RouteRule("POST", "/api/service/restart", MODERATOR, "restart the server"),
    RouteRule("POST", "/api/players/watchlist", MODERATOR, "watch a player"),
    RouteRule("DELETE", "/api/players/watchlist", MODERATOR, "unwatch a player"),
    RouteRule("POST", "/api/priority", MODERATOR, "priority queue"),
    RouteRule("DELETE", "/api/priority", MODERATOR, "priority queue"),
    RouteRule("POST", "/api/whitelist", MODERATOR, "whitelist"),
    RouteRule("DELETE", "/api/whitelist", MODERATOR, "whitelist"),
)

# ── writes every session may make, whatever its role ────────────────────
#
# Things you do to **your own account**, which default-deny would otherwise
# make `admin`. That default is right for a write that acts on the server; it
# is wrong for one whose entire effect is on the caller, and each of these
# routes enforces "your own" itself rather than trusting a role to mean it.
#
# `/api/auth/logout` came first: without it a viewer has no way out of the
# panel but to clear the cookie by hand or wait a week for it to expire.
#
# `/api/settings/password` is the odd one out on its router, every other route
# of which is admin. A moderator who cannot rotate their own password has to
# ask an admin to do it for them — which means telling somebody the new one,
# or being told it. The route changes the caller's row and nobody else's.
# The three `/totp/` routes act on the caller's own row and no other; an admin
# un-enrolling somebody else's lost phone goes through the CLI. A second factor
# is not a privilege, and a moderator who cannot protect their own account is
# the weakest password on the panel whatever their role says.
SESSION_WRITES: tuple[RouteRule, ...] = (
    RouteRule("POST", "/api/auth/logout", VIEWER, "end your own session"),
    RouteRule("POST", "/api/settings/password", VIEWER, "change your own password"),
    RouteRule("POST", "/api/auth/totp/start", VIEWER, "set up your own second factor"),
    RouteRule("POST", "/api/auth/totp/enable", VIEWER, "confirm your own second factor"),
    RouteRule("POST", "/api/auth/totp/disable", VIEWER, "turn off your own second factor"),
)

# ── reads that are not viewer-safe ──────────────────────────────────────
NON_VIEWER_READS: tuple[RouteRule, ...] = (
    RouteRule("GET", "/api/settings", ADMIN, "serves steam_api_key and the Discord webhook URL"),
    RouteRule("GET", "/api/bundle/download", ADMIN, "zip contains config.json in cleartext"),
    RouteRule("GET", "/api/backups/download", ADMIN, "zip may contain config.json"),
    RouteRule("GET", "/api/backups/remote", ADMIN, "offsite destination"),
    RouteRule("GET", "/api/security", ADMIN, "how exposed this panel is"),
    RouteRule("GET", "/api/security/access-log", ADMIN, "a security log"),
    RouteRule("GET", "/api/configs/{editor_id}/raw", ADMIN,
              "serverDZ.cfg holds password and passwordAdmin"),
    RouteRule("GET", "/api/configs/{editor_id}/download", ADMIN, "same files, as a file"),
    # `GET /api/runcommand` is deliberately not here (plans/68): the same
    # launch line and paths are in `GET /api/configs/runcommand`, which every
    # role reads, and the Dashboard card asks for it. Changing it stays admin.
    RouteRule("GET", "/api/users", ADMIN, "who can reach this panel, and as what"),
    RouteRule("GET", "/api/self-update", ADMIN,
              "runs git fetch, and names where Zedstead is installed"),
    RouteRule("GET", "/api/adopt", ADMIN,
              "the server's own start script and unit lines, which Zedstead did not write (plans/78)"),
    # Not a credential, but it is the moderators' own action history.
    RouteRule("GET", "/api/rcon/history", MODERATOR, "what moderators have run"),
    # Player IP addresses are for moderators (SEC-7); the other ban and
    # player reads hide them from a viewer instead (routers/players.py).
    RouteRule("GET", "/api/bans/export", MODERATOR, "bans.txt holds player IP addresses"),
    RouteRule("GET", "/api/analysis/logs/download", MODERATOR,
              "the whole server log, with player names and Steam IDs"),
)

# Reads that touch something, kept as a named list so they are known rather
# than overlooked. They are *allowed* for a viewer; see the module docstring.
SIDE_EFFECTING_READS: tuple[str, ...] = (
    "/api/steam/status",  # only with ?check=1 (plans/51 D)
    "/api/a2s",
    "/api/bans/rcon",
    "/api/maps/download-sizes",  # asks Steam per id: a size kept 6 h, none 5 min, one request at a time (routers/maps.py)
    # Reads the server's world file (a PBO), or downloads a cleared map's
    # height grid from zedstead.com (maker_heights), and writes a cache under
    # maps/terrain/ in the config dir (terrain_map.cached_build).
    "/api/map-editor/terrain-heights",
    # The same world-file read and the same cache, for the roads (road_map).
    "/api/map-editor/roads",
    # A cleared community map's image, downloaded from zedstead.com into maps/
    # in the config dir on first use (map_data.cleared_image).
    "/api/map-editor/map-image",
    # git fetch from the checkout's own remote (app/ops/self_update.py).
    "/api/self-update",
)

_COMPILED = tuple(
    (rule, _rx(rule.pattern))
    for rule in (*SESSION_WRITES, *MODERATOR_WRITES, *NON_VIEWER_READS)
)

# Reachable without a session at all; `require_session` already lets these by.
UNGATED = ("/api/auth/login", "/api/health")


def required_role(method: str, path: str) -> str:
    """The minimum role for *method* *path*.

    A write nobody classified comes back ``admin``. That is the whole design:
    the omission costs privilege rather than granting it.
    """
    method = (method or "").upper()
    for rule, rx in _COMPILED:
        if rule.method == method and rx.match(path):
            return rule.role
    return VIEWER if method in ("GET", "HEAD") else ADMIN


def rule_for(method: str, path: str) -> RouteRule | None:
    """The explicit rule that matched, or ``None`` when the default applied."""
    method = (method or "").upper()
    for rule, rx in _COMPILED:
        if rule.method == method and rx.match(path):
            return rule
    return None


# ── enumerating the app ─────────────────────────────────────────────────


def enumerate_routes(app) -> list[tuple[str, str]]:
    """Every ``(method, path)`` the app serves, sorted and de-duplicated.

    **It has to recurse into included routers.** This FastAPI keeps an
    included router as an opaque ``_IncludedRouter`` rather than flattening its
    routes into ``app.routes``, so the obvious walk finds **six** routes — the
    handful defined directly on the app — and a test written that way would
    declare all 288 classified while checking almost none. Found the first time
    this was run (2026 Q3).

    **Through ``effective_candidates``, not ``original_router``** (plans/68
    review B6). A router's own routes carry only its own prefix, so the map
    editor's router, included inside ``/api/map-editor``'s, reported
    ``/traders`` for ``/api/map-editor/traders``. The effective candidates
    carry the path as it is served, and a nested router comes back with the
    prefixes combined. A FastAPI that flattens included routes again has no
    such method, and the route itself is then the served one.
    """
    def walk(routes) -> list[tuple[str, str]]:
        found: list[tuple[str, str]] = []
        for route in routes:
            effective = getattr(route, "effective_candidates", None)
            if effective is not None:
                found += walk(effective())
                continue
            nested = getattr(route, "routes", None)
            if nested:
                found += walk(nested)
                continue
            path = getattr(route, "path", None)
            if not path:
                continue
            for method in getattr(route, "methods", None) or ():
                if method not in ("HEAD", "OPTIONS"):
                    found.append((method, path))
        return found

    return sorted(set(walk(app.routes)))


def would_refuse(method: str, path: str, role: str) -> str | None:
    """A sentence naming the refusal, or ``None`` when the caller may proceed.

    Phrased for the **log**: it repeats the method and path, which the reader
    of a log file does not otherwise have. `refusal_message` is the half meant
    for the caller.
    """
    needed = required_role(method, path)
    if role_at_least(role, needed):
        return None
    return f"{method} {path} needs {needed}, caller is {role}"


def refusal_message(method: str, path: str, role: str) -> str:
    """What the refused caller is told.

    Names the role required and the role held, and nothing else. A 403 that
    only says "forbidden" sends someone hunting for a bug in a panel that is
    working exactly as configured — the commonest reading of this response is
    "zsm is broken", and it is worth two extra words to prevent.
    """
    return (
        f"This needs the {required_role(method, path)} role. "
        f"You are signed in as {role}."
    )


def audit_app(app) -> list[dict]:
    """Every route with the role it would need and where that came from."""
    out = []
    for method, path in enumerate_routes(app):
        rule = rule_for(method, path)
        out.append(
            {
                "method": method,
                "path": path,
                "role": required_role(method, path),
                "source": "rule" if rule else "default",
                "why": rule.why if rule else "",
            }
        )
    return out
