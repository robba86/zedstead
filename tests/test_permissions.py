"""Tests for app/web/permissions.py — the route classifier (plans/43 step 3).

Nothing is enforced yet. What this file protects is the classification itself,
and the two ways it can be quietly wrong:

1. **The enumeration missing routes.** If `enumerate_routes` under-reports, a
   test that "every route is classified" passes while checking almost nothing.
   This is not hypothetical — see `test_the_enumeration_finds_the_whole_app`.
2. **A rule whose pattern matches no real route.** For a moderator write that
   costs a moderator their job (the route silently becomes admin). For a
   `NON_VIEWER_READS` entry it is worse: the route silently stays **viewer**,
   which is the direction this whole design exists to avoid.
"""

from __future__ import annotations

import pytest

from app.web import permissions
from app.web.permissions import (
    MODERATOR_WRITES,
    NON_VIEWER_READS,
    SESSION_WRITES,
    SIDE_EFFECTING_READS,
    audit_app,
    enumerate_routes,
    required_role,
)


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    """A real app, because the point is to enumerate the real route table."""
    import app.shared.config as cfgmod
    import app.shared.paths as paths
    from app.shared.config import DayzConfig
    from app.web import server

    tmp = tmp_path_factory.mktemp("perms")
    config_dir = tmp / "cfg"
    config_dir.mkdir()
    paths.CONFIG_DIR = config_dir
    paths.CONFIG_FILE = config_dir / "config.json"
    cfgmod.CONFIG_DIR = config_dir
    cfgmod.CONFIG_FILE = config_dir / "config.json"
    return server.create_app(DayzConfig(server_dir=str(tmp / "srv")))


@pytest.fixture(scope="module")
def routes(app):
    return enumerate_routes(app)


# ── the enumeration ─────────────────────────────────────────────────────


def test_the_enumeration_finds_the_whole_app(routes):
    """The guard everything else depends on.

    This FastAPI keeps an included router as an opaque `_IncludedRouter`
    instead of flattening it into `app.routes`, so the obvious walk finds
    **six** routes — the ones defined directly on the app — and every
    "is it classified?" assertion below would pass while checking six of 288.
    Measured in 2026 Q3: 288 routes across 23 routers.
    """
    assert len(routes) > 200, f"only {len(routes)} routes — the walk stopped early"
    paths = {p for _m, p in routes}
    # One from each corner of the app, including the spawn editor's own
    # router, which is the largest group and the least covered elsewhere.
    # Its routes sit in a router included inside `/api/map-editor`'s, and are
    # reported with both prefixes, as they are served (plans/68 review B6).
    for path in ("/api/mods", "/api/players/online", "/api/backups",
                 "/api/map-editor/status", "/api/map-editor/traders",
                 "/api/map-editor/expansion/safezones"):
        assert path in paths, path
    assert "/traders" not in paths


def test_the_enumeration_lists_what_is_served(app):
    """plans/68 review B6: the same set the session tripwire walks, which
    goes through `effective_candidates` as the router does."""
    served = {(m, p) for m, p in _ungated(app) | _gated(app) if m not in ("WS", "MOUNT")}

    assert set(enumerate_routes(app)) == served


def test_every_route_has_a_role(app):
    for row in audit_app(app):
        assert row["role"] in ("viewer", "moderator", "admin"), row


# ── default deny ────────────────────────────────────────────────────────


def test_an_unclassified_write_is_admin():
    """The whole design. Forgetting to classify a new write costs a moderator
    a 403 — a bug report — instead of leaving it open, which is a breach."""
    assert required_role("POST", "/api/some/brand/new/thing") == "admin"
    assert required_role("PUT", "/api/whatever") == "admin"
    assert required_role("DELETE", "/api/whatever") == "admin"
    assert required_role("PATCH", "/api/whatever") == "admin"


def test_an_unclassified_read_is_viewer():
    assert required_role("GET", "/api/some/harmless/report") == "viewer"


def test_every_write_in_the_app_is_admin_unless_it_is_named(app):
    """No write may become moderator or viewer by accident — only by a rule."""
    named = {(r.method, r.pattern) for r in (*MODERATOR_WRITES, *SESSION_WRITES)}
    for row in audit_app(app):
        if row["method"] == "GET":
            continue
        if row["role"] != "admin":
            assert (row["method"], row["path"]) in named, row


