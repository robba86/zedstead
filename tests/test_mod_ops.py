"""Unit tests for shared mod helpers used by the web install flow
(app/modmgr/mod_ops.py + app/traderplus/trader_common.py)."""

from __future__ import annotations

import pytest

from app.modmgr.mod_ops import (
    is_placeholder_mod_name,
    is_traderplus_installed,
    list_known_mods,
    list_mod_files,
    mod_file_path,
    name_for_workshop_id,
    resolve_mod_ref,
    resolve_mod_workshop_id,
)
from app.traderplus.trader_common import copy_traderplus_default_config
from app.modmgr import modlist
from tests.conftest import learned_ids, server_only_names, set_test_flags, write_test_ids, write_test_mods


def test_is_traderplus_installed_by_name():
    assert is_traderplus_installed(["@TraderPlus"])
    assert is_traderplus_installed(["traderplus"])
    assert not is_traderplus_installed(["@CF"])


def test_is_traderplus_installed_by_cached_id():
    assert is_traderplus_installed(["@TP"], {"@TP": "2458896948"})
    assert not is_traderplus_installed(["@TP"], {"@TP": "999"})


def test_copy_traderplus_default_config_lifecycle(sample_config):
    # Nothing to copy until the mod's default config exists.
    assert copy_traderplus_default_config(sample_config) == "no_source"

    src = sample_config.server_dir_path / "@TraderPlus" / "ServerProfile" / "TraderPlus"
    (src / "TraderPlusConfig").mkdir(parents=True)
    assert copy_traderplus_default_config(sample_config) == "no_source", "an empty folder"
    (src / "TraderPlusConfig" / "a.json").write_text("{}")

    assert copy_traderplus_default_config(sample_config) == "copied"
    dest = sample_config.server_dir_path / sample_config.profiles_path / "TraderPlus" / "TraderPlusConfig"
    assert (dest / "a.json").read_text() == "{}"

    # Non-destructive: a second run never overwrites.
    (dest / "a.json").write_text('{"mine": 1}')
    assert copy_traderplus_default_config(sample_config) == "exists"
    assert (dest / "a.json").read_text() == '{"mine": 1}'


def _mod_server_profile(server_dir):
    """The mod's ServerProfile/TraderPlus in its shipped layout, with invented
    files (the author's terms forbid copying theirs): configs, a sample bank
    account and garage, and the empty database and log folders."""
    src = server_dir / "@TraderPlus" / "ServerProfile" / "TraderPlus"
    for rel, text in {
        "TraderPlusConfig/TraderPlusGeneralConfig.json": '{"default": 1}',
        "TraderPlusConfig/TraderPlusPriceConfig.json": '{"TraderCategories": []}',
        "TraderPlusBankDatabase/Account_76561190000000009.json": '{"MoneyAmount": 999}',
        "TraderPlusGarageDatabase/Garage_00000009.json": "{}",
        "TraderPlusGarageDatabase/00000009/1.json": "{}",
        "TraderPlusDatabase/I'm here for nothing.txt": "",
        "TraderPlusLogs/I'm here for nothing.txt": "",
    }.items():
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_text(text)


def test_the_install_offer_copies_the_configs_and_no_sample_data(sample_config):
    """mods-13: the offer copied the mod's whole ServerProfile/TraderPlus, so a
    fresh server got the mod author's sample bank account and garage (a real
    Steam ID). Only TraderPlusConfig is the config part, the same files Reset
    TraderPlus JSON copies."""
    from app.traderplus.trader_common import reset_traderplus_json

    _mod_server_profile(sample_config.server_dir_path)
    live = sample_config.server_dir_path / sample_config.profiles_path / "TraderPlus"

    assert copy_traderplus_default_config(sample_config) == "copied"
    copied = sorted(p.relative_to(live).as_posix() for p in live.rglob("*"))
    assert copied == [
        "TraderPlusConfig",
        "TraderPlusConfig/TraderPlusGeneralConfig.json",
        "TraderPlusConfig/TraderPlusPriceConfig.json",
    ]
    assert (live / "TraderPlusConfig" / "TraderPlusGeneralConfig.json").read_text() == '{"default": 1}'

    ok, _msgs = reset_traderplus_json(sample_config)
    assert ok
    assert sorted(p.relative_to(live).as_posix() for p in live.rglob("*")) == copied


