"""How large a request body the panel reads, decided before anything reads it.

Two things read a body before a route's session check runs (plans/68 P1,
NEW-1): `json_guard.RejectNonFiniteJson` buffers every JSON write to scan it,
and FastAPI reads a body-model route's body before `require_session`. uvicorn
sets no limit of its own, so a request without a session could make the panel
hold a body of any size. This middleware sits outside both:

* **Any request may carry up to `ANONYMOUS_LIMIT`** (64 KB). The only route
  that takes a body without a session is the sign-in, whose body (name,
  password, authenticator code) is about 100 bytes; 64 KB leaves room for a
  password of tens of thousands of characters.
* **Past that, the request must carry a session**, checked the way
  `require_session` checks it (the cookie and `SessionStore.validate`, never
  the body), and may then carry up to `SIGNED_IN_LIMIT` (32 MB), or its
  route's own upload cap where `_route_caps` names one.

`SIGNED_IN_LIMIT` is sized by the largest legitimate JSON body: a raw editor
save or an XML Format/Check, which carries a whole mission file as a JSON
string. Bohemia's own types.xml is 880 KB a map, a heavily modded one 1-3 MB,
and JSON escaping adds 8-15 % (measured on types.xml-shaped text). 32 MB is
twice the config upload's cap, so a file the upload accepts can also be saved
through the editor.

A declared ``Content-Length`` over the limit is refused before a byte is read.
A body without one (chunked) is counted as it arrives: the wrapped ``receive``
raises the moment the count passes the limit, so whatever was reading stops,
and whatever response that reader then tried to send is replaced with the 413.
A WebSocket and the lifespan pass through untouched.
"""

from __future__ import annotations

import logging
import re

from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.maps import map_data
from app.web import uploads
from app.web.deps import session_token

log = logging.getLogger(__name__)

KIB = 1024
MIB = 1024 * KIB

ANONYMOUS_LIMIT = 64 * KIB
SIGNED_IN_LIMIT = 32 * MIB

_CONFIG_UPLOAD = re.compile(r"^/api/configs/[^/]+/upload$")
_MAP_IMAGE = re.compile(r"^/api/map-editor/map-image$")


def _route_caps() -> tuple[tuple[re.Pattern[str], int], ...]:
    """The streamed uploads' own caps, read when asked so they stay the routes'
    numbers: the route and this limit can never disagree about a body."""
    return (
        (_CONFIG_UPLOAD, uploads.MAX_UPLOAD_BYTES),
        (_MAP_IMAGE, map_data.MAX_OWN_IMAGE_BYTES),
    )


def limit_for(path: str, signed_in: bool) -> int:
    """The most a request to *path* may carry. *path* is the routed one,
    after `InstancePrefix` has taken off any ``/i/<id>``."""
    if not signed_in:
        return ANONYMOUS_LIMIT
    for pattern, cap in _route_caps():
        if pattern.match(path):
            return max(cap, ANONYMOUS_LIMIT)
    return SIGNED_IN_LIMIT


def has_session(scope: Scope) -> bool:
    """Whether the request carries a session `require_session` would accept.

    The same cookie (`deps.session_token`) and the same
    `SessionStore.validate` the route's gate calls, so it also slides the
    session's TTL the way that gate is about to. A failure answers False:
    the small limit is the safe one.
    """
    try:
        conn = HTTPConnection(scope)
        sessions = conn.app.state.panel.startup.sessions
        return sessions.validate(session_token(conn)) is not None
    except Exception:  # noqa: BLE001 - see the docstring
        log.warning("Could not check the session for a request body", exc_info=True)
        return False


class BodyTooLarge(Exception):
    """Raised from the wrapped ``receive`` once a body passes its limit."""


class _Limit:
    """One request's limit. The session is looked up only once a body is
    larger than `ANONYMOUS_LIMIT`, so the sign-in and every small write never
    touch the session store here."""

    def __init__(self, scope: Scope) -> None:
        self.scope = scope
        self.value = ANONYMOUS_LIMIT
        self.signed_in: bool | None = None

    def allows(self, size: int) -> bool:
        if size <= self.value:
            return True
        if self.signed_in is None:
            self.signed_in = has_session(self.scope)
            self.value = limit_for(self.scope["path"], self.signed_in)
        return size <= self.value

    def response(self) -> JSONResponse:
        size = _size(self.value)
        if self.signed_in:
            detail = f"That request is larger than {size}, the most the app accepts."
        else:
            detail = (
                f"That request is larger than {size}, the most the app accepts "
                "before you sign in. Sign in and try again."
            )
        # The rest of the body was never read, so the connection cannot carry
        # another request.
        return JSONResponse({"detail": detail}, status_code=413, headers={"Connection": "close"})


class BodyLimit:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limit = _Limit(scope)
        declared = _declared_length(scope)
        if declared is not None and not limit.allows(declared):
            await limit.response()(scope, receive, send)
            return

        received = 0
        exceeded = False
        started = False

        async def limited_receive() -> Message:
            nonlocal received, exceeded
            if exceeded:
                raise BodyTooLarge()
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if not limit.allows(received):
                    exceeded = True
                    raise BodyTooLarge()
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal started
            if exceeded and not started:
                # The reader's own answer to the exception (FastAPI's "error
                # parsing the body" 400, say); the 413 below replaces it.
                return
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except Exception:
            if not exceeded or started:
                raise
        if exceeded and not started:
            await limit.response()(scope, receive, send)


def _declared_length(scope: Scope) -> int | None:
    for name, value in scope.get("headers") or ():
        if name == b"content-length":
            return int(value) if value.isdigit() else None
    return None


def _size(n: int) -> str:
    if n % MIB == 0:
        return f"{n // MIB} MB"
    if n % KIB == 0:
        return f"{n // KIB} KB"
    return f"{n} bytes"
