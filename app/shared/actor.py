"""Who is acting: the name a saved version records (the Timeline's "who").

A web request carries the signed-in user, a CLI run the system user, and the
web process's own background work (notifiers, the map editor's watcher) the
name ``Zedstead``. ``config_versions.snapshot`` reads :func:`current` deep in any
save path, so no save function has to pass a user along.

The web sets it in two steps, because the user is known only inside
``deps.require_session``, a sync dependency that FastAPI runs in a worker
thread with a *copy* of the request's context: ``ActorScope`` (a middleware)
puts a fresh holder in the request's context, and ``require_session`` writes
the name into that holder. The copy shares the holder, so the route, its
``asyncio.to_thread`` calls and any job it starts all see the name.
"""

from __future__ import annotations

import getpass
import uuid
from contextvars import ContextVar, Token

_holder: ContextVar[dict | None] = ContextVar("zsm_actor", default=None)
_process_default: str | None = None
# Outside a request, the whole process is one operation (a CLI run).
_process_operation = uuid.uuid4().hex


def begin() -> Token:
    """Open a request's holder; pass the token to :func:`end`."""
    return _holder.set({})


def end(token: Token) -> None:
    """Close the holder :func:`begin` opened."""
    _holder.reset(token)


def set_name(name: str) -> None:
    """Name the user of the current request (inside a :func:`begin`)."""
    holder = _holder.get()
    if holder is not None:
        holder["name"] = name


def set_process_default(name: str) -> None:
    """The name for work outside a request, in this process (``zsm web``)."""
    global _process_default
    _process_default = name


def operation_id() -> str:
    """One id per request, or per process outside one: what counts as "the
    same operation" for config_versions._note_unchanged, so a save nested in
    a preset never takes over the preset's own cause."""
    holder = _holder.get()
    if holder is None:
        return _process_operation
    return holder.setdefault("op", uuid.uuid4().hex)


def current() -> str:
    """Who acts now: the request's user, the process default, or the CLI's
    system user."""
    holder = _holder.get()
    if holder and holder.get("name"):
        return holder["name"]
    if _process_default:
        return _process_default
    try:
        return f"CLI ({getpass.getuser()})"
    except Exception:  # noqa: BLE001 — no user name in this environment
        return "CLI"


class ActorScope:
    """ASGI middleware: a fresh actor holder per HTTP request (see the module
    docstring). Pure ASGI, so the route runs in this same context."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        token = begin()
        try:
            await self.app(scope, receive, send)
        finally:
            end(token)