def test_the_install_offer_fills_a_missing_config_folder_and_keeps_player_data(sample_config):
    """A profiles/TraderPlus with player data and no TraderPlusConfig gets the
    configs; the account it holds is not touched."""
    _mod_server_profile(sample_config.server_dir_path)
    live = sample_config.server_dir_path / sample_config.profiles_path / "TraderPlus"
    account = live / "TraderPlusBankDatabase" / "Account_76561190000000001.json"
    account.parent.mkdir(parents=True)
    account.write_text('{"MoneyAmount": 250000}')

    assert copy_traderplus_default_config(sample_config) == "copied"
    assert account.read_text() == '{"MoneyAmount": 250000}'
    assert sorted(p.name for p in (live / "TraderPlusBankDatabase").iterdir()) == [account.name]
    assert (live / "TraderPlusConfig" / "TraderPlusGeneralConfig.json").is_file()


def test_resolve_mod_workshop_id_prefers_the_registry_over_the_network(sample_config, monkeypatch):
    # 2026 Q3: @DayZ-Expansion-AI has a registry ID, but resolution went
    # straight to Steam's search page, which zsm could no longer read titles
    # from — so an install logged "No workshop ID found" and skipped the mod.
    def _no_network(name, cached_names):
        raise AssertionError("the registry should have answered this")

    monkeypatch.setattr("app.shared.steam.resolve_mod_id", _no_network)

    mod_id, was_cached = resolve_mod_workshop_id(sample_config, "@DayZ-Expansion-AI")

    assert (mod_id, was_cached) == ("2792982069", True)
    # And it is written through, so later lookups never ask again.

    assert modlist.workshop_ids(sample_config).get("@DayZ-Expansion-AI") == "2792982069"


def test_resolve_mod_workshop_id_still_asks_steam_for_an_unknown_name(sample_config, monkeypatch):
    monkeypatch.setattr("app.shared.steam.resolve_mod_id", lambda name, cached: "555")

    assert resolve_mod_workshop_id(sample_config, "@NotInAnyRegistry") == ("555", False)


def test_name_for_workshop_id_order_of_preference():
    # The server's own cache wins…
    assert name_for_workshop_id("2792982069", {"2792982069": "@Renamed"}) == "@Renamed"
    # …unless the cached name is only the ID again, which is what an install
    # writes when nothing could name the mod.
    assert name_for_workshop_id("2792982069", {"2792982069": "@2792982069"}) == "@DayZ-Expansion-AI"
    # Then the registry, then the caller's hint, then the ID itself.
    assert name_for_workshop_id("2792982069", {}) == "@DayZ-Expansion-AI"
    assert name_for_workshop_id("4242424242", {}, hint="@Fresh") == "@Fresh"
    assert name_for_workshop_id("4242424242", {}) == "@4242424242"


def test_is_placeholder_mod_name():
    assert is_placeholder_mod_name("@2792982069")
    assert is_placeholder_mod_name("2792982069")
    assert not is_placeholder_mod_name("@DayZ-Expansion-AI")


def test_a_search_caches_only_titles_that_can_be_a_mod_folder(sample_config, monkeypatch):
    from app.modmgr import mod_ops

    write_test_ids({'@CF': '1559212036'})
    monkeypatch.setattr(
        "app.shared.steam.search_workshop",
        lambda query: [
            ("111", "Good Mod"),
            ("222", "Bad\u2028@CF=333\u2028x"),
            ("444", "a/../b"),
        ],
    )
    results = mod_ops.search_mod_workshop(sample_config, "cf")
    assert len(results) == 3  # the search still lists every result
    assert learned_ids() == {
        "@CF": "1559212036",
        "@Good Mod": "111",
    }


