"""``/i/<id>/…``: which instance a request is about (plans/62 §2.2, D1).

A pure ASGI middleware. A path under ``/i/<id>/`` is routed as the rest of it
(``/i/pvp/api/mods`` as ``/api/mods``, ``/i/pvp/ws`` as ``/ws``, ``/i/pvp/mods``
as the SPA page), with the id left in ``scope["state"]["instance"]`` for
``Panel.bundle_for``. Everything downstream, the role gate and the access log
included, sees today's paths.

No prefix is the startup instance (D8), so a single-instance install never
sees one and old bookmarks keep working. An id that is not an instance this
panel serves answers 404 here, before auth, with a body that names nothing:
a visitor without a session must not learn which instances exist.
"""

from __future__ import annotations

import re

from starlette.responses import JSONResponse

from app.web.ws import CLOSE_INSTANCE_GONE

# The id half is checked against the panel's bundles, which only ever hold
# ids `instances._ID_RE` accepted; anything else after /i/ is a 404 too.
_PREFIX = re.compile(r"^/i/([^/]*)(/.*)?$")
INSTANCE_KEY = "instance"


class InstancePrefix:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        match = _PREFIX.match(scope["path"])
        if match is None:
            await self.app(scope, receive, send)
            return

        instance_id, rest = match.group(1), match.group(2) or "/"
        panel = scope["app"].state.panel
        if not await panel.has(instance_id):
            await _refuse(scope, receive, send)
            return

        scope = dict(scope)
        scope["path"] = rest
        prefix = f"/i/{instance_id}".encode()
        raw = scope.get("raw_path")
        if raw is not None and raw.startswith(prefix):
            scope["raw_path"] = raw[len(prefix):] or b"/"
        scope["state"] = {**scope.get("state", {}), INSTANCE_KEY: instance_id}
        await self.app(scope, receive, send)


async def _refuse(scope, receive, send) -> None:
    if scope["type"] == "websocket":
        # Closing before accept: the browser sees the handshake refused.
        await send({"type": "websocket.close", "code": CLOSE_INSTANCE_GONE})
        return
    await JSONResponse({"detail": "Not Found"}, status_code=404)(scope, receive, send)
