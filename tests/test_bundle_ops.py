"""Tests for app/ops/bundle_ops.py — the shared export used by the CLI and the
web export job."""

from __future__ import annotations

import os
import stat
import zipfile

import pytest

from app.commands.bundle import _bundle_import
from app.ops import bundle_ops
from app.parsers.battleye_cfg import BeserverCfgInUse
from app.shared.platform import IS_WINDOWS
from tests.conftest import installed_names, set_test_flags, write_test_mods


def _config(sample_config, tmp_path, tmp_server_dir):
    sample_config.server_dir = str(tmp_server_dir)
    sample_config.bundle_export_dir = str(tmp_path / "bundles")
    return sample_config


def test_export_bundle_logs_every_entry(sample_config, tmp_path, tmp_server_dir):
    config = _config(sample_config, tmp_path, tmp_server_dir)
    lines: list[str] = []

    zip_path = bundle_ops.export_bundle(config, log=lines.append)

    assert zip_path is not None and zip_path.is_file()
    assert zipfile.is_zipfile(zip_path)
    assert lines[0].startswith("Creating bundle:")
    assert f"Bundle exported to {zip_path}" in lines[-2:]
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            assert f"  + {name}" in lines


def test_the_mod_list_travels_in_the_bundle(sample_config, tmp_path, tmp_server_dir):
    """mods.json goes in whole, flags and load order with it (plans/77), and
    an import makes it this instance's list."""
    config = _config(sample_config, tmp_path, tmp_server_dir)
    write_test_mods(config, ["@CF", "@Made-Up"], disabled=["@Made-Up"], manual=True, pinned=["@CF"])
    zip_path = bundle_ops.export_bundle(config)
    with zipfile.ZipFile(zip_path) as zf:
        data = zf.read(bundle_ops.MODS_ARC_NAME)
        names = bundle_ops.file_entries(zf)
    assert "mods.txt" not in names and "cached-mod-ids.txt" not in names

    write_test_mods(config, [])
    line = bundle_ops.import_mod_list(config, names, data)
    assert line == "+ mods.json (2 mod(s))"
    from app.modmgr import modlist

    ml = modlist.load(config)
    assert ml.names() == ["@CF", "@Made-Up"] and ml.disabled() == {"@Made-Up"}
    assert ml.manual and ml.pinned == ["@CF"]


def test_a_bundle_mod_list_zsm_cannot_read_changes_nothing(sample_config):
    write_test_mods(sample_config, ["@Mine"])
    line = bundle_ops.import_mod_list(sample_config, ["mods.json"], b'{"version": 1, "mods": [{"name": "../x"}]}')
    assert line.startswith("- mods.json was not imported: ")
    assert installed_names(sample_config) == ["@Mine"]


def test_export_bundle_lines_carry_no_rich_markup(sample_config, tmp_path, tmp_server_dir):
    """The web job log renders these raw — a `[dim]` would show up literally."""
    config = _config(sample_config, tmp_path, tmp_server_dir)
    lines: list[str] = []

    bundle_ops.export_bundle(config, log=lines.append)

    assert not any("[" in line and "]" in line for line in lines), lines


def test_export_bundle_cancel_removes_partial_zip(sample_config, tmp_path, tmp_server_dir):
    config = _config(sample_config, tmp_path, tmp_server_dir)

    result = bundle_ops.export_bundle(config, should_cancel=lambda: True)

    assert result is None
    assert list(bundle_ops.get_bundle_dir(config).glob("*.zip")) == []


def test_bundle_zip_path_increments(sample_config, tmp_path, tmp_server_dir):
    config = _config(sample_config, tmp_path, tmp_server_dir)
    first = bundle_ops.bundle_zip_path(config)
    first.parent.mkdir(parents=True, exist_ok=True)
    first.write_bytes(b"PK")

    second = bundle_ops.bundle_zip_path(config)

    assert second != first
    assert second.name == f"{first.stem}-2.zip"


@pytest.mark.parametrize("name", ["cfgEffectArea.json", "cfgeffectarea.json"])
def test_the_mission_json_files_go_in_under_their_name_on_disk(
    sample_config, tmp_path, tmp_server_dir, name
):
    """The Linux server download has cfgeffectarea.json in lowercase, on 1.29 and
    1.30 alike (measured in 2026 Q4, plans/66). An exact-name lookup for the CE
    repo's cfgEffectArea.json found nothing there, so a Linux bundle left the
    file out."""
    config = _config(sample_config, tmp_path, tmp_server_dir)
    mission = tmp_server_dir / "mpmissions" / "dayzOffline.chernarusplus"
    mission.mkdir(parents=True, exist_ok=True)
    (mission / name).write_text("{}")
    (mission / "cfggameplay.json").write_text("{}")

    arcs = [f.arc_name for f in bundle_ops.collect_export_manifest(config).included]

    assert f"mpmissions/dayzOffline.chernarusplus/{name}" in arcs
    assert "mpmissions/dayzOffline.chernarusplus/cfggameplay.json" in arcs


