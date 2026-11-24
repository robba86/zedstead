"""Expansion's mission files (app/ops/expansion_ops.py).

Fixture templates, never the real repo: the numbers measured on a real
copy live in plans/36, and a test that needs a 60 000-line types.xml on disk is
a test nobody runs.
"""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from app.ops import expansion_ops as eo
from tests.conftest import manual_dir, write_test_mods

CORE = "2291785308"
VEHICLES = "2291785437"
WEAPONS = "2792985069"
MARKET = "2572328470"
LICENSED = "2116157322"

TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<types>
    <type name="ExpVehicle"><nominal>5</nominal></type>
    <type name="ExpGun"><nominal>3</nominal></type>
    <type name="WoodenLog"><nominal>9</nominal></type>
    <type name="Expansion_Longhorn"><nominal>1</nominal></type>
</types>
"""

SPAWNABLETYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<spawnabletypes>
    <type name="ExpGun">
        <attachments chance="1.00">
            <item name="ExpMag" chance="1.00" />
            <item name="M4Suppressor" chance="0.50" />
        </attachments>
        <attachments chance="1.00">
            <item name="Mag_STANAGCoupled_30Rnd" chance="1.00" />
        </attachments>
    </type>
    <type name="Expansion_LAW">
        <attachments chance="1.00">
            <item name="ExpMag" chance="1.00" />
        </attachments>
    </type>
</spawnabletypes>
"""

EVENTS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<events>
    <event name="VehicleHatchback02">
        <nominal>10</nominal>
        <children>
            <child lootmax="0" lootmin="0" max="5" min="3" type="ExpVehicle"/>
        </children>
    </event>
    <event name="ExpansionHeli">
        <nominal>2</nominal>
        <children>
            <child lootmax="0" lootmin="0" max="1" min="1" type="ExpVehicle"/>
        </children>
    </event>
    <event name="ExpansionMarketTruck">
        <nominal>2</nominal>
        <children>
            <child lootmax="0" lootmin="0" max="1" min="1" type="MarketTruck"/>
        </children>
    </event>
</events>
"""

EVENTSPAWNS = """<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>
<eventposdef>
    <event name="ExpansionHeli">
        <pos x="1.0" y="2.0" z="3.0" a="0.0" />
    </event>
    <event name="ExpansionMarketTruck">
        <pos x="4.0" y="5.0" z="6.0" a="0.0" />
    </event>
    <event name="StaticHeliCrash">
        <pos x="7.0" y="8.0" z="9.0" a="0.0" />
    </event>
</eventposdef>
"""

MISSION_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<types>
    <type name="WoodenLog"><nominal>50</nominal></type>
</types>
"""

MISSION_EVENTS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<events>
    <event name="VehicleHatchback02"><nominal>5</nominal></event>
    <event name="StaticHeliCrash"><nominal>3</nominal></event>
