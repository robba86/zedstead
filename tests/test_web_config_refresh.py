"""Config changes reach the web app's long-lived services (plans/11).

`WebState.mutate_config` swaps `state.config` for a freshly loaded object. The
RPT tailer, status poller and notifier captured the startup object, so they
kept serving it — and the notifier *saved* it whenever Discord handed back a
new status-message id, which rolled config.json back to the startup snapshot
and discarded every setting changed in the web UI since.
"""

from __future__ import annotations

import asyncio

import pytest

from app.rcon.rcon_manager import RconManager
from app.rcon.rcon_scheduler import RestartWarningState
from app.shared.config import DayzConfig
from app.web import panel, server, tasks
from app.web.notifier import WebNotifier
from app.web.state import WebState


class RecordingHub:
    def __init__(self):
        self.messages: list[dict] = []

    async def broadcast(self, message, channel=None):  # noqa: ARG002
        self.messages.append(message)


async def test_mutate_config_refreshes_rpt_tailer(sample_config):
    state = WebState(sample_config)
    state.rpt_tailer._path = sample_config.server_dir_path / "old.RPT"
    state.rpt_tailer._pos = 123

    await state.mutate_config(lambda cfg: setattr(cfg, "profiles_path", "other"))

    assert state.rpt_tailer.config.profiles_path == "other"
    # A different profiles dir means a different log; the old offset is void.
    assert state.rpt_tailer._path is None
    assert state.rpt_tailer._pos == 0


async def test_mutate_config_refreshes_status_poller(sample_config):
    state = WebState(sample_config)
    await state.mutate_config(lambda cfg: setattr(cfg, "metrics_interval", 77))
    assert state.status_poller.config is state.config
    assert state.status_poller.config.metrics_interval == 77


async def test_a_failing_listener_does_not_break_the_mutation(sample_config):
    state = WebState(sample_config)
    seen: list[DayzConfig] = []

    def broken(_cfg):
        raise RuntimeError("listener bug")

    state.on_config_change(broken)
    state.on_config_change(seen.append)

    result = await state.mutate_config(lambda cfg: setattr(cfg, "backup_max_count", 9))

    assert result.backup_max_count == 9
    assert seen == [state.config]


async def test_notifier_does_not_clobber_config_on_status_persist(sample_config):
    """The headline regression: persisting the Discord message id must not
    write back the notifier's own (older) config object."""
    import app.shared.config as config_mod

    sample_config.web_notifier_enabled = True
    sample_config.discord_webhook_url = "https://discord.invalid/webhook"
    sample_config.save()
    app = server.create_app(sample_config)
    state: WebState = app.state.ctx
    old_config = state.config

    await state.mutate_config(lambda cfg: setattr(cfg, "backup_max_count", 42))
    wn = WebNotifier(old_config, RecordingHub(), config_saver=state.mutate_config)
    await wn._persist_status_message_id("msg-123")

    on_disk = DayzConfig.load()
    assert config_mod.CONFIG_FILE.exists()
    assert on_disk.backup_max_count == 42  # not rolled back
    assert on_disk.discord_status_message_id == "msg-123"  # and still persisted


async def test_notifier_persist_without_saver_stays_in_memory(sample_config):
    import app.shared.config as config_mod

    wn = WebNotifier(sample_config, RecordingHub())
    await wn._persist_status_message_id("x")

    assert sample_config.discord_status_message_id == "x"
    assert not config_mod.CONFIG_FILE.exists()


async def test_the_app_wires_the_notifier_to_config_changes(sample_config, monkeypatch):
    # Never the real loops: they would run doctor checks against this machine.
    monkeypatch.setattr(WebNotifier, "start", lambda self: None)
    sample_config.web_notifier_enabled = True
    sample_config.session_tracker_enabled = False
    app = server.create_app(sample_config)
    state: WebState = app.state.ctx
    server._start_notifier_and_rcon(state)
    try:
        await state.mutate_config(lambda cfg: setattr(cfg, "toast_player_connect", False))
        assert state.notifier._config is state.config
        assert state.notifier._config_saver == state.mutate_config
    finally:
        await server._stop_notifier_and_rcon(state)


# ── the persistent RCon connection follows the config ───────────────────
#
# It was built once, at startup, and only if RCon was configured then. Outside
# Docker the BattlEye editor is what gives zsm its RCon password, so a fresh
# install had no session log, join/leave posts, chat or live player list until
# `zsm web` was restarted (plans/68 open findings, row 8).