def test_the_top_level_files_the_panel_edits_go_in(sample_config, tmp_path, tmp_server_dir):
    """A bundle had none of cfgplayerspawnpoints.xml,
    cfgweather.xml, the limits definitions, cfgenvironment.xml,
    cfgignorelist.xml, cfgrandompresets.xml or init.c, so a migration lost
    their edits. The stock mapgroup files stay out."""
    config = _config(sample_config, tmp_path, tmp_server_dir)
    mission = tmp_server_dir / "mpmissions" / "dayzOffline.chernarusplus"
    mission.mkdir(parents=True, exist_ok=True)
    for name in bundle_ops.MISSION_TOP_FILES + ["mapgroupproto.xml", "mapgrouppos.xml"]:
        (mission / name).write_text("<x/>")

    arcs = {f.arc_name for f in bundle_ops.collect_export_manifest(config).included}

    for name in bundle_ops.MISSION_TOP_FILES:
        assert f"mpmissions/dayzOffline.chernarusplus/{name}" in arcs, name
    assert "mpmissions/dayzOffline.chernarusplus/mapgroupproto.xml" not in arcs
    assert "mpmissions/dayzOffline.chernarusplus/mapgrouppos.xml" not in arcs


def test_the_underground_triggers_go_in(sample_config, tmp_path, tmp_server_dir):
    """Every stock mission ships cfgundergroundtriggers.json (the underground
    areas' eye-accommodation and ambient-sound triggers,
    dayz-docs/wiki/pages/underground-areas-configuration.wiki), and a bundle
    used to leave it out."""
    config = _config(sample_config, tmp_path, tmp_server_dir)
    mission = tmp_server_dir / "mpmissions" / "dayzOffline.chernarusplus"
    mission.mkdir(parents=True, exist_ok=True)
    (mission / "cfgundergroundtriggers.json").write_text("{}")

    arcs = [f.arc_name for f in bundle_ops.collect_export_manifest(config).included]

    assert "mpmissions/dayzOffline.chernarusplus/cfgundergroundtriggers.json" in arcs


def test_the_expansion_configuration_goes_in_and_its_player_data_stays_out(sample_config, tmp_path, tmp_server_dir):
    """A bundle carried no Expansion file at all, so moving an Expansion server
    lost every setting, trader zone and market price. Its bank accounts (ATM),
    logs and P2P market listings are player data and stay out."""
    config = _config(sample_config, tmp_path, tmp_server_dir)
    mission = tmp_server_dir / "mpmissions" / "dayzOffline.chernarusplus"
    profiles = tmp_server_dir / config.profiles_path / "ExpansionMod"
    files = {
        mission / "expansion" / "settings" / "SafeZoneSettings.json": True,
        mission / "expansion" / "traderzones" / "Green.json": True,
        mission / "expansion" / "objects" / "camp.map": True,
        mission / "expansion" / "p2pmarket" / "listing.json": False,
        profiles / "Settings" / "GeneralSettings.json": True,
        profiles / "Market" / "Ammo.json": True,
        profiles / "Traders" / "Gunsmith.json": True,
        profiles / "Loadouts" / "EastLoadout.json": True,
        profiles / "AI" / "LootDrops" / "Drop.json": True,
        profiles / "ATM" / "76561198000000000.json": False,
        profiles / "Logs" / "ExpLog.log": False,
    }
    for path in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}")

    arcs = {f.arc_name for f in bundle_ops.collect_export_manifest(config).included}

    for path, wanted in files.items():
        arc = path.relative_to(tmp_server_dir).as_posix()
        assert (arc in arcs) is wanted, arc


