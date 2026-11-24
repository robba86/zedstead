"""Auto configure: types entries for a mod that ships no XML (app/ops/mod_autoconfig.py).

Every config, PBO and Workshop text here is invented in the shape of the real
thing. No file comes from a mod.
"""

from __future__ import annotations

import pytest

from app.modmgr import modlist
from app.modmgr.mod_conflict import extract_mod_class_info
from app.ops import mod_autoconfig
from app.shared import rap
from app.xml import xml_manual
from tests.test_terrain_map import pbo_bytes
from tests.test_world_names import rap_bytes

MOD = "@Invented Guns"

STOCK_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>
<types>
\t<type name="VanillaRifle">
\t\t<nominal>7</nominal>
\t\t<lifetime>28800</lifetime>
\t\t<restock>0</restock>
\t\t<min>3</min>
\t\t<quantmin>-1</quantmin>
\t\t<quantmax>-1</quantmax>
\t\t<cost>100</cost>
\t\t<flags count_in_cargo="0" count_in_hoarder="0" count_in_map="1" count_in_player="0" crafted="0" deloot="0"/>
\t\t<category name="weapons"/>
\t\t<usage name="Military"/>
\t\t<value name="Tier3"/>
\t</type>
\t<type name="AlreadyThere">
\t\t<nominal>1</nominal>
\t</type>
</types>
"""

CONFIG = {
    "CfgVehicles": {
        "Inventory_Base": {},
        "InventedBox": {"__parent__": "Inventory_Base", "scope": 2},
        "InventedAbstract": {"__parent__": "Inventory_Base", "scope": 0},
        "InventedLooseThing": {"__parent__": "SomethingUnknown"},
        "AlreadyThere": {"__parent__": "Inventory_Base", "scope": 2},
    },
    "CfgWeapons": {
        "InventedRifle_Base": {"__parent__": "VanillaRifle", "scope": 0},
        "InventedRifle": {"__parent__": "InventedRifle_Base", "scope": 2},
        "InventedRifle_Black": {"__parent__": "InventedRifle"},
    },
}


def _install_mod(server_dir, config=CONFIG, *, text: bytes | None = None):
    addons = server_dir / MOD / "addons"
    addons.mkdir(parents=True, exist_ok=True)
    entry = {"config.cpp": text} if text is not None else {"config.bin": rap_bytes(config)}
    (addons / "guns.pbo").write_bytes(pbo_bytes(entry))


@pytest.fixture()
def mission(sample_config, tmp_mission_dir):
    (tmp_mission_dir / "db" / "types.xml").write_text(STOCK_TYPES)
    (tmp_mission_dir / "cfgeconomycore.xml").write_text(
        '<?xml version="1.0"?><economycore><classes/><defaults/></economycore>'
    )
    _install_mod(sample_config.server_dir_path)
    modlist.add(sample_config, MOD, "1234567890")
    return tmp_mission_dir


def _no_workshop(_mod_id):
    return None


# Every option off: the plain copy and the plain defaults.
PLAIN = mod_autoconfig.AutoConfigOptions(split_nominal=False, guess_category=False, starter_nominal=False)


# --- reading classes ------------------------------------------------------------


def test_rap_reads_parent_and_scope():
    data = rap_bytes({"CfgVehicles": {"A": {"__parent__": "Base", "scope": 2}, "B": {}}})
    body = rap.find_class(data, ["CfgVehicles", "A"])
    assert rap.class_parent(data, body) == "Base"
    assert rap.class_scope(data, body) == 2
    other = rap.find_class(data, ["CfgVehicles", "B"])
    assert rap.class_parent(data, other) == "" and rap.class_scope(data, other) is None


def test_class_info_from_a_binary_config(sample_config):
    _install_mod(sample_config.server_dir_path)
    info = {c.name: c for c in extract_mod_class_info(sample_config.server_dir_path, MOD)}
    assert info["InventedRifle"].parent == "InventedRifle_Base"
    assert info["InventedRifle"].section == "CfgWeapons"
    assert info["InventedAbstract"].scope == 0
    assert info["InventedLooseThing"].scope is None


def test_class_info_from_a_text_config(sample_config):
    text = b"""
    class CfgPatches { class Guns { requiredAddons[] = {}; }; };
    class CfgVehicles {
        class Inventory_Base;
        class InventedBox : Inventory_Base {
            scope = 2;
            class Inner { scope = 0; };
        };
        class InventedNoScope : Inventory_Base { class Inner { scope = 2; }; };
    };
    """
    _install_mod(sample_config.server_dir_path, text=text)
    info = {c.name: c for c in extract_mod_class_info(sample_config.server_dir_path, MOD)}
    assert set(info) == {"InventedBox", "InventedNoScope"}  # the forward declaration has no body
    assert info["InventedBox"].parent == "Inventory_Base" and info["InventedBox"].scope == 2
    # A nested class's scope is not the class's own.
    assert info["InventedNoScope"].scope is None


# --- building from the PBO ------------------------------------------------------


def test_pbo_build_copies_the_parent_and_skips_bases(sample_config, mission):
    preview = mod_autoconfig.build(sample_config, MOD, fetch_description=_no_workshop, options=PLAIN)
    assert preview.source == "pbo"
    parents = {row["name"]: row["parent"] for row in preview.from_parent}
    # Both walk up to the vanilla rifle; the colour variant inherits scope 2.
    assert parents == {"InventedRifle": "VanillaRifle", "InventedRifle_Black": "VanillaRifle"}
    assert preview.from_defaults == ["InventedBox"]
    skipped = {row["name"]: row["reason"] for row in preview.skipped}
    assert "base class" in skipped["InventedAbstract"]
    assert "base class" in skipped["InventedRifle_Base"]
    assert "no scope" in skipped["InventedLooseThing"]
    assert "already" in skipped["AlreadyThere"]
    assert "Inventory_Base" in skipped  # scope unknown, no known parent

    assert '<type name="InventedRifle">' in preview.xml
    rifle = preview.xml.split('<type name="InventedRifle">')[1].split("</type>")[0]
    assert "<nominal>7</nominal>" in rifle and '<value name="Tier3"' in rifle
    # The orphan has nominal 0; the copied rifle has 7 and is not listed.
    assert preview.zero_nominal == ["InventedBox"]
    box = preview.xml.split('<type name="InventedBox">')[1].split("</type>")[0]
    assert "<nominal>0</nominal>" in box and "<category" not in box


def test_a_mod_with_no_classes_is_refused(sample_config, mission):
    with pytest.raises(mod_autoconfig.AutoConfigError, match="no item classes"):
        mod_autoconfig.build(sample_config, "@NotInstalled", fetch_description=_no_workshop)


# --- the Workshop description ---------------------------------------------------

DESCRIPTION = """Invented guns for testing.