@pytest.fixture
def rcon_calls(monkeypatch):
    """`RconManager.start`/`stop` recorded instead of run, as (what, the
    password that manager was started with): no socket, no session-log
    cleanup."""
    calls: list[tuple[str, str]] = []
    started_with: dict[int, str] = {}

    def _start(self):
        started_with[id(self)] = self._config.rcon_password
        calls.append(("start", started_with[id(self)]))

    async def _stop(self):
        calls.append(("stop", started_with[id(self)]))

    monkeypatch.setattr(RconManager, "start", _start)
    monkeypatch.setattr(RconManager, "stop", _stop)
    return calls


@pytest.fixture
async def started(sample_config, monkeypatch, rcon_calls):
    """Starts the services of a panel on *sample_config* as it is when called."""
    # Never the real loops: they would run doctor checks against this machine.
    monkeypatch.setattr(WebNotifier, "start", lambda self: None)
    states: list[WebState] = []

    def _start() -> WebState:
        state = server.create_app(sample_config).state.ctx
        server._start_notifier_and_rcon(state)
        states.append(state)
        return state

    yield _start
    for state in states:
        await server._stop_notifier_and_rcon(state)


async def _settled() -> None:
    """Wait for what `spawn` started; the connection swap is one of them."""
    while tasks._BACKGROUND_TASKS:
        await asyncio.gather(*list(tasks._BACKGROUND_TASKS), return_exceptions=True)
        await asyncio.sleep(0)


def _set_rcon(password: str):
    def change(cfg: DayzConfig) -> None:
        cfg.rcon_password = password
        cfg.rcon_port = 2305

    return change


async def test_rcon_starts_once_it_gets_configured(started, rcon_calls):
    state = started()
    assert state.rcon_manager is None  # nothing to dial yet

    await state.mutate_config(_set_rcon("secret"))
    await _settled()

    assert rcon_calls == [("start", "secret")]
    assert state.rcon_manager._config is state.config
    # The restart warnings go through the notifier's reference.
    assert state.notifier._rcon_manager is state.rcon_manager


async def test_a_changed_rcon_password_replaces_the_connection(sample_config, started, rcon_calls):
    _set_rcon("old")(sample_config)
    state = started()
    first = state.rcon_manager

    await state.mutate_config(_set_rcon("new"))
    await _settled()

    # The old connection ends before the new one starts: it closes its
    # players' sessions on the way out, and the new one reopens them.
    assert rcon_calls == [("start", "old"), ("stop", "old"), ("start", "new")]
    assert state.rcon_manager is not first
    assert state.notifier._rcon_manager is state.rcon_manager


async def test_a_changed_password_is_noticed_with_a_config_file_too(sample_config, started, rcon_calls):
    """With a file to re-read, `mutate_config` hands out a new config object
    and the manager keeps the old one; without, it changes the one both hold.
    The swap has to happen either way."""
    _set_rcon("old")(sample_config)
    sample_config.save()
    state = started()

    await state.mutate_config(_set_rcon("new"))
    await _settled()

    assert rcon_calls == [("start", "old"), ("stop", "old"), ("start", "new")]
    assert state.rcon_manager._config is state.config


async def test_a_new_rcon_host_or_port_replaces_the_connection_too(sample_config, started, rcon_calls):
    _set_rcon("secret")(sample_config)
    state = started()

    await state.mutate_config(lambda cfg: setattr(cfg, "rcon_host", "10.0.0.5"))
    await _settled()
    await state.mutate_config(lambda cfg: setattr(cfg, "rcon_port", 2310))
    await _settled()

    assert [what for what, _ in rcon_calls] == ["start", "stop", "start", "stop", "start"]
    assert state.rcon_target == ("10.0.0.5", 2310, "secret")
    assert state.rcon_manager._config is state.config


async def test_an_unrelated_setting_keeps_the_connection_and_reaches_it(sample_config, started, rcon_calls):
    _set_rcon("secret")(sample_config)
    state = started()
    manager = state.rcon_manager

    await state.mutate_config(lambda cfg: setattr(cfg, "chat_log_enabled", False))
    await _settled()

    assert state.rcon_manager is manager
    assert rcon_calls == [("start", "secret")]
    # The chat log switch is read per chat line, from the manager's config.
    assert manager._config is state.config
    assert manager._config.chat_log_enabled is False