def test_a_search_does_not_cache_a_title_the_launch_command_refuses(sample_config, monkeypatch):
    from app.modmgr import mod_ops

    monkeypatch.setattr("app.server.service_control.IS_WINDOWS", False)
    write_test_ids({})
    monkeypatch.setattr(
        "app.shared.steam.search_workshop",
        lambda query: [("111", "Bob's Mod"), ("222", "100% Loot"), ("333", "Good (Beta)")],
    )
    results = mod_ops.search_mod_workshop(sample_config, "mod")
    assert len(results) == 3
    assert learned_ids() == {"@Good (Beta)": "333"}


def test_a_search_does_not_cache_a_title_holding_a_separator(sample_config, monkeypatch):
    """2P-C6..C8. `=CRG= Pack` would be written as `@=CRG= Pack=444`, which
    reads back without its id."""
    from app.modmgr import mod_ops

    monkeypatch.setattr("app.server.service_control.IS_WINDOWS", False)
    write_test_ids({})
    monkeypatch.setattr(
        "app.shared.steam.search_workshop",
        lambda query: [("111", "Foo;Bar"), ("222", "Foo;"), ("333", "Big -- Mod"),
                       ("444", "=CRG= Pack"), ("555", "Good - Mod")],
    )
    results = mod_ops.search_mod_workshop(sample_config, "mod")
    assert len(results) == 5
    assert learned_ids() == {"@Good - Mod": "555"}


def test_every_registry_name_would_pass_the_title_gate():
    """A registry mod installs under its own name; none of them holds a
    separator the title gate refuses."""
    from app.modmgr import mod_ops

    names = mod_ops.registry_mod_ids()
    assert names
    assert [n for n in names if any(sep in n for sep in mod_ops.TITLE_SEPARATORS)] == []


def test_list_known_mods_includes_seed_and_cached(sample_config):
    write_test_ids({'@Custom': '999'})
    known = dict(list_known_mods(sample_config))
    assert known.get("@CF") == "1559212036"  # seed registry
    assert known.get("@Custom") == "999"  # cached-mod-ids.txt


def test_list_known_mods_drops_a_placeholder_whose_id_has_a_name(sample_config):
    """F18: the Search tab offered installed @DayZ-Expansion-Market again as
    @2572328470, from a placeholder line an existing cache still holds."""
    sample_config.workshop_content_dir = str(sample_config.server_dir_path / "no-workshop")
    write_test_ids({'@2572328470': '2572328470', '@DayZ-Expansion-Market': '2572328470', '@4242424242': '4242424242'})
    known = dict(list_known_mods(sample_config))
    assert "@2572328470" not in known
    assert known["@DayZ-Expansion-Market"] == "2572328470"
    # A placeholder for an id nothing names is all zsm has, so it stays.
    assert known["@4242424242"] == "4242424242"


# --- resolve_mod_ref (the /mods/:id URL segment) -----------------------------


@pytest.fixture()
def ref_config(sample_config, tmp_path):
    # workshop_content_dir MUST be pinned — DayzConfig.workshop_dir otherwise
    # auto-detects the host's real Steam install.
    sample_config.workshop_content_dir = str(tmp_path / "workshop")
    write_test_ids({'@Custom': '999'})
    write_test_mods(sample_config, ['@Custom'])
    (sample_config.server_dir_path / "@Custom").mkdir(parents=True, exist_ok=True)
    return sample_config


def test_resolve_mod_ref_by_name_and_by_id(ref_config):
    by_name = resolve_mod_ref(ref_config, "@Custom")
    by_id = resolve_mod_ref(ref_config, "999")
    assert by_name.name == by_id.name == "@Custom"
    assert by_name.workshop_id == by_id.workshop_id == "999"
    assert by_name.listed is True
    assert by_name.installed is True
    assert by_name.cached is False  # nothing in the workshop folder


