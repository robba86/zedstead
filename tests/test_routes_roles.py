"""The frontend's copy of the role list, and the `minRole` on every route.

There is no JS test harness here, so these are structural checks over the
source — the same approach `tests/test_help_md.py` takes to `routes.js`.

What they are worth: the browser cannot ask the server which role a nav link
needs without a round trip per link, so `webui/src/role.jsx` keeps a copy of
`app/web/users.py::ROLES` and `routes.js` keeps a `minRole` per page. Both are
duplication, and duplication drifts silently — a page added without a
`minRole` resolves to `admin` and simply never appears for anybody else, with
no error anywhere.

**None of this is the permission.** `app/web/deps.py::require_session` refuses
the API whatever the bundle believes; `tests/test_permissions.py` is what
guards that. These guard the panel being *usable* under a role.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.web.users import ROLES

WEBUI = Path(__file__).resolve().parent.parent / "webui" / "src"
ROUTES_JS = WEBUI / "routes.js"
APP_JSX = WEBUI / "App.jsx"
ROLE_JSX = WEBUI / "role.jsx"
LAYOUT_JSX = WEBUI / "components" / "Layout.jsx"


def _route_entries() -> list[tuple[str, str | None]]:
    """Every `path:` in ROUTES with the `minRole:` that follows it.

    Parsed rather than imported, because the only thing that could import it
    is a JS runtime. The entries are one per line or one per object, so the
    text between a `path:` and the next is where its `minRole` has to be.
    """
    text = ROUTES_JS.read_text(encoding="utf-8")
    body = text[text.index("export const ROUTES = ["):]
    body = body[: body.index("\n];")]
    chunks = re.split(r'\bpath:\s*"', body)[1:]
    out = []
    for chunk in chunks:
        path = chunk[: chunk.index('"')]
        role = re.search(r'\bminRole:\s*"([a-z]+)"', chunk)
        out.append((path, role.group(1) if role else None))
    return out


def _app_route_paths() -> set[str]:
    return set(re.findall(r'<Route path="([^"]+)"', APP_JSX.read_text(encoding="utf-8")))


# ── the duplicated role list ────────────────────────────────────────────


def test_the_frontends_role_list_matches_the_backends():
    """Same three strings, same order. `roleAtLeast` is an index comparison on
    both sides, so a reordering here silently inverts privilege rather than
    failing."""
    text = ROLE_JSX.read_text(encoding="utf-8")
    listed = re.search(r"export const ROLES = \[([^\]]+)\]", text).group(1)
    assert [v.strip().strip("'\"") for v in listed.split(",") if v.strip()] == list(ROLES)


def test_an_unknown_role_is_never_privileged_on_the_frontend_either():
    """The rule `app/web/users.py::role_at_least` states, asserted on the
    source because there is nothing here that can run it: a value neither side
    recognises must cost access, not grant it."""
    text = ROLE_JSX.read_text(encoding="utf-8")
    assert "if (held < 0 || needed < 0) return false" in text


# ── minRole on every route ──────────────────────────────────────────────


def test_every_route_declares_a_min_role():
    """A missing one resolves to `admin` and the page is invisible to everyone
    else — no error, no log line, just a nav entry that never appears."""
    missing = [path for path, role in _route_entries() if role is None]
    assert not missing, f"no minRole on: {missing}"


@pytest.mark.parametrize("path,role", _route_entries())
def test_each_min_role_is_a_real_role(path, role):
    assert role in ROLES, f"{path} wants {role!r}"


def test_every_page_app_renders_is_listed_with_a_role():
    """`minRoleFor` resolves through ROUTES. A `<Route>` in App.jsx that is not
    there falls to the `admin` default, so the page exists and nobody below
    admin can reach it."""
    listed = {path for path, _ in _route_entries()}
    # `*` is the not-found page: it sits outside RoleGate because an unknown
    # path has no role to check, so it has no ROUTES entry either.
    assert _app_route_paths() - {"/login", "*"} <= listed


def test_the_pages_a_viewer_gets_are_the_ones_b4_promises():
    """TODO B4: *"a viewer sees status and logs"*, and §2's table adds players,
    configs and analysis. Pinned as a list rather than a rule, because the
    next person to add a page should have to decide rather than inherit."""
    viewer = {path for path, role in _route_entries() if role == "viewer"}
    assert viewer == {
        "/",
        "/wizard",
        # Your own password and your own second factor (TODO B5). It is the
        # only page every role can reach that *writes*, and it has to be:
        # /settings is admin-only, which since B4 left a moderator unable to
        # rotate the password they sign in with.
        "/account",
        "/players",
        "/doctor",
        "/analysis",
        "/help",
        # The design reference (plan 89): sample data only, no API call.
        "/design",
        "/configs",
        "/configs/mod-xml",
        "/configs/types",
        "/configs/tp-price",
        "/configs/:id",
    }


def test_everything_that_changes_the_server_is_admin():
    """The other half of the same table. A moderator acts on players and the
    service — both through controls on pages a viewer can already open — so no
    *page* is moderator-only, and that is not an oversight."""
    admin = {path for path, role in _route_entries() if role == "admin"}
    assert admin == {
        "/maps",
        "/map-editor",
        "/mods",
        "/mods/:id",
        "/quick-setup",
        "/my-server",
        "/presets",
        "/adopt",
        "/update",
        "/scheduler",
        "/cleanup",
        "/backups",
        "/instances",
        "/settings",
    }


# ── the two places that have to consume it ──────────────────────────────


def test_the_sidebar_filters_by_role():
    """`NAV_ITEMS` is the unfiltered list and still exists for other callers;
    the sidebar must use `navItemsFor`."""
    text = LAYOUT_JSX.read_text(encoding="utf-8")
    assert "navItemsFor(role," in text
    assert "NAV_ITEMS.map" not in text


def test_rookie_mode_keeps_the_pages_a_new_admin_needs():
    """Rookie mode filters on the flag. The Guide button renders beside the /help
    entry, so that entry must stay, or Rookie mode loses the Guide."""
    entries = [line for line in ROUTES_JS.read_text(encoding="utf-8").splitlines() if "rookie: true" in line and "path: " in line]
    paths = {line.split('path: "')[1].split('"')[0] for line in entries}
    assert paths == {"/", "/quick-setup", "/my-server", "/maps", "/mods", "/presets", "/backups", "/doctor", "/help"}
    assert "rookieOnly" not in ROUTES_JS.read_text(encoding="utf-8"), "every rookie page also shows with all pages"


def test_the_gate_wraps_every_page():
    """RoleGate is mounted once, inside Layout's route — outside it, a refusal
    would replace the whole window and leave no way on but the Back button."""
    text = APP_JSX.read_text(encoding="utf-8")
    assert "<Route element={<RoleGate />}>" in text
    assert text.index("<Layout") < text.index("<RoleGate />")
