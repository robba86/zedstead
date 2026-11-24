"""Only a page this panel served may change anything (plans/68 P1).

The session cookie is ``SameSite=Lax``, which keeps another *site* from sending
it along with a POST. It does not cover another *origin on the same site*: a
page on another port of this host, or on a sibling subdomain behind the same
domain, is same-site, so the browser sends the cookie with whatever that page
submits. A JSON body would not get through (FastAPI refuses another content
type for a JSON model), but many writes take no body at all, read
``request.json()`` themselves, or take a multipart upload, and an ordinary HTML
form can send each of those.

So a request that is not a read is refused when its ``Origin`` names a host
other than the one it reached. It is the rule the WebSocket handshake has used
since plans/52 B, and both go through `is_same_origin`: a browser always sends
``Origin`` with such a request and a page cannot forge it, while a request with
none comes from something that is not a browser (curl, a script), which holds
no visitor's cookie to misuse. Behind a reverse proxy the browser's ``Host``
has to reach the panel, which https://zedstead.com/docs/reverse-proxy/ already requires for the
socket; ``X-Forwarded-Host`` covers a proxy that rewrites it.
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

log = logging.getLogger(__name__)

# Default-deny like `permissions`: anything that is not a read is checked.
_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

DETAIL = (
    "Refused: this request came from a page on another site or port. If you "
    "reach the app through a reverse proxy, it has to pass the browser's "
    "Host header through (https://zedstead.com/docs/reverse-proxy/)."
)


def is_same_origin(headers) -> bool:
    """Whether the page that sent a request with these *headers* was served
    by this panel. No ``Origin`` at all counts as yes (see the module)."""
    origin = headers.get("origin")
    if not origin:
        return True
    try:
        origin_host = urlsplit(origin).netloc.lower()
    except ValueError:
        return False
    hosts = {
        (headers.get("host") or "").lower(),
        (headers.get("x-forwarded-host") or "").split(",")[0].strip().lower(),
    }
    return bool(origin_host) and origin_host in hosts


class SameOriginWrites:
    """Refuse a cross-origin request that is not a read, before any route."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] not in _READ_METHODS:
            headers = Headers(scope=scope)
            if not is_same_origin(headers):
                log.warning(
                    "Refused a cross-origin %s %s: Origin %r, Host %r",
                    scope["method"], scope["path"], headers.get("origin"), headers.get("host"),
                )
                await JSONResponse({"detail": DETAIL}, status_code=403)(scope, receive, send)
                return
        await self.app(scope, receive, send)
