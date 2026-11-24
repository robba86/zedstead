"""Panel — every instance one ``zsm web`` serves (plans/62, TODO E2).

One ``WebState`` per instance (a *bundle*), all on one ``PanelShared``. Kept
on ``app.state.panel``; ``app.state.ctx`` stays the startup instance's bundle,
which is what a single-instance panel, the tests and every panel-wide setting
(bind host, TLS, the fallback password) read.

The panel builds a bundle for every instance in ``instances.json`` at startup
and follows the file while it runs (``refresh``): an instance added from the
CLI or the Instances page gets a bundle with its services started, and one
removed has its services stopped, its pages' sockets closed with 4404 and its
routes answering 404. The layout from before instances has no registry and
exactly one bundle, ``""``.
"""

from __future__ import annotations

import asyncio
import logging
import time

from fastapi import HTTPException
from starlette.requests import HTTPConnection

from app.shared.config import DayzConfig
from app.web.map_editor_svc import register_channel_hooks, shutdown_map_editor
from app.web.state import PanelShared, WebState
from app.web.tasks import spawn

log = logging.getLogger(__name__)

# How often a running panel looks at instances.json on its own. It also looks
# on GET /api/instances and on a prefix it does not know.
REFRESH_INTERVAL = 30.0
# How often a request for an instance the panel does not serve may make it read
# instances.json again when the file has not changed (`Panel.has`).
MISS_RETRY_INTERVAL = 5.0


def _start_notifier_and_rcon(state: WebState) -> None:
    """Start the headless notifier + a persistent RconManager, and the
    zedstead.com heartbeat.

    Only one process should run these against a given config at a time —
    two duplicate Discord posts and metrics rows.
    """
    from app.web.site_heartbeat import SiteHeartbeat
    from app.web.notifier import WebNotifier

    config = state.config

    # Outside the notifier's `if` on purpose: turning the notifier off must
    # not withdraw an opt-in (plans/57 B4). Sends nothing unless one is on.
    state.site_heartbeat = SiteHeartbeat(config)
    state.on_config_change(state.site_heartbeat.set_config)
    state.site_heartbeat.start()

    if config.web_notifier_enabled:
        state.notifier = WebNotifier(config, state.hub, config_saver=state.mutate_config)
        state.on_config_change(state.notifier.set_config)
        # Doctor checks pause while a job mutates the server and re-run once
        # it finishes (see WebNotifier.busy).
        state.notifier.attach_jobs(state.jobs)
        state.notifier.start()

    state.services_running = True
    if _rcon_target(config) is not None:
        _start_rcon(state)


def _rcon_target(config: DayzConfig) -> tuple[str, int, str] | None:
    """What the persistent RCon connection dials, or ``None`` when the panel
    keeps none: the session tracker is off, or RCon is not configured."""
    from app.rcon.rcon_ops import is_rcon_configured, rcon_host

    if not config.session_tracker_enabled or not is_rcon_configured(config):
        return None
    return rcon_host(config), config.rcon_port, config.rcon_password


def _start_rcon(state: WebState) -> None:
    from app.rcon.rcon_manager import RconManager

    def _on_event(event_type: str, guid: str, name: str) -> None:
        spawn(
            state.hub.broadcast(
                {"type": "rcon_event", "event": event_type, "guid": guid, "name": name, "ts": time.time()}
            ),
            name="rcon-event",
        )
        if state.notifier is not None:
            state.notifier.on_player_event(event_type, guid, name)

    def _on_chat(name: str, guid: str, channel: str, message: str) -> None:
        spawn(
            state.hub.broadcast(
                {
                    "type": "rcon_event",
                    "event": "chat",
                    "guid": guid,
                    "name": name,
                    "channel": channel,
                    "message": message,
                    "ts": time.time(),
                }
            ),
            name="rcon-chat",
        )

    def _on_message(level: str, text: str) -> None:
        # Its own type, so the Players views do not reload on every message.
        spawn(
            state.hub.broadcast({"type": "rcon_message", "level": level, "message": text, "ts": time.time()}),
            name="rcon-message",
        )

    state.rcon_manager = RconManager(
        state.config, on_event=_on_event, on_chat=_on_chat, on_message=_on_message
    )
    state.rcon_target = _rcon_target(state.config)
    if state.notifier is not None:
        state.notifier.set_rcon_manager(state.rcon_manager)
    state.rcon_manager.start()


def _follow_rcon_config(state: WebState, config: DayzConfig) -> None:
    """Config-change listener: keep the persistent connection on the RCon
    settings the panel has now.

    It used to be built once, when `zsm web` started, and only if RCon was
    configured then. Outside Docker the BattlEye editor is what sets zsm's
    RCon password, so a fresh install had no session log, no join/leave posts,
    no chat and no live player list until the panel was restarted, and a
    changed password was never picked up (plans/68 open findings, row 8).

    Same host, port and password: the manager keeps its connection and gets
    the new config for everything it reads per event. Anything else is a
    different connection, or none, and `_sync_rcon` makes the swap.
    """
    if not state.services_running:
        return
    # Against the target recorded at start, not the manager's config: with no
    # config file to re-read, `mutate_config` changes the object it holds.
    if _rcon_target(config) != state.rcon_target:
        spawn(_sync_rcon(state), name="rcon-follow-config")
    elif state.rcon_manager is not None:
        state.rcon_manager.set_config(config)