# ── the rules have to match something ───────────────────────────────────


def _matches_a_real_route(rule, routes) -> bool:
    return any(
        m == rule.method and required_role(m, p) == rule.role and
        permissions.rule_for(m, p) is rule
        for m, p in routes
    )


@pytest.mark.parametrize("rule", MODERATOR_WRITES, ids=lambda r: f"{r.method} {r.pattern}")
def test_each_moderator_write_matches_a_real_route(rule, routes):
    """A typo here silently makes the route admin, and a moderator cannot do
    the job TODO B4 defines for them."""
    assert _matches_a_real_route(rule, routes), f"{rule.method} {rule.pattern} matches no route"


@pytest.mark.parametrize("rule", NON_VIEWER_READS, ids=lambda r: f"{r.method} {r.pattern}")
def test_each_restricted_read_matches_a_real_route(rule, routes):
    """The dangerous direction. A typo here leaves the route at **viewer** —
    which for these paths means handing a read-only account the RCon password
    or a security log."""
    assert _matches_a_real_route(rule, routes), f"{rule.method} {rule.pattern} matches no route"


@pytest.mark.parametrize("path", SIDE_EFFECTING_READS)
def test_each_known_side_effecting_read_still_exists(path, routes):
    """These are allowed for a viewer, but listed so they stay a decision.
    If one is renamed, the note that says "we looked at this" goes stale."""
    assert ("GET", path) in routes, path


# What a read's handler can call that reaches outside this machine: Steam's
# file sizes (routers/maps.py) and the update check (app/ops/self_update.py),
# or that writes a cache into the config dir: the world-file caches.
_SIDE_EFFECT_CALLS = (
    "_download_sizes", "self_update.check", "terrain_heights(", "terrain_roads(", "cached_build(",
)


def test_a_read_that_downloads_or_builds_a_cache_is_listed(app):
    """plans/68 P1 NEW-6: reads that did were missing from the list. A GET
    whose handler calls one of these is a side-effecting read, and the list
    is where that becomes a decision."""
    import inspect

    found = {
        route.path
        for route in _served_routes(app)
        if "GET" in (getattr(route, "methods", None) or ())
        and any(call in inspect.getsource(route.endpoint) for call in _SIDE_EFFECT_CALLS)
    }

    assert {
        "/api/maps/download-sizes", "/api/self-update", "/api/map-editor/terrain-heights", "/api/map-editor/roads",
    } <= found
    assert found <= set(SIDE_EFFECTING_READS), found - set(SIDE_EFFECTING_READS)


# ── the findings this audit produced ────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        "/api/settings",            # steam_api_key + the Discord webhook URL
        "/api/bundle/download",     # config.json, cleartext, in a zip
        "/api/backups/download",    # same
        "/api/security/access-log",  # a security log
        "/api/configs/serverdz/raw",  # password + passwordAdmin
    ],
)
def test_the_reads_that_carry_credentials_are_admin(path):
    """GET is not automatically safe here — the root CLAUDE.md says reports
    still write, and these five say reports still *leak*. Each was found by
    walking the real route table, not assumed."""
    assert required_role("GET", path) == "admin", path


def test_a_viewer_reads_the_launch_command_but_cannot_change_it(sample_config):
    """plans/68: the launch line was admin on `/api/runcommand` and viewer on
    `/api/configs/runcommand`, the same data twice. Both are viewer now, so
    the Dashboard card works for every role; the write is still admin."""
    assert required_role("GET", "/api/runcommand") == "viewer"
    assert required_role("GET", "/api/configs/runcommand") == "viewer"
    assert required_role("PUT", "/api/configs/runcommand") == "admin"

    client = _signed_in_as(_app_with_roles(sample_config), "looker", "lookpass")
    resp = client.get("/api/runcommand")
    assert resp.status_code == 200, resp.text
    assert "parts" in resp.json()
    assert client.get("/api/configs/runcommand").status_code == 200
    assert client.put("/api/configs/runcommand", json={"values": {}}).status_code == 403


def test_a_parameterised_pattern_matches_any_value():
    assert required_role("GET", "/api/configs/types/raw") == "admin"
    assert required_role("GET", "/api/configs/globals/download") == "admin"
    assert required_role("DELETE", "/api/bans/rcon/3") == "moderator"


