"""App-level tests for app/web/server.py: health, WS, SPA fallback."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from tests.conftest import site_doc


@pytest.fixture()
def client(sample_config):
    from app.web import server

    app = server.create_app(sample_config)
    return TestClient(app)


def test_health_is_unauthenticated(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "version" in body


def test_spa_fallback_serves_index(client):
    # Checks the SPA mount point, not page text: app/web/static/index.html
    # is the built React app now (replaced the Stage 1 placeholder), and
    # its content hash changes on every rebuild.
    resp = client.get("/")
    assert resp.status_code == 200
    assert '<div id="root">' in resp.text

    resp = client.get("/some/frontend/route")
    assert resp.status_code == 200
    assert '<div id="root">' in resp.text


def test_spa_fallback_404s_unknown_api_paths(client):
    resp = client.get("/api/does-not-exist")
    assert resp.status_code == 404


@pytest.mark.parametrize("name", ["favicon.svg", "zsm-badge.svg"])
def test_root_static_files_are_served_as_files(client, name):
    # Vite copies webui/public/ to the root of app/web/static/, outside the
    # /assets mount, so only the catch-all can serve it. It used to answer
    # index.html instead: the favicon fell back to the browser default and
    # the CSS masks that paint the login badge and the page watermark
    # (`mask: url("/zsm-badge.svg")`) got a document and painted nothing.
    resp = client.get(f"/{name}")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/svg+xml")
    assert resp.text.lstrip().startswith("<svg")


def test_root_static_serving_does_not_escape_the_build_dir(client):
    # `path` is attacker-controlled; a traversal must land on the SPA index,
    # never on a file above app/web/static/.
    for attempt in ("/../CLAUDE.md", "/%2e%2e/CLAUDE.md", "/../../pyproject.toml"):
        resp = client.get(attempt)
        assert resp.status_code == 200
        assert '<div id="root">' in resp.text


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
@pytest.mark.parametrize("logged_in", [False, True])
def test_the_api_docs_are_not_served(sample_config, path, logged_in):
    """Test pass 2, N1: all three answered 200 without a session, handing out
    every path and body schema. An admin session must not bring them back."""
    from app.web import server
    from app.web.auth import hash_password

    sample_config.web_password_hash = hash_password("pw12345678")
    client = TestClient(server.create_app(sample_config))
    if logged_in:
        assert client.post("/api/auth/login", json={"password": "pw12345678"}).status_code == 200
    resp = client.get(path)
    # The SPA catch-all answers any unknown path with index.html; what matters
    # is that neither the schema nor Swagger/ReDoc comes back.
    assert "application/json" not in resp.headers.get("content-type", "")
    assert "swagger" not in resp.text.lower()
    assert "redoc" not in resp.text.lower()
    assert '"paths"' not in resp.text


def test_index_html_is_revalidated_on_every_load(client):
    """Test pass 2, N29: with no Cache-Control a browser reused a stale
    index.html whose hashed bundles were gone after an upgrade."""
    for path in ("/", "/settings", "/favicon.svg"):
        assert client.get(path).headers["cache-control"] == "no-cache", path


def test_no_response_may_be_framed(client):
    """plans/68 P1: a page on another port of this host is same-site, so the
    browser sends the session cookie into a frame it puts the panel in. No
    response may be shown in a frame: the SPA page, a root file, a bundle,
    an API answer, a refusal."""
    from app.web.server import STATIC_DIR

    bundle = next((STATIC_DIR / "assets").glob("*.js")).name
    for path in ("/", "/settings", "/favicon.svg", f"/assets/{bundle}",
                 "/api/health", "/api/status", "/api/does-not-exist"):
        resp = client.get(path)
        assert resp.headers.get("x-frame-options") == "DENY", path
        assert _csp(resp)["frame-ancestors"] == ["'none'"], path

    # A refusal made by another middleware, before any route runs.
    refused = client.post("/api/auth/logout", headers={"Origin": "http://elsewhere:9"})
    assert refused.status_code == 403
    assert refused.headers.get("x-frame-options") == "DENY"


def _csp(resp) -> dict[str, list[str]]:
    """The response's Content-Security-Policy as {directive: [values]}."""
    policy = resp.headers.get("content-security-policy", "")
    directives = {}
    for part in policy.split(";"):
        if part.strip():
            name, *values = part.split()
            directives[name] = values
    return directives


