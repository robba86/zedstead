"""Tests for the mod list (mods.json), Workshop IDs, mission detection, exclusions."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from app.modmgr import mod_conflict, modlist, mods
from app.modmgr.modlist import active as get_active_mods
from app.modmgr.mods import (
    EXCLUDED_MOD_KEYWORDS,
    get_current_mission,
    kebab_case,
    mod_is_excluded,
    set_mod_disabled,
    set_mod_enabled,
)
from app.server import service_control
from tests.conftest import dayz_docs, disabled_names, is_manual, learned_ids, pinned_names, set_test_flags, write_test_ids, write_test_mods


# ---------------------------------------------------------------------------
# mods.json (plans/77)
# ---------------------------------------------------------------------------


class TestModList:
    def test_no_file_is_an_empty_list(self, sample_config):
        assert modlist.installed(sample_config) == []

    def test_save_and_load_round_trip(self, sample_config):
        write_test_mods(sample_config, ["@CF", "@TraderPlus", "@Dabs Framework"])
        assert modlist.installed(sample_config) == ["@CF", "@TraderPlus", "@Dabs Framework"]

    def test_a_kept_mod_keeps_its_flags(self, sample_config):
        write_test_mods(sample_config, ["@CF", "@Made-Up"], disabled=["@Made-Up"], server_only=["@Made-Up"])
        modlist.save_installed(sample_config, ["@Made-Up", "@Other"])
        ml = modlist.load(sample_config)
        assert ml.names() == ["@Made-Up", "@Other"]
        assert ml.disabled() == {"@Made-Up"} and ml.server_only() == ["@Made-Up"]

    def test_the_file_carries_the_ids_zsm_knows(self, sample_config):
        write_test_ids({"@Made-Up": "9000000001"})
        write_test_mods(sample_config, ["@CF", "@Made-Up", "@Nobody"])
        data = json.loads(sample_config.paths.mods_json.read_text(encoding="utf-8"))
        assert data["version"] == modlist.VERSION
        ids = {m["name"]: m.get("id") for m in data["mods"]}
        assert ids == {"@CF": "1559212036", "@Made-Up": "9000000001", "@Nobody": None}

    def test_a_write_that_fails_leaves_the_file_as_it_was(self, sample_config):
        """`write_text` emptied the file before it encoded the text. A lone
        surrogate (JSON's ``"\\ud83d"``) cannot be encoded, and is no mod name."""
        from app.modmgr.mods import breaks_a_line, is_safe_mod_dir_name

        assert breaks_a_line("\ud83d") and not is_safe_mod_dir_name("@Emoji\ud83d")
        write_test_mods(sample_config, ["@CF", "@TraderPlus"])
        with pytest.raises(UnicodeEncodeError):
            modlist.save(sample_config, modlist.ModList([modlist.Mod("@Emoji\ud83d")]))
        assert modlist.installed(sample_config) == ["@CF", "@TraderPlus"]

    def test_a_local_mod_keeps_no_id_and_round_trips(self, sample_config):
        """plans/78 D1: the admin's own folder. `local` is written only when
        true, so a list without one is byte for byte what it was."""
        modlist.save(sample_config, modlist.ModList([modlist.Mod("@CF"), modlist.Mod("@Dabs Framework", local=True)]))
        data = json.loads(sample_config.paths.mods_json.read_text(encoding="utf-8"))
        rows = {m["name"]: m for m in data["mods"]}
        assert "local" not in rows["@CF"] and rows["@CF"]["id"] == "1559212036"
        # A name zsm knows an ID for still gets none when it is local.
        assert rows["@Dabs Framework"]["local"] is True and "id" not in rows["@Dabs Framework"]
        assert modlist.local(sample_config) == {"@Dabs Framework"}

    def test_update_writes_only_a_change(self, sample_config):
        write_test_mods(sample_config, ["@CF"])
        path = sample_config.paths.mods_json
        path.write_text(path.read_text(encoding="utf-8").replace("\n", "\r\n"), encoding="utf-8")
        before = path.read_bytes()
        assert modlist.add(sample_config, "@CF") is False
        assert path.read_bytes() == before

    @pytest.mark.parametrize("data, says", [
        ("[]", "not a JSON object"),
        ('{"mods": []}', "no version number"),
        ('{"version": 99, "mods": []}', "newer Zedstead"),
        ('{"version": 1, "mods": [{"name": "CF"}]}', "not a mod folder name"),
        ('{"version": 1, "mods": [{"name": "@A;@B"}]}', "holds ';'"),
        ('{"version": 1, "mods": [{"name": "@CF"}, {"name": "@CF"}]}', "listed twice"),
        ('{"version": 1, "mods": [{"name": "@CF", "id": "../x"}]}', "not a Workshop ID"),
        ('{"version": 1, "mods": [{"name": "@CF", "enabled": "yes"}]}', "not true or false"),
        ('{"version": 1, "mods": [{"name": "@CF", "local": 1}]}', "not true or false"),
        ('{"version": 1, "mods": [{"name": "@CF", "id": "1", "local": true}]}', "local and has a Workshop ID"),
        ('{"version": 1, "mods": [], "load_order": {"pinned": "@CF"}}', "not a list of names"),
        ("{", "not valid JSON"),
    ])
    def test_a_bad_file_is_refused_and_named(self, sample_config, data, says):
        """An unreadable list is never an empty one: an update copies back only
        the listed mods."""
        path = sample_config.paths.mods_json
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data, encoding="utf-8")
        with pytest.raises(modlist.ModListError) as e:
            modlist.installed(sample_config)
        assert says in str(e.value) and str(path) in str(e.value)

    def test_a_list_another_process_changed_is_not_overwritten(self, sample_config):
        """`update` reads under the lock, so a change made between two calls is kept."""
        write_test_mods(sample_config, ["@CF"])
        modlist.add(sample_config, "@A")
        modlist.add(sample_config, "@B")
        assert modlist.installed(sample_config) == ["@CF", "@A", "@B"]
        assert sample_config.paths.mods_json.with_name("mods.json.lock").exists()