def test_a_pattern_does_not_match_across_a_slash():
    """`{editor_id}` is one segment. Without that, `/api/configs/x/y/raw`
    would match and the rule would cover paths nobody checked."""
    assert required_role("GET", "/api/configs/a/b/raw") == "viewer"


def test_reading_the_player_list_is_viewer_but_acting_on_it_is_not():
    """The viewer/moderator line, in the one place it matters most."""
    assert required_role("GET", "/api/players/online") == "viewer"
    assert required_role("POST", "/api/players/kick") == "moderator"
    assert required_role("POST", "/api/players/ban") == "moderator"


def test_editing_the_mission_is_admin_not_moderator():
    """TODO B4 gives a moderator the server and the players, not the content."""
    assert required_role("PUT", "/api/configs/types/raw") == "admin"
    assert required_role("POST", "/api/mods/install") == "admin"
    assert required_role("POST", "/api/update/run") == "admin"


def test_no_session_is_still_a_401_not_a_403(app):
    """Roles refuse people who *are* signed in. Somebody who is not signed in
    at all must still be told to sign in, or the login page never appears."""
    from fastapi.testclient import TestClient

    client = TestClient(app)
    assert client.get("/api/settings").status_code == 401


# ── every route is behind the session gate ──────────────────────────────

# Answering without a session, on purpose, and nothing else may. `/ws` checks
# the cookie itself at its handshake (tests/test_web_ws_session.py); `/assets`
# and the SPA fallback serve the built frontend; health is a liveness probe.
PUBLIC_ROUTES = {
    ("POST", "/api/auth/login"),
    ("GET", "/api/health"),
    ("GET", "/{path:path}"),
    ("WS", "/ws"),
    ("MOUNT", "/assets"),
}


def _served_routes(app) -> list:
    """Every route as it is *served*.

    Not what a walk of each router's own routes gives: an included router's
    routes carry neither the prefix nor the dependencies of the router that
    included them (the map editor's routes get both from `/api/map-editor`'s
    router), so the walk goes through `effective_candidates`, where FastAPI has
    already combined the two. A FastAPI that flattens included routes again has
    no such method, and the route itself is then the served one.
    """
    def walk(routes):
        for route in routes:
            effective = getattr(route, "effective_candidates", None)
            if effective is not None:
                yield from walk(effective())
            else:
                yield route

    served = list(walk(app.routes))
    assert len(served) > 200, f"only {len(served)} routes — the walk stopped early"
    return served


def _needs_session(route) -> bool:
    from app.web.deps import require_session

    def calls(dependant):
        for dep in dependant.dependencies:
            yield dep.call
            yield from calls(dep)

    dependant = getattr(route, "dependant", None)
    return dependant is not None and require_session in set(calls(dependant))


def _gated(app) -> set[tuple[str, str]]:
    """Every served (method, path) whose route needs a session."""
    return {
        (method, route.path)
        for route in _served_routes(app) if _needs_session(route)
        for method in getattr(route, "methods", None) or ()
        if method not in ("HEAD", "OPTIONS")
    }


def _ungated(app) -> set[tuple[str, str]]:
    """Every route whose dependencies do not include `require_session`."""
    found = set()
    for route in _served_routes(app):
        if _needs_session(route):
            continue
        methods = getattr(route, "methods", None)
        kind = "WS" if "WebSocket" in type(route).__name__ else "MOUNT"
        for method in sorted(methods or {kind}):
            if method not in ("HEAD", "OPTIONS"):
                found.add((method, route.path))
    return found


def test_every_route_but_the_public_ones_needs_a_session(app):
    """The tripwire for the next route registered outside a guarded router,
    or on one built without `dependencies=[Depends(require_session)]`. The
    role gate lives inside that dependency, so a route without it has no role
    check either."""
    assert _ungated(app) == PUBLIC_ROUTES


def test_the_tripwire_sees_an_unguarded_route(sample_config):
    from fastapi import APIRouter

    from app.web import server

    app = server.create_app(sample_config)
    stray = APIRouter(prefix="/api/stray")
    stray.add_api_route("/write", lambda: {}, methods=["POST"])
    app.include_router(stray)
    app.add_api_route("/api/direct", lambda: {}, methods=["DELETE"])

    assert _ungated(app) - PUBLIC_ROUTES == {
        ("POST", "/api/stray/write"),
        ("DELETE", "/api/direct"),
    }


