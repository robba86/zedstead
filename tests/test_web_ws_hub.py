"""WsHub channel subscription bookkeeping.

The teardown hooks (``on_last_unsubscriber``) stop real background work — the
RPT tail, the status poll, the map editor's mission-file watcher — so they have
to fire when a client goes away, not only when it politely unsubscribes.
"""

from __future__ import annotations

from app.web.ws import InstanceHub, WsHub


class _FakeWs:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.fail = False

    async def send_json(self, message: dict) -> None:
        if self.fail:
            raise RuntimeError("socket closed")
        self.sent.append(message)


def _hub_with_hooks(channel: str = "rpt"):
    hub = WsHub()
    fired = {"start": 0, "stop": 0}

    async def on_first() -> None:
        fired["start"] += 1

    async def on_last() -> None:
        fired["stop"] += 1

    hub.on_first_subscriber(channel, on_first)
    hub.on_last_unsubscriber(channel, on_last)
    return hub, fired


async def test_subscribe_and_unsubscribe_fire_hooks_once():
    hub, fired = _hub_with_hooks()
    a, b = _FakeWs(), _FakeWs()
    hub.connect(a)
    hub.connect(b)

    await hub.subscribe(a, "rpt")
    await hub.subscribe(b, "rpt")
    assert fired["start"] == 1, "only the first subscriber starts the work"

    await hub.unsubscribe(a, "rpt")
    assert fired["stop"] == 0, "still one subscriber left"
    await hub.unsubscribe(b, "rpt")
    assert fired["stop"] == 1


async def test_disconnect_releases_subscriptions():
    """A closed browser tab never sends `unsubscribe` — dropping the
    connection must still release its channels."""
    hub, fired = _hub_with_hooks()
    ws = _FakeWs()
    hub.connect(ws)
    await hub.subscribe(ws, "rpt")

    await hub.disconnect(ws)

    assert fired["stop"] == 1
    assert not hub._channel_has_subscribers("rpt")


async def test_disconnect_keeps_channel_alive_for_other_clients():
    hub, fired = _hub_with_hooks()
    a, b = _FakeWs(), _FakeWs()
    hub.connect(a)
    hub.connect(b)
    await hub.subscribe(a, "rpt")
    await hub.subscribe(b, "rpt")

    await hub.disconnect(a)
    assert fired["stop"] == 0
    assert hub._channel_has_subscribers("rpt")

    await hub.disconnect(b)
    assert fired["stop"] == 1


async def test_broadcast_prunes_dead_socket_and_fires_hook():
    hub, fired = _hub_with_hooks()
    ws = _FakeWs()
    hub.connect(ws)
    await hub.subscribe(ws, "rpt")

    ws.fail = True
    await hub.broadcast({"type": "ping"}, channel="rpt")

    assert fired["stop"] == 1
    assert not hub._channel_has_subscribers("rpt")


async def test_broadcast_is_channel_gated():
    hub = WsHub()
    subbed, other = _FakeWs(), _FakeWs()
    hub.connect(subbed)
    hub.connect(other)
    await hub.subscribe(subbed, "mapeditor")

    await hub.broadcast({"type": "mapeditor_spawns_changed"}, channel="mapeditor")
    await hub.broadcast({"type": "settings_changed"})

    assert [m["type"] for m in subbed.sent] == [
        "mapeditor_spawns_changed",
        "settings_changed",
    ]
    assert [m["type"] for m in other.sent] == ["settings_changed"]


# ---------------------------------------------------------------------------
# One hub, several instances (plans/62 step 3)
# ---------------------------------------------------------------------------


async def test_an_instances_message_reaches_only_its_own_sockets():
    hub = WsHub()
    main, pvp = InstanceHub(hub, "main"), InstanceHub(hub, "pvp")
    a, b = _FakeWs(), _FakeWs()
    main.connect(a)
    pvp.connect(b)

    await main.broadcast({"type": "settings_changed"})

    assert a.sent == [{"type": "settings_changed"}]
    assert b.sent == []


async def test_an_untagged_message_reaches_every_instance():
    hub = WsHub()
    a, b = _FakeWs(), _FakeWs()
    InstanceHub(hub, "main").connect(a)
    InstanceHub(hub, "pvp").connect(b)

    await hub.broadcast({"type": "job_status"})

    assert a.sent == b.sent == [{"type": "job_status"}]


async def test_a_channel_message_respects_both_channel_and_instance():
    hub = WsHub()
    main, pvp = InstanceHub(hub, "main"), InstanceHub(hub, "pvp")
    a, b, c = _FakeWs(), _FakeWs(), _FakeWs()
    main.connect(a)
    main.connect(b)
    pvp.connect(c)
    await hub.subscribe(a, "status")
    await hub.subscribe(c, "status")

    await main.broadcast({"type": "status"}, channel="status")

    assert a.sent == [{"type": "status"}]
    assert b.sent == [] and c.sent == []


async def test_an_instances_first_subscriber_starts_only_its_own_work():
    hub = WsHub()
    fired: list[str] = []
    for name in ("main", "pvp"):
        view = InstanceHub(hub, name)
        view.on_first_subscriber("status", lambda name=name: fired.append(f"start {name}"))
        view.on_last_unsubscriber("status", lambda name=name: fired.append(f"stop {name}"))
    a, b = _FakeWs(), _FakeWs()
    InstanceHub(hub, "main").connect(a)
    InstanceHub(hub, "pvp").connect(b)

    await hub.subscribe(a, "status")
    assert fired == ["start main"]

    await hub.subscribe(b, "status")
    assert fired == ["start main", "start pvp"]

    await hub.disconnect(a)
    assert fired == ["start main", "start pvp", "stop main"]