</events>
"""

# What the (patched) PBO scan reports each installed module defines.
MOD_CLASSES = {
    "@DayZ-Expansion-Core": ["ExpansionCoreThing"],
    "@DayZ-Expansion-Vehicles": ["ExpVehicle"],
    "@DayZ-Expansion-Weapons": ["ExpGun", "ExpMag"],
    "@DayZ-Expansion-Market": ["MarketTruck"],
}


def _write_template(root: Path, name: str, *, complete: bool = True, maps: bool = True) -> Path:
    base = root / ("" if complete else eo.INCOMPLETE_DIR) / name
    ce = base / eo.CE_SUBDIR
    ce.mkdir(parents=True)
    (ce / "expansion_types.xml").write_text(TYPES)
    (ce / "expansion_spawnabletypes.xml").write_text(SPAWNABLETYPES)
    (ce / "expansion_events.xml").write_text(EVENTS)
    (base / eo.EVENTSPAWNS_FILE).write_text(EVENTSPAWNS)
    if maps:
        for sub in eo.MAP_FOLDERS:
            folder = base / "expansion" / sub
            folder.mkdir(parents=True)
            (folder / f"{name}_{sub}.map").write_text("ExpansionTraderJudy|1 2 3|0 0 0\n")
    return base


@pytest.fixture()
def templates(tmp_path: Path) -> Path:
    root = tmp_path / "expansion-templates"
    _write_template(root, "Chernarus")
    _write_template(root, "Enoch", maps=False)
    _write_template(root, "DeerIsle", complete=False, maps=False)
    # Namalsk ships two templates in one folder.
    _write_template(root / "Namalsk", "Regular", maps=False)
    return root


@pytest.fixture()
def config(sample_config, tmp_mission_dir: Path, templates: Path, monkeypatch):
    sample_config.expansion_template_dir = str(templates)
    (tmp_mission_dir / "db" / "types.xml").write_text(MISSION_TYPES)
    (tmp_mission_dir / "db" / "events.xml").write_text(MISSION_EVENTS)
    # Every stock mission ships both; the direct merge splices into the first
    # and the CE registration lands in the second.
    (tmp_mission_dir / "cfgeventspawns.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>\n<eventposdef>\n</eventposdef>\n'
    )
    (tmp_mission_dir / "cfgeconomycore.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>\n'
        "<economycore>\n\t<classes />\n\t<defaults />\n</economycore>\n"
    )
    write_test_mods(sample_config, ['@DayZ-Expansion-Core', '@DayZ-Expansion-Vehicles', '@DayZ-Expansion-Weapons'])

    def _classes(server_dir, mod_list, sections=(), prune=True):
        return {m: MOD_CLASSES[m] for m in mod_list if m in MOD_CLASSES}

    monkeypatch.setattr(
        "app.modmgr.mod_conflict.extract_mod_classnames_cached", _classes
    )
    return sample_config


# ── templates on disk ──────────────────────────────────────────────


def test_list_templates_reports_nesting_completeness_and_terrain(config):
    found = {t.name: t for t in eo.list_templates(config)}
    assert set(found) == {"Chernarus", "Enoch", "Namalsk/Regular", "DeerIsle"}
    assert found["Chernarus"].terrain == "chernarusplus"
    # Enoch is the terrain Livonia ships under — resolved through the registry,
    # not a second map list.
    assert found["Enoch"].terrain == "enoch"
    assert found["Namalsk/Regular"].terrain == "namalsk"
    assert found["DeerIsle"].complete is False
    assert found["Chernarus"].complete is True
    assert found["Chernarus"].has_map_folders is True
    assert found["Enoch"].has_map_folders is False


def test_find_template_matches_the_mission_terrain_and_skips_incomplete(config):
    assert eo.find_template(config, "chernarusplus").name == "Chernarus"
    assert eo.find_template(config, "enoch").name == "Enoch"
    # DeerIsle is a 0_INCOMPLETE template: listed, never auto-matched.
    assert eo.find_template(config, "deerisle") is None
    assert eo.find_template(config, "") is None


def test_template_root_defaults_under_the_config_dir(sample_config):
    from app.shared.paths import CONFIG_DIR

    sample_config.expansion_template_dir = ""
    assert eo.template_root(sample_config) == CONFIG_DIR / "expansion-templates"
    assert eo.list_templates(sample_config) == []


# ── downloading empties only zsm's own folder ────────────────────


@pytest.fixture()
def fake_download(monkeypatch, tmp_path):
    """Stands in for the GitHub zip: one Chernarus template, unpacked by the real
    `download_and_extract_mission`, so its unpack-beside-then-swap is what runs."""
    import zipfile

    from app.maps import map_ops

    source = _write_template(tmp_path / "zip-source" / eo.TEMPLATE_SUBDIR, "Chernarus").parent
    calls: list[Path] = []

    def _archive(zip_url, zip_path):
        with zipfile.ZipFile(zip_path, "w") as zf:
            for f in sorted(source.rglob("*")):
                if f.is_file():
                    zf.write(f, f"repo-master/{eo.TEMPLATE_SUBDIR}/{f.relative_to(source)}")

    real = map_ops.download_and_extract_mission

    def _download(zip_url, mission_path, dest_dir, log=None, **kw):
        calls.append(dest_dir)
        return real(zip_url, mission_path, dest_dir, log=log, **kw)

    monkeypatch.setattr(map_ops, "_download_archive", _archive)
    monkeypatch.setattr(map_ops, "download_and_extract_mission", _download)
    return calls


def _someone_elses_folder(path: Path) -> Path:
    path.mkdir(parents=True)
    (path / "notes.txt").write_text("keep me")
    _write_template(path, "Chernarus")
    return path


def test_download_refuses_a_folder_it_did_not_fill(sample_config, tmp_path, fake_download):
    folder = _someone_elses_folder(tmp_path / "box" / "mine")
    sample_config.expansion_template_dir = str(folder)

    with pytest.raises(eo.TemplateDirRefused) as e:
        eo.download_templates(sample_config)

    assert str(folder) in str(e.value)
    assert (folder / "notes.txt").read_text() == "keep me"
    assert fake_download == []


def test_download_refuses_a_folder_of_missions(sample_config, tmp_path, fake_download):
    """Every mission folder carries a cfgeventspawns.xml; db/ is what tells them apart."""
    mpmissions = tmp_path / "box" / "mpmissions"
    mission = mpmissions / "dayzOffline.chernarusplus"
    (mission / "db").mkdir(parents=True)
    (mission / "db" / "types.xml").write_text("<types/>")
    (mission / eo.EVENTSPAWNS_FILE).write_text("<eventposdef/>")
    sample_config.expansion_template_dir = str(mpmissions)

    assert eo.template_dir_refusal(sample_config)
    with pytest.raises(eo.TemplateDirRefused):
        eo.download_templates(sample_config)
    assert (mission / "db" / "types.xml").is_file()


def test_download_marks_its_folder_and_replaces_it_next_time(
    sample_config, tmp_path, fake_download
):
    folder = tmp_path / "box" / "templates"
    sample_config.expansion_template_dir = str(folder)

    eo.download_templates(sample_config)
    assert (folder / eo.TEMPLATE_MARKER).is_file()

    (folder / "left-behind.txt").write_text("stale")
    eo.download_templates(sample_config)

    assert not (folder / "left-behind.txt").exists()
    assert (folder / eo.TEMPLATE_MARKER).is_file()
    assert [t.name for t in eo.list_templates(sample_config)] == ["Chernarus"]
    assert fake_download == [folder, folder]


def test_download_empties_an_empty_folder(sample_config, tmp_path, fake_download):
    folder = tmp_path / "box" / "empty"
    folder.mkdir(parents=True)
    sample_config.expansion_template_dir = str(folder)

    eo.download_templates(sample_config)
    assert (folder / eo.TEMPLATE_MARKER).is_file()


def test_download_replaces_templates_an_older_zsm_left_unmarked(config, templates, fake_download):
    """The fixture is the download's layout, nested Namalsk and 0_INCOMPLETE included."""
    assert not (templates / eo.TEMPLATE_MARKER).exists()

    eo.download_templates(config)

    assert [t.name for t in eo.list_templates(config)] == ["Chernarus"]
    assert (templates / eo.TEMPLATE_MARKER).is_file()