def test_the_subfolder_xml_goes_in_with_windows_paths(sample_config, tmp_path, tmp_server_dir, monkeypatch):
    """The custom/ and expansion/ files were matched on str() of their path,
    which holds backslashes on Windows, so "/" was never in it and a Windows
    bundle left every one of them out. relative_to is made to return a
    Windows path here, as it does there."""
    import pathlib

    config = _config(sample_config, tmp_path, tmp_server_dir)
    mission = tmp_server_dir / "mpmissions" / "dayzOffline.chernarusplus"
    (mission / "custom").mkdir(parents=True, exist_ok=True)
    (mission / "custom" / "mod_types.xml").write_text("<types/>")
    (mission / "db").mkdir(exist_ok=True)
    (mission / "db" / "types.xml").write_text("<types/>")
    real = pathlib.PurePath.relative_to
    monkeypatch.setattr(
        pathlib.PurePath, "relative_to",
        lambda self, *other, **kw: pathlib.PureWindowsPath(real(self, *other, **kw).as_posix()),
    )

    arcs = [f.arc_name for f in bundle_ops.collect_export_manifest(config).included]

    assert "mpmissions/dayzOffline.chernarusplus/custom/mod_types.xml" in arcs
    assert arcs.count("mpmissions/dayzOffline.chernarusplus/db/types.xml") == 1


def test_a_missing_mission_folder_is_not_an_error(sample_config, tmp_path, tmp_server_dir):
    config = _config(sample_config, tmp_path, tmp_server_dir)
    manifest = bundle_ops.collect_export_manifest(config)
    assert not any("cfgeffectarea" in f.arc_name.lower() for f in manifest.included)


def _import_with_cli(config, zip_path, target_mission=None):
    config.save()
    _bundle_import(zip_path, ni=True, target_mission=target_mission)
    return []


def _import_with_web(config, zip_path, target_mission=None):
    from app.web.routers.backups import _import_bundle_sync

    lines: list[str] = []
    _import_bundle_sync(config, zip_path, target_mission, lines.append)
    return lines


@pytest.mark.parametrize("importer", [_import_with_cli, _import_with_web])
def test_a_windows_bundle_writes_over_the_linux_casing(
    sample_config, tmp_path, tmp_server_dir, importer
):
    """A Windows bundle carries cfgEffectArea.json, the Linux mission has
    cfgeffectarea.json. Writing the bundle's name beside it left two files."""
    config = _config(sample_config, tmp_path, tmp_server_dir)
    mission = tmp_server_dir / "mpmissions" / "dayzOffline.enoch"
    mission.mkdir(parents=True)
    (mission / "cfgeffectarea.json").write_text("server")
    zip_path = tmp_path / "windows-bundle.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("mpmissions/dayzOffline.chernarusplus/cfgEffectArea.json", "bundle")
        zf.writestr("mpmissions/dayzOffline.chernarusplus/cfgGameplay.json", "new")

    lines = importer(config, zip_path, target_mission="dayzOffline.enoch")

    assert sorted(p.name for p in mission.iterdir()) == ["cfgGameplay.json", "cfgeffectarea.json"]
    assert (mission / "cfgeffectarea.json").read_text() == "bundle"
    if lines:
        assert "  + mpmissions/dayzOffline.enoch/cfgeffectarea.json" in lines


def _bundle_with_username(tmp_path, username):
    import json

    zip_path = tmp_path / "shared-bundle.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("config.json", json.dumps({"steam_username": username, "server_name": "Shared"}))
    return zip_path


@pytest.mark.parametrize("username", ["+quit", "two words", "anonymous", 7])
def test_a_bundle_username_its_setter_would_refuse_is_not_imported(username):
    updates, refused = bundle_ops.importable_config(
        {"steam_username": username, "server_name": "Shared"}
    )
    assert updates == {"server_name": "Shared"}
    assert len(refused) == 1 and refused[0].startswith("steam_username was not imported")


@pytest.mark.parametrize("username", ["real_owner", ""])
def test_a_bundle_username_that_is_fine_is_imported(username):
    updates, refused = bundle_ops.importable_config({"steam_username": username})
    assert updates == {"steam_username": username}
    assert refused == []


@pytest.mark.parametrize(
    "name", ['X";\nverifySignatures = 0;\nhostname = "X', "A\u2028B", "A\x0bB", 'Say "hi"', 7]
)
def test_a_bundle_server_name_the_cfg_writer_would_refuse_is_not_imported(name):
    """server_name becomes serverDZ.cfg's quoted hostname at every container
    start and every reinstall (plans/68 LB-6)."""
    updates, refused = bundle_ops.importable_config({"server_name": name, "check_steam_login": True})
    assert updates == {"check_steam_login": True}
    assert len(refused) == 1 and refused[0].startswith("server_name was not imported: it ")
    assert "verifySignatures" not in refused[0]


@pytest.mark.parametrize("name", ["My Server", "[EU] PvE #1 | Traders\tLoot", ""])
def test_a_bundle_server_name_that_is_fine_is_imported(name):
    assert bundle_ops.importable_config({"server_name": name}) == ({"server_name": name}, [])


