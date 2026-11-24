"""Install -> Supported: the catalog in app/ops/supported_mods.py and its route.

The catalog repeats three names other files own (the Configs -> Mods subtabs,
the editor groups and the map editor's layer keys), so these tests keep them
in step.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.ops.supported_mods import SUPPORTED_MODS, supported_mods
from app.web import editors
from tests.conftest import write_test_ids, write_test_mods

from tests.test_web_mods import client  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parents[1]
# ConfigsPage.jsx takes its tab lists from pageTabs.js (plans/75).
PAGE_TABS = ROOT / "webui" / "src" / "pageTabs.js"
LAYER_TOGGLES = ROOT / "webui" / "src" / "pages" / "mapeditor" / "panels" / "LayerToggles.jsx"


def test_one_entry_per_configs_mods_subtab():
    text = PAGE_TABS.read_text(encoding="utf-8")
    block = re.search(r"export const CONFIGS_MODS_SUBTABS = \[(.*?)\]", text)
    assert block, "CONFIGS_MODS_SUBTABS not found in pageTabs.js"
    subtabs = re.findall(r"'([^']+)'", block.group(1))
    assert [m.group for m in SUPPORTED_MODS] == subtabs


def test_every_group_has_editors():
    groups = {spec.group for spec in editors.list_editors()}
    assert {m.group for m in SUPPORTED_MODS} <= groups


def test_every_layer_key_is_a_map_editor_layer():
    text = LAYER_TOGGLES.read_text(encoding="utf-8")
    keys = set(re.findall(r"^\s*key: '([\w-]+)'", text, re.M))
    for m in SUPPORTED_MODS:
        assert set(m.layers) <= keys, m.group


def test_installed_is_matched_by_workshop_id(sample_config):
    write_test_mods(sample_config, ['@My RadZone'])
    write_test_ids({'@My RadZone': '2294323732'})
    rows = {r["group"]: r for r in supported_mods(sample_config, {"RadZone": 5})}
    assert rows["RadZone"]["installed"] is True
    assert rows["RadZone"]["editors"] == 5
    assert rows["TraderPlus"]["installed"] is False
    assert rows["Expansion"]["picker"] is True


def test_route_lists_the_supported_mods(client):  # noqa: F811
    resp = client.get("/api/mods/supported")
    assert resp.status_code == 200
    mods = resp.json()["mods"]
    assert [m["group"] for m in mods] == [m.group for m in SUPPORTED_MODS]
    assert all(m["editors"] > 0 for m in mods)