def test_every_seed_registry_reader_splits_a_name_at_its_last_equals_sign(monkeypatch):
    """WORKSHOP_MOD_IDS has three readers besides the cached-ids file and
    mods.txt; all go through `mods.split_name_id`."""
    from app.ops import changeset_ops
    from app.shared import seed_data

    registry = "@CF=1559212036\n@Mod=Tools=1564026768\n"
    monkeypatch.setattr(seed_data, "WORKSHOP_MOD_IDS", registry)

    assert seed_data.parse_workshop_mod_ids() == {"1559212036": "@CF", "1564026768": "@Mod=Tools"}
    assert service_control._parse_workshop_registry(registry) == ["@CF", "@Mod=Tools"]
    assert changeset_ops._build_id_lookup(None) == {"@CF": "1559212036", "@Mod=Tools": "1564026768"}
    assert changeset_ops._parse_mod_entry("@Mod=Tools=1564026768") == ("@Mod=Tools", "1564026768")
    assert changeset_ops._parse_mod_entry("@Plain") == ("@Plain", "")


# ---------------------------------------------------------------------------
# workshop-ids.json (plans/77)
# ---------------------------------------------------------------------------


class TestWorkshopIds:
    def test_none_learned_yet(self, sample_config):
        assert learned_ids() == {}
        assert modlist.workshop_ids(sample_config)["@CF"] == "1559212036"

    def test_remember_and_read(self, sample_config):
        modlist.remember_id(sample_config, "@Made-Up", "9000000001")
        modlist.remember_id(sample_config, "@Also-Made-Up", "9000000002")
        assert learned_ids() == {"@Made-Up": "9000000001", "@Also-Made-Up": "9000000002"}
        assert modlist.workshop_ids(sample_config)["@Made-Up"] == "9000000001"

    def test_a_known_name_keeps_its_id(self, sample_config):
        modlist.remember_id(sample_config, "@Made-Up", "9000000001")
        modlist.remember_id(sample_config, "@Made-Up", "9000000009")
        assert learned_ids() == {"@Made-Up": "9000000001"}

    def test_a_shipped_id_is_never_written(self, sample_config):
        modlist.remember_id(sample_config, "@CF", "1559212036")
        assert learned_ids() == {}

    def test_the_ids_in_the_list_count_as_known(self, sample_config):
        write_test_mods(sample_config, ["@Made-Up"])
        modlist.update(sample_config, lambda ml: setattr(ml.get("@Made-Up"), "id", "9000000001"))
        modlist.remember_id(sample_config, "@Made-Up", "9000000009")
        assert learned_ids() == {}
        assert modlist.workshop_ids(sample_config)["@Made-Up"] == "9000000001"

    def test_an_entry_that_is_not_a_workshop_id_is_skipped(self, sample_config):
        # The ID goes to SteamCMD and onto the workshop path: only digits may get that far.
        write_test_ids({"@A": "9000000001", "@Up": "../other", "@Plus": "+quit", "@Wide": "１２３"})
        assert learned_ids() == {"@A": "9000000001", "@Up": "../other", "@Plus": "+quit", "@Wide": "１２３"}
        known = modlist.workshop_ids(sample_config)
        assert known["@A"] == "9000000001" and not {"@Up", "@Plus", "@Wide"} & set(known)

    def test_a_name_holding_an_equals_sign_is_kept(self, sample_config):
        modlist.remember_id(sample_config, "@Mod=Tools", "9000000003")
        assert modlist.workshop_ids(sample_config)["@Mod=Tools"] == "9000000003"

    @pytest.mark.parametrize("sep", ["\n", "\r", " ", "\x85", "\x0b"])
    def test_a_name_that_breaks_the_line_is_not_written(self, sample_config, sep):
        # Names can come from a Workshop title.
        modlist.remember_id(sample_config, f"@Title{sep}@CF", "9000000004")
        assert learned_ids() == {}