# A '%' or '"' is launch-command syntax for bash and cmd alike, quoted or not.
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("config_path", 'serverDZ.cfg" -mod="@x'),
        ("profiles_path", "profiles%PATH%"),
        ("battleye_path", 'battleye" "-BEpath=elsewhere'),
        ("server_args", "-dologs & calc"),
        ("profiles_path", 7),
    ],
)
def test_a_launch_value_the_run_command_save_would_refuse_is_not_imported(
    sample_config, field, value
):
    updates, refused = bundle_ops.importable_config(
        {field: value, "server_name": "Shared"}, sample_config
    )
    assert updates == {"server_name": "Shared"}
    assert len(refused) == 1 and refused[0].startswith(f"{field} was not imported: ")


def test_a_normal_bundle_still_migrates_every_field(sample_config):
    bundle_cfg = {
        "server_name": "Shared",
        "server_args": "-dologs -adminlog -netlog -freezecheck -cpuCount=4",
        "server_port": 2402,
        "steam_username": "real_owner",
        "battleye_path": "battleye",
        "profiles_path": "My Profiles",
        "config_path": "serverDZ.cfg",
        "check_steam_login": True,
    }
    assert set(bundle_cfg) == bundle_ops.IMPORTABLE_CONFIG_FIELDS

    updates, refused = bundle_ops.importable_config(bundle_cfg, sample_config)

    assert updates == bundle_cfg
    assert refused == []


@pytest.mark.skipif(IS_WINDOWS, reason="';' is launch syntax for bash only")
def test_typed_server_mods_follow_the_install_they_are_imported_into(sample_config):
    """With a mod marked server-side, the launch builder moves typed -serverMod=
    entries into its own quoted flag, so their ';' is no longer shell syntax."""
    bundle_cfg = {"server_args": "-dologs -serverMod=@A;@B"}

    assert bundle_ops.importable_config(bundle_cfg, sample_config)[0] == {}
    set_test_flags(sample_config, server_only=["@A"])
    assert bundle_ops.importable_config(bundle_cfg, sample_config)[0] == bundle_cfg


def _bundle_with_launch_values(tmp_path):
    import json

    zip_path = tmp_path / "shared-bundle.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(
            "config.json",
            json.dumps(
                {
                    "server_name": "Shared",
                    "profiles_path": "profiles%PATH%",
                    "server_args": "-dologs & calc",
                }
            ),
        )
    return zip_path


def test_the_web_import_logs_the_refused_launch_values(sample_config, tmp_path, tmp_server_dir):
    from app.web.routers.backups import _import_bundle_sync

    config = _config(sample_config, tmp_path, tmp_server_dir)
    lines: list[str] = []
    result = _import_bundle_sync(config, _bundle_with_launch_values(tmp_path), None, lines.append)
    assert result["config_updates"] == {"server_name": "Shared"}
    assert any(line.startswith("  - profiles_path was not imported") for line in lines)
    assert any(line.startswith("  - server_args was not imported") for line in lines)


def test_the_cli_import_keeps_the_configured_launch_values(sample_config, tmp_path, tmp_server_dir):
    from app.shared.config import DayzConfig

    config = _config(sample_config, tmp_path, tmp_server_dir)
    config.profiles_path = "profiles"
    config.server_args = "-dologs"
    _import_with_cli(config, _bundle_with_launch_values(tmp_path))
    reloaded = DayzConfig.load()
    assert (reloaded.profiles_path, reloaded.server_args) == ("profiles", "-dologs")
    assert reloaded.server_name == "Shared"


def test_the_web_import_logs_the_refused_username(sample_config, tmp_path, tmp_server_dir):
    from app.web.routers.backups import _import_bundle_sync

    config = _config(sample_config, tmp_path, tmp_server_dir)
    lines: list[str] = []
    result = _import_bundle_sync(config, _bundle_with_username(tmp_path, "+quit"), None, lines.append)
    assert result["config_updates"] == {"server_name": "Shared"}
    assert any(line.startswith("  - steam_username was not imported") for line in lines)


def test_the_cli_import_keeps_the_configured_username(sample_config, tmp_path, tmp_server_dir):
    from app.shared.config import DayzConfig

    config = _config(sample_config, tmp_path, tmp_server_dir)
    config.steam_username = "real_owner"
    _import_with_cli(config, _bundle_with_username(tmp_path, "+quit"))
    reloaded = DayzConfig.load()
    assert reloaded.steam_username == "real_owner"
    assert reloaded.server_name == "Shared"