def test_download_replaces_an_older_download_with_upstreams_readme(
    config, templates, fake_download
):
    """Upstream's Template/0_INCOMPLETE/ holds a README.txt beside its templates,
    so every real older download has one."""
    (templates / eo.INCOMPLETE_DIR / "README.txt").write_text("unfinished maps")

    eo.download_templates(config)

    assert (templates / eo.TEMPLATE_MARKER).is_file()


def test_download_refuses_other_loose_files_beside_templates(config, templates, fake_download):
    (templates / eo.INCOMPLETE_DIR / "my-notes.txt").write_text("keep me")

    with pytest.raises(eo.TemplateDirRefused):
        eo.download_templates(config)

    assert (templates / eo.INCOMPLETE_DIR / "my-notes.txt").read_text() == "keep me"


def test_download_always_replaces_zsm_own_folder(sample_config, fake_download):
    from app.shared.paths import CONFIG_DIR

    sample_config.expansion_template_dir = ""
    own = CONFIG_DIR / "expansion-templates"
    own.mkdir(parents=True)
    (own / "anything.txt").write_text("x")

    eo.download_templates(sample_config)

    assert not (own / "anything.txt").exists()
    assert fake_download == [own]


def test_a_failed_download_keeps_the_old_templates(config, templates, fake_download, monkeypatch):
    from app.maps import map_ops

    def _fail(zip_url, zip_path):
        raise OSError("network down")

    monkeypatch.setattr(map_ops, "_download_archive", _fail)
    before = [t.name for t in eo.list_templates(config)]

    with pytest.raises(OSError):
        eo.download_templates(config)

    assert [t.name for t in eo.list_templates(config)] == before
    assert not [p for p in templates.parent.iterdir() if p.name.startswith(".")]