def test_resolve_mod_ref_accepts_bare_name_and_seed_ids(ref_config):
    assert resolve_mod_ref(ref_config, "Custom").name == "@Custom"
    # Known to the seed registry but not installed — still a real mod page.
    seeded = resolve_mod_ref(ref_config, "1559212036")
    assert seeded.name == "@CF"
    assert seeded.listed is False


def test_resolve_mod_ref_marks_workshop_cache(ref_config):
    (ref_config.workshop_dir / "999").mkdir(parents=True)
    ref = resolve_mod_ref(ref_config, "999")
    assert ref.cached is True
    assert ref.workshop_path == ref_config.workshop_dir / "999"


@pytest.mark.parametrize(
    "ref",
    ["", "   ", "@Unknown", "424242424242", "..", "../../etc/passwd", "@a/b", "@.."],
)
def test_resolve_mod_ref_rejects_unknown_and_unsafe(ref_config, ref):
    # Unknown names and anything that isn't a bare directory component resolve
    # to nothing — the web route turns that into a 404 rather than joining it
    # onto server_dir/workshop_dir.
    assert resolve_mod_ref(ref_config, ref) is None


# ── Mod folder browsing ─────────────────────────────────────────────


def test_list_mod_files_walks_and_tags_viewable(tmp_path):
    (tmp_path / "Addons").mkdir()
    (tmp_path / "Addons" / "core.pbo").write_bytes(b"\0\1\2")
    (tmp_path / "types.xml").write_text("<types/>")
    (tmp_path / "README.md").write_text("hi")

    rows, truncated = list_mod_files(tmp_path)
    assert truncated is False
    assert [r["path"] for r in rows] == ["Addons/core.pbo", "README.md", "types.xml"]
    langs = {r["path"]: r["language"] for r in rows}
    # Only the text kinds the viewer serves get a language; the rest are listed
    # by name only.
    assert langs == {"Addons/core.pbo": "", "README.md": "txt", "types.xml": "xml"}


def test_list_mod_files_caps_the_walk(tmp_path):
    for i in range(5):
        (tmp_path / f"f{i}.txt").write_text("x")
    rows, truncated = list_mod_files(tmp_path, limit=3)
    assert len(rows) == 3
    assert truncated is True


def test_list_mod_files_of_a_missing_dir_is_empty(tmp_path):
    assert list_mod_files(tmp_path / "nope") == ([], False)


def test_mod_file_path_resolves_inside_the_mod(tmp_path):
    (tmp_path / "config").mkdir()
    target = tmp_path / "config" / "a.json"
    target.write_text("{}")
    assert mod_file_path(tmp_path, "config/a.json") == target.resolve()
    # Backslashes are what a Windows-authored listing would send back.
    assert mod_file_path(tmp_path, "config\\a.json") == target.resolve()


@pytest.mark.parametrize("rel", ["", "../secret.txt", "config/../../secret.txt", "/etc/passwd", "missing.xml"])
def test_mod_file_path_rejects_escapes_and_misses(tmp_path, rel):
    (tmp_path / "mod").mkdir()
    (tmp_path / "secret.txt").write_text("nope")
    assert mod_file_path(tmp_path / "mod", rel) is None


