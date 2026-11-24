"""WebState — one instance's container, hung off ``app.state.ctx``.

Holds everything a request handler needs: the current config, the WS hub,
the job manager, and the RPT tailer. Deliberately not a module global — each
``create_app(config)`` call builds its own, so tests can run multiple apps
with different configs concurrently.

What belongs to the panel rather than to one DayZ server (the sockets, the
sessions and accounts, the job slot, the listener, the Steam login) lives on
``PanelShared``, which every instance's ``WebState`` points at (plans/62
§2.1). ``WebState`` re-exposes it under the old attribute names, so a router
still reads ``state.hub`` and ``state.sessions``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Callable

from app.shared import settings_registry
from app.shared.config import DayzConfig
from app.web.auth import LoginRateLimiter, SessionStore, dev_session_path
from app.web.jobs import InstanceJobs, JobManager
from app.web.rpt import RptTailer
from app.web.status_poller import StatusPoller
from app.web.tasks import spawn
from app.web.totp import PendingSecrets as TotpPending
from app.web.totp import Replay as TotpReplay
from app.web.users import UserStore
from app.web.ws import InstanceHub, WsHub

if TYPE_CHECKING:
    from app.rcon.rcon_manager import RconManager
    from app.spawn_editor.state_container import MapEditorState
    from app.web.site_heartbeat import SiteHeartbeat
    from app.web.notifier import WebNotifier

log = logging.getLogger(__name__)


class ConfigLocked(Exception):
    """A web request tried to change a path/program setting while
    ``web_lock_paths`` is on (plans/58). Raised by ``mutate_config`` before it
    saves, so nothing is written. Lives in ``app/web`` on purpose — the CLI is
    never locked and never raises it. The routers map it to 403 with
    ``settings_registry.lock_refusal_message``.
    """

    def __init__(self, fields: list[str], panel_instance: str = "", own_instance: str = "") -> None:
        self.fields = list(fields)
        super().__init__(
            settings_registry.lock_refusal_message(self.fields, panel_instance, own_instance)
        )


# How long a Steam-login answer is reused before it is measured again. Only
# explicit checks reach steam_probe since plans/51 D (startup, after a job that
# ran SteamCMD, "Check again"), so this now only collapses a double click. A
# login made *through* the app does not wait for it — both routes call
# note_steam_ok.
STEAM_PROBE_TTL = 30.0


class SteamState:
    """What is known about one STEAMROOT's login: the last answer, when it
    was measured, and the lock a probe holds. Instances usually share one
    SteamCMD and one login (``instances.MACHINE_FIELDS``), so this is keyed
    by the two, not by instance."""

    def __init__(self) -> None:
        self.ok: bool | None = None
        self.checked_at: float = 0.0
        self.lock = asyncio.Lock()


class PanelShared:
    """The parts of a panel that are not about one DayZ server (plans/62).

    The browser's sockets, sessions, accounts and TOTP state; the one job
    slot (D3: one job at a time, panel-wide); how the listener was started;
    and the Steam login per STEAMROOT. One per panel, however many instances
    it serves.
    """

    def __init__(self) -> None:
        self.hub = WsHub()
        # persist_path is None outside `zsm web --reload` — see
        # app/web/auth.py::dev_session_path.
        self.sessions = SessionStore(persist_path=dev_session_path())
        self.sessions.on_revoke = self.hub.close_sessions_soon
        self.login_limiter = LoginRateLimiter()
        # Accounts (plans/43). The migration from config.web_password_hash
        # runs in create_app, not here, so constructing one stays free of
        # disk writes (tests build a lot of them).
        self.users = UserStore()
        # Which TOTP step each account last spent (TODO B5). In memory, like
        # the sessions above and for the same reason: a restart already ends
        # every session, so a restart forgetting which codes were spent costs
        # nothing that was not already lost.
        self.totp_replay = TotpReplay()
        # Secrets handed out for enrollment but not yet confirmed by a
        # code. Nothing reaches users.json until one does — see
        # `totp.PendingSecrets`.
        self.totp_pending = TotpPending()
        self.jobs = JobManager(self.hub)
        self.jobs.before_exclusive = self._wait_for_steam_probes
        # How `zsm web` was actually started. Set by `start_server`; left
        # empty/False when the app is built without one (tests, an embedding
        # caller), which reads as "nothing to warn about" — a security banner
        # must be raised on evidence, not on a default.
        self.bind_host: str = ""
        self.tls_enabled: bool = False
        self._steam: dict[tuple[str, str], SteamState] = {}
        # Every instance's WebState, by id, and the one `zsm web` started
        # with. Filled by `Panel`; empty/None for a WebState built on its own.
        self.bundles: dict[str, WebState] = {}
        self.startup: WebState | None = None

    def steam_for(self, config: DayzConfig) -> SteamState:
        key = (str(config.steamcmd_path), config.steam_username)
        if key not in self._steam:
            self._steam[key] = SteamState()
        return self._steam[key]

    async def _wait_for_steam_probes(self) -> None:
        """A job waits for any Steam probe already running (see
        ``JobManager.before_exclusive``), whichever instance started it."""
        for steam in list(self._steam.values()):
            async with steam.lock:
                pass


class _OnShared:
    """A ``WebState`` attribute that lives on its ``PanelShared``."""

    def __set_name__(self, owner, name: str) -> None:
        self.name = name

    def __get__(self, obj, owner=None):
        if obj is None:
            return self
        return getattr(obj.shared, self.name)

    def __set__(self, obj, value) -> None:
        setattr(obj.shared, self.name, value)


class _OnSteam:
    """A ``WebState`` attribute that lives on its STEAMROOT's ``SteamState``."""

    def __init__(self, field: str) -> None:
        self.field = field

    def __get__(self, obj, owner=None):
        if obj is None:
            return self
        return getattr(obj.shared.steam_for(obj.config), self.field)

    def __set__(self, obj, value) -> None:
        setattr(obj.shared.steam_for(obj.config), self.field, value)