def test_scripts_run_only_from_the_panel_itself(client):
    """plans/68 P1, FE-4: a script runs only when the panel served it as a
    file. Inline scripts and event-handler attributes, `eval`, plugins and a
    `<base>` pointing elsewhere are refused, so text that reaches the page as
    markup cannot run. Styles, images and connections stay unrestricted:
    Leaflet's icons carry inline styles and react-tooltip injects a <style>."""
    from app.web.server import STATIC_DIR

    bundle = next((STATIC_DIR / "assets").glob("*.js")).name
    for path in ("/", "/i//settings", "/map-editor", f"/assets/{bundle}",
                 "/theme-init.js", "/api/health", "/api/status"):
        directives = _csp(client.get(path))
        assert directives == {
            "script-src": ["'self'"],
            "object-src": ["'none'"],
            "base-uri": ["'self'"],
            "frame-ancestors": ["'none'"],
        }, path


def _scripts(html: str) -> list[tuple[dict, str, bool]]:
    """Every <script> in *html*: (attributes, body text, inside <head>)."""
    from html.parser import HTMLParser

    found: list[tuple[dict, str, bool]] = []

    class Parser(HTMLParser):
        in_head = False
        current = None

        def handle_starttag(self, tag, attrs):
            if tag == "head":
                self.in_head = True
            if tag == "script":
                self.current = [dict(attrs), ""]
            for name, _ in attrs:
                assert not name.lower().startswith("on"), f"<{tag} {name}=…>"

        def handle_endtag(self, tag):
            if tag == "head":
                self.in_head = False
            if tag == "script" and self.current is not None:
                found.append((self.current[0], self.current[1], self.in_head))
                self.current = None

        def handle_data(self, data):
            if self.current is not None:
                self.current[1] += data

    Parser().feed(html)
    return found


@pytest.mark.parametrize("which", ["source", "built"])
def test_index_html_has_no_inline_script(which):
    """The script policy refuses inline scripts and on*= attributes, so
    index.html (the Vite source and the committed build) may hold neither.
    The theme script is a file loaded in <head>, blocking, ahead of the
    bundle, which is what keeps the first paint in the right theme."""
    from pathlib import Path

    from app.web.server import STATIC_DIR

    root = Path(__file__).resolve().parent.parent
    path = root / "webui" / "index.html" if which == "source" else STATIC_DIR / "index.html"
    scripts = _scripts(path.read_text(encoding="utf-8"))

    assert scripts, path
    for attrs, body, _ in scripts:
        assert attrs.get("src"), f"inline <script> in {path}"
        assert not body.strip(), f"<script src> with a body in {path}"

    srcs = [attrs["src"] for attrs, _, _ in scripts]
    theme = next(s for s in scripts if s[0]["src"] == "/theme-init.js")
    attrs, _, in_head = theme
    assert in_head
    assert not {"async", "defer"} & set(attrs) and attrs.get("type") != "module"
    assert srcs.index("/theme-init.js") == 0


def test_the_theme_script_is_served_as_javascript(client):
    resp = client.get("/theme-init.js")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/javascript")
    assert "data-theme" in resp.text


def test_no_response_is_sniffed_or_sends_a_referrer_elsewhere(client):
    """plans/68 P1, FE-5 and FE-6: a response is read as the type the panel
    gave it, never as what its bytes look like, and the panel's address goes
    to no other site in a Referer. Every kind of response, refusals too."""
    from app.web.server import STATIC_DIR

    bundle = next((STATIC_DIR / "assets").glob("*.js")).name
    responses = [client.get(path) for path in (
        "/", "/i//settings", "/favicon.svg", "/theme-init.js", f"/assets/{bundle}",
        "/api/health", "/api/status", "/api/does-not-exist",
    )]
    responses.append(client.post("/api/auth/logout", headers={"Origin": "http://elsewhere:9"}))
    assert responses[-1].status_code == 403

    for resp in responses:
        where = f"{resp.request.method} {resp.request.url.path} -> {resp.status_code}"
        assert resp.headers.get("x-content-type-options") == "nosniff", where
        assert resp.headers.get("referrer-policy") == "same-origin", where


