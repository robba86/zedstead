"""FastAPI app factory + uvicorn runner for ``zsm web``.

Follows the same create_app/start_server shape as the rest of the app
(REST + single ``/ws`` endpoint + static/SPA serving), with one deliberate
divergence: ``create_app(config)`` builds a **fresh** ``FastAPI()`` every
call — no module-level ``app`` or globals. All state hangs off
``app.state.ctx: WebState`` so tests can spin up multiple independent apps
(different ``sample_config``s) in the same process.
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.shared.config import DayzConfig
from app.web.allowed_hosts import AllowedHosts
from app.web.body_limit import BodyLimit
from app.web.deps import session_token
from app.web.instance_prefix import InstancePrefix
from app.web.json_guard import RejectNonFiniteJson
from app.web.panel import (  # noqa: F401 - re-exported; tests call them as server.*
    Panel,
    _start_notifier_and_rcon,
    _stop_notifier_and_rcon,
    build_bundle,
)
from app.web.routers import ALL_ROUTERS
from app.web.same_origin import SameOriginWrites, is_same_origin
from app.web.security_headers import SecurityHeaders
from app.web.state import PanelShared, WebState
from app.web.ws import CLOSE_INSTANCE_GONE

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

# index.html names the current build's bundles, so a browser must check it on
# every load. Without an explicit header it caches heuristically (about 10% of
# the file's age), and after an upgrade a cached index asks for an
# /assets/index-<oldhash>.js that no longer exists: a blank panel until a hard
# reload (test pass 2, N29).
_REVALIDATE = {"Cache-Control": "no-cache"}


class _HashedAssets(StaticFiles):
    """``/assets``: Vite names these files by content hash, so they never change."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