# ── enforcement (step 4) ────────────────────────────────────────────────


def test_would_refuse_says_what_and_why():
    assert permissions.would_refuse("POST", "/api/mods/install", "viewer") == (
        "POST /api/mods/install needs admin, caller is viewer"
    )
    assert permissions.would_refuse("POST", "/api/players/kick", "moderator") is None
    assert permissions.would_refuse("GET", "/api/status", "viewer") is None
    assert permissions.would_refuse("POST", "/api/mods/install", "admin") is None


def _app_with_roles(sample_config):
    """A real app with one account per role, and the admin already migrated."""
    from app.web import server
    from app.web.auth import hash_password

    sample_config.web_password_hash = hash_password("testpass123")
    app = server.create_app(sample_config)
    users = app.state.ctx.users
    users.add("mandy", "modpass-1", "moderator")
    users.add("looker", "lookpass", "viewer")
    return app


def _signed_in_as(app, name, password):
    from fastapi.testclient import TestClient

    client = TestClient(app)
    resp = client.post("/api/auth/login", json={"username": name, "password": password})
    assert resp.status_code == 200, resp.text
    return client


def test_a_viewer_is_refused_a_credential_read(sample_config, caplog):
    """End to end, and the single most important row in the table: a read-only
    account must not be able to read `steam_api_key` off `GET /api/settings`."""
    import logging

    client = _signed_in_as(_app_with_roles(sample_config), "looker", "lookpass")

    with caplog.at_level(logging.INFO, logger="zsm.web"):
        resp = client.get("/api/settings")

    assert resp.status_code == 403
    assert resp.json()["detail"] == (
        "This needs the admin role. You are signed in as viewer."
    )
    assert any("refused looker" in r.getMessage() for r in caplog.records), caplog.text


@pytest.mark.parametrize("escaped", ["%3F", "%23"])
def test_an_escaped_question_mark_does_not_shorten_the_path_the_gate_reads(sample_config, escaped):
    """plans/68 AUTH-7. The router matches `/api/configs/serverdz?/raw` (the
    decoded path), an admin-only read; the gate has to classify that same
    path, not `request.url.path`, which Starlette re-parses and cuts at the
    `?` or `#`. Cut short, a viewer passed as if asking for
    `/api/configs/serverdz` and reached the route (404 on the odd id)."""
    client = _signed_in_as(_app_with_roles(sample_config), "looker", "lookpass")

    resp = client.get(f"/api/configs/serverdz{escaped}/raw")

    assert resp.status_code == 403, resp.text


def test_a_moderator_may_act_on_players_but_not_edit_the_mission(sample_config):
    """§2 Q2 as decided: the server and the players, not
    the content. Asserted through the app, not against the table."""
    client = _signed_in_as(_app_with_roles(sample_config), "mandy", "modpass-1")

    assert client.put("/api/configs/types/raw", json={"content": "<types/>"}).status_code == 403
    assert client.post("/api/mods/install", json={}).status_code == 403
    # Not 403 — it reaches the route, which then fails on its own terms
    # because no server is running. That is the line being tested.
    assert client.post("/api/players/kick", json={"name": "nobody"}).status_code != 403


def test_anybody_signed_in_may_sign_out(sample_config):
    """Default-deny would make logout admin, leaving a viewer no way out of
    the panel but to clear the cookie by hand."""
    client = _signed_in_as(_app_with_roles(sample_config), "looker", "lookpass")
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/auth/me").status_code == 401


def test_an_install_with_no_accounts_is_still_a_full_admin(sample_config):
    """The migration not having run must not lock the admin out of their
    own panel — this is the state every install is in before it upgrades."""
    from fastapi.testclient import TestClient

    from app.web import server
    from app.web.auth import hash_password

    sample_config.web_password_hash = hash_password("testpass123")
    app = server.create_app(sample_config)
    app.state.ctx.users.path.unlink()  # undo the migration create_app just did

    client = TestClient(app)
    assert client.post("/api/auth/login", json={"password": "testpass123"}).status_code == 200
    assert client.get("/api/settings").status_code == 200