def test_a_write_under_the_referrer_policy_still_passes_the_origin_check():
    """`same-origin` makes a browser send ``Origin: null`` on a cross-origin
    write and leaves a same-origin write's Origin alone. The write check must
    refuse the first and accept the second, or the policy breaks every Save."""
    from app.web.same_origin import is_same_origin

    assert is_same_origin({"host": "panel:8433", "origin": "http://panel:8433"})
    assert not is_same_origin({"host": "panel:8433", "origin": "null"})


def test_hashed_assets_are_cached_for_good(client):
    from app.web.server import STATIC_DIR

    name = next((STATIC_DIR / "assets").glob("*.js")).name
    resp = client.get(f"/assets/{name}")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "public, max-age=31536000, immutable"
    # A missing bundle stays a 404: an index.html here would be parsed as JS.
    assert client.get("/assets/index-doesnotexist.js").status_code == 404


def test_websocket_rejected_without_cookie(sample_config):
    from app.web import server

    app = server.create_app(sample_config)
    tclient = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with tclient.websocket_connect("/ws"):
            pass
    assert exc_info.value.code == 4401


def test_websocket_accepted_with_valid_cookie(sample_config):
    from app.web import server

    app = server.create_app(sample_config)
    tclient = TestClient(app)
    token = app.state.ctx.sessions.create("admin")
    tclient.cookies.set("zsm_session", token)

    with tclient.websocket_connect("/ws") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "hello"
        assert "server_time" in msg

        ws.send_json({"action": "ping"})
        pong = ws.receive_json()
        assert pong == {"type": "pong"}


def test_dayzconfig_roundtrips_web_password_hash(sample_config):
    sample_config.web_password_hash = "scrypt$16384$8$1$aa$bb"
    sample_config.save()

    from app.shared.config import DayzConfig

    reloaded = DayzConfig.load()
    assert reloaded.web_password_hash == "scrypt$16384$8$1$aa$bb"


def test_no_cli_output_points_at_the_deleted_menus():
    """The CLI is flag-driven only, so a string telling the user to "go to the
    main menu" names a surface that does not exist. Six of them lingered for
    over a month (F9 of the 2026 Q3 VPS test), which is why this is a sweep and
    not six assertions.

    Genuine uses of the word are fine and must stay — DayZ has a pause menu and
    TraderPlus has a trader menu — so this looks only for the phrasing that
    sends someone *to* zsm's own menu. Comments are skipped: `config.py` still
    explains why a dead `*_enabled` field is kept.
    """
    from pathlib import Path

    app_dir = Path(__file__).resolve().parents[1] / "app"
    phrases = ("the main menu", "from the menu")
    offenders: list[str] = []
    for path in app_dir.rglob("*.py"):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if any(phrase in stripped.lower() for phrase in phrases):
                offenders.append(f"{path.relative_to(app_dir.parent)}:{n}: {stripped}")
    assert not offenders, "CLI text still points at the deleted menus:\n" + "\n".join(offenders)


def test_exactly_one_websocket_route_at_ws(sample_config):
    """The reverse proxy page's recipes proxy exactly one WebSocket path.

    A second one would make the published nginx config silently incomplete —
    the panel would load, and the new channel would simply never connect, which
    is the hardest failure on that page to diagnose. The Map Editor is a
    *channel* multiplexed onto /ws for this reason, not a second path. If this
    fails, either multiplex the new socket onto /ws or add a `location` block
    to the doc. See plans/28 D3.
    """
    from starlette.routing import WebSocketRoute

    from app.web import server

    app = server.create_app(sample_config)
    sockets = [r for r in app.routes if isinstance(r, WebSocketRoute)]

    assert [r.path for r in sockets] == ["/ws"]


def _proxy_recipes() -> dict[str, str]:
    """The Caddyfile and nginx config blocks on the reverse proxy page
    (zedstead-site's src/docs/reverse-proxy.md)."""
    import re

    guide = site_doc("reverse-proxy.md").read_text(encoding="utf-8")
    return dict(re.findall(r"```(caddyfile|nginx)\n(.*?)```", guide, re.S))