# ---------------------------------------------------------------------------
# get_current_mission
# ---------------------------------------------------------------------------


def _in_missions(lines: str) -> str:
    """*lines* where DayZ reads the mission: class Missions, class DayZ."""
    return "class Missions\n{\n    class DayZ\n    {\n" + lines + "\n    };\n};\n"


def _wiki_cfg() -> str:
    """Bohemia's own serverDZ.cfg, verbatim: both blocks of the wiki page."""
    import re

    wiki = dayz_docs("wiki", "pages", "server-configuration.wiki")
    blocks = re.findall(r'<syntaxhighlight lang="cpp">\n(.*?)</syntaxhighlight>', wiki.read_text(), re.S)
    return blocks[0] + blocks[1]


class TestGetCurrentMission:
    def test_basic_template(self, tmp_path: Path):
        cfg = tmp_path / "serverDZ.cfg"
        cfg.write_text(textwrap.dedent("""\
            hostname = "Test";
            class Missions {
                class DayZ {
                    template = "dayzOffline.chernarusplus";
                };
            };
        """))
        assert get_current_mission(cfg) == "dayzOffline.chernarusplus"

    def test_with_inline_comment(self, tmp_path: Path):
        cfg = tmp_path / "serverDZ.cfg"
        cfg.write_text(_in_missions('    template = "dayzOffline.namalsk"; // main mission'))
        assert get_current_mission(cfg) == "dayzOffline.namalsk"

    def test_missing_file(self, tmp_path: Path):
        assert get_current_mission(tmp_path / "nope.cfg") is None

    def test_no_template_line(self, tmp_path: Path):
        cfg = tmp_path / "serverDZ.cfg"
        cfg.write_text('hostname = "Test";\n')
        assert get_current_mission(cfg) is None

    def test_commented_template_ignored(self, tmp_path: Path):
        cfg = tmp_path / "serverDZ.cfg"
        cfg.write_text('// template = "dayzOffline.chernarusplus";\n')
        assert get_current_mission(cfg) is None

    def test_unquoted_template(self, tmp_path: Path):
        cfg = tmp_path / "serverDZ.cfg"
        cfg.write_text(_in_missions("template=dayzOffline.chernarusplus;"))
        assert get_current_mission(cfg) == "dayzOffline.chernarusplus"

    def test_bohemias_own_file(self, tmp_path: Path):
        cfg = tmp_path / "serverDZ.cfg"
        cfg.write_text(_wiki_cfg(), encoding="utf-8")
        assert get_current_mission(cfg) == "dayzOffline.chernarusplus"

    def test_a_hostname_that_says_template_is_not_the_mission(self, tmp_path: Path):
        """The hostname sits above class Missions in Bohemia's file, and was
        read as the mission: the storage wipe, the merge and Clear XML backups
        would all have worked in mpmissions/Best template server."""
        cfg = tmp_path / "serverDZ.cfg"
        stock = _wiki_cfg()
        assert stock.startswith('hostname = "EXAMPLE NAME";')
        cfg.write_text(stock.replace("EXAMPLE NAME", "Best template server", 1), encoding="utf-8")
        assert get_current_mission(cfg) == "dayzOffline.chernarusplus"

    def test_a_commented_out_template_in_missions_is_skipped(self, tmp_path: Path):
        cfg = tmp_path / "serverDZ.cfg"
        cfg.write_text(_in_missions(
            '// template = "dayzOffline.enoch";\n'
            'template = "dayzOffline.chernarusplus"; // was "template = x;"'
        ))
        assert get_current_mission(cfg) == "dayzOffline.chernarusplus"

    @pytest.mark.parametrize("cfg_text", [
        'template = "dayzOffline.enoch";\n',
        'class Other { class DayZ { template = "dayzOffline.enoch"; }; };\n',
        'class Missions { template = "dayzOffline.enoch"; };\n',
    ])
    def test_a_template_anywhere_else_is_not_the_mission(self, tmp_path: Path, cfg_text: str):
        cfg = tmp_path / "serverDZ.cfg"
        cfg.write_text(cfg_text)
        assert get_current_mission(cfg) is None

    @pytest.mark.parametrize(
        "line",
        [
            'template = "../../elsewhere";',
            'template = "..";',
            'template = "dayzOffline.chernarusplus/db";',
            'template = "/srv/elsewhere";',
            'template = "..\\elsewhere";',
            "template=../elsewhere;",
        ],
    )
    def test_a_template_that_is_not_one_folder_is_no_mission(self, tmp_path: Path, line: str):
        """Every caller joins the mission onto mpmissions/ and reads, writes or
        deletes under it (Clear XML backups, the storage wipe, the merge)."""
        cfg = tmp_path / "serverDZ.cfg"
        cfg.write_text(f"class Missions {{ class DayZ {{ {line} }}; }};\n")
        assert get_current_mission(cfg) is None


