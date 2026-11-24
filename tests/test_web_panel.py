"""One panel, several instances: what the bundles share (plans/62 step 2)."""

from __future__ import annotations

from dataclasses import replace

from app.web.panel import Panel
from app.web.server import create_app
from app.web.state import PanelShared, WebState


def _two_bundles(sample_config, tmp_path):
    shared = PanelShared()
    other = replace(sample_config, server_dir=str(tmp_path / "other"), instance_id="pvp")
    return WebState(sample_config, shared), WebState(other, shared)


def test_bundles_share_the_browser_side(sample_config, tmp_path):
    main, pvp = _two_bundles(sample_config, tmp_path)

    for name in ("sessions", "login_limiter", "users", "totp_replay", "totp_pending"):
        assert getattr(main, name) is getattr(pvp, name), name
    # One job manager, seen through a view per instance (step 5).
    assert main.jobs.manager is pvp.jobs.manager is main.shared.jobs
    # One hub, seen through a view per instance (step 3).
    assert main.hub.hub is pvp.hub.hub is main.shared.hub
    assert (main.hub.instance, pvp.hub.instance) == (main.config.instance_id, "pvp")


def test_bundles_keep_their_own_server_side(sample_config, tmp_path):
    main, pvp = _two_bundles(sample_config, tmp_path)

    assert main.config is not pvp.config
    assert main.status_poller is not pvp.status_poller
    assert main.rpt_tailer is not pvp.rpt_tailer
    assert main.rcon_history is not pvp.rcon_history
    assert main.map_editor_lock is not pvp.map_editor_lock


def test_a_listener_setting_is_the_panels(sample_config, tmp_path):
    main, pvp = _two_bundles(sample_config, tmp_path)

    main.bind_host = "0.0.0.0"
    main.tls_enabled = True

    assert (pvp.bind_host, pvp.tls_enabled) == ("0.0.0.0", True)


def test_one_steam_login_is_shared_and_another_is_not(sample_config, tmp_path):
    main, pvp = _two_bundles(sample_config, tmp_path)
    main.note_steam_ok(True)

    assert pvp.steam_ok is True  # same SteamCMD, same login

    pvp.config = replace(pvp.config, steamcmd_dir=str(tmp_path / "steamcmd2"))
    assert pvp.steam_ok is None
    assert main.steam_ok is True


def test_a_single_webstate_builds_its_own_panel(sample_config):
    one, two = WebState(sample_config), WebState(sample_config)

    assert one.hub is not two.hub
    assert one.sessions is not two.sessions


def test_create_app_hangs_the_panel_beside_ctx(sample_config):
    app = create_app(sample_config)

    panel = app.state.panel
    assert isinstance(panel, Panel)
    assert panel.startup is app.state.ctx
    assert panel.bundles == {sample_config.instance_id: app.state.ctx}
    assert panel.shared is app.state.ctx.shared


async def test_steam_changed_reaches_the_instances_on_that_login_only(sample_config, tmp_path):
    class _Ws:
        def __init__(self):
            self.sent = []

        async def send_json(self, message):
            self.sent.append(message)

    shared = PanelShared()
    main = WebState(sample_config, shared)
    same = WebState(replace(sample_config, instance_id="pvp"), shared)
    other = WebState(
        replace(sample_config, instance_id="solo", steamcmd_dir=str(tmp_path / "steamcmd2")), shared
    )
    Panel(shared, main.config.instance_id, {b.config.instance_id: b for b in (main, same, other)})
    sockets = {}
    for bundle in (main, same, other):
        sockets[bundle] = _Ws()
        bundle.hub.connect(sockets[bundle])

    main.note_steam_ok(True)
    await main.announce_steam()

    expected = [{"type": "steam_changed", "logged_in": True}]
    assert sockets[main].sent == expected
    assert sockets[same].sent == expected
    assert sockets[other].sent == []


def test_only_the_instance_that_ran_steamcmd_rechecks_the_login(sample_config, monkeypatch):
    from app.web import state as state_mod
    from app.web.jobs import Job

    shared = PanelShared()
    main = WebState(sample_config, shared)
    WebState(replace(sample_config, instance_id="pvp"), shared)
    rechecked = []
    monkeypatch.setattr(state_mod, "spawn", lambda coro, name=None: coro.close())
    monkeypatch.setattr(WebState, "refresh_steam",
                        lambda self: (rechecked.append(self.config.instance_id), _noop())[1])

    job = Job(id="j", name="Update", kind="update", hub=shared.hub, instance="pvp", ran_steamcmd=True)
    for callback in shared.jobs.on_finished:
        callback(job)

    assert rechecked == ["pvp"]
    assert main.config.instance_id not in rechecked


async def _noop():
    return None