def test_mod_file_path_rejects_a_symlink_out_of_the_mod(tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_text("nope")
    base = tmp_path / "mod"
    base.mkdir()
    (base / "link.txt").symlink_to(outside)
    # The resolved path leaves the mod dir, so the read is refused even though
    # the link itself sits inside it.
    assert mod_file_path(base, "link.txt") is None


def test_missing_deps_ex_walks_the_expansion_graph(sample_config):
    """Installing -Vehicles by ID pulls Licensed, Core and both frameworks.

    MOD_DEPS holds no Expansion entries, so without the catalog every install
    surface that isn't the picker (install by ID, the CLI, a Doctor fix action)
    would install a module whose dependencies are simply absent.
    """
    from app.modmgr.mod_ops import get_missing_deps_ex

    missing = dict(get_missing_deps_ex(sample_config, ["2291785437"], set()))
    assert missing == {
        "2116157322": "@DayZ-Expansion-Licensed",
        "2291785308": "@DayZ-Expansion-Core",
        "1559212036": "@CF",
        "2545327648": "@Dabs Framework",
    }


def test_missing_deps_ex_skips_expansion_deps_already_installed(sample_config):
    from app.modmgr.mod_ops import get_missing_deps_ex

    missing = dict(
        get_missing_deps_ex(sample_config, ["2291785437"], {"1559212036", "2291785308"})
    )
    assert set(missing) == {"2116157322", "2545327648"}


def test_missing_deps_ex_still_answers_for_a_non_expansion_mod(sample_config):
    from app.modmgr.mod_ops import get_missing_deps_ex

    # @TraderPlus -> @CF, straight out of MOD_DEPS.
    assert dict(get_missing_deps_ex(sample_config, ["2458896948"], set())) == {
        "1559212036": "@CF"
    }


# --- server-side mods (plans/47 A) ---


def test_every_server_side_catalog_id_is_a_catalog_mod():
    from app.shared.seed_data import SERVER_SIDE_MOD_IDS, parse_workshop_mod_ids

    assert SERVER_SIDE_MOD_IDS <= set(parse_workshop_mod_ids())


def test_dynamic_ai_is_catalogued_with_its_expansion_ai_dependency():
    from app.shared.seed_data import MOD_DEPS, SERVER_SIDE_MOD_IDS, parse_workshop_mod_ids

    assert parse_workshop_mod_ids()["2874589934"] == "@DayZ-Dynamic-AI-Addon"
    assert "2874589934" in SERVER_SIDE_MOD_IDS
    assert MOD_DEPS["2874589934"]["deps"] == {"2792982069": "@DayZ-Expansion-AI"}


def test_known_server_side_matches_by_workshop_id_not_folder_name():
    from app.modmgr.mod_ops import known_server_side

    names = ["@CF", "@DynAI", "@DayZ-Dynamic-AI-Addon"]
    # @DynAI is the same workshop item installed under another folder name.
    assert known_server_side(names, {"@DynAI": "2874589934"}) == [
        "@DynAI",
        "@DayZ-Dynamic-AI-Addon",
    ]
    assert known_server_side(["@CF"], {}) == []


def test_uninstall_drops_the_server_side_mark(sample_config):
    from app.modmgr.mod_ops import uninstall_mods
    from app.shared.config import DayzConfig

    write_test_mods(sample_config, ['@CF', '@DayZ-Dynamic-AI-Addon'])
    set_test_flags(sample_config, server_only=["@DayZ-Dynamic-AI-Addon", "@Other"])
    sample_config.save()

    uninstall_mods(sample_config, ["@DayZ-Dynamic-AI-Addon"])

    assert server_only_names(sample_config) == ["@Other"]
    assert server_only_names(sample_config) == ["@Other"]


def test_update_check_takes_ids_from_the_cache_not_mods_txt(sample_config, monkeypatch):
    """plans/50 B: mods.txt holds `@CF;` and the IDs live in cached-mod-ids.txt.
    detect_mod_updates read only `@Name=ID` lines from mods.txt, a form zsm
    never writes, so a real 23-mod install was checked for no mods at all."""
    from app.modmgr import mod_ops, workshop_api

    write_test_mods(sample_config, ['@CF', '@Dabs Framework', '@NoId'])
    write_test_ids({'@CF': '1559212036', '@Dabs Framework': '2545327648'})
    asked = []

    def _check(mod_ids, local_ts, api_key):
        asked.extend(mod_ids)
        return workshop_api.UpdateCheck(total=len(mod_ids), checked_at=1)

    monkeypatch.setattr(workshop_api, "check_for_updates", _check)
    sample_config.steam_api_key = "k"
    assert mod_ops.detect_mod_updates(sample_config).total == 2
    assert asked == ["1559212036", "2545327648"]