def test_the_guide_explains_a_socket_without_x_forwarded_proto():
    """plans/68 B5. A hand-written proxy that marks `/` as https but not
    `/ws` breaks the socket since the https cookie got its `__Host-` name;
    the troubleshooting table names the case and its fix."""
    guide = site_doc("reverse-proxy.md").read_text(encoding="utf-8")
    rows = [line for line in guide.splitlines()
            if line.startswith("|") and "X-Forwarded-Proto" in line and "`/ws`" in line]

    assert rows, "no troubleshooting row for X-Forwarded-Proto missing on /ws"
    assert "__Host-zsm_session" in rows[0]
    assert "proxy_set_header X-Forwarded-Proto $scheme;" in rows[0]


def test_a_socket_that_arrives_as_plain_ws_does_not_read_the_https_cookie(sample_config):
    """What that row claims: the cookie a browser holds over https is read on
    `wss`, and a `/ws` the app sees as plain `ws` (no X-Forwarded-Proto from
    the proxy) looks for the other name and is refused."""
    from app.web import server
    from app.web.auth import SECURE_SESSION_COOKIE_NAME, hash_password

    sample_config.web_password_hash = hash_password("testpass123")
    client = TestClient(server.create_app(sample_config), base_url="https://testserver")
    assert client.post("/api/auth/login", json={"password": "testpass123"}).status_code == 200
    cookie = {"cookie": f"{SECURE_SESSION_COOKIE_NAME}={client.cookies.get(SECURE_SESSION_COOKIE_NAME)}"}

    with client.websocket_connect("wss://testserver/ws", headers=cookie) as ws:
        assert ws.receive_json()["type"] == "hello"
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("ws://testserver/ws", headers=cookie) as ws:
            ws.receive_json()


def test_each_proxy_recipe_offers_hsts_as_an_optional_line():
    """plans/68 NET-11. zsm sends no HSTS (self-signed panels and IP
    addresses), so the recipes carry it, commented out: it pins the name to
    https for its max-age, which is the reader's choice to make."""
    recipes = _proxy_recipes()

    assert '# header Strict-Transport-Security "max-age=31536000"' in recipes["caddyfile"]
    assert '# add_header Strict-Transport-Security "max-age=31536000" always;' in recipes["nginx"]
    for text in recipes.values():
        assert "includeSubDomains;" not in text and 'includeSubDomains"' not in text


# ---------------------------------------------------------------------------
# Dev mode: `zsm web --reload` (server.run_dev_server)
# ---------------------------------------------------------------------------


@pytest.fixture()
def dev_reload_env(monkeypatch):
    """ZSM_DEV_RELOAD unset for the test, and removed again after it.

    run_dev_server sets it in os.environ itself. ``delenv(raising=False)`` on
    an unset variable records nothing to undo, so the "1" outlived the test,
    and every later WebState in the run persisted its sessions to
    auth.dev_session_path(). ``setenv`` records the variable first, and its
    undo removes whatever the test left there.
    """
    monkeypatch.setenv("ZSM_DEV_RELOAD", "")
    monkeypatch.delenv("ZSM_DEV_RELOAD")


def test_run_dev_server_reloads_only_app_dir(monkeypatch, dev_reload_env):
    """reload_dirs must stay pinned to app/.

    The uvicorn default is the CWD, which at the repo root makes watchfiles
    walk webui/node_modules on every scan.
    """
    from pathlib import Path

    from app.web import server

    captured = {}
    monkeypatch.setattr(
        "uvicorn.run", lambda app, **kw: captured.update(app=app, **kw)
    )

    server.run_dev_server(host="127.0.0.1", port=9999)

    assert captured["app"] == "app.web.server:create_dev_app"
    assert captured["factory"] is True
    assert captured["reload"] is True
    assert captured["port"] == 9999
    assert [Path(d).name for d in captured["reload_dirs"]] == ["app"]


def test_run_dev_server_marks_the_process_as_dev(monkeypatch, dev_reload_env):
    """The env var is what keeps the login alive across reloads; the reload
    worker inherits it (see auth.dev_session_path)."""
    import os

    from app.web import server

    monkeypatch.setattr("uvicorn.run", lambda app, **kw: None)

    server.run_dev_server()

    assert os.environ["ZSM_DEV_RELOAD"] == "1"


