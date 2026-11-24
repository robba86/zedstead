"""WsHub — single-endpoint WebSocket connection registry + channel subscriptions.

Mirrors ``app.spawn_editor.state.Broadcaster`` (connect/disconnect/broadcast,
prune dead sockets on send failure) but adds per-connection channel
subscriptions: ``status``, ``rpt`` and ``mapeditor`` (``KNOWN_CHANNELS``) are
subscription-gated, everything else broadcasts to every connected client. Each
socket remembers the session it authenticated with, so revoking the session
closes it (``close_sessions``).

One panel can serve several instances (plans/62). Each socket is bound to the
instance whose page opened it, and an instance's messages reach only its own
sockets: a bundle broadcasts through an ``InstanceHub`` view, which tags every
message without its caller knowing. Untagged messages (a job's log, the Steam
login, a socket's hello) go to every socket. A channel's start/stop hooks are
per instance too, so pvp's status page starts pvp's poller and nobody else's.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from fastapi import WebSocket

from app.web.tasks import spawn

log = logging.getLogger(__name__)

_HookFn = Callable[[], "Awaitable[None] | None"]
# (channel, instance); the instance is None for a hub nobody tags.
_HookKey = tuple[str, "str | None"]

# Every channel a client may subscribe to. `subscribe` ignores any other name,
# because a subscription lasts as long as the socket and nothing else bounded
# them (test pass 2, N27). A new channel has to be added here.
KNOWN_CHANNELS = frozenset({"status", "rpt", "mapeditor"})

# Close code for "your session is gone". The UI goes to /login on it.
CLOSE_SESSION_ENDED = 4401
# Close code for "the instance this page is about is gone" (removed while the
# panel runs). The UI goes to the panel's root on it (plans/62 §2.5).
CLOSE_INSTANCE_GONE = 4404


class WsHub:
    def __init__(self) -> None:
        self._connections: dict[WebSocket, set[str]] = {}
        # The session each socket authenticated with, so revoking a session
        # can close the sockets that rode in on it.
        self._tokens: dict[WebSocket, str | None] = {}
        # The instance each socket's page is about ("" is the layout from
        # before instances; None is a socket nobody bound).
        self._instances: dict[WebSocket, str | None] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._on_first_subscriber: dict[_HookKey, _HookFn] = {}
        self._on_last_unsubscriber: dict[_HookKey, _HookFn] = {}

    # -- lifecycle ----------------------------------------------------

    def connect(self, ws: WebSocket, token: str | None = None, instance: str | None = None) -> None:
        self._connections[ws] = set()
        self._tokens[ws] = token
        self._instances[ws] = instance
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            pass

    async def disconnect(self, ws: WebSocket) -> None:
        """Drop *ws* and release the channels it was the last subscriber to.

        A browser tab closing never sends ``unsubscribe``, so without this a
        channel's teardown hook would never fire and its background task
        (RPT tail, status poll, the map editor's mission-file watcher) would
        run forever with nobody listening.
        """
        self._tokens.pop(ws, None)
        instance = self._instances.pop(ws, None)
        subs = self._connections.pop(ws, None)
        if not subs:
            return
        for channel in subs:
            if not self._channel_has_subscribers(channel, instance):
                await self._fire(self._on_last_unsubscriber.get((channel, instance)))

    async def close_sessions(self, tokens: set[str]) -> None:
        """Close every socket that authenticated with one of *tokens*."""
        doomed = [ws for ws, token in list(self._tokens.items()) if token in tokens]
        for ws in doomed:
            try:
                await ws.close(code=CLOSE_SESSION_ENDED)
            except Exception:
                pass  # already closing; the disconnect below still runs
            await self.disconnect(ws)

    async def close_instance(self, instance: str) -> None:
        """Close every socket bound to *instance* and forget its hooks.

        The sockets go first, so their last-unsubscriber hooks still stop
        the instance's tail and poller on the way out.
        """
        doomed = [ws for ws, bound in list(self._instances.items()) if bound == instance]
        for ws in doomed:
            try:
                await ws.close(code=CLOSE_INSTANCE_GONE)
            except Exception:
                pass  # already closing; the disconnect below still runs
            await self.disconnect(ws)
        for hooks in (self._on_first_subscriber, self._on_last_unsubscriber):
            for key in [key for key in hooks if key[1] == instance]:
                del hooks[key]

    def close_sessions_soon(self, tokens: set[str]) -> None:
        """``close_sessions`` from anywhere, including a worker thread.

        ``SessionStore`` calls this, and some of its callers (the sync
        ``require_session`` dependency) run in FastAPI's thread pool.
        """
        if not tokens or not any(t in tokens for t in list(self._tokens.values())):
            return
        loop = self._loop
        if loop is None:
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            spawn(self.close_sessions(tokens), name="ws-close-sessions")
            return
        try:
            loop.call_soon_threadsafe(
                lambda: spawn(self.close_sessions(tokens), name="ws-close-sessions")
            )
        except RuntimeError:
            pass  # the loop is closed, and its sockets with it

    # -- hooks (used by RptTailer to start/stop the tail task) --------

    def on_first_subscriber(self, channel: str, callback: _HookFn, instance: str | None = None) -> None:
        self._on_first_subscriber[(channel, instance)] = callback

    def on_last_unsubscriber(self, channel: str, callback: _HookFn, instance: str | None = None) -> None:
        self._on_last_unsubscriber[(channel, instance)] = callback

    # -- subscriptions --------------------------------------------------

    async def subscribe(self, ws: WebSocket, channel: str) -> None:
        subs = self._connections.get(ws)
        if subs is None or channel in subs or channel not in KNOWN_CHANNELS:
            return
        instance = self._instances.get(ws)
        had_subscribers = self._channel_has_subscribers(channel, instance)
        subs.add(channel)
        if not had_subscribers:
            await self._fire(self._on_first_subscriber.get((channel, instance)))

    async def unsubscribe(self, ws: WebSocket, channel: str) -> None:
        subs = self._connections.get(ws)
        if subs is None or channel not in subs:
            return
        subs.discard(channel)
        instance = self._instances.get(ws)
        if not self._channel_has_subscribers(channel, instance):
            await self._fire(self._on_last_unsubscriber.get((channel, instance)))

    def _channel_has_subscribers(self, channel: str, instance: str | None = None) -> bool:
        return any(
            channel in subs and self._instances.get(ws) == instance
            for ws, subs in self._connections.items()
        )

    @staticmethod
    async def _fire(callback: _HookFn | None) -> None:
        if callback is None:
            return
        result = callback()
        if hasattr(result, "__await__"):
            await result

    # -- sending ----------------------------------------------------------

    async def send_to(self, ws: WebSocket, message: dict[str, Any]) -> None:
        try:
            await ws.send_json(message)
        except Exception:
            await self.disconnect(ws)

    async def broadcast(
        self, message: dict[str, Any], channel: str | None = None, instance: str | None = None
    ) -> None:
        """Send *message* to all clients, or only ``channel`` subscribers;
        with *instance*, only to the sockets bound to that instance."""
        dead: list[WebSocket] = []
        # Snapshot: a client connecting/disconnecting while a send awaits
        # would otherwise mutate the dict mid-iteration (RuntimeError).
        for ws, subs in list(self._connections.items()):
            if channel is not None and channel not in subs:
                continue
            if instance is not None and self._instances.get(ws) != instance:
                continue
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.disconnect(ws)


class InstanceHub:
    """One instance's view of the panel's ``WsHub`` (plans/62 §2.3).

    A bundle's ``hub`` is one of these, so the tailer, the poller, the map
    editor's ``Broadcaster``, the notifier and every router reach only that
    instance's sockets without passing an instance anywhere. A socket
    connected through it is bound to the instance, and hooks registered
    through it fire for that instance's subscribers only. Everything else
    is the shared hub's.
    """

    def __init__(self, hub: WsHub, instance: str) -> None:
        self.hub = hub
        self.instance = instance

    def connect(self, ws: WebSocket, token: str | None = None) -> None:
        self.hub.connect(ws, token, self.instance)

    def on_first_subscriber(self, channel: str, callback: _HookFn) -> None:
        self.hub.on_first_subscriber(channel, callback, self.instance)

    def on_last_unsubscriber(self, channel: str, callback: _HookFn) -> None:
        self.hub.on_last_unsubscriber(channel, callback, self.instance)

    async def broadcast(self, message: dict[str, Any], channel: str | None = None) -> None:
        await self.hub.broadcast(message, channel, instance=self.instance)

    def __getattr__(self, name: str):
        return getattr(self.hub, name)