def create_app(config: DayzConfig) -> FastAPI:
    """Build and wire a FastAPI app for *config*. Business endpoints are
    added stage by stage via ``ALL_ROUTERS``; this stage only wires auth,
    the WebSocket hub, health, and static/SPA serving.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await panel.start()
        try:
            yield
        finally:
            await panel.stop()

    # No /docs, /redoc or /openapi.json (test pass 2, N1): they handed the
    # whole API map to anyone who could reach the port, without a session, and
    # /docs loaded its code from a third-party CDN. Nothing in the UI uses them.
    app = FastAPI(
        title="zsm", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )
    # Who a saved version names (app/shared/actor.py): innermost, so the
    # holder it opens is the route's own context.
    from app.shared import actor

    actor.set_process_default("Zedstead")
    app.add_middleware(actor.ActorScope)
    app.add_middleware(RejectNonFiniteJson)
    # A write from a page this panel did not serve (another port or subdomain
    # is same-site, so SameSite=lax still sends the cookie). See same_origin.py.
    app.add_middleware(SameOriginWrites)
    # How large a body may be, 64 KB without a session (body_limit.py,
    # NEW-1). Outside everything that reads a body, RejectNonFiniteJson and
    # FastAPI's body parsing included, so neither can hold more than this.
    app.add_middleware(BodyLimit)
    # The headers every response carries: no frame, only the panel's own
    # scripts, no sniffing, no referrer to other sites (security_headers.py).
    # Outside the three above, so their refusals carry the headers too.
    app.add_middleware(SecurityHeaders)
    # Runs before the four above (only AllowedHosts runs earlier), so
    # everything after it sees today's paths.
    app.add_middleware(InstancePrefix)
    # Added last, so it runs first: a Host not in web_allowed_hosts is refused
    # before an instance id is looked up (allowed_hosts.py, NET-9). Empty, the
    # default, lets everything through.
    app.add_middleware(AllowedHosts)
    shared = PanelShared()
    state = build_bundle(config, shared)
    # The other instances' bundles are built when the lifespan starts.
    panel = Panel(shared, config.instance_id, {config.instance_id: state})
    app.state.panel = panel
    app.state.ctx = state
    # Turn the single pre-accounts password into the first admin, once
    # (plans/43 step 1). A no-op when users.json exists or there is no
    # password yet, and nothing reads the result until step 2 — so this
    # changes who can log in for nobody.
    state.users.migrate_from_config(config)

    for router in ALL_ROUTERS:
        app.include_router(router)

    @app.get("/api/health")
    async def health() -> dict:
        return {"ok": True, "version": __version__}

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        """The one /ws socket: a hello, then subscribe, unsubscribe and ping messages."""
        # The page's instance (its /i/<id>/ prefix), or the startup one.
        try:
            state: WebState = ws.app.state.panel.bundle_for(ws)
        except HTTPException:
            await ws.close(code=CLOSE_INSTANCE_GONE)
            return
        token = session_token(ws)
        # `validate` returns the session's username since plans/43 step 2.
        # `is None` rather than falsiness to match `require_session`: the two
        # agree today only because `create` refuses a blank name, and a socket
        # that disagreed with REST about being logged in is the shape of "the
        # panel loads, logs you in, and never updates".
        #
        # **No role gate here, deliberately** (plans/43 §5). This channel is a
        # read — status, job logs, editor broadcasts — and a read is viewer
        # unless it is named, so every session may listen. The cost is stated
        # rather than half-fixed: a viewer receives the log output of a job
        # they could not have started. Filtering it properly means per-
        # subscriber channels in `WsHub`, which is its own plan. The socket
        # accepts no write actions — `_handle_ws_message` handles subscribe,
        # unsubscribe and ping, and nothing else — so there is no way to act
        # through it that the REST gate would have refused.
        #
        # The session is checked here and again whenever it is revoked or
        # expires: `SessionStore.on_revoke` closes the sockets that rode in on
        # it (`WsHub.close_sessions`). Before that, a logged-out or deleted
        # account kept every broadcast until its tab closed (test pass 2, N27).
        if state.sessions.validate(token) is None:
            await ws.close(code=4401)
            return
        if not _same_origin(ws):
            await ws.close(code=4403)
            return

        await ws.accept()
        state.hub.connect(ws, token)
        try:
            await state.hub.send_to(ws, {"type": "hello", "server_time": time.time()})
            while True:
                msg = await ws.receive_json()
                # A list or a number has no .get(): it raised and closed the
                # socket. Such a message is ignored instead.
                if not isinstance(msg, dict):
                    continue
                await _handle_ws_message(state, ws, msg)
        except WebSocketDisconnect:
            pass
        except Exception:
            log.debug("WebSocket connection error", exc_info=True)
        finally:
            await state.hub.disconnect(ws)

    assets_dir = STATIC_DIR / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", _HashedAssets(directory=str(assets_dir)), name="assets")

    static_root = STATIC_DIR.resolve()

    @app.get("/{path:path}")
    async def spa_fallback(path: str):
        if path.startswith("api/"):
            raise HTTPException(status_code=404)
        # Everything Vite copies from webui/public/ (favicon.svg,
        # zsm-badge.svg) lands at the *root* of STATIC_DIR, not under
        # /assets — so this catch-all is the only route that can reach it.
        # Falling straight through to index.html answered them 200 text/html:
        # the favicon quietly reverted to the browser default, and the CSS
        # `mask: url("/zsm-badge.svg")` behind the login badge and the page
        # watermark resolved to a document and painted nothing. Resolve and
        # containment-check first — `path` is attacker-controlled and `..`
        # would otherwise escape the build output.
        if path:
            candidate = (STATIC_DIR / path).resolve()
            if candidate.is_relative_to(static_root) and candidate.is_file():
                return FileResponse(str(candidate), headers=_REVALIDATE)
        index = STATIC_DIR / "index.html"
        if not index.is_file():
            raise HTTPException(status_code=404)
        return FileResponse(str(index), headers=_REVALIDATE)

    return app


def _same_origin(ws: WebSocket) -> bool:
    """Whether the page that opened *ws* was served by this panel.

    A browser always sends ``Origin`` on a WebSocket handshake and a page
    cannot forge it, so a mismatch means another site (or another port on
    this host, which SameSite=lax still counts as same-site) is using the
    visitor's cookie. The rule is shared with every REST write
    (`app.web.same_origin`), so the socket and the API cannot disagree about
    which pages are the panel's.
    """
    return is_same_origin(ws.headers)


async def _handle_ws_message(state: WebState, ws: WebSocket, msg: dict[str, Any]) -> None:
    action = msg.get("action")
    if action == "subscribe":
        channel = msg.get("channel")
        if channel:
            await state.hub.subscribe(ws, channel)
    elif action == "unsubscribe":
        channel = msg.get("channel")
        if channel:
            await state.hub.unsubscribe(ws, channel)
    elif action == "ping":
        await state.hub.send_to(ws, {"type": "pong"})


async def start_server(
    config: DayzConfig,
    host: str = "127.0.0.1",
    port: int = 8433,
    ssl_certfile: str | None = None,
    ssl_keyfile: str | None = None,
) -> bool:
    """Start the FastAPI server (called from the ``zsm web`` CLI command).

    Returns True when the panel stopped to restart itself (after
    ``POST /api/self-update``); the caller then runs itself again.

    ``ssl_*`` are resolved by the CLI (flags > config fields — see
    ``app/commands/web.py::_resolve_tls``); passing them here is what serves
    the panel over HTTPS with no proxy involved, which in turn makes
    ``request.url.scheme`` ``https`` at the source.
    """
    import uvicorn

    app = create_app(config)
    # Record how we were actually started, so the panel can say when it is
    # reachable from off this machine with nothing encrypting it. Only this
    # function knows: create_app takes neither the bind address nor the certs.
    app.state.ctx.bind_host = host
    app.state.ctx.tls_enabled = bool(ssl_certfile and ssl_keyfile)
    # Honour X-Forwarded-Proto/-For from a reverse proxy so request.url.scheme
    # is https behind a TLS terminator — that is what drives the session
    # cookie's Secure attribute and name (deps.cookie_is_secure).
    #
    # None is NOT the same as "127.0.0.1" here: passing a value explicitly
    # overrides uvicorn's own FORWARDED_ALLOW_IPS environment handling
    # (uvicorn/config.py falls back to that env var, default 127.0.0.1, only
    # when the argument is None). The reverse proxy docs page documents that env var
    # as the off-host-proxy lever, so defaulting it here would break it.
    # web_trusted_proxies is how you override deliberately; never "*" on an
    # untrusted network.
    forwarded = config.web_trusted_proxies.strip() or None
    uvi_config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="warning",
        ssl_certfile=ssl_certfile,
        ssl_keyfile=ssl_keyfile,
        proxy_headers=True,
        forwarded_allow_ips=forwarded,
    )
    server = uvicorn.Server(uvi_config)
    restart = {"requested": False}

    def request_restart() -> None:
        restart["requested"] = True
        server.should_exit = True

    app.state.request_restart = request_restart
    await server.serve()
    return restart["requested"]


def create_dev_app() -> FastAPI:
    """App factory for uvicorn's reloader — see ``run_dev_server``.

    The reloader re-imports this module in a fresh worker process on every
    change, so the config has to be re-read here rather than captured from
    the parent (which is also what makes a config.json edit take effect on
    reload).
    """
    return create_app(DayzConfig.load())


def run_dev_server(
    host: str = "127.0.0.1",
    port: int = 8433,
    ssl_certfile: str | None = None,
    ssl_keyfile: str | None = None,
) -> None:
    """Run the server under uvicorn's auto-reloader (``zsm web --reload``).

    Blocking and **not** async: uvicorn's reloader is a process supervisor
    that spawns the worker itself, so it must own the event loop — hence
    ``uvicorn.run`` with an import string instead of ``start_server``'s
    already-built app object.

    ``reload_dirs`` is pinned to ``app/`` on purpose. The default is the
    CWD, which at the repo root means watchfiles walks ``webui/node_modules``
    (tens of thousands of files) on every scan. Only ``*.py`` is watched
    (uvicorn's default ``reload_includes``), so ``npm run build`` writing
    ``app/web/static/`` does not bounce the backend.
    """
    import uvicorn

    from app.web.auth import DEV_RELOAD_ENV

    # Read by dev_session_path() in this process and inherited by the reload
    # worker: keeps the browser logged in across restarts.
    os.environ[DEV_RELOAD_ENV] = "1"

    uvicorn.run(
        "app.web.server:create_dev_app",
        factory=True,
        host=host,
        port=port,
        reload=True,
        reload_dirs=[str(Path(__file__).resolve().parents[1])],
        log_level="warning",
        # Dev mode gets TLS too, so `--reload --self-signed` does not quietly
        # serve HTTP while the banner says https.
        ssl_certfile=ssl_certfile,
        ssl_keyfile=ssl_keyfile,
    )