async def _sync_rcon(state: WebState) -> None:
    """Stop the connection the settings no longer describe, then start the one
    they do.

    One swap at a time, and the old connection first: it writes a disconnect
    for every player it knew when it ends, and the new one writes their
    connects after its first sync. The other way round would leave the session
    log saying everyone had left. It reads `state.config` when it runs, not
    when it was asked, so two changes in a row end on the second.
    """
    def wanted() -> tuple[str, int, str] | None:
        return _rcon_target(state.config) if state.services_running else None

    async with state.rcon_swap_lock:
        if wanted() == state.rcon_target:
            return
        await _stop_rcon(state)
        # Asked again: the stop yields, and the settings can change under it.
        if wanted() is not None:
            _start_rcon(state)


async def _stop_rcon(state: WebState) -> None:
    manager, state.rcon_manager, state.rcon_target = state.rcon_manager, None, None
    if manager is None:
        return
    if state.notifier is not None:
        state.notifier.set_rcon_manager(None)
    await manager.stop()


async def _stop_notifier_and_rcon(state: WebState) -> None:
    # Under the swap lock, and the flag first: a swap already asked for must
    # not start a connection after this has stopped the last one.
    async with state.rcon_swap_lock:
        state.services_running = False
        await _stop_rcon(state)
    if state.notifier is not None:
        await state.notifier.stop()
        state.notifier = None
    if state.site_heartbeat is not None:
        await state.site_heartbeat.stop()
        state.site_heartbeat = None


def build_bundle(config: DayzConfig, shared: PanelShared) -> WebState:
    """One instance's ``WebState``, its channels wired, nothing started."""
    state = WebState(config, shared)
    state.hub.on_first_subscriber("rpt", state.rpt_tailer.start)
    state.hub.on_last_unsubscriber("rpt", state.rpt_tailer.stop)
    state.hub.on_first_subscriber("status", state.status_poller.start)
    state.hub.on_last_unsubscriber("status", state.status_poller.stop)
    register_channel_hooks(state)
    # Here rather than where the services start, so it is registered once per
    # bundle; it does nothing until they run.
    state.on_config_change(lambda config: _follow_rcon_config(state, config))
    return state


async def _stop_bundle(state: WebState) -> None:
    await _stop_notifier_and_rcon(state)
    await shutdown_map_editor(state)


def _registry_stamp() -> int | None:
    from app.shared.paths import instances_file

    try:
        return instances_file().stat().st_mtime_ns
    except OSError:
        return None


