"""Answer only to the host names the admin listed (plans/68 NET-9).

**DNS rebinding.** A page on ``attacker.example`` can point its own name at
``127.0.0.1`` once it has loaded. The browser then sends that page's requests
to whatever listens on loopback, a panel bound there behind a reverse proxy or
reached through an SSH tunnel included, and counts them as same-origin with
the page, so `same_origin` lets its writes through. The session cookie belongs
to the panel's real name and does not go along, which leaves the routes that
answer without one: the login form (guessing at the limiter's pace) and the
health check. The one thing such a request cannot choose is the name it
carries: its ``Host`` header is ``attacker.example``.

So ``web_allowed_hosts`` (empty by default, and then nothing here does
anything) lists the names the panel answers to. Set, every HTTP request and
WebSocket handshake whose ``Host``, port stripped and compared case-
insensitively, is not on the list is refused before anything else runs: 400
for HTTP, a policy-violation close (1008) for a socket. ``/api/health``, the
login route and ``/i/<id>/`` pages are covered like every other path; this
runs before `InstancePrefix`, so an id is not looked up for a name the panel
does not answer to.

**``X-Forwarded-Host`` is not consulted.** A rebinding page's requests are
same-origin to the browser, so it may set any header a page is allowed to
set, and that is one of them; ``Host`` is not. Forwarded headers cannot be
trusted on loopback for the same reason. The reverse-proxy recipes pass
``Host`` through; a proxy that rewrites it needs the rewritten name
(``localhost``, ``127.0.0.1``) on the list, which a rebinding page cannot
send.

**A typo must not be the end.** A save from the panel that leaves out the
name the saving browser used is refused with 400 (`lockout_problem`). The
setting is read from the startup
instance's file, again whenever that config object is replaced (a save from
the panel) and at most `REREAD_SECONDS` after a change from anywhere else, so
``zsm settings set web_allowed_hosts ""`` on the server lets the browser back
in without restarting the panel. A file that cannot be read leaves the
panel's own copy of the config in force.

Pure ASGI, like `same_origin` and `instance_prefix`.
"""

from __future__ import annotations

import logging
import time

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.shared.settings_registry import host_entries
from app.web.security_headers import HEADERS

log = logging.getLogger(__name__)

# How long a value read from the file is used before the file is read again.
REREAD_SECONDS = 1.0

# RFC 6455 "policy violation": the handshake is refused for what it carries.
CLOSE_POLICY = 1008

DETAIL = (
    "Refused: this app does not answer to that host name. Its admin lists "
    "the names it answers to in the web_allowed_hosts setting."
)


def request_host(header: str | None) -> str:
    """A ``Host`` header as the list is compared: lowercased, without its
    port, a bracketed IPv6 literal unwrapped, a trailing dot dropped."""
    host = (header or "").strip().lower()
    if host.startswith("["):
        end = host.find("]")
        return host[1:end] if end > 0 else host
    if host.count(":") == 1:
        host = host.split(":", 1)[0]
    return host.rstrip(".")


def lockout_problem(value: str, host_header: str | None) -> str | None:
    """Why saving *value* as ``web_allowed_hosts`` from a request carrying
    *host_header* would shut that browser out, or None. The panel's save
    refuses on it; the CLI does not ask, since it is the way back. An empty
    list answers to any host, so it is never a problem."""
    entries = host_entries(value)
    host = request_host(host_header)
    if not entries or host in entries:
        return None
    if not host:
        return ("Not saved: this request carries no Host header, so the app "
                "would refuse this browser with web_allowed_hosts set.")
    return (f"Not saved: this browser reaches the app as {host}, which "
            f"web_allowed_hosts does not list, so the app would refuse it. "
            f"Add {host} to the list.")


def _value_on_disk(config) -> str:
    try:
        fresh = config.reloaded()
    except Exception:  # noqa: BLE001 - a half-written file must not refuse everything
        log.warning("config.json could not be read; web_allowed_hosts as the app holds it",
                    exc_info=True)
        fresh = None
    return str((fresh or config).web_allowed_hosts or "")


class AllowedHosts:
    """Refuse a request or socket whose Host is not in ``web_allowed_hosts``."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._config = None
        self._held = ""
        self._read_at = 0.0
        self._hosts: frozenset[str] = frozenset()

    def allowed(self, config) -> frozenset[str]:
        """The listed names, empty when the setting is. *config* is the
        startup instance's: this is a panel setting (plans/62 D4).

        Read again at once when the panel's own copy changed (a save from the
        panel replaces the object, or changes it in place when there was no
        file yet), otherwise at most every `REREAD_SECONDS`."""
        now = time.monotonic()
        held = config.web_allowed_hosts
        if (config is not self._config or held != self._held
                or now - self._read_at >= REREAD_SECONDS):
            self._hosts = frozenset(host_entries(_value_on_disk(config)))
            self._config, self._held, self._read_at = config, held, now
        return self._hosts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            allowed = self.allowed(scope["app"].state.panel.startup.config)
            if allowed:
                header = Headers(scope=scope).get("host")
                if request_host(header) not in allowed:
                    log.warning("Refused %s %s: Host %r is not in web_allowed_hosts",
                                scope.get("method", "WEBSOCKET"), scope["path"], header)
                    await _refuse(scope, receive, send)
                    return
        await self.app(scope, receive, send)


async def _refuse(scope: Scope, receive: Receive, send: Send) -> None:
    if scope["type"] == "websocket":
        # Closing before accept: the browser sees the handshake refused.
        await send({"type": "websocket.close", "code": CLOSE_POLICY})
        return
    # Outside `SecurityHeaders`, so the refusal carries its headers itself.
    await JSONResponse({"detail": DETAIL}, status_code=400, headers=dict(HEADERS))(
        scope, receive, send
    )