# ---------------------------------------------------------------------------
# mod_is_excluded
# ---------------------------------------------------------------------------


class TestModIsExcluded:
    def test_namalsk_excluded(self):
        assert mod_is_excluded("@Namalsk-Survival") is True

    def test_cf_not_excluded(self):
        assert mod_is_excluded("@CF") is False

    def test_case_insensitive(self):
        assert mod_is_excluded("@BANOV_Island") is True

    def test_custom_exclusion_list(self):
        assert mod_is_excluded("@MyMod", excluded_parts=["mymod"]) is True
        assert mod_is_excluded("@OtherMod", excluded_parts=["mymod"]) is False


# ---------------------------------------------------------------------------
# kebab_case
# ---------------------------------------------------------------------------


class TestKebabCase:
    def test_basic(self):
        assert kebab_case("@Mod_Name") == "mod-name"

    def test_strips_at_sign(self):
        assert kebab_case("@CF") == "cf"

    def test_collapses_dashes(self):
        assert kebab_case("@Some__Mod--Name") == "some-mod-name"

    def test_spaces_to_dashes(self):
        assert kebab_case("@My Cool Mod") == "my-cool-mod"


# ---------------------------------------------------------------------------
# get_active_mods / set_mod_disabled / set_mod_enabled
# ---------------------------------------------------------------------------