def test_web_cmd_reload_flag_uses_the_dev_server(monkeypatch, sample_config):
    from unittest.mock import patch

    from click.testing import CliRunner

    from app.commands.web import web_cmd
    from app.web.auth import hash_password

    sample_config.web_password_hash = hash_password("pw")

    with patch("app.shared.config.DayzConfig.load", return_value=sample_config), \
         patch("app.web.server.run_dev_server") as dev, \
         patch("app.web.server.start_server") as normal:
        result = CliRunner().invoke(web_cmd, ["--reload", "--port", "8433"])

    assert result.exit_code == 0, result.output
    dev.assert_called_once_with(
        host="127.0.0.1", port=8433, ssl_certfile=None, ssl_keyfile=None
    )
    normal.assert_not_called()


def test_web_cmd_without_reload_starts_the_normal_server(monkeypatch, sample_config):
    from unittest.mock import AsyncMock, patch

    from click.testing import CliRunner

    from app.commands.web import web_cmd
    from app.web.auth import hash_password

    sample_config.web_password_hash = hash_password("pw")

    with patch("app.shared.config.DayzConfig.load", return_value=sample_config), \
         patch("app.web.server.run_dev_server") as dev, \
         patch("app.web.server.start_server", new_callable=AsyncMock) as normal:
        result = CliRunner().invoke(web_cmd, [])

    assert result.exit_code == 0, result.output
    dev.assert_not_called()
    normal.assert_awaited_once()


def test_web_cmd_does_not_ask_for_a_password_when_accounts_exist(monkeypatch, sample_config):
    """An instance from `zsm instances add` has no password of its own, but
    the accounts in users.json are machine-wide. Asking aborted a headless
    `zsm --instance pvp web` and set a password nothing would read."""
    from unittest.mock import AsyncMock, patch

    from click.testing import CliRunner

    from app.commands.web import web_cmd
    from app.web.users import UserStore

    UserStore().add("admin", "a-long-enough-password", "admin")
    assert not sample_config.web_password_hash

    with patch("app.shared.config.DayzConfig.load", return_value=sample_config), \
         patch("app.web.server.start_server", new_callable=AsyncMock) as normal:
        result = CliRunner().invoke(web_cmd, [], input="")

    assert result.exit_code == 0, result.output
    assert "No web UI password" not in result.output
    normal.assert_awaited_once()
    assert not sample_config.web_password_hash


def test_web_cmd_still_asks_on_a_box_with_no_login_at_all(sample_config):
    from unittest.mock import AsyncMock, patch

    from click.testing import CliRunner

    from app.commands.web import web_cmd

    with patch("app.shared.config.DayzConfig.load", return_value=sample_config), \
         patch("app.web.server.start_server", new_callable=AsyncMock) as normal:
        result = CliRunner().invoke(web_cmd, [], input="pw-one-two\npw-one-two\n")

    assert "No web UI password set yet" in result.output
    assert sample_config.web_password_hash
    normal.assert_awaited_once()


def test_web_cmd_asks_again_for_a_password_that_is_too_short(sample_config):
    """Both `zsm web` prompts: the first password, and `--set-password`."""
    from unittest.mock import AsyncMock, patch

    from click.testing import CliRunner

    from app.commands.web import web_cmd
    from app.web.auth import verify_password

    typed = "short\npw-one-two\npw-one-two\n"
    with patch("app.shared.config.DayzConfig.load", return_value=sample_config), \
         patch("app.web.server.start_server", new_callable=AsyncMock):
        first = CliRunner().invoke(web_cmd, [], input=typed)
    assert "at least 8 characters" in first.output
    assert verify_password("pw-one-two", sample_config.web_password_hash)

    with patch("app.shared.config.DayzConfig.load", return_value=sample_config):
        again = CliRunner().invoke(web_cmd, ["--set-password"], input="tiny\n" + typed)
    assert again.exit_code == 0, again.output
    assert again.output.count("at least 8 characters") == 2
    assert verify_password("pw-one-two", sample_config.web_password_hash)


def _unresolvable(host, port, *args, **kwargs):
    import socket

    raise socket.gaierror(-2, "Name or service not known")


