"""Steam Workshop dependency resolution (app/modmgr/workshop_api.py children
fetch + app/modmgr/mod_ops.py resolver/combiner)."""

from __future__ import annotations

import json
import os

import pytest


class _FakeResp:
    def __init__(self, payload: dict) -> None:
        self._b = json.dumps(payload).encode()

    def read(self) -> bytes:
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_query_children_parses_and_skips_collections(monkeypatch):
    from app.modmgr import workshop_api

    payload = {
        "response": {
            "publishedfiledetails": [
                {
                    "result": 1,
                    "publishedfileid": "100",
                    "file_type": 0,  # Community item → children are dependencies
                    "children": [
                        {"publishedfileid": "200", "sortorder": 0, "file_type": 0},
                        {"publishedfileid": "300", "sortorder": 1, "file_type": 0},
                    ],
                },
                {
                    "result": 1,
                    "publishedfileid": "999",
                    "file_type": 2,  # Collection → children are members, not deps
                    "children": [{"publishedfileid": "111"}],
                },
            ]
        }
    }
    monkeypatch.setattr(
        workshop_api.urllib.request, "urlopen", lambda req, timeout=30: _FakeResp(payload)
    )

    out = workshop_api.get_workshop_children(["100", "999"], "KEY")
    assert out["100"] == ["200", "300"]
    assert out["999"] == []


def test_get_workshop_children_network_error_is_empty(monkeypatch):
    from app.modmgr import workshop_api

    def _boom(req, timeout=30):
        raise OSError("no network")

    monkeypatch.setattr(workshop_api.urllib.request, "urlopen", _boom)
    assert workshop_api.get_workshop_children(["100"], "KEY") == {}


def test_resolve_workshop_deps_transitive_with_cache(monkeypatch, tmp_path):
    from app.modmgr import mod_ops, workshop_api
    from app.modmgr.workshop_api import WorkshopModInfo

    graph = {"100": ["200"], "200": ["300"], "300": []}
    calls = {"children": 0}

    def _children(ids, key, timeout=30):
        calls["children"] += 1
        return {i: graph.get(i, []) for i in ids}

    def _details(ids, key, timeout=30):
        return {i: WorkshopModInfo(mod_id=i, title=f"Title {i}", time_updated=0) for i in ids}

    monkeypatch.setattr(workshop_api, "get_workshop_children", _children)
    monkeypatch.setattr(workshop_api, "get_workshop_details", _details)

    cache = tmp_path / "workshop_deps.json"
    deps = mod_ops.resolve_workshop_deps(["100"], "KEY", cache_path=cache)
    assert deps == {"200": "@Title 200", "300": "@Title 300"}
    assert cache.is_file()

    # A second resolve is served entirely from the cache (no re-fetch).
    before = calls["children"]
    assert mod_ops.resolve_workshop_deps(["100"], "KEY", cache_path=cache) == deps
    assert calls["children"] == before


def test_an_unanswered_lookup_is_not_cached_as_no_dependencies(monkeypatch, tmp_path):
    """plans/50 B: every lookup used to be a POST that Steam answered 405, and
    each was written to the cache as `children: []` for a week, so even with
    the request fixed an install would find no dependencies until then."""
    import json

    from app.modmgr import mod_ops, workshop_api

    answers = [{}, {"100": ["200"]}]  # offline first, then Steam answers

    monkeypatch.setattr(workshop_api, "get_workshop_children", lambda ids, key, timeout=30: answers.pop(0) if answers else {})
    monkeypatch.setattr(workshop_api, "get_workshop_details", lambda ids, key, timeout=30: {})

    cache = tmp_path / "workshop_deps.json"
    assert mod_ops.resolve_workshop_deps(["100"], "KEY", cache_path=cache) == {}
    assert "100" not in json.loads(cache.read_text())
    assert mod_ops.resolve_workshop_deps(["100"], "KEY", cache_path=cache) == {"200": "@Mod 200"}


def test_a_cache_entry_from_before_the_fix_is_fetched_again(monkeypatch, tmp_path):
    import json
    import time

    from app.modmgr import mod_ops, workshop_api

    cache = tmp_path / "workshop_deps.json"
    cache.write_text(json.dumps({"100": {"children": [], "fetched_at": int(time.time()), "title": ""}}))
    monkeypatch.setattr(workshop_api, "get_workshop_children", lambda ids, key, timeout=30: {"100": ["200"]})
    monkeypatch.setattr(workshop_api, "get_workshop_details", lambda ids, key, timeout=30: {})

    assert mod_ops.resolve_workshop_deps(["100"], "KEY", cache_path=cache) == {"200": "@Mod 200"}