class TestGetActiveMods:
    def test_all_enabled_by_default(self, sample_config):
        write_test_mods(sample_config, ["@CF", "@TraderPlus"])
        assert get_active_mods(sample_config) == ["@CF", "@TraderPlus"]

    def test_filters_disabled(self, sample_config):
        write_test_mods(sample_config, ["@CF", "@TraderPlus", "@VPP"])
        set_test_flags(sample_config, disabled=["@TraderPlus"])
        assert get_active_mods(sample_config) == ["@CF", "@VPP"]

    def test_empty_mods_list(self, sample_config):
        assert get_active_mods(sample_config) == []

    def test_disabled_mod_not_in_list_is_harmless(self, sample_config):
        write_test_mods(sample_config, ["@CF"])
        set_test_flags(sample_config, disabled=["@NonExistent"])
        assert get_active_mods(sample_config) == ["@CF"]

    def test_all_disabled(self, sample_config):
        write_test_mods(sample_config, ["@CF", "@VPP"])
        set_test_flags(sample_config, disabled=["@CF", "@VPP"])
        assert get_active_mods(sample_config) == []


class TestSetModDisabled:
    def test_adds_to_disabled(self, sample_config):
        write_test_mods(sample_config, ["@CF"])
        set_mod_disabled(sample_config, "@CF")
        assert "@CF" in disabled_names(sample_config)

    def test_no_duplicate(self, sample_config):
        set_test_flags(sample_config, disabled=["@CF"])
        set_mod_disabled(sample_config, "@CF")
        assert disabled_names(sample_config).count("@CF") == 1


class TestSetModEnabled:
    def test_removes_from_disabled(self, sample_config):
        set_test_flags(sample_config, disabled=["@CF", "@VPP"])
        set_mod_enabled(sample_config, "@CF")
        assert "@CF" not in disabled_names(sample_config)
        assert "@VPP" in disabled_names(sample_config)

    def test_noop_if_not_disabled(self, sample_config):
        set_test_flags(sample_config, disabled=["@VPP"])
        set_mod_enabled(sample_config, "@CF")
        assert disabled_names(sample_config) == ["@VPP"]


# ── F18: a numeric placeholder and the real name for one id (plans/55 D) ──


def test_a_real_name_replaces_the_ids_placeholder(sample_config):
    write_test_ids({"@Other": "9000000001", "@9000000005": "9000000005"})

    modlist.remember_id(sample_config, "@Made-Up-Market", "9000000005")

    assert learned_ids() == {"@Other": "9000000001", "@Made-Up-Market": "9000000005"}


def test_a_placeholder_for_a_named_id_adds_nothing(sample_config):
    write_test_ids({"@Made-Up-Market": "9000000005"})

    modlist.remember_id(sample_config, "@9000000005", "9000000005")

    assert learned_ids() == {"@Made-Up-Market": "9000000005"}


def test_a_placeholder_the_server_uses_is_kept(sample_config):
    """A by-ID install named its folder @<id> before names were resolved;
    the list holds it under that name, and readers look it up by name."""
    write_test_ids({"@9000000005": "9000000005"})
    write_test_mods(sample_config, ["@9000000005"])

    modlist.remember_id(sample_config, "@Made-Up-Market", "9000000005")

    assert learned_ids() == {"@9000000005": "9000000005", "@Made-Up-Market": "9000000005"}


def test_a_placeholder_with_a_server_folder_is_kept(sample_config):
    write_test_ids({"@9000000005": "9000000005"})
    (sample_config.server_dir_path / "@9000000005").mkdir()

    modlist.remember_id(sample_config, "@Made-Up-Market", "9000000005")

    assert learned_ids()["@9000000005"] == "9000000005"


# ---------------------------------------------------------------------------
# Manual load order (plans/49)
# ---------------------------------------------------------------------------

@pytest.fixture()
def ordered(sample_config, monkeypatch):
    """*sample_config* with mods.txt and a dependency map standing in for the
    PBO scan. ``deps[m]`` = the mods *m* requires."""
    deps: dict[str, set[str]] = {}

    def graph(mod_list, server_dir, cached_ids=None, **kw):
        return {m: deps[m] & set(mod_list) for m in mod_list if deps.get(m, set()) & set(mod_list)}

    monkeypatch.setattr(mod_conflict, "_build_mod_dependency_graph", graph)

    def setup(names: list[str], requires: dict[str, set[str]] | None = None):
        write_test_mods(sample_config, names)
        deps.clear()
        deps.update(requires or {})
        return sample_config

    return setup