def test_download_replaces_a_symlinked_template_folder(sample_config, tmp_path, fake_download):
    real = tmp_path / "box" / "real-templates"
    real.mkdir(parents=True)
    (real / eo.TEMPLATE_MARKER).write_text("x")
    (real / "left-behind.txt").write_text("stale")
    link = tmp_path / "link-templates"
    link.symlink_to(real, target_is_directory=True)
    sample_config.expansion_template_dir = str(link)

    eo.download_templates(sample_config)

    assert link.is_symlink()
    assert not (real / "left-behind.txt").exists()
    assert [t.name for t in eo.list_templates(sample_config)] == ["Chernarus"]


# ── the filter ─────────────────────────────────────────────────────


def _plan(config, **kw):
    return eo.build_plan(config, eo.find_template(config, "chernarusplus"), **kw)


def test_installed_modules_comes_from_mods_txt(config):
    assert eo.installed_modules(config) == [CORE, VEHICLES, WEAPONS]


def test_types_keep_module_and_vanilla_classes_and_drop_the_rest(config):
    plan = _plan(config)
    kept = [t.get("name") for t in ET.fromstring(plan.content["types"]).findall("type")]
    assert kept == ["ExpVehicle", "ExpGun", "WoodenLog"]
    assert plan.kept["types"] == 3 and plan.total["types"] == 4
    # WoodenLog is the mission's own class, so it is reported as vanilla.
    assert plan.per_module == {"Vehicles": 1, "Weapons": 1, "vanilla": 1}
    dropped = [d for d in plan.dropped if d.kind == "types"]
    assert [(d.name, d.owner, d.reason) for d in dropped] == [
        ("Expansion_Longhorn", "", "nothing installed defines it")
    ]


def test_a_class_only_a_deselected_module_defines_names_that_module(config):
    # Market is installed, but this preview asks what Core+Vehicles alone give.
    write_test_mods(config, ['@DayZ-Expansion-Core', '@DayZ-Expansion-Vehicles', '@DayZ-Expansion-Market'])
    plan = _plan(config, module_ids=[CORE, VEHICLES])
    reasons = {d.name: (d.owner, d.reason) for d in plan.dropped}
    assert reasons["MarketTruck"] == ("Market", "needs Market")


def test_attachments_are_filtered_and_an_emptied_holder_goes_with_them(config):
    plan = _plan(config)
    root = ET.fromstring(plan.content["spawnabletypes"])
    assert [t.get("name") for t in root.findall("type")] == ["ExpGun"]
    # The second <attachments> held only an undefined magazine, so it is gone
    # rather than left as an empty element.
    assert len(root.findall("type/attachments")) == 1
    assert [i.get("name") for i in root.iter("item")] == ["ExpMag"]
    dropped = {(d.element, d.name) for d in plan.dropped if d.kind == "spawnabletypes"}
    assert dropped == {
        ("type", "Expansion_LAW"),
        ("item", "M4Suppressor"),
        ("item", "Mag_STANAGCoupled_30Rnd"),
    }


def test_events_drop_undefined_children_and_the_events_left_empty(config):
    plan = _plan(config)
    root = ET.fromstring(plan.content["events"])
    assert [e.get("name") for e in root.findall("event")] == [
        "VehicleHatchback02",
        "ExpansionHeli",
    ]
    # Market is not installed, so its truck event has nothing left to spawn.
    assert ("event", "ExpansionMarketTruck") in {
        (d.element, d.name) for d in plan.dropped if d.kind == "events"
    }


def test_an_event_that_overrides_a_stock_one_is_named(config):
    plan = _plan(config)
    assert plan.overrides == ["VehicleHatchback02"]
    assert plan.new_events == ["ExpansionHeli"]


def test_spawn_positions_follow_their_events(config):
    plan = _plan(config)
    spawns = [e.get("name") for e in ET.fromstring(plan.content["cfgeventspawns"]).findall("event")]
    # ExpansionHeli survived; StaticHeliCrash is the mission's own event;
    # ExpansionMarketTruck's event was dropped, so its positions go too —
    # positions for an event that doesn't exist is a log finding of its own.
    assert spawns == ["ExpansionHeli", "StaticHeliCrash"]
    assert [d.reason for d in plan.dropped if d.kind == "cfgeventspawns"] == [
        "its event is not in the mission"
    ]