[b]Types:[/b]
[code]
<type name="InventedRifle">
  <nominal>2</nominal>
  <lifetime>100</lifetime>
  <category name="weapons"/>
</type>
<type name="VanillaRifle"><nominal>99</nominal></type>
<type name="InventedBox"><nominal>broken
[/code]
"""


def test_workshop_blocks_win_and_outside_classes_are_dropped(sample_config, mission):
    asked = []

    def fetch(mod_id):
        asked.append(mod_id)
        return DESCRIPTION

    preview = mod_autoconfig.build(sample_config, MOD, fetch_description=fetch)
    assert asked == ["1234567890"]
    assert preview.from_workshop == ["InventedRifle"]
    assert preview.source == "workshop+pbo"
    rifle = preview.xml.split('<type name="InventedRifle">')[1].split("</type>")[0]
    assert "<nominal>2</nominal>" in rifle
    # A block for a vanilla class never reaches the mission.
    assert "VanillaRifle" not in preview.xml
    assert any(r["name"] == "VanillaRifle" and "not a class of this mod" in r["reason"] for r in preview.skipped)
    # The broken block is ignored, and the PBO fills in the box.
    assert "InventedBox" in preview.from_defaults


def test_bbcode_and_entities_are_removed_before_parsing():
    text = '[code]&lt;type name="X1"&gt;&lt;nominal&gt;3&lt;/nominal&gt;&lt;/type&gt;[/code]'
    blocks = mod_autoconfig.workshop_type_blocks(text)
    assert [b.get("name") for b in blocks] == ["X1"]


def test_no_answer_from_steam_falls_back_to_the_pbo(sample_config, mission):
    preview = mod_autoconfig.build(sample_config, MOD, fetch_description=_no_workshop)
    assert preview.from_workshop == []
    assert "did not answer" in preview.workshop_note


def test_file_id_is_a_safe_slug():
    assert mod_autoconfig.file_id_for("@M134 Minigun") == "auto-m134-minigun"
    assert xml_manual.is_safe_file_id(mod_autoconfig.file_id_for("@--Weird!!Name--"))


# --- applying -------------------------------------------------------------------


def test_apply_stores_a_manual_file_and_registers_it(sample_config, mission):
    preview = mod_autoconfig.build(sample_config, MOD, fetch_description=_no_workshop)
    result = mod_autoconfig.apply(sample_config, preview)
    assert result["applied"] is True
    link = xml_manual.get_file(preview.file_id, manual_dir=sample_config.paths.custom_xml_dir)
    assert link["mod"] == MOD and link["kind"] == "types"
    assert "InventedRifle" in (mission / "custom" / f"user-{preview.file_id}.xml").read_text()
    assert f'name="user-{preview.file_id}.xml"' in (mission / "cfgeconomycore.xml").read_text()

    with pytest.raises(mod_autoconfig.AutoConfigError, match="already has the manual file"):
        mod_autoconfig.apply(sample_config, preview)


# --- web ------------------------------------------------------------------------

from fastapi.testclient import TestClient  # noqa: E402

from tests.test_web_mods import _login, _poll_job, app_and_config  # noqa: E402,F401


def test_web_preview_and_job(app_and_config, sample_config, mission, monkeypatch):  # noqa: F811
    monkeypatch.setattr("app.modmgr.workshop_api.get_public_description", _no_workshop)
    with TestClient(app_and_config) as client:
        _login(client)
        resp = client.post(f"/api/mods/{MOD}/xml/auto-config/preview")
        assert resp.status_code == 200
        body = resp.json()
        assert body["source"] == "pbo" and body["count"] == 3 and "InventedRifle" in body["xml"]

        resp = client.post(f"/api/mods/{MOD}/xml/auto-config")
        assert resp.status_code == 200
        job = _poll_job(client, resp.json()["job_id"])
        assert job["status"] == "success", job

        # A second run is refused in the job, with the reason in its error.
        job = _poll_job(client, client.post(f"/api/mods/{MOD}/xml/auto-config").json()["job_id"])
        assert job["status"] == "failed"
    assert (mission / "custom" / "user-auto-invented-guns.xml").is_file()


def test_web_refuses_an_unknown_mod(app_and_config, mission):  # noqa: F811
    with TestClient(app_and_config) as client:
        _login(client)
        assert client.post("/api/mods/@Nope/xml/auto-config/preview").status_code == 404
        assert client.post("/api/mods/@Nope/xml/auto-config").status_code == 404


def test_xml_status_names_the_auto_configure_file(app_and_config, sample_config, mission, monkeypatch):  # noqa: F811
    """The row menu opens the Types form of this file (Edit auto config)."""
    preview = mod_autoconfig.build(sample_config, MOD, fetch_description=_no_workshop)
    mod_autoconfig.apply(sample_config, preview)
    with TestClient(app_and_config) as client:
        _login(client)
        row = client.get("/api/mods/xml-status").json()["mods"][MOD]
    assert row["manual"] == 1 and row["auto_file"] == "auto-invented-guns"


# --- the three options ----------------------------------------------------------


def _type_block(xml, name):
    return xml.split(f'<type name="{name}">')[1].split("</type>")[0]


def test_split_nominal_shares_the_parent_values(sample_config, mission):
    """Two variants of a rifle with nominal 7 and min 3 get 4 and 2 each, not 7 and 3."""
    preview = mod_autoconfig.build(sample_config, MOD, fetch_description=_no_workshop)
    assert preview.split == [
        {"parent": "VanillaRifle", "names": ["InventedRifle", "InventedRifle_Black"], "nominal": 4, "min": 2}
    ]
    for name in ("InventedRifle", "InventedRifle_Black"):
        block = _type_block(preview.xml, name)
        assert "<nominal>4</nominal>" in block and "<min>2</min>" in block


def test_split_keeps_a_spawning_item_at_nominal_one():
    from xml.etree import ElementTree as ET

    group = [ET.fromstring("<type name='A'><nominal>1</nominal><min>1</min></type>") for _ in range(5)]
    assert mod_autoconfig._split(group) == {"nominal": 1, "min": 1}
    zero = [ET.fromstring("<type name='B'><nominal>0</nominal><min>0</min></type>") for _ in range(2)]
    assert mod_autoconfig._split(zero) == {"nominal": 0, "min": 0}


def test_guess_category_and_starter_nominal_on_a_default_entry(sample_config, mission):
    """InventedBox has no known parent: CfgVehicles and the keyword "box" are
    no weapon, so it gets the item starter and no category; a class in
    CfgMagazines with no known parent is a magazine."""
    config = dict(CONFIG)
    config["CfgMagazines"] = {"Magazine_Base": {}, "InventedDrum": {"__parent__": "Magazine_Base", "scope": 2}}
    config["CfgVehicles"] = dict(CONFIG["CfgVehicles"], InventedTunaCan={"__parent__": "Inventory_Base", "scope": 2})
    _install_mod(sample_config.server_dir_path, config)
    preview = mod_autoconfig.build(sample_config, MOD, fetch_description=_no_workshop)
    guessed = {row["name"]: row for row in preview.guessed}
    assert guessed["InventedDrum"] == {"name": "InventedDrum", "category": "weapons", "nominal": 4, "min": 2}
    assert guessed["InventedTunaCan"] == {"name": "InventedTunaCan", "category": "food", "nominal": 3, "min": 1}
    assert guessed["InventedBox"] == {"name": "InventedBox", "nominal": 3, "min": 1}  # Inventory_Base is loot
    assert '<category name="food"' in _type_block(preview.xml, "InventedTunaCan")
    assert preview.zero_nominal == []


def test_options_off_leave_nominal_zero(sample_config, mission):
    preview = mod_autoconfig.build(sample_config, MOD, fetch_description=_no_workshop, options=PLAIN)
    assert preview.guessed == [] and preview.split == []
    assert preview.zero_nominal == ["InventedBox"]


def test_options_from_a_request_body():
    opts = mod_autoconfig.AutoConfigOptions.from_dict({"split_nominal": False, "other": 1})
    assert opts == mod_autoconfig.AutoConfigOptions(split_nominal=False)
    assert mod_autoconfig.AutoConfigOptions.from_dict(None) == mod_autoconfig.AutoConfigOptions()


def test_web_preview_takes_the_switches(app_and_config, sample_config, mission, monkeypatch):  # noqa: F811
    monkeypatch.setattr("app.modmgr.workshop_api.get_public_description", _no_workshop)
    with TestClient(app_and_config) as client:
        _login(client)
        on = client.post(f"/api/mods/{MOD}/xml/auto-config/preview").json()
        off = client.post(
            f"/api/mods/{MOD}/xml/auto-config/preview",
            json={"split_nominal": False, "guess_category": False, "starter_nominal": False},
        ).json()
    assert on["options"] == {"split_nominal": True, "guess_category": True, "starter_nominal": True}
    assert on["split"] and on["zero_nominal"] == []
    assert off["split"] == [] and off["zero_nominal"] == ["InventedBox"]


def test_category_guess_matches_whole_words_only():
    from app.ops.economy_analyzer import category_from_classname, class_words

    assert class_words("M134Minigun_Mag") == {"m", "134", "minigun", "mag"}
    assert category_from_classname("InventedTunaCan", whole_words=True) == "food"
    # Short keywords inside a longer word ("case", "fal", "map") are no match.
    for name in ("Showcase", "Waterfall", "Mapletree_Sign"):
        assert category_from_classname(name, whole_words=True) == "", name
    assert category_from_classname("Showcase") == "containers"  # the analyzer's own substring match is unchanged


def test_starter_nominal_skips_cars_buildings_and_unknown_bases(sample_config, mission):
    """CfgVehicles holds cars and buildings too: only a chain that leaves the
    mod at a loot base gets the starter, the rest keep nominal 0 and say why."""
    config = dict(CONFIG)
    config["CfgVehicles"] = dict(
        CONFIG["CfgVehicles"],
        InventedCar={"__parent__": "CarScript", "scope": 2},
        InventedShed_Base={"__parent__": "House", "scope": 0},
        InventedShed={"__parent__": "InventedShed_Base", "scope": 2},
    )
    _install_mod(sample_config.server_dir_path, config)
    preview = mod_autoconfig.build(sample_config, MOD, fetch_description=_no_workshop)
    guessed = {row["name"]: row for row in preview.guessed}
    assert guessed["InventedCar"] == {"name": "InventedCar", "not_loot": "CarScript"}  # no category, no nominal
    assert guessed["InventedShed"]["not_loot"] == "House"
    assert guessed["InventedBox"]["nominal"] == 3  # Inventory_Base is a loot base
    assert {"InventedCar", "InventedShed"} <= set(preview.zero_nominal)


def test_xml_status_flags_a_mod_with_no_item_classes(app_and_config, sample_config, mission):  # noqa: F811
    """@CF ships no XML and defines no item class. Its Auto configure button
    only learned that on the click, as a red error toast. xml-status now says
    so first, and the page disables the button with that sentence."""
    (sample_config.server_dir_path / "@CF" / "addons").mkdir(parents=True)
    ((sample_config.server_dir_path / "@CF" / "addons") / "cf.pbo").write_bytes(
        pbo_bytes({"config.bin": rap_bytes({"CfgPatches": {"CF": {}}})})
    )
    modlist.add(sample_config, "@CF", "1559212036")
    with TestClient(app_and_config) as client:
        _login(client)
        rows = client.get("/api/mods/xml-status").json()["mods"]
    assert rows["@CF"]["no_items"] is True
    assert "no_items" not in rows.get(MOD, {})
