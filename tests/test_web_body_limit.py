"""A request body is bounded before anything reads it (plans/68 P1, NEW-1).

`json_guard` buffers every JSON write and FastAPI reads a body-model route's
body before the session check, so without `body_limit.BodyLimit` a request
with no session could make the panel hold a body of any size. Most tests here
drive the app as raw ASGI, the way uvicorn does, so they can count how much of
a streamed body was actually asked for: `TestClient` reads a generator body
whole before the app sees it.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app.web import body_limit
from app.web.body_limit import ANONYMOUS_LIMIT, MIB, SIGNED_IN_LIMIT

PASSWORD = "testpass123"
CHUNK = 16 * 1024
PNG = b"\x89PNG\r\n\x1a\n"


@pytest.fixture()
def web_app(sample_config):
    from app.web import server
    from app.web.auth import hash_password

    sample_config.web_password_hash = hash_password(PASSWORD)
    return server.create_app(sample_config)


def _session_cookie(app) -> tuple[bytes, bytes]:
    client = TestClient(app)
    assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
    return (b"cookie", b"zsm_session=" + client.cookies["zsm_session"].encode())


class _Body:
    """An ASGI ``receive`` that hands out *chunks* chunks and counts how many
    were asked for, like a client still sending."""

    def __init__(self, chunks: int, chunk: bytes = b" " * CHUNK) -> None:
        self.chunks, self.chunk, self.read = chunks, chunk, 0

    async def __call__(self) -> dict:
        if self.read < self.chunks:
            self.read += 1
            return {"type": "http.request", "body": self.chunk, "more_body": self.read < self.chunks}
        return {"type": "http.disconnect"}


def _call(app, method: str, path: str, receive, *, headers=(), query: bytes = b""):
    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": method, "scheme": "http", "path": path, "raw_path": path.encode(),
        "query_string": query, "root_path": "",
        "headers": [(b"host", b"127.0.0.1:8433"), *headers],
        "client": ("203.0.113.9", 5555), "server": ("127.0.0.1", 8433),
    }
    asyncio.run(app(scope, receive, send))
    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], (json.loads(body) if body else None)


JSON = (b"content-type", b"application/json")


def test_a_body_without_a_session_stops_being_read_at_the_small_limit(web_app, monkeypatch):
    """No Content-Length (chunked): counted as it arrives, and the sign-in
    route never runs."""
    calls = []
    monkeypatch.setattr(web_app.state.ctx.users, "authenticate", lambda *a: calls.append(a))
    body = _Body(chunks=256)  # 4 MB if it were all read

    status, answer = _call(web_app, "POST", "/api/auth/login", body, headers=[JSON])

    assert status == 413
    assert body.read <= ANONYMOUS_LIMIT // CHUNK + 1
    assert calls == []
    assert "64 KB" in answer["detail"] and "Sign in" in answer["detail"]


def test_a_declared_length_past_the_limit_is_refused_before_a_byte_is_read(web_app):
    cookie = _session_cookie(web_app)
    for headers, size in (([JSON], ANONYMOUS_LIMIT + 1), ([JSON, cookie], SIGNED_IN_LIMIT + 1)):
        body = _Body(chunks=1)
        length = (b"content-length", str(size).encode())
        status, _ = _call(web_app, "PUT", "/api/settings", body, headers=[*headers, length])
        assert status == 413
        assert body.read == 0


def test_a_signed_in_body_past_the_large_limit_is_refused_while_it_streams(web_app):
    body = _Body(chunks=SIGNED_IN_LIMIT // MIB + 8, chunk=b" " * MIB)

    status, answer = _call(
        web_app, "PUT", "/api/settings", body, headers=[JSON, _session_cookie(web_app)]
    )

    assert status == 413
    assert body.read <= SIGNED_IN_LIMIT // MIB + 1
    assert "32 MB" in answer["detail"]


def test_a_large_mission_file_still_saves_through_the_raw_editor(web_app, tmp_mission_dir):
    """The body the signed-in limit is sized by: a whole mission file as a JSON
    string. Six MB of events is twice a heavily modded types.xml."""
    events = tmp_mission_dir / "db" / "events.xml"
    events.write_text("<events>\n</events>\n", encoding="utf-8")
    entry = (
        '\t<event name="StaticHeliCrash{n}">\n\t\t<nominal>3</nominal>\n\t\t<min>1</min>\n'
        '\t\t<max>3</max>\n\t\t<lifetime>2100</lifetime>\n\t\t<restock>0</restock>\n'
        '\t\t<saferadius>1000</saferadius>\n\t\t<distanceradius>1000</distanceradius>\n'
        '\t\t<cleanupradius>1000</cleanupradius>\n'
        '\t\t<flags deletable="1" init_random="0" remove_damaged="0"/>\n'
        '\t\t<position>fixed</position>\n\t\t<limit>child</limit>\n\t\t<active>1</active>\n'
        '\t\t<children>\n\t\t\t<child lootmax="15" lootmin="10" max="3" min="1" '
        'type="Wreck_UH1Y"/>\n\t\t</children>\n\t</event>\n'
    )
    content = "<events>\n" + "".join(entry.format(n=n) for n in range(6 * MIB // len(entry))) + "</events>\n"
    assert len(content) > 6 * MIB - len(entry)

    client = TestClient(web_app)
    assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
    resp = client.put("/api/configs/events/raw", json={"content": content})

    assert resp.status_code == 200, resp.text
    assert events.read_text(encoding="utf-8") == content


def test_signing_in_still_works(web_app):
    client = TestClient(web_app)
    long_password = {"password": "x" * 4096, "username": "admin", "code": "123456"}
    assert client.post("/api/auth/login", json=long_password).status_code == 401
    assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200


def test_a_json_typed_body_cannot_get_past_the_map_image_cap(web_app, monkeypatch):
    """`json_guard` buffers a JSON body before the route's own streamed cap
    sees it, so the cap has to hold in front of `json_guard` too."""
    from app.maps import map_data

    monkeypatch.setattr(map_data, "MAX_OWN_IMAGE_BYTES", 128 * 1024)
    body = _Body(chunks=64)  # 1 MB

    status, _ = _call(
        web_app, "POST", "/api/map-editor/map-image", body,
        headers=[JSON, _session_cookie(web_app)], query=b"terrain=chernarusplus",
    )

    assert status == 413
    assert body.read <= 128 * 1024 // CHUNK + 1
    assert map_data.own_image("chernarusplus") is None


def test_the_upload_routes_keep_their_own_caps(web_app, monkeypatch):
    """Each streamed upload's limit is its route's cap, not the default: an
    image larger than the signed-in default still saves."""
    from app.maps import map_data
    from app.web.uploads import MAX_UPLOAD_BYTES

    assert body_limit.limit_for("/api/map-editor/map-image", True) == map_data.MAX_OWN_IMAGE_BYTES
    assert body_limit.limit_for("/api/configs/serverdz/upload", True) == MAX_UPLOAD_BYTES
    assert body_limit.limit_for("/api/settings", True) == SIGNED_IN_LIMIT
    for path in ("/api/map-editor/map-image", "/api/configs/serverdz/upload", "/api/settings"):
        assert body_limit.limit_for(path, False) == ANONYMOUS_LIMIT

    monkeypatch.setattr(body_limit, "SIGNED_IN_LIMIT", 96 * 1024)
    monkeypatch.setattr(map_data, "MAX_OWN_IMAGE_BYTES", 256 * 1024)
    cookie = _session_cookie(web_app)
    image = _Body(chunks=13, chunk=PNG + b"\x00" * (CHUNK - len(PNG)))  # 208 KB

    status, answer = _call(
        web_app, "POST", "/api/map-editor/map-image", image,
        headers=[(b"content-type", b"application/octet-stream"), cookie],
        query=b"terrain=chernarusplus",
    )
    assert status == 200, answer
    assert answer["size"] == 13 * CHUNK

    status, _ = _call(web_app, "PUT", "/api/settings", _Body(chunks=13), headers=[JSON, cookie])
    assert status == 413


def test_a_websocket_is_passed_through_untouched():
    seen = []

    async def inner(scope, receive, send):
        seen.append(receive)

    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        pass

    scope = {"type": "websocket", "path": "/ws", "headers": [(b"content-length", b"999999999")]}
    asyncio.run(body_limit.BodyLimit(inner)(scope, receive, send))
    assert seen == [receive]
