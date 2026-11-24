"""Guards `MAP_EDITOR_LINKS` in `webui/src/crossLinks.js` — the table that
sends a config editor to the map editor showing that file's data.

Every failure mode here is silent. A stale editor id renders no button, a
typo'd layer key opens the map editor with that layer *off*, and a bad tab name
falls through to the default tab — three ways to look like the feature simply
does not work, none of which raise anything in a browser.

The same parse-the-frontend-table approach as `tests/test_help_md.py`, and for
the same reason: the ids are the server-side registry's, so Python is where the
two halves can actually be compared.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from app.web import editors

ROOT = Path(__file__).resolve().parents[1]
CROSS_LINKS = ROOT / "webui" / "src" / "crossLinks.js"
LAYER_TOGGLES = ROOT / "webui" / "src" / "pages" / "mapeditor" / "panels" / "LayerToggles.jsx"
MAP_EDITOR = ROOT / "webui" / "src" / "pages" / "mapeditor" / "MapEditor.jsx"
URL_STATE = ROOT / "webui" / "src" / "pages" / "mapeditor" / "state" / "urlState.js"

#: Config views with their own page instead of an EditorSpec, so they are not
#: in the registry. Mirrors `tests/test_help_md.py::DEDICATED_CONFIG_PAGES`.
DEDICATED_CONFIG_PAGES = {"types", "tp-price", "mod-xml"}

#: Text-kind editors with no richer view anywhere, each with the reason it is
#: allowed to be a textarea. A reason per entry rather than a bare set, because
#: the two cases are not the same: "a textarea is the right answer here" and
#: "the right answer is not built yet" want different follow-ups, and a set
#: flattens them into the same silence.
#:
#: Everything else that is text-kind has to prove it is not hiding a
#: map-editor surface behind a raw box — that was exactly the state of
#: `expansion-ai-locations` until 2026 Q3 (TODO H4).
#:
#: Empty since 2026 Q4 (plans/76 step 2b): `expansion-socialmedia`, its
#: last entry, became a form whose links are a tree. The rule stays for the
#: next text-kind editor.
TEXT_WITHOUT_A_RICHER_VIEW: dict[str, str] = {}


@pytest.fixture(scope="module")
def links() -> dict[str, str]:
    text = CROSS_LINKS.read_text(encoding="utf-8")
    block = re.search(r"export const MAP_EDITOR_LINKS = \{(.*?)\n\}", text, re.S)
    assert block, "MAP_EDITOR_LINKS not found — did the export get renamed?"
    pairs = re.findall(r"^\s*'?([\w-]+)'?:\s*'([^']+)'", block.group(1), re.M)
    assert pairs
    return dict(pairs)


@pytest.fixture(scope="module")
def layer_keys() -> set[str]:
    text = LAYER_TOGGLES.read_text(encoding="utf-8")
    keys = set(re.findall(r"^\s*key: '([\w-]+)'", text, re.M))
    assert keys, "no layer keys parsed from LayerToggles.jsx"
    return keys


@pytest.fixture(scope="module")
def url_layer_keys() -> set[str]:
    """`LAYER_KEYS` — the allowlist `?layers=` is filtered against.

    A layer needs an entry in *both* this and `LayerToggles.jsx`, and missing
    either one fails silently in its own way: no toggle to click, or a toggle
    whose key the URL drops on the way in. The underground layer shipped with
    the toggle and without the key, which is why this fixture exists.
    """
    text = URL_STATE.read_text(encoding="utf-8")
    block = re.search(r"export const LAYER_KEYS = \[(.*?)\]", text, re.S)
    assert block, "LAYER_KEYS not found in urlState.js"
    keys = set(re.findall(r"'([\w-]+)'", block.group(1)))
    assert keys
    return keys


@pytest.fixture(scope="module")
def tab_names() -> set[str]:
    text = MAP_EDITOR.read_text(encoding="utf-8")
    tabs = set(re.findall(r"url\.tab === '([\w-]+)'", text))
    assert tabs, "no tabs parsed from MapEditor.jsx"
    return tabs


def test_every_link_names_a_real_editor(links):
    known = {spec.id for spec in editors.list_editors()} | DEDICATED_CONFIG_PAGES
    for editor_id in links:
        assert editor_id in known, editor_id


def test_every_link_switches_on_layers_that_exist(links, layer_keys):
    for editor_id, url in links.items():
        wanted = parse_qs(urlparse(url).query).get("layers", [])
        assert wanted, f"{editor_id}: no layers — the map opens with nothing shown"
        for layer in wanted[0].split(","):
            assert layer in layer_keys, f"{editor_id}: unknown layer {layer!r}"


def test_every_toggleable_layer_is_a_key_the_url_keeps(layer_keys, url_layer_keys):
    """Every layer the panel offers has to survive `?layers=`, or the toggle
    works and the shared link does not."""
    assert layer_keys - url_layer_keys == set()


def test_every_link_names_a_tab_that_exists(links, tab_names):
    for editor_id, url in links.items():
        tab = parse_qs(urlparse(url).query).get("tab", [])
        if not tab:
            continue  # the default tab, deliberately omitted — see the table.
        assert tab[0] in tab_names, f"{editor_id}: unknown tab {tab[0]!r}"


def test_the_default_tab_is_never_written_out(links):
    """`urlState`'s applyTab drops `tab=spawns` as the default, so emitting it
    round-trips to nothing — a link that looks explicit and is not."""
    for editor_id, url in links.items():
        assert "tab=spawns" not in url, editor_id


def test_a_textarea_editor_is_not_hiding_a_map_view(links):
    """A text-kind editor with no map link is claiming no better view exists.
    That claim has been wrong once: `expansion-ai-locations` was a raw box for
    a file the map editor can already edit feature by feature.
    """
    for spec in editors.list_editors():
        if spec.kind != "text":
            continue
        if spec.id in TEXT_WITHOUT_A_RICHER_VIEW:
            continue
        assert spec.id in links, (
            f"{spec.id} renders as a textarea and links to no richer view — "
            "add a MAP_EDITOR_LINKS entry, or a TEXT_WITHOUT_A_RICHER_VIEW "
            "entry saying why a textarea is what it gets"
        )


def test_the_textarea_exemptions_are_all_still_text_kind():
    """An entry left behind after its editor graduated to a form or table is a
    silent hole in the rule above — the next text-kind editor with that id
    would inherit an exemption nobody meant to give it."""
    kinds = {spec.id: spec.kind for spec in editors.list_editors()}
    for editor_id, reason in TEXT_WITHOUT_A_RICHER_VIEW.items():
        assert editor_id in kinds, f"{editor_id} is not a registered editor"
        assert kinds[editor_id] == "text", (
            f"{editor_id} is {kinds[editor_id]}-kind now — drop its exemption"
        )
        assert reason.strip(), editor_id