@pytest.mark.parametrize("importer", [_import_with_cli, _import_with_web])
@pytest.mark.parametrize(
    "arc_name",
    [
        "mpmissions/dayzOffline.chernarusplus/../../keep.txt",
        "mpmissions/dayzOffline.chernarusplus/db/../../other/keep.txt",
        "profiles/../keep.txt",
        "profiles/../mpmissions/dayzOffline.chernarusplus/keep.txt",
    ],
)
def test_an_entry_stays_inside_its_own_folder(
    sample_config, tmp_path, tmp_server_dir, importer, arc_name
):
    """A mission entry lands in its mission folder and a profiles entry in the
    profiles folder. Containment in the server dir alone let a bundle write
    over any file of the install."""
    config = _config(sample_config, tmp_path, tmp_server_dir)
    targets = [
        tmp_server_dir / "keep.txt",
        tmp_server_dir / "mpmissions" / "other" / "keep.txt",
        tmp_server_dir / "mpmissions" / "dayzOffline.chernarusplus" / "keep.txt",
    ]
    for target in targets:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("original")
    zip_path = tmp_path / "bundle.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(arc_name, "from the bundle")

    lines = importer(config, zip_path)

    assert [t.read_text() for t in targets] == ["original"] * 3
    if lines:
        assert any("(unsafe path)" in line for line in lines)


@pytest.mark.parametrize("importer", [_import_with_cli, _import_with_web])
@pytest.mark.parametrize("target_mission", ["..", "a/b", "../mpmissions"])
def test_a_target_mission_must_be_a_folder_name(
    sample_config, tmp_path, tmp_server_dir, importer, target_mission
):
    config = _config(sample_config, tmp_path, tmp_server_dir)
    (tmp_server_dir / "keep.txt").write_text("original")
    zip_path = tmp_path / "bundle.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("mpmissions/dayzOffline.chernarusplus/keep.txt", "from the bundle")

    importer(config, zip_path, target_mission=target_mission)

    assert (tmp_server_dir / "keep.txt").read_text() == "original"
    assert not (tmp_server_dir / "mpmissions" / "keep.txt").exists()


@pytest.mark.parametrize("importer", [_import_with_cli, _import_with_web])
def test_ordinary_entries_still_import(sample_config, tmp_path, tmp_server_dir, importer):
    config = _config(sample_config, tmp_path, tmp_server_dir)
    zip_path = tmp_path / "bundle.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("mpmissions/dayzOffline.chernarusplus/db/globals.xml", "<variables/>")
        zf.writestr("profiles/TraderPlus/TraderPlusConfig/a.json", "{}")

    importer(config, zip_path, target_mission="dayzOffline.enoch")

    assert (tmp_server_dir / "mpmissions" / "dayzOffline.enoch" / "db" / "globals.xml").is_file()
    assert (tmp_server_dir / "profiles" / "TraderPlus" / "TraderPlusConfig" / "a.json").is_file()


@pytest.mark.skipif(IS_WINDOWS, reason="POSIX file modes")
@pytest.mark.parametrize("importer", [_import_with_cli, _import_with_web])
def test_an_imported_battleye_cfg_is_owner_only(
    sample_config, tmp_path, tmp_server_dir, server_stopped, importer
):
    """The BattlEye cfg holds the RCon password, so a new one is 0600, not
    the umask's 0644."""
    config = _config(sample_config, tmp_path, tmp_server_dir)
    zip_path = tmp_path / "bundle.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("battleye/beserver_x64.cfg", "RConPassword frombundle\n")
    old_umask = os.umask(0o022)
    try:
        importer(config, zip_path)
    finally:
        os.umask(old_umask)

    dest = config.battleye_dir / "beserver_x64.cfg"
    assert dest.read_text() == "RConPassword frombundle\n"
    assert stat.S_IMODE(dest.stat().st_mode) == 0o600