def test_web_cmd_names_the_setting_behind_a_host_it_cannot_resolve(sample_config):
    """plans/51 A (N6): a typo in `web_bind_host` used to end in uvicorn's bare
    "Name or service not known", with nothing pointing at the setting."""
    from unittest.mock import AsyncMock, patch

    from click.testing import CliRunner

    from app.commands.web import web_cmd
    from app.web.auth import hash_password

    sample_config.web_password_hash = hash_password("pw")
    sample_config.web_bind_host = "not a host!"

    with patch("app.shared.config.DayzConfig.load", return_value=sample_config), \
         patch("socket.getaddrinfo", side_effect=_unresolvable), \
         patch("app.web.server.start_server", new_callable=AsyncMock) as normal:
        result = CliRunner().invoke(web_cmd, [])

    assert result.exit_code == 2
    assert "web_bind_host setting" in result.output
    assert "zsm settings set web_bind_host ''" in result.output
    normal.assert_not_called()


def test_web_cmd_blames_the_flag_when_the_host_came_from_it(sample_config):
    from unittest.mock import AsyncMock, patch

    from click.testing import CliRunner

    from app.commands.web import web_cmd

    with patch("app.shared.config.DayzConfig.load", return_value=sample_config), \
         patch("socket.getaddrinfo", side_effect=_unresolvable), \
         patch("app.web.server.start_server", new_callable=AsyncMock) as normal:
        result = CliRunner().invoke(web_cmd, ["--host", "nope!"])

    assert result.exit_code == 2
    assert "(from --host)" in result.output
    normal.assert_not_called()


def test_web_cmd_does_not_resolve_the_auto_default(sample_config):
    """The auto default is always 0.0.0.0 or 127.0.0.1; starting must not wait
    on (or fail with) name resolution when nothing was configured."""
    from unittest.mock import AsyncMock, patch

    from click.testing import CliRunner

    from app.commands.web import web_cmd
    from app.web.auth import hash_password

    sample_config.web_password_hash = hash_password("pw")
    sample_config.web_bind_host = ""

    with patch("app.shared.config.DayzConfig.load", return_value=sample_config), \
         patch("socket.getaddrinfo", side_effect=_unresolvable) as resolve, \
         patch("app.web.server.start_server", new_callable=AsyncMock) as normal:
        result = CliRunner().invoke(web_cmd, [])

    assert result.exit_code == 0, result.output
    resolve.assert_not_called()
    normal.assert_awaited_once()


def test_nan_and_infinity_are_refused_on_every_json_route(sample_config):
    """plans/51 B (N20): Python's json accepts the bare tokens; JSON does not.
    Refused before any route runs, so the unrelated settings route is covered
    the same as the map editor."""
    from fastapi.testclient import TestClient

    from app.web import server
    from app.web.auth import hash_password

    sample_config.web_password_hash = hash_password("pw")
    client = TestClient(server.create_app(sample_config))
    assert client.post("/api/auth/login", json={"password": "pw"}).status_code == 200

    for token in (b"NaN", b"Infinity", b"-Infinity"):
        resp = client.put(
            "/api/settings",
            content=b'{"values": {"metrics_interval": ' + token + b"}}",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400, token
        assert "NaN" in resp.json()["detail"]


def test_nan_is_refused_on_a_json_route_sent_without_a_content_type(sample_config):
    """FastAPI parses a body with no Content-Type as JSON, so a script that
    left the header out got NaN past the guard and into the route."""
    from fastapi.testclient import TestClient

    from app.web import server
    from app.web.auth import hash_password
    from app.web.json_guard import DETAIL

    sample_config.web_password_hash = hash_password("pw")
    client = TestClient(server.create_app(sample_config))
    assert client.post("/api/auth/login", json={"password": "pw"}).status_code == 200

    resp = client.put("/api/settings", content=b'{"values": {"metrics_interval": NaN}}')
    assert resp.status_code == 400
    assert resp.json()["detail"] == DETAIL


def test_the_words_nan_and_infinity_inside_a_string_still_pass():
    from app.web.json_guard import has_non_finite

    assert has_non_finite(b'{"name": "NaNa to Infinity"}') is False
    assert has_non_finite(b'{"a": [1, 2, NaN]}') is True
    assert has_non_finite(b"{not json NaN") is False  # the route reports malformed JSON