def test_automatic_order_puts_a_mod_after_what_it_requires(ordered):
    config = ordered(["@B", "@A"], {"@B": {"@A"}})
    set_test_flags(config, pinned=["@B", "@A"])
    assert mods.get_resolved_mod_order(config) == ["@A", "@B"]


def test_manual_order_keeps_a_mod_before_what_it_requires(ordered):
    config = ordered(["@B", "@A"], {"@B": {"@A"}})
    set_test_flags(config, pinned=["@B", "@A"])
    set_test_flags(config, manual=True)
    assert mods.get_resolved_mod_order(config) == ["@B", "@A"]
    assert service_control._get_mod_string(config) == "@B;@A"
    assert mods.load_order_breaks(config) == [("@B", "@A")]


def test_switching_manual_on_changes_nothing_the_server_loads(ordered):
    """Resolving with a disabled mod can order the enabled ones differently
    (@A waits for @X), so seeding from that list would reorder -mod=."""
    config = ordered(["@A", "@B", "@X"], {"@A": {"@X"}})
    set_test_flags(config, pinned=["@A", "@B", "@X"])
    set_test_flags(config, disabled=["@X"])
    rows_before = mods.get_resolved_mod_order(config, include_disabled=True)
    assert [m for m in rows_before if m != "@X"] != mods.get_resolved_mod_order(config)
    before = service_control._get_mod_string(config)

    mods.apply_load_order(config, manual=True)

    assert is_manual(config) is True
    assert service_control._get_mod_string(config) == before == "@A;@B"
    assert pinned_names(config) == ["@A", "@B", "@X"]


def test_a_mod_the_manual_order_lacks_goes_last_in_automatic_order(ordered):
    config = ordered(["@A", "@B", "@D", "@C"], {"@D": {"@C"}})
    set_test_flags(config, pinned=["@B", "@A"])
    set_test_flags(config, manual=True)
    assert mods.get_resolved_mod_order(config) == ["@B", "@A", "@C", "@D"]


def test_a_disabled_mod_stays_out_of_the_manual_order_and_keeps_its_row(ordered):
    config = ordered(["@A", "@B", "@C"])
    set_test_flags(config, pinned=["@C", "@B", "@A"])
    set_test_flags(config, manual=True)
    set_test_flags(config, disabled=["@B"])
    assert mods.get_resolved_mod_order(config) == ["@C", "@A"]
    assert mods.get_resolved_mod_order(config, include_disabled=True) == ["@C", "@B", "@A"]


def test_saving_a_manual_order_keeps_a_disabled_mod_after_the_mod_it_followed(ordered):
    config = ordered(["@A", "@X", "@B"])
    set_test_flags(config, pinned=["@A", "@X", "@B"])
    set_test_flags(config, manual=True)
    set_test_flags(config, disabled=["@X"])

    mods.apply_load_order(config, order=["@B", "@A"])

    assert pinned_names(config) == ["@B", "@A", "@X"]
    set_test_flags(config, disabled=[])
    assert mods.get_resolved_mod_order(config) == ["@B", "@A", "@X"]


def test_switching_back_to_automatic_keeps_the_list_and_sorts_again(ordered):
    config = ordered(["@B", "@A", "@C"], {"@B": {"@A"}})
    set_test_flags(config, pinned=["@C", "@B", "@A"])
    set_test_flags(config, manual=True)

    mods.apply_load_order(config, manual=False)

    assert pinned_names(config) == ["@C", "@B", "@A"]
    assert mods.get_resolved_mod_order(config) == ["@C", "@A", "@B"]
    assert mods.load_order_breaks(config) == []


def test_the_setting_switches_through_the_same_path(ordered):
    from app.shared import settings_registry

    config = ordered(["@B", "@A"], {"@B": {"@A"}})
    settings_registry._write(settings_registry._BY_KEY["manual_load_order"], config, True)
    assert is_manual(config) is True
    assert pinned_names(config) == ["@A", "@B"]