@pytest.mark.parametrize("importer", [_import_with_cli, _import_with_web])
def test_only_the_battleye_cfg_goes_into_the_battleye_folder(
    sample_config, tmp_path, tmp_server_dir, server_stopped, importer
):
    """An export packs only the cfg. Every `battleye/` entry was written, so a
    shared bundle could replace the BattlEye library the server loads."""
    config = _config(sample_config, tmp_path, tmp_server_dir)
    config.battleye_dir.mkdir(parents=True, exist_ok=True)
    library = config.battleye_dir / "beserver_x64.so"
    library.write_bytes(b"real library")
    zip_path = tmp_path / "bundle.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("battleye/beserver_x64.so", b"from the bundle")
        zf.writestr("battleye/BEServer_x64.dll", b"from the bundle")
        zf.writestr("battleye/x/..", b"from the bundle")
        zf.writestr(f"battleye/{config.beserver_cfg_path.name.upper()}", "RConPassword frombundle\n")

    lines = importer(config, zip_path)

    assert library.read_bytes() == b"real library"
    assert sorted(p.name for p in config.battleye_dir.iterdir()) == sorted(
        ["beserver_x64.so", config.beserver_cfg_path.name]
    )
    assert config.beserver_cfg_path.read_text() == "RConPassword frombundle\n"
    if lines:
        assert "  - battleye/beserver_x64.so (not the BattlEye cfg)" in lines


@pytest.mark.parametrize("importer", [_import_with_cli, _import_with_web])
def test_folder_entries_are_not_written_as_files(
    sample_config, tmp_path, tmp_server_dir, server_stopped, importer
):
    """plans/68 B12: a zip made with `zip -r` has an entry per folder. Taken as
    files, `battleye/` became an empty file named `battleye` in the BattlEye
    dir, and a mission or profiles folder entry stopped the import."""
    config = _config(sample_config, tmp_path, tmp_server_dir)
    zip_path = tmp_path / "bundle.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for folder in (
            "battleye/",
            "mpmissions/",
            "mpmissions/dayzOffline.chernarusplus/",
            "mpmissions/dayzOffline.chernarusplus/db/",
            "profiles/",
            "profiles/TraderPlus/",
        ):
            zf.writestr(folder, b"")
        zf.writestr("battleye/beserver_x64.cfg", "RConPassword frombundle\n")
        zf.writestr("mpmissions/dayzOffline.chernarusplus/db/globals.xml", "<variables/>")
        zf.writestr("profiles/TraderPlus/a.json", "{}")

    lines = importer(config, zip_path)

    assert sorted(p.name for p in config.battleye_dir.iterdir()) == ["beserver_x64.cfg"]
    assert config.beserver_cfg_path.read_text() == "RConPassword frombundle\n"
    mission = tmp_server_dir / "mpmissions" / "dayzOffline.chernarusplus"
    assert (mission / "db" / "globals.xml").read_text() == "<variables/>"
    assert (tmp_server_dir / "profiles" / "TraderPlus" / "a.json").read_text() == "{}"
    assert not any("unsafe path" in line for line in lines)


def test_the_casing_lookup(tmp_path):
    (tmp_path / "cfgeffectarea.json").write_text("{}")
    (tmp_path / "Sub").mkdir()
    assert bundle_ops.existing_casing(tmp_path / "cfgEffectArea.json") == tmp_path / "cfgeffectarea.json"
    assert bundle_ops.existing_casing(tmp_path / "cfgeffectarea.json") == tmp_path / "cfgeffectarea.json"
    assert bundle_ops.existing_casing(tmp_path / "other.json") == tmp_path / "other.json"
    # Only files: a folder named like the entry is not written over.
    assert bundle_ops.existing_casing(tmp_path / "sub") == tmp_path / "sub"
    assert bundle_ops.existing_casing(tmp_path / "missing" / "a.json") == tmp_path / "missing" / "a.json"


# ---------------------------------------------------------------------------
# config.json travels in the bundle, and it carries credentials (2026 Q3)
# ---------------------------------------------------------------------------


def _no_cfg_passwords(config):
    """The shared fixture's beserver_x64.cfg carries `RConPassword mypass`,
    which the warning names since test pass 2, N24. Tests about the config.json
    fields clear it so they check only what they are about."""
    config.beserver_cfg_path.write_text("RConPort 2305\n")
    return config


def _with_secrets(config):
    from app.web.auth import hash_password

    config.rcon_password = "SUPERSECRET"
    config.discord_webhook_url = "https://discord.com/api/webhooks/1/TOKEN"
    config.steam_api_key = "STEAMKEY"
    config.web_password_hash = hash_password("hunter2")
    config.save()
    return config


def test_the_exported_bundle_really_does_contain_the_credentials(
    sample_config, tmp_path, tmp_server_dir
):
    """The measurement behind the warning, kept as a test so the warning can't
    outlive the thing it warns about — or quietly stop being true."""
    config = _with_secrets(_config(sample_config, tmp_path, tmp_server_dir))

    zip_path = bundle_ops.export_bundle(config)
    with zipfile.ZipFile(zip_path) as zf:
        assert "config.json" in zf.namelist()
        blob = zf.read("config.json").decode("utf-8")

    assert "SUPERSECRET" in blob
    assert "TOKEN" in blob
    assert "STEAMKEY" in blob