def test_a_corrupt_users_file_does_not_lock_the_panel(sample_config):
    """Step 1 made an unreadable users.json fall back to the single password
    at **login**. Step 4 adds a second place the same file can lock you out:
    the gate. The two must agree, or the failure is "you sign in fine and are
    then refused everything", which reads as a broken panel rather than as a
    broken file.

    **Only the session that signed in that way is admin** (plans/68). The
    sessions opened through accounts name somebody the panel cannot look up
    while the file is broken, so they are refused, and a viewer's tab no
    longer becomes an admin's. They are kept rather than revoked: once the
    file reads again they work as before.
    """
    from fastapi.testclient import TestClient

    app = _app_with_roles(sample_config)
    viewer = _signed_in_as(app, "looker", "lookpass")
    admin_account = _signed_in_as(app, "admin", "testpass123")
    path = app.state.ctx.users.path
    good = path.read_text(encoding="utf-8")
    path.write_text("{ not json at all", encoding="utf-8")

    client = TestClient(app)
    assert client.post("/api/auth/login", json={"password": "testpass123"}).status_code == 200
    assert client.get("/api/settings").status_code == 200

    for session in (viewer, admin_account):
        refused = session.get("/api/settings")
        assert refused.status_code == 401
        assert "users.json" in refused.json()["detail"]
    assert viewer.get("/api/status").status_code == 401

    path.write_text(good, encoding="utf-8")
    assert viewer.get("/api/status").status_code == 200
    assert viewer.get("/api/settings").status_code == 403
    assert admin_account.get("/api/settings").status_code == 200


def test_a_missing_users_file_is_the_same_rule(sample_config):
    """`has_accounts` treats a missing file like an unreadable one, and so
    does the gate: an account session is refused, the fallback login is not."""
    from fastapi.testclient import TestClient

    app = _app_with_roles(sample_config)
    viewer = _signed_in_as(app, "looker", "lookpass")
    app.state.ctx.users.path.unlink()

    assert viewer.get("/api/settings").status_code == 401
    client = TestClient(app)
    assert client.post("/api/auth/login", json={"password": "testpass123"}).status_code == 200
    assert client.get("/api/settings").status_code == 200


def test_a_demotion_takes_effect_on_the_next_request(sample_config):
    """The role is read from users.json per request, never off the session —
    otherwise demoting somebody waits for their week-old cookie to expire."""
    app = _app_with_roles(sample_config)
    client = _signed_in_as(app, "mandy", "modpass-1")

    assert client.post("/api/players/kick", json={"name": "nobody"}).status_code != 403
    app.state.ctx.users.set_role("mandy", "viewer")
    assert client.post("/api/players/kick", json={"name": "nobody"}).status_code == 403


def test_a_permitted_request_logs_nothing(sample_config, caplog):
    """A warning that fires on every request is one nobody reads."""
    import logging

    from fastapi.testclient import TestClient

    from app.web import server
    from app.web.auth import hash_password

    sample_config.web_password_hash = hash_password("testpass123")
    app = server.create_app(sample_config)
    app.state.ctx.users.add("boss", "bosspass", "admin")

    client = TestClient(app)
    client.post("/api/auth/login", json={"username": "boss", "password": "bosspass"})

    with caplog.at_level(logging.INFO, logger="zsm.web"):
        client.get("/api/settings")

    assert not [r for r in caplog.records if "refused" in str(r.msg)]


def test_a_broken_classifier_refuses_rather_than_guessing(sample_config, monkeypatch):
    """**Step 4 inverts this, and that is the point.**

    While nothing was enforced, a bug in here had to cost a log line rather
    than the request. Now that the answer decides access, "could not tell"
    must not read as "allowed" — the rule `firewall_ops` states for a port it
    could not probe, with the sign flipped to match the stakes. An admin shut
    out by a bug here still has the CLI, which plans/43 §5 keeps free of roles
    on purpose.
    """
    from fastapi.testclient import TestClient

    from app.web import deps, server
    from app.web.auth import hash_password

    def _boom(*_a, **_k):
        raise RuntimeError("classifier exploded")

    monkeypatch.setattr(deps.permissions, "would_refuse", _boom)

    sample_config.web_password_hash = hash_password("testpass123")
    app = server.create_app(sample_config)
    app.state.ctx.users.add("boss", "bosspass", "admin")
    client = TestClient(app)
    client.post("/api/auth/login", json={"username": "boss", "password": "bosspass"})

    resp = client.get("/api/settings")
    assert resp.status_code == 403
    assert "decide" in resp.json()["detail"]
