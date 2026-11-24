"""The headers every response carries (plans/68 P1).

**No frame.** The session cookie is ``SameSite=Lax``. A page on another port
of this host, or on a sibling subdomain, is the same *site*, so the browser
sends the cookie into a frame that page puts the panel in. The panel then
loads signed in, and every click in it comes from the panel's own page, which
`same_origin` cannot tell apart from the admin's own. A page laid over that
frame can steer those clicks onto Delete, Uninstall or Wipe.

Nothing in the panel frames itself (the map editor stopped being an iframe in
its React port), so every response says no frame at all: ``X-Frame-Options``
for older browsers, ``frame-ancestors`` for current ones.

**Only the panel's own scripts** (FE-4). The page shows text it did not write
(log lines, mod files, player names, the Workshop's descriptions), and a slip
that let one of them in as markup would otherwise run as the signed-in admin.
With the policy only a script file the panel served runs: no inline
``<script>``, no ``on*=`` attribute, no ``javascript:`` URL, no ``eval``.
``object-src 'none'`` closes plugins, and ``base-uri 'self'`` stops an
injected ``<base>`` from sending the page's relative script URLs elsewhere.
Nothing the panel ships needs more: index.html loads ``/theme-init.js`` and
the Vite bundle, both files of its own, at ``/`` and under ``/i/<id>/`` alike.

Styles, images and connections are left open on purpose. Leaflet's markers
carry ``style=`` attributes and react-tooltip injects a ``<style>`` at
runtime, so a ``style-src`` would need ``'unsafe-inline'`` and say nothing.
``connect-src 'self'`` covers the panel's own WebSocket only in browsers that
implement CSP level 3. Without a ``default-src`` none of the three is
restricted.

**No sniffing** (FE-5). A file is read as the type the panel says it is, so
text that ends up in a response cannot be run as a script or a page because
it happens to look like one.

**No referrer to other sites** (FE-6). ``same-origin``: the panel's own
requests keep the full ``Referer``, and no other site learns the panel's
address, which is often a bare IP and port, or the paths and searches behind
it. ``strict-origin-when-cross-origin``, every current browser's default,
would still send the origin. Nothing in zsm reads ``Referer``. The policy
leaves ``Origin`` alone on the panel's own writes, which is what `same_origin`
checks; a cross-origin write arrives with ``Origin: null`` and is refused
there as before.

Pure ASGI like `same_origin` and `json_guard`. A header a route set itself is
kept. WebSocket scopes pass through untouched.
"""

from __future__ import annotations

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

POLICY = "; ".join((
    "script-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "frame-ancestors 'none'",
))

HEADERS: tuple[tuple[str, str], ...] = (
    ("X-Frame-Options", "DENY"),
    ("Content-Security-Policy", POLICY),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "same-origin"),
)


class SecurityHeaders:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in HEADERS:
                    headers.setdefault(name, value)
            await send(message)

        await self.app(scope, receive, send_with_headers)