@pytest.mark.skipif(IS_WINDOWS, reason="POSIX modes; Windows takes the folder's ACL")
def test_the_exported_bundle_is_readable_by_its_owner_only(
    sample_config, tmp_path, tmp_server_dir
):
    """It carries what the test above measures, which zsm keeps at 0600 in
    config.json; under the usual umask 022 the zip came out 0644."""
    import os
    import stat

    config = _with_secrets(_config(sample_config, tmp_path, tmp_server_dir))
    old = os.umask(0o022)
    try:
        zip_path = bundle_ops.export_bundle(config)
    finally:
        os.umask(old)

    assert stat.S_IMODE(zip_path.stat().st_mode) == 0o600


def test_the_export_says_so_on_the_last_line(sample_config, tmp_path, tmp_server_dir):
    config = _with_secrets(_config(sample_config, tmp_path, tmp_server_dir))
    lines: list[str] = []

    bundle_ops.export_bundle(config, log=lines.append)

    assert "this bundle carries" in lines[-1]
    for phrase in ("RCon password", "Discord webhook URL", "Steam API key",
                   "app password hash"):
        assert phrase in lines[-1], phrase


def test_the_warning_names_fields_never_values(sample_config, tmp_path, tmp_server_dir):
    """A warning that printed the secret would be the leak it exists to flag —
    and it goes into a job log the browser renders."""
    config = _with_secrets(_config(sample_config, tmp_path, tmp_server_dir))

    note = bundle_ops.sensitive_warning(config)
    for secret in ("SUPERSECRET", "TOKEN", "STEAMKEY", config.web_password_hash):
        assert secret not in note


def test_nothing_is_said_when_nothing_is_set(sample_config, tmp_path, tmp_server_dir):
    """Most installs have no Discord webhook and no Steam API key. A warning
    that always fires is one nobody reads."""
    config = _no_cfg_passwords(_config(sample_config, tmp_path, tmp_server_dir))
    config.rcon_password = ""
    config.discord_webhook_url = ""
    config.steam_api_key = ""
    config.web_password_hash = ""

    assert bundle_ops.sensitive_warning(config) is None
    lines: list[str] = []
    zip_path = bundle_ops.export_bundle(config, log=lines.append)
    assert lines[-1] == f"Bundle exported to {zip_path}"


def test_only_the_fields_that_are_set_are_named(sample_config, tmp_path, tmp_server_dir):
    config = _no_cfg_passwords(_config(sample_config, tmp_path, tmp_server_dir))
    config.rcon_password = "x"
    config.discord_webhook_url = ""
    config.steam_api_key = "   "  # whitespace is not a credential
    config.web_password_hash = ""

    assert bundle_ops.sensitive_fields_in(config) == ["your RCon password"]


def test_the_warning_is_markup_free_for_the_job_log(sample_config, tmp_path, tmp_server_dir):
    config = _with_secrets(_config(sample_config, tmp_path, tmp_server_dir))
    note = bundle_ops.sensitive_warning(config)
    assert "[" not in note and "]" not in note


# ---------------------------------------------------------------------------
# The .cfg files in the bundle carry passwords too (test pass 2, N24)
# ---------------------------------------------------------------------------


def _blank_config_fields(config):
    config.rcon_password = ""
    config.discord_webhook_url = ""
    config.steam_api_key = ""
    config.web_password_hash = ""
    return config


def test_the_battleye_rcon_password_is_named(sample_config, tmp_path, tmp_server_dir):
    config = _blank_config_fields(_config(sample_config, tmp_path, tmp_server_dir))
    assert bundle_ops.sensitive_fields_in(config) == [
        "the BattlEye RCon password (beserver_x64.cfg)"
    ]
    _no_cfg_passwords(config)
    assert bundle_ops.sensitive_fields_in(config) == []


def test_serverdz_passwords_are_named_only_when_set(sample_config, tmp_path, tmp_server_dir):
    config = _no_cfg_passwords(_blank_config_fields(_config(sample_config, tmp_path, tmp_server_dir)))
    cfg = config.server_cfg_path
    cfg.write_text(cfg.read_text() + 'password = "";\npasswordAdmin = "ADMINSECRET";\n')
    assert bundle_ops.sensitive_fields_in(config) == [
        "the server admin password (serverDZ.cfg)"
    ]


