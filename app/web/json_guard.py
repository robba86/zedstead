"""Refuse ``NaN`` / ``Infinity`` in JSON request bodies, for every route.

JSON has no such numbers, but Python's ``json.loads`` accepts the bare tokens,
and both Starlette's ``request.json()`` and FastAPI's model parsing go through
it. Nothing downstream checked: test pass 2 (plans/51 B, N20) wrote
``<pos x="nan" z="nan" />`` into cfgeventspawns.xml and ``NaN,`` into
Expansion's SafeZoneSettings.json, which then answered 500 or 200 depending on
the route. A browser never sends these (``JSON.stringify(NaN)`` is ``null``),
so this only ever refuses a script or an integration, before any route runs.

Pure ASGI rather than ``BaseHTTPMiddleware``: the body is read once, checked,
and replayed to the app unchanged, and nothing else about the request changes.
The substring test keeps the common case to one scan of the bytes; the strict
parse only runs when a token appears, so ``"name": "NaNa"`` still passes.

It runs before any session check, so it reads through `body_limit.BodyLimit`,
which ``server.py`` adds outside it: the buffer below never holds more than
that limit (plans/68 P1, NEW-1).
"""

from __future__ import annotations

import json

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_TOKENS = (b"NaN", b"Infinity")

DETAIL = "NaN and Infinity are not numbers JSON allows; send a finite number."


class _NonFinite(ValueError):
    pass


def _reject(token: str) -> float:
    raise _NonFinite(token)


def has_non_finite(body: bytes) -> bool:
    """True when *body* is JSON that uses NaN/Infinity as a number.

    Malformed JSON is not this module's business: it answers False and the
    route gives its usual error.
    """
    if not any(token in body for token in _TOKENS):
        return False
    try:
        json.loads(body, parse_constant=_reject)
    except _NonFinite:
        return True
    except ValueError:
        return False
    return False


class RejectNonFiniteJson:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in _WRITE_METHODS:
            await self.app(scope, receive, send)
            return
        content_type = dict(scope.get("headers") or []).get(b"content-type", b"")
        # No Content-Type at all is checked too: FastAPI parses such a body
        # as JSON, so leaving the header out walked NaN past this guard.
        if content_type and b"json" not in content_type.lower():
            await self.app(scope, receive, send)
            return

        chunks: list[bytes] = []
        while True:
            message = await receive()
            if message["type"] != "http.request":
                # The client went away mid-body; let the app see that as usual.
                await self.app(scope, _replay([message], receive), send)
                return
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)

        if has_non_finite(body):
            await JSONResponse({"detail": DETAIL}, status_code=400)(scope, receive, send)
            return
        await self.app(
            scope,
            _replay([{"type": "http.request", "body": body, "more_body": False}], receive),
            send,
        )


def _replay(messages: list[Message], receive: Receive) -> Receive:
    pending = list(messages)

    async def _receive() -> Message:
        if pending:
            return pending.pop(0)
        return await receive()

    return _receive