def test_the_map_folders_need_market(config):
    plan = _plan(config)
    assert plan.map_files == []
    assert any("Market" in w for w in plan.warnings)

    write_test_mods(config, ['@DayZ-Expansion-Core', '@DayZ-Expansion-Market'])
    plan = _plan(config)
    assert plan.map_files == [
        "expansion/objects/Chernarus_objects.map",
        "expansion/traders/Chernarus_traders.map",
    ]


def test_no_modules_installed_is_a_warning_not_a_crash(config):
    write_test_mods(config, [])
    plan = _plan(config)
    assert plan.modules == []
    assert any("No Expansion modules are installed" in w for w in plan.warnings)
    # Only the mission's own class survives.
    kept = [t.get("name") for t in ET.fromstring(plan.content["types"]).findall("type")]
    assert kept == ["WoodenLog"]


def test_orphans_are_the_names_nothing_defines(config):
    plan = _plan(config)
    # Market is not installed here, so its truck is an orphan too: nothing on
    # this server defines it, which is exactly what the list claims.
    assert plan.orphans == [
        "Expansion_Longhorn",
        "M4Suppressor",
        "Mag_STANAGCoupled_30Rnd",
        "Expansion_LAW",
        "MarketTruck",
        "ExpansionMarketTruck",
    ]
    assert plan.summary()["dropped_count"] == len(plan.dropped)


ENTITY_TYPES = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE types [<!ENTITY gun "ExpGun">]>
<types>
    <type name="&gun;"><nominal>3</nominal></type>
