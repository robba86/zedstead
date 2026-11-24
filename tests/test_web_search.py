"""GET /api/search-index (app/web/routers/search.py) and the palette's
client-side lists (plans/75).

The index is built from static tables, so most of these are drift checks:
every editor has a field source decision, every link lands on a route the
frontend has, and the two lists the backend repeats from the frontend
(the interactive editors, the Mods Install subtabs) still match it. The
frontend half has no JS harness, so its checks read the source, the way
`tests/test_routes_roles.py` does.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from app.web.editors import list_editors
from app.web.routers import search

PASSWORD = "testpass123"
WEBUI = Path(__file__).resolve().parent.parent / "webui" / "src"


def _app(sample_config):
    from app.web import server
    from app.web.auth import hash_password

    sample_config.web_password_hash = hash_password(PASSWORD)
    return server.create_app(sample_config)


def _signed_in(app, username=None, password=PASSWORD) -> TestClient:
    client = TestClient(app)
    body = {"password": password}
    if username:
        body["username"] = username
    assert client.post("/api/auth/login", json=body).status_code == 200
    return client


# ── the route ───────────────────────────────────────────────────────────


def test_the_index_needs_a_session(sample_config):
    assert TestClient(_app(sample_config)).get("/api/search-index").status_code == 401


def test_an_admin_gets_every_kind(sample_config):
    data = _signed_in(_app(sample_config)).get("/api/search-index").json()

    assert {e["kind"] for e in data} == {"setting", "editor", "field", "help"}
    for entry in data:
        assert set(entry) == {"kind", "title", "sub", "text", "to", "min_role"}
        assert entry["title"] and entry["to"].startswith("/")
    tos = {e["to"] for e in data}
    assert "/settings?q=bundle_export_dir" in tos
    assert "/configs/serverdz?q=maxPlayers" in tos


def test_a_viewer_gets_nothing_an_admin_page_holds(sample_config):
    app = _app(sample_config)
    app.state.ctx.users.add("watcher", "watcherpass1", "viewer")
    data = _signed_in(app, "watcher", "watcherpass1").get("/api/search-index").json()

    assert data, "a viewer can open /configs, so the editors are theirs"
    assert {e["min_role"] for e in data} == {"viewer"}
    assert not any(e["to"].startswith(("/settings", "/mods", "/map-editor")) for e in data)


def test_text_carries_no_markup():
    for entry in search.build_index():
        assert "[/" not in entry["text"], entry


# ── drift against the backend's own registries ──────────────────────────


def test_every_editor_has_a_field_source_decision():
    """A new editor has to be listed in FIELD_SOURCES (a source or None);
    field_source raises KeyError for one that is not."""
    from app.web.editors import list_editors

    for spec in list_editors():
        source = search.field_source(spec.id)
        if source is not None:
            assert all(len(t) == 3 for t in source()), spec.id


def test_every_form_field_links_to_its_own_filter():
    for entry in search.build_index():
        if entry["kind"] == "field" and "q=" in entry["to"]:
            assert entry["to"].startswith("/configs/")


# ── drift against the frontend ──────────────────────────────────────────


def _route_patterns() -> list[re.Pattern]:
    text = (WEBUI / "routes.js").read_text(encoding="utf-8")
    body = text[text.index("export const ROUTES = [") :]
    body = body[: body.index("\n];")]
    paths = re.findall(r'\bpath:\s*"([^"]+)"', body)
    return [re.compile("^" + re.sub(r":[a-zA-Z]+", "[^/]+", p) + "$") for p in paths]


def test_every_link_lands_on_a_route():
    patterns = _route_patterns()
    for entry in search.build_index():
        path = urlsplit(entry["to"]).path
        assert any(p.match(path) for p in patterns), entry["to"]


def test_a_field_link_lands_on_a_form_that_reads_its_q():
    """A field link is ``/configs/<id>?q=<key>`` with no tab: an editor has one
    Edit tab since 2026 Q4. FormEditor reads ``?q=``; a bespoke view from
    interactiveViews.jsx is that editor's Edit tab instead and need not, so no
    form editor with a field source may have one."""
    text = (WEBUI / "interactiveViews.jsx").read_text(encoding="utf-8")
    body = text[text.index("const INTERACTIVE_VIEWS = {") :]
    body = body[: body.index("\n}")]
    bespoke = set(re.findall(r"^\s+'?([a-z-]+)'?:", body, re.M))
    assert bespoke
    forms = {spec.id for spec in list_editors() if spec.kind == "form" and search.field_source(spec.id)}
    assert not bespoke & forms
    for entry in search.build_index():
        if entry["kind"] == "field" and entry["to"].startswith("/configs/"):
            assert "tab=" not in entry["to"], entry["to"]


def test_the_mods_install_subtabs_match_the_frontends():
    text = (WEBUI / "pageTabs.js").read_text(encoding="utf-8")
    line = re.search(r"export const MODS_INSTALL_SUBTABS = \[(.*)\]", text).group(1)
    assert tuple(re.findall(r"'([^']+)'", line)) == search.MODS_INSTALL_SUBTABS


@pytest.mark.parametrize(
    "page, names",
    [
        ("BackupsPage.jsx", ["BACKUPS_TABS"]),
        ("AnalysisPage.jsx", ["ANALYSIS_TABS", "ANALYSIS_ECONOMY_SUBTABS"]),
        ("ConfigsPage.jsx", ["CONFIGS_TABS", "CONFIGS_MODS_SUBTABS"]),
        ("PlayersPage.jsx", ["PLAYERS_TABS", "PLAYERS_MODERATOR_TABS"]),
        ("PresetsPage.jsx", ["PRESETS_TABS"]),
        ("InstancesPage.jsx", ["INSTANCES_TABS"]),
        ("SchedulerPage.jsx", ["SCHEDULER_TABS"]),
        ("ModsPage.jsx", ["MODS_TABS", "MODS_INSTALL_SUBTABS"]),
    ],
)
def test_pages_take_their_tabs_from_page_tabs(page, names):
    """The palette searches pageTabs.js, so a page with its own copy of the
    list would have tabs the palette does not know."""
    text = (WEBUI / "pages" / page).read_text(encoding="utf-8")
    assert "from '../pageTabs.js'" in text
    assert not re.search(r"^const (TABS|INSTALL_SUBTABS|MISSION_SUBTABS|MODS_SUBTABS) = \[", text, re.M)
    sources = (WEBUI / "searchSources.js").read_text(encoding="utf-8")
    for name in names:
        assert name in text and name in sources, name


def test_the_palette_stays_out_of_the_entry_chunk():
    layout = (WEBUI / "components" / "Layout.jsx").read_text(encoding="utf-8")
    assert "const loadPalette = () => import('./CommandPalette.jsx')" in layout
    assert "lazy(loadPalette)" in layout
    assert "import CommandPalette" not in layout


def test_every_settings_link_names_a_setting_or_a_section():
    """A `/settings?q=` link written into the source names a setting by its
    config key or a section by its title. The page's filter matches both, and
    neither changes when a label is reworded, so a renamed label cannot leave
    a link that finds nothing."""
    from app.shared import settings_registry

    keys = {s.key for s in settings_registry._REGISTRY}
    sections = {s.section for s in settings_registry._REGISTRY}
    app_dir = WEBUI.parent.parent / "app"
    sources = [p for p in WEBUI.rglob("*") if p.suffix in {".js", ".jsx"}]
    sources += [p for p in app_dir.rglob("*.py")]
    bad = []
    for path in sources:
        for term in re.findall(r"/settings\?q=([A-Za-z0-9_.%+-]+)", path.read_text(encoding="utf-8")):
            term = term.rstrip(".")  # a sentence that ends on the link
            if term not in keys and term not in sections:
                bad.append(f"{path.name}: {term}")
    assert not bad, bad