def test_the_warning_joins_with_and_and_prints_no_cfg_values(
    sample_config, tmp_path, tmp_server_dir
):
    config = _blank_config_fields(_config(sample_config, tmp_path, tmp_server_dir))
    config.rcon_password = "SUPERSECRET"
    cfg = config.server_cfg_path
    cfg.write_text(cfg.read_text() + 'passwordAdmin = "ADMINSECRET";\n')

    note = bundle_ops.sensitive_warning(config)

    assert note.startswith(
        "Note: this bundle carries your RCon password, the server admin password "
        "(serverDZ.cfg) and the BattlEye RCon password (beserver_x64.cfg). "
    )
    for secret in ("SUPERSECRET", "ADMINSECRET", "mypass"):
        assert secret not in note


# ---------------------------------------------------------------------------
# The zedstead.com listing key (plans/57 B7)
# ---------------------------------------------------------------------------


def test_the_site_listing_key_is_named_when_set(sample_config, tmp_path, tmp_server_dir):
    from app.ops.site_ops import set_switch

    config = _no_cfg_passwords(_blank_config_fields(_config(sample_config, tmp_path, tmp_server_dir)))
    assert bundle_ops.sensitive_fields_in(config) == []

    set_switch(config, "site_listing", True)

    assert bundle_ops.sensitive_fields_in(config) == ["your zedstead.com listing key"]


def test_the_warning_says_a_restored_copy_publishes_as_the_same_server(
    sample_config, tmp_path, tmp_server_dir
):
    """Warned, not stripped: the standing bundle decision."""
    from app.ops.site_ops import set_switch

    config = _no_cfg_passwords(_blank_config_fields(_config(sample_config, tmp_path, tmp_server_dir)))
    set_switch(config, "site_ping", True)

    note = bundle_ops.sensitive_warning(config)

    assert note.startswith("Note: this bundle carries your zedstead.com listing key. ")
    assert note.endswith("would also publish to zedstead.com as this same server.")
    assert config.site_secret not in note
    assert config.site_install_id not in note


def test_no_listing_sentence_without_the_key(sample_config, tmp_path, tmp_server_dir):
    config = _config(sample_config, tmp_path, tmp_server_dir)
    note = bundle_ops.sensitive_warning(config)
    assert "zedstead.com" not in note


# --- BattlEye's active copy while the server runs (measured in 2026 Q3) -----


def test_an_export_while_running_packs_battleyes_copy(
    sample_config, tmp_path, tmp_server_dir, battleye_running
):
    """Only the active copy exists then; it goes in under the cfg's own name,
    which is what an import writes back."""
    config = _config(sample_config, tmp_path, tmp_server_dir)
    zip_path = bundle_ops.export_bundle(config)
    with zipfile.ZipFile(zip_path) as zf:
        assert [n for n in zf.namelist() if n.startswith("battleye/")] == [
            "battleye/beserver_x64.cfg"
        ]
        assert zf.read("battleye/beserver_x64.cfg").decode() == battleye_running.read_text()


def test_the_warning_reads_battleyes_copy_while_running(
    sample_config, tmp_path, tmp_server_dir, battleye_running
):
    config = _blank_config_fields(_config(sample_config, tmp_path, tmp_server_dir))
    assert bundle_ops.sensitive_fields_in(config) == [
        "the BattlEye RCon password (beserver_x64.cfg)"
    ]


def test_a_bundle_with_the_battleye_cfg_is_refused_while_running(sample_config, battleye_running):
    with pytest.raises(BeserverCfgInUse):
        bundle_ops.refuse_battleye_import_while_running(
            sample_config, ["mods.txt", "battleye/beserver_x64.cfg"]
        )
    # Without that entry there is nothing BattlEye would put back.
    bundle_ops.refuse_battleye_import_while_running(sample_config, ["mods.txt"])


def test_the_cli_import_writes_nothing_while_running(sample_config, tmp_path, battleye_running):
    sample_config.save()
    zip_path = tmp_path / "be-bundle.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("mods.txt", "@CF;\n")
        zf.writestr("battleye/beserver_x64.cfg", "RConPassword frombundle\n")

    _bundle_import(zip_path, ni=True)

    assert not sample_config.beserver_cfg_path.exists()
    assert not sample_config.paths.mods_json.exists()


def test_the_cli_import_when_stopped_is_unchanged(sample_config, tmp_path, server_stopped):
    sample_config.save()
    zip_path = tmp_path / "be-bundle.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("battleye/beserver_x64.cfg", "RConPassword frombundle\n")

    _bundle_import(zip_path, ni=True)

    assert sample_config.beserver_cfg_path.read_text() == "RConPassword frombundle\n"