</types>
"""


def test_a_template_file_that_declares_entities_is_not_used(config):
    # The template is downloaded from a third-party repository, so it is parsed
    # like every other XML zsm did not write: entity declarations are refused.
    template = eo.find_template(config, "chernarusplus")
    (template.path / eo.CE_SUBDIR / "expansion_types.xml").write_text(ENTITY_TYPES)
    plan = eo.build_plan(config, template)
    assert "types" not in plan.content
    assert "types" not in plan.total
    # The other template files still go through.
    assert "events" in plan.content


def test_a_mission_file_that_declares_entities_counts_as_unreadable(tmp_mission_dir: Path):
    (tmp_mission_dir / "db" / "types.xml").write_text(ENTITY_TYPES)
    (tmp_mission_dir / "db" / "events.xml").write_text(
        '<!DOCTYPE events [<!ENTITY e "StaticHeliCrash">]>\n<events><event name="&e;"/></events>\n'
    )
    assert eo.mission_class_names(tmp_mission_dir) == set()
    assert eo.mission_event_names(tmp_mission_dir) == set()


# ── applying ───────────────────────────────────────────────────────


def test_apply_writes_manual_records_and_the_mission_files(config, tmp_mission_dir: Path):
    from app.xml import xml_manual

    plan = _plan(config)
    result = eo.apply_plan(config, plan)

    assert result["files"] == [
        "expansion-types",
        "expansion-spawnabletypes",
        "expansion-events",
        "expansion-eventspawns",
    ]
    assert result["applied"] == result["files"]

    # The CE half: a custom/ copy plus a registration in cfgeconomycore.xml.
    custom = sorted(p.name for p in (tmp_mission_dir / "custom").glob("*.xml"))
    assert custom == [
        "user-expansion-events.xml",
        "user-expansion-spawnabletypes.xml",
        "user-expansion-types.xml",
    ]
    core = (tmp_mission_dir / "cfgeconomycore.xml").read_text()
    assert 'folder="custom"' in core and "user-expansion-types.xml" in core

    # The direct half: spliced into cfgeventspawns.xml inside markers.
    spawns = (tmp_mission_dir / "cfgeventspawns.xml").read_text()
    assert "START MOD: @user:expansion-eventspawns" in spawns
    assert "ExpansionHeli" in spawns

    # Records are scoped to the map they came from, so switching maps does not
    # apply Chernarus positions to Livonia.
    assert xml_manual.get_file(
        "expansion-types", manual_dir=manual_dir(),
    )["terrain"] == "chernarusplus"
    assert xml_manual.get_file(
        "expansion-types", manual_dir=manual_dir(),
    )["mod"] == eo.MANUAL_FILE_MOD


def test_apply_is_idempotent(config):
    from app.xml import xml_manual

    plan = _plan(config)
    eo.apply_plan(config, plan)
    eo.apply_plan(config, plan)
    ids = [f["id"] for f in xml_manual.load_files(manual_dir=manual_dir())]
    assert ids == sorted(set(ids), key=ids.index)  # no duplicates
    assert len(ids) == 4


def test_a_kind_with_no_entries_gets_no_file_and_loses_an_old_one(config, tmp_mission_dir: Path):
    """An empty expansion-events/-eventspawns file made every later merge warn
    "contributed nothing" (browser test 2026-11-17, F2)."""
    from app.xml import xml_manual

    eo.apply_plan(config, _plan(config))
    plan = _plan(config)
    plan.kept["events"] = plan.kept["cfgeventspawns"] = 0
    lines: list[str] = []
    result = eo.apply_plan(config, plan, log=lines.append)

    assert result["files"] == ["expansion-types", "expansion-spawnabletypes"]
    ids = [f["id"] for f in xml_manual.load_files(manual_dir=manual_dir())]
    assert ids == ["expansion-types", "expansion-spawnabletypes"]
    assert not (tmp_mission_dir / "custom" / "user-expansion-events.xml").exists()
    assert "user-expansion-events.xml" not in (tmp_mission_dir / "cfgeconomycore.xml").read_text()
    assert "@user:expansion-eventspawns" not in (tmp_mission_dir / "cfgeventspawns.xml").read_text()
    assert any("Removed manual XML file expansion-events" in line for line in lines)


def test_a_first_apply_writes_no_file_for_an_empty_kind(config):
    from app.xml import xml_manual

    plan = _plan(config)
    plan.kept["events"] = 0
    eo.apply_plan(config, plan)
    assert xml_manual.get_file("expansion-events", manual_dir=manual_dir()) is None


def test_apply_copies_the_map_files_only_when_they_are_in_the_plan(config, tmp_mission_dir: Path):
    write_test_mods(config, ['@DayZ-Expansion-Core', '@DayZ-Expansion-Market'])
    plan = _plan(config)
    result = eo.apply_plan(config, plan)
    assert result["copied"] == plan.map_files
    assert (tmp_mission_dir / "expansion" / "traders" / "Chernarus_traders.map").is_file()


def test_apply_refuses_without_a_mission(config):
    config.server_cfg_path.write_text('hostname = "x";\n')
    plan = _plan(config)
    with pytest.raises(ValueError):
        eo.apply_plan(config, plan)


def test_apply_refuses_a_mission_with_no_cfgeconomycore(config, tmp_mission_dir: Path):
    # The CE half would copy four files into custom/ and register none of them,
    # which reads as a successful apply that loads nothing.
    (tmp_mission_dir / "cfgeconomycore.xml").unlink()
    with pytest.raises(ValueError, match="cfgeconomycore"):
        eo.apply_plan(config, _plan(config))


def test_applied_files_and_remove_are_the_undo(config, tmp_mission_dir: Path):
    eo.apply_plan(config, _plan(config))
    assert [f["kind"] for f in eo.applied_files(config)] == [
        "types",
        "spawnabletypes",
        "events",
        "cfgeventspawns",
    ]

    removed = eo.remove_applied(config)
    assert len(removed) == 4
    assert eo.applied_files(config) == []
    # The artifacts go with the records — otherwise the mission keeps serving
    # content whose record is gone.
    assert list((tmp_mission_dir / "custom").glob("user-expansion-*.xml")) == []
    assert "START MOD: @user:expansion-eventspawns" not in (
        tmp_mission_dir / "cfgeventspawns.xml"
    ).read_text()


def test_apply_logs_every_drop_and_the_overrides(config):
    lines: list[str] = []
    eo.apply_plan(config, _plan(config), log=lines.append)
    text = "\n".join(lines)
    assert "VehicleHatchback02" in text
    assert "Dropped 7 entr" in text


def test_a_missing_settings_file_names_the_installed_modules(sample_config, monkeypatch):
    """Expansion writes the settings of its installed modules only, so "start the
    server once" sent an admin into a restart that never wrote the file."""
    monkeypatch.setattr(eo, "installed_modules", lambda config: ["2291785308", "2792982069"])

    text = eo.missing_settings_message(sample_config, "GeneralSettings.json")

    assert "only when the module that uses it is installed" in text
    assert "Installed modules: Core, AI." in text


def test_with_no_module_the_text_names_none(sample_config, monkeypatch):
    monkeypatch.setattr(eo, "installed_modules", lambda config: [])

    assert "Installed modules" not in eo.missing_settings_message(sample_config, "GeneralSettings.json")