class WebState:
    sessions = _OnShared()
    login_limiter = _OnShared()
    users = _OnShared()
    totp_replay = _OnShared()
    totp_pending = _OnShared()
    bind_host = _OnShared()
    tls_enabled = _OnShared()
    # Last known Steam-login state, served while a job is running (see
    # steam_probe below) so a read-only poll never spawns a second SteamCMD
    # next to a download.
    steam_ok = _OnSteam("ok")
    steam_checked_at = _OnSteam("checked_at")
    _steam_lock = _OnSteam("lock")

    def __init__(self, config: DayzConfig, shared: PanelShared | None = None) -> None:
        self.config = config
        # None builds a panel of one, which is what a test or a single
        # instance gets; create_app passes the panel's own.
        self.shared = shared or PanelShared()
        # This instance's view of the panel's hub: what it broadcasts reaches
        # only its own pages (plans/62 §2.3).
        self.hub = InstanceHub(self.shared.hub, config.instance_id)
        # Likewise for jobs: one slot for the panel, each job labelled with
        # the instance that started it (D3).
        self.jobs = InstanceJobs(self.shared.jobs, config.instance_id)
        self.rpt_tailer = RptTailer(config, self.hub)
        self.status_poller = StatusPoller(config, self.hub)
        self._config_lock = asyncio.Lock()
        self._config_listeners: list[Callable[[DayzConfig], None]] = []
        self.on_config_change(self.rpt_tailer.set_config)
        self.on_config_change(self.status_poller.set_config)
        # Set by create_app's lifespan when web_notifier_enabled / RCON is
        # configured; None otherwise. Exposed here (rather than kept as
        # lifespan-local closures) so tests/routers can introspect them.
        self.notifier: "WebNotifier | None" = None
        self.rcon_manager: "RconManager | None" = None
        # panel.py replaces rcon_manager when the RCon settings change: one
        # swap at a time, and none while the services are not running.
        # rcon_target is what the manager was started on (host, port,
        # password), None with no manager.
        self.rcon_target: tuple[str, int, str] | None = None
        self.rcon_swap_lock = asyncio.Lock()
        self.services_running = False
        # Always set by the lifespan, notifier or not (plans/57 B4).
        self.site_heartbeat: "SiteHeartbeat | None" = None
        # Map editor state for the /api/map-editor routes (web/map_editor_svc.py).
        # Lazily built and then sticky; its file watcher only runs while the
        # `mapeditor` WS channel has subscribers.
        self.map_editor: "MapEditorState | None" = None
        self.map_editor_lock = asyncio.Lock()
        self.jobs.on_finished.append(self._recheck_steam_after)
        # What the RCon console has sent this run, newest last (TODO D1).
        # In memory and bounded: a console's value is the last few commands
        # and their replies, and the replies are meaningless once the server
        # has restarted anyway. Not persisted, and deliberately not merged
        # into the access log, which answers a different question.
        self.rcon_history: list[dict] = []

    @property
    def is_startup(self) -> bool:
        """Whether this is the instance the panel started with, whose panel
        settings (bind host, TLS, the cookie) are the ones in effect."""
        return self.shared.startup is None or self.shared.startup is self

    def panel_locked(self) -> bool:
        """Whether the startup instance's ``web_lock_paths`` is on, read from
        its file so a CLI change counts at once. The lock is panel-wide
        (plans/62 D5): it would mean little if a second instance, unlocked,
        let a web admin repoint *its* ``steamcmd_dir`` at any program, which
        this process would then run."""
        if self.is_startup:
            return False
        startup = self.shared.startup.config
        return bool((startup.reloaded() or startup).web_lock_paths)

    def paths_locked(self) -> bool:
        """The path lock as it applies to this instance: its own or the
        panel's. Its own is read from its file, as ``panel_locked`` reads the
        panel's, because the lock is set with the CLI: the copy this process
        holds says nothing of a ``zsm settings set web_lock_paths true`` made
        after the panel started, and a check that read it let a web request
        add an instance or restore a backup over the lock until a restart
        (user test S8, 2026 Q4)."""
        own = self.config.reloaded() or self.config
        return bool(own.web_lock_paths) or self.panel_locked()

    def note_steam_ok(self, value: bool) -> None:
        """Record a login result the app already knows, without probing.

        The two login routes end holding the answer, so they set it here
        instead of leaving the next ``steam_probe`` to spend a SteamCMD run
        rediscovering it — and, more importantly, so the cache can never serve
        a stale "not logged in" to the card the user just logged in from.
        """
        self.steam_ok = value
        self.steam_checked_at = time.monotonic()

    def steam_known(self) -> bool | None:
        """The last Steam-login answer, never a probe. ``None`` = not measured.

        What every read-only route serves (plans/51 D). ``/api/status`` used to
        probe instead: a Dashboard reload more than 30 s after the last one ran
        a real ``steamcmd +login`` inside the request, 10-20 s, while the page
        had already filled its status strip from the WebSocket (test pass 2,
        N3). The answer is kept fresh by `refresh_steam`, not by page loads.

        With no username or no SteamCMD binary the answer is ``False`` without
        measuring, as ``check_steam_login`` itself answers. Otherwise a fresh
        install would stay "unknown" for good (the startup check skips it) and
        never be offered the login card.
        """
        if not (self.config.steam_username and self.config.steamcmd_path.is_file()):
            return False
        return self.steam_ok

    async def refresh_steam(self) -> bool:
        """Measure now (skipping the TTL). A measurement is announced to every
        open page by `steam_probe`. Run at startup, after a job that ran
        SteamCMD, and by "Check again"."""
        return await self.steam_probe(force=True)

    async def announce_steam(self) -> None:
        """``steam_changed`` to every open page: the Dashboard's login card,
        the Mods/Update banner and the Guide all follow it. Sent to every
        instance on this STEAMROOT, since the answer is theirs too, and to
        no other: an instance with its own SteamCMD has its own answer."""
        mine = self.shared.steam_for(self.config)
        peers = [b for b in self.shared.bundles.values() if b.shared.steam_for(b.config) is mine]
        if self not in peers:
            peers.append(self)
        for bundle in peers:
            try:
                await bundle.hub.broadcast({"type": "steam_changed", "logged_in": self.steam_ok})
            except Exception:  # noqa: BLE001 - a dead socket must not lose the answer
                log.debug("steam_changed broadcast failed", exc_info=True)

    def start_steam_check(self) -> None:
        """One background measurement when ``zsm web`` starts. Not awaited:
        startup must not wait 10-20 s, and the Dashboard shows nothing for an
        unknown state rather than a login card."""
        if self.config.steam_username and self.config.steamcmd_path.is_file():
            spawn(self.refresh_steam(), name="steam-startup-check")

    def _recheck_steam_after(self, job) -> None:
        """A job that ran SteamCMD may have changed what the login is.

        Its status can't say: an update pipeline "succeeds" on a dry run or
        with its Steam steps unticked, a mod install succeeds from cache, and
        a failed download can be the workshop manifest rather than the login
        (root CLAUDE.md). So it is measured, once, after the lock is free. The
        login job itself is skipped: its route already records what it
        learned (`note_steam_ok`).
        """
        # Every instance's bundle hears every job; the one that ran it checks.
        if job.instance is not None and job.instance != self.config.instance_id:
            return
        if getattr(job, "ran_steamcmd", False) and job.kind != "steam_login":
            spawn(self.refresh_steam(), name="steam-recheck")

    async def steam_probe(self, *, force: bool = False) -> bool:
        """Cached, job-aware, one-at-a-time ``check_steam_login``.

        Two SteamCMD processes sharing one STEAMROOT corrupt the cached login
        token and the workshop manifest, so while an exclusive job is running
        (it may well *be* a SteamCMD download) this returns the last known
        answer instead of probing. Unknown + busy reads as logged-in: a job
        that got this far had credentials, and a spurious "log in again" card
        is exactly the confusing symptom this avoids.

        ``jobs.busy`` does not cover two *read-only* callers racing each
        other, and that is easy to arrange: two browser tabs hydrating at
        once, or ``/api/status`` and ``/api/setup-state`` both refreshing on a
        single ``service_changed`` broadcast. Each would spawn its own
        SteamCMD — the very damage the busy guard exists to prevent. So the
        probe is serialised on a lock, and an answer younger than
        ``STEAM_PROBE_TTL`` is reused rather than re-measured: without the TTL
        the lock would merely queue the second caller behind another 10-15s
        SteamCMD run.
        """
        from app.shared.steam import check_steam_login
        from app.shared.steamcmd_lock import SteamcmdBusy

        if self.jobs.busy:
            return True if self.steam_ok is None else self.steam_ok
        async with self._steam_lock:
            # Re-checked under the lock: whoever held it may have just
            # answered the question, or started a job while we waited.
            if self.jobs.busy:
                return True if self.steam_ok is None else self.steam_ok
            if (
                not force
                and self.steam_ok is not None
                and time.monotonic() - self.steam_checked_at < STEAM_PROBE_TTL
            ):
                return self.steam_ok
            try:
                # wait=0: another process's SteamCMD (a scheduled update,
                # another instance's panel) is the same hazard as our own job.
                ok = await asyncio.to_thread(check_steam_login, self.config, wait=0)
            except SteamcmdBusy:
                return True if self.steam_ok is None else self.steam_ok
            self.note_steam_ok(ok)
            await self.announce_steam()
            return self.steam_ok

    def on_config_change(self, callback: Callable[[DayzConfig], None]) -> None:
        """Register *callback* to receive the new config after every mutation.

        Long-lived services (the RPT tailer, status poller, notifier) capture a
        DayzConfig at construction; without this they keep serving the startup
        snapshot after a settings change — and the notifier once *saved* that
        snapshot, rolling config.json back (plans/11). Any new object that
        caches a config for the life of the app must register here. Callbacks
        run synchronously inside mutate_config and must not block or raise.
        """
        self._config_listeners.append(callback)

    async def mutate_config(self, fn: Callable[[DayzConfig], object]) -> DayzConfig:
        """Apply *fn* to a freshly-loaded config, save, and broadcast the change.

        Re-reads from disk under the lock (falling back to the in-memory
        config if no file exists yet, e.g. in tests) so a concurrent CLI
        edit isn't clobbered. It re-reads the file this config came from,
        never whatever `load()` resolves now: the panel keeps the instance it
        started with (plans/44). This is the only path allowed to call
        ``config.save()`` — routers must never call it directly.

        *fn* changes the config in place and writes nothing itself. A change
        that also writes files (the BattlEye editor writes beserver_x64.cfg
        and serverDZ.cfg) **returns** those writes as a function instead of
        doing them. It runs after the path-lock check and before the save, so
        a change the lock refuses writes nothing: no file, no version
        snapshot, no config.json. Any other return value (a bool, a task) is
        ignored, as before.
        """
        async with self._config_lock:
            cfg = self.config.reloaded() or self.config
            # Path lock (plans/58): snapshot the protected values of the config
            # as loaded, apply fn, and refuse if any changed while the lock was
            # already on. Turning the lock *on* is allowed (it was off before fn
            # ran), so a web request can enable it but never lift it or slip a
            # path change through. Nothing is written or saved on refusal.
            # Whose lock it is, decided before fn: a request that turns this
            # instance's own lock off reads False afterwards, and the refusal
            # then blamed the panel's instance.
            own_locked = bool(cfg.web_lock_paths)
            was_locked = own_locked or self.panel_locked()
            before = settings_registry.locked_values(cfg) if was_locked else None
            writes = fn(cfg)
            if before is not None:
                after = settings_registry.locked_values(cfg)
                # Turning this instance's own lock on is allowed under the
                # panel's, as it is with no lock at all.
                changed = [
                    k for k in set(before) | set(after)
                    if before.get(k) != after.get(k)
                    and not (k == "web_lock_paths" and after.get(k))
                ]
                if changed:
                    if own_locked:
                        raise ConfigLocked(changed, own_instance=cfg.instance_id)
                    raise ConfigLocked(changed, self.shared.startup.config.instance_id)
            if callable(writes):
                writes()
            cfg.save()
            self.config = cfg
        return await self._announce_config()

    async def adopt_config_file(self) -> DayzConfig:
        """Take on this config's file as it is now, without writing it.

        For a route that wrote config.json wholesale (the unlocked full-backup
        restore): without this the panel and every listener kept the config
        from before the write until some later save re-read the file.
        """
        async with self._config_lock:
            self.config = self.config.reloaded() or self.config
        return await self._announce_config()

    async def _announce_config(self) -> DayzConfig:
        # Outside the lock: a listener that ever awaited it would deadlock.
        for listener in self._config_listeners:
            try:
                listener(self.config)
            except Exception:
                log.warning("config-change listener failed", exc_info=True)
        await self.hub.broadcast({"type": "settings_changed"})
        return self.config