class Panel:
    def __init__(self, shared: PanelShared, startup_id: str, bundles: dict[str, WebState]) -> None:
        self.shared = shared
        # "" is the layout from before instances, which has exactly one.
        self.startup_id = startup_id
        self.bundles = bundles
        shared.bundles = bundles
        shared.startup = bundles[startup_id]
        self._stamp: int | None = None
        # instances.json's stamp and the time when a miss last looked again.
        self._missed: tuple[object, float] = (object(), 0.0)
        self._refresh_lock = asyncio.Lock()
        self._refresher: asyncio.Task | None = None
        # Services run between start() and stop(). A bundle added outside
        # them (an app with no lifespan, as most tests build) gets none.
        self._running = False

    @property
    def startup(self) -> WebState:
        return self.bundles[self.startup_id]

    def bundle_for(self, conn: HTTPConnection) -> WebState:
        """The bundle the request's ``/i/<id>/`` prefix names, else the
        startup one. 404 for an instance removed since the middleware let
        the request through."""
        from app.web.instance_prefix import INSTANCE_KEY

        instance_id = conn.scope.get("state", {}).get(INSTANCE_KEY)
        if instance_id is None:
            return self.startup
        bundle = self.bundles.get(instance_id)
        if bundle is None:
            raise HTTPException(status_code=404)
        return bundle

    async def has(self, instance_id: str) -> bool:
        """Whether this panel serves *instance_id*, looking at instances.json
        once more before saying no: it may have been added a moment ago.

        This runs before auth, so a miss looks again only for a name an
        instance could have (`instances._ID_RE`), and only when instances.json
        changed since the last miss looked or `MISS_RETRY_INTERVAL` has passed
        (plans/68 AUTH-18). Without that, while a registered instance's config
        was missing every anonymous request for a made-up id re-read the
        registry and retried that config.
        """
        if instance_id in self.bundles:
            return True
        from app.server import instances

        if not instances._ID_RE.match(instance_id):
            return False
        stamp, now = _registry_stamp(), time.monotonic()
        last_stamp, last_at = self._missed
        if stamp == last_stamp and now - last_at < MISS_RETRY_INTERVAL:
            return False
        self._missed = (stamp, now)
        await self.refresh()
        return instance_id in self.bundles

    def served(self) -> list[dict]:
        """``[{id, label, branch}]`` for every instance a page can switch to,
        in the registry's order. Empty with fewer than two: there is nothing to
        switch between, and a single-instance install answers as before.
        ``branch`` lets the switcher mark an Experimental server (plans/66)."""
        if len(self.bundles) < 2:
            return []
        from app.server import instances
        from app.shared.steam import server_branch

        try:
            registry = instances.load_registry()
        except (OSError, ValueError, KeyError, TypeError):
            registry = None
        order = [entry.id for entry in registry.instances] if registry else []
        ids = [i for i in order if i in self.bundles] + sorted(set(self.bundles) - set(order))
        rows = []
        for instance_id in ids:
            entry = registry.get(instance_id) if registry else None
            rows.append({"id": instance_id, "label": entry.display_name if entry else instance_id,
                         "branch": server_branch(self.bundles[instance_id].config)})
        return rows

    # -- lifecycle ----------------------------------------------------

    async def start(self) -> None:
        """Build every registered instance's bundle and start every bundle's
        services. Called by the app's lifespan."""
        if self.startup_id:
            await self.refresh()
        self._running = True
        for bundle in self.bundles.values():
            _start_notifier_and_rcon(bundle)
        # One Steam measurement per STEAMROOT, not per instance: instances on
        # one login would otherwise queue a SteamCMD run each.
        measured: set[int] = set()
        for bundle in self.bundles.values():
            steam = id(self.shared.steam_for(bundle.config))
            if steam not in measured:
                measured.add(steam)
                bundle.start_steam_check()
        if len(self.bundles) > 1:
            self._warn_about_other_panels()
        if self.startup_id:
            self._refresher = spawn(self._refresh_loop(), name="panel-refresh")

    async def stop(self) -> None:
        self._running = False
        if self._refresher is not None:
            self._refresher.cancel()
            self._refresher = None
        for bundle in list(self.bundles.values()):
            await _stop_bundle(bundle)

    def _warn_about_other_panels(self) -> None:
        from app.server import instance_migration

        if instance_migration.other_panel_running():
            log.warning(
                "Another `zsm web` is running. This app now serves every instance, so a "
                "app started for one of them (`zsm --instance <name> web`) runs that "
                "instance's notifier and Discord posts a second time. Stop it."
            )

    async def _refresh_loop(self) -> None:
        while True:
            await asyncio.sleep(REFRESH_INTERVAL)
            try:
                await self.refresh()
            except Exception:  # noqa: BLE001 - try again next tick
                log.warning("instances refresh failed", exc_info=True)

    async def refresh(self) -> bool:
        """Bring the bundles in line with instances.json, and say whether
        that changed anything.

        Cheap when nothing changed: one ``stat``. The startup instance is
        never dropped, since the listener runs on its settings. An instance
        whose config.json is missing is skipped and tried again next time.
        A change is announced as ``instances_changed``, so a page left open
        follows an instance added or removed from the CLI.
        """
        if not self.startup_id:
            return False
        stamp = _registry_stamp()
        if stamp is not None and stamp == self._stamp:
            return False
        async with self._refresh_lock:
            from app.server import instances

            try:
                registry = await asyncio.to_thread(instances.load_registry)
            except (OSError, ValueError, KeyError, TypeError):
                log.warning("instances.json could not be read; keeping the instances served",
                            exc_info=True)
                return False
            wanted = {instance.id for instance in registry.instances} | {self.startup_id}
            before = set(self.bundles)
            complete = True
            for instance_id in sorted(wanted - set(self.bundles)):
                try:
                    config = await asyncio.to_thread(DayzConfig.for_instance, instance_id)
                except FileNotFoundError:
                    complete = False
                    continue
                except Exception:  # noqa: BLE001 - one broken config must not stop the rest
                    log.warning("instance %r could not be loaded", instance_id, exc_info=True)
                    complete = False
                    continue
                bundle = build_bundle(config, self.shared)
                self.bundles[instance_id] = bundle
                if self._running:
                    _start_notifier_and_rcon(bundle)
                log.info("serving instance %r", instance_id)
            for instance_id in sorted(set(self.bundles) - wanted):
                await self._drop(instance_id)
            self._stamp = stamp if complete else None
            changed = set(self.bundles) != before
        if changed:
            await self.shared.hub.broadcast({"type": "instances_changed"})
        return changed

    async def _drop(self, instance_id: str) -> None:
        bundle = self.bundles.pop(instance_id)
        await self.shared.hub.close_instance(instance_id)
        await _stop_bundle(bundle)
        try:
            self.shared.jobs.on_finished.remove(bundle._recheck_steam_after)
        except ValueError:
            pass
        log.info("no longer serving instance %r", instance_id)