@pytest.mark.parametrize(
    "title", ["Bad\u2028@CF=111\u2028x", "Bad\n@CF", "Up/../Out", "Tab\there"]
)
def test_a_title_that_cannot_be_a_mod_folder_names_the_dependency_by_id(
    monkeypatch, tmp_path, title
):
    """The title is the mod author's, and it becomes the folder name and a line
    in mods.txt and cached-mod-ids.txt."""
    from app.modmgr import mod_ops, workshop_api
    from app.modmgr.workshop_api import WorkshopModInfo

    monkeypatch.setattr(
        workshop_api, "get_workshop_children", lambda ids, key, timeout=30: {"100": ["200"], "200": []}
    )
    monkeypatch.setattr(
        workshop_api,
        "get_workshop_details",
        lambda ids, key, timeout=30: {i: WorkshopModInfo(mod_id=i, title=title, time_updated=0) for i in ids},
    )
    deps = mod_ops.resolve_workshop_deps(["100"], "KEY", cache_path=tmp_path / "deps.json")
    assert deps == {"200": "@Mod 200"}


def _deps_named_by(monkeypatch, tmp_path, title):
    from app.modmgr import mod_ops, workshop_api
    from app.modmgr.workshop_api import WorkshopModInfo

    monkeypatch.setattr(
        workshop_api, "get_workshop_children", lambda ids, key, timeout=30: {"100": ["200"], "200": []}
    )
    monkeypatch.setattr(
        workshop_api,
        "get_workshop_details",
        lambda ids, key, timeout=30: {i: WorkshopModInfo(mod_id=i, title=title, time_updated=0) for i in ids},
    )
    return mod_ops.resolve_workshop_deps(["100"], "KEY", cache_path=tmp_path / "deps.json")


@pytest.mark.parametrize("title", ["Bob's Mod", "Cost $5", "100% Loot", 'Say "hi"', "Back`tick"])
def test_a_title_the_launch_command_refuses_names_the_dependency_by_id(monkeypatch, tmp_path, title):
    """The name goes into the server's launch command, whose render refuses
    these characters in a mod name (`service_control.mod_name_problem`)."""
    monkeypatch.setattr("app.server.service_control.IS_WINDOWS", False)
    assert _deps_named_by(monkeypatch, tmp_path, title) == {"200": "@Mod 200"}


@pytest.mark.parametrize(
    "title", ["Weapons; Expanded", "Foo;", "=CRG= Weapons Pack", "Base Building -- Reborn"]
)
def test_a_title_with_a_separator_names_the_dependency_by_id(monkeypatch, tmp_path, title):
    """2P-C6..C8: `;` splits mods.txt and -mod=, `=` splits a cached-mod-ids.txt
    line so the id is lost, and `--` breaks the XML comment a disable writes."""
    monkeypatch.setattr("app.server.service_control.IS_WINDOWS", False)
    assert _deps_named_by(monkeypatch, tmp_path, title) == {"200": "@Mod 200"}


@pytest.mark.parametrize("title", ["Ninjins-PvP-PvE", "MMG - Mightys Military Gear", "ScreenTela-"])
def test_a_single_hyphen_still_names_the_dependency(monkeypatch, tmp_path, title):
    """Registry names such as these carry one; only `--` is refused."""
    monkeypatch.setattr("app.server.service_control.IS_WINDOWS", False)
    assert _deps_named_by(monkeypatch, tmp_path, title) == {"200": f"@{title}"}


def test_a_title_with_spaces_and_parentheses_still_names_the_dependency(monkeypatch, tmp_path):
    monkeypatch.setattr("app.server.service_control.IS_WINDOWS", False)
    assert _deps_named_by(monkeypatch, tmp_path, "Good Mod (Beta)") == {"200": "@Good Mod (Beta)"}


def test_resolve_workshop_deps_no_key_is_empty():
    from app.modmgr import mod_ops

    assert mod_ops.resolve_workshop_deps(["100"], "") == {}


def test_get_missing_deps_ex_offline_uses_static(sample_config):
    from app.modmgr import mod_ops

    sample_config.steam_api_key = ""
    missing = dict(mod_ops.get_missing_deps_ex(sample_config, ["2458896948"], set()))
    assert missing.get("1559212036") == "@CF"  # static MOD_DEPS: @TraderPlus → @CF


def test_get_missing_deps_ex_merges_api_deps(sample_config, monkeypatch):
    from app.modmgr import mod_ops

    sample_config.steam_api_key = "KEY"
    monkeypatch.setattr(
        mod_ops, "resolve_workshop_deps", lambda ids, key, cache_path=None: {"777": "@Extra"}
    )
    missing = dict(mod_ops.get_missing_deps_ex(sample_config, ["500"], set()))
    assert missing.get("777") == "@Extra"


@pytest.mark.skipif(
    not os.environ.get("STEAM_API_KEY"), reason="live Steam Workshop API key required"
)
def test_traderplus_requires_cf_live():
    """Validates the children-are-dependencies semantics against real data.

    Run with STEAM_API_KEY set. @TraderPlus (2458896948) declares @CF
    (1559212036) as a Required Item.
    """
    from app.modmgr import mod_ops

    deps = mod_ops.resolve_workshop_deps(["2458896948"], os.environ["STEAM_API_KEY"])
    assert "1559212036" in deps