async def test_turning_the_session_tracker_off_ends_the_connection(sample_config, started, rcon_calls):
    _set_rcon("secret")(sample_config)
    state = started()

    await state.mutate_config(lambda cfg: setattr(cfg, "session_tracker_enabled", False))
    await _settled()

    assert rcon_calls == [("start", "secret"), ("stop", "secret")]
    assert state.rcon_manager is None
    assert state.notifier._rcon_manager is None


async def test_two_changes_at_once_make_one_connection(started, rcon_calls):
    state = started()
    _set_rcon("secret")(state.config)

    # Both swaps are asked for before either has run.
    panel._follow_rcon_config(state, state.config)
    panel._follow_rcon_config(state, state.config)
    await _settled()

    assert rcon_calls == [("start", "secret")]


async def test_a_change_made_while_the_old_connection_stops_is_the_one_that_counts(
    sample_config, started, rcon_calls, monkeypatch
):
    """Stopping a connection takes a moment. RCon switched off in that moment
    must not be answered with the connection the swap set out to start."""
    _set_rcon("old")(sample_config)
    state = started()
    stopping, release = asyncio.Event(), asyncio.Event()

    async def _slow_stop(self):
        rcon_calls.append(("stop", "old"))
        stopping.set()
        await release.wait()

    monkeypatch.setattr(RconManager, "stop", _slow_stop)

    _set_rcon("new")(state.config)
    panel._follow_rcon_config(state, state.config)
    await stopping.wait()
    state.config.session_tracker_enabled = False
    panel._follow_rcon_config(state, state.config)
    release.set()
    await _settled()

    assert rcon_calls == [("start", "old"), ("stop", "old")]
    assert state.rcon_manager is None


async def test_a_swap_asked_for_before_the_services_stop_starts_nothing(started, rcon_calls):
    """Removing an instance stops its services. A change made in the same
    moment must not leave a connection running for an instance that is gone."""
    state = started()
    _set_rcon("secret")(state.config)

    panel._follow_rcon_config(state, state.config)
    await server._stop_notifier_and_rcon(state)
    await _settled()

    assert rcon_calls == []
    assert state.rcon_manager is None


async def test_a_config_change_before_the_services_start_starts_nothing(sample_config, rcon_calls):
    state = server.create_app(sample_config).state.ctx

    await state.mutate_config(_set_rcon("secret"))
    await _settled()

    assert rcon_calls == []
    assert state.rcon_manager is None


def test_new_restart_warning_thresholds_reach_a_running_loop(sample_config):
    wn = WebNotifier(sample_config, RecordingHub())
    wn._rcon_warning_state = RestartWarningState(thresholds=[15, 5], template="old {minutes}")
    wn._rcon_warning_state._sent.add(15)

    changed = DayzConfig(**{**sample_config.__dict__})
    changed.restart_warning_minutes = [30, 10, 1]
    changed.restart_warning_template = "new {minutes}"
    wn.set_config(changed)

    assert wn._rcon_warning_state.thresholds == [30, 10, 1]
    assert wn._rcon_warning_state.template == "new {minutes}"
    assert 15 in wn._rcon_warning_state._sent  # this cycle's history is kept


async def test_mutate_config_writes_the_instance_the_panel_started_with(sample_config, monkeypatch):
    """plans/44 step 4: re-reading through `DayzConfig.load()` would follow the
    registry's default and save one instance's edit into another's file."""
    from app.server.instances import ServerInstance, add_instance, load_registry, save_registry

    for name in ("main", "pvp"):
        cfg = DayzConfig(server_name=name)
        cfg.instance_id = name
        cfg.save()
        save_registry(add_instance(load_registry(), ServerInstance(name)))
    monkeypatch.delenv("ZSM_INSTANCE", raising=False)
    assert DayzConfig.load().instance_id == "main"  # the default

    state = WebState(DayzConfig.for_instance("pvp"))
    await state.mutate_config(lambda cfg: setattr(cfg, "metrics_interval", 77))

    assert DayzConfig.for_instance("pvp").metrics_interval == 77
    assert DayzConfig.for_instance("main").metrics_interval != 77
    assert state.config.instance_id == "pvp"
