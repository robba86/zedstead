"""Tests for app/ops/adopt.py — reading a server zsm did not install (plans/78 step 1).

The RPT line is the shape plans/78 step 0 measured on 1.29: the command the
engine ran, its quotes dropped, a mod name with a space in it.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.ops import adopt

RPT_LINE = (
    "== ./DayZServer ./DayZServer -config=serverDZ.cfg "
    "-mod=@CF;@Dabs Framework;@MyRadZone -serverMod=@AdminTool "
    "-profiles=custom_profiles -BEpath=battleye -adminLog -cpuCount=4 -port=2502 "
)
UNIT_EXEC = (
    "ExecStart=/bin/bash -c 'exec ./DayZServer \"-config=serverDZ.cfg\" "
    "\"-mod=@CF;@Dabs Framework\" \"-profiles=unit_profiles\" -doLogs -port=2402'"
)
SCRIPT = (
    "#!/bin/sh\n"
    "# ./DayZServer -port=1 (a comment)\n"
    "./DayZServer -config=serverDZ.cfg \"-mod=@CF\" \\\n"
    "  -profiles=script_profiles -port=2302\n"
)


@pytest.fixture()
def units(tmp_path, monkeypatch):
    """Both scopes' unit dirs in tmp, so the machine's own units are never read."""
    dirs = {"system": tmp_path / "units-system", "user": tmp_path / "units-user"}
    for d in dirs.values():
        d.mkdir()
    monkeypatch.setattr(adopt, "unit_dirs", lambda: list(dirs.items()))
    monkeypatch.setattr(adopt, "IS_WINDOWS", False)
    return dirs


def _mod(server: Path, name: str, published: str | None = None) -> Path:
    d = server / name
    (d / "addons").mkdir(parents=True)
    if published is not None:
        (d / "meta.cpp").write_text(f'protocol = 1;\npublishedid = {published};\nname = "{name}";\n')
    return d


def _rpt(server: Path, folder: str, name: str, line: str = RPT_LINE, mtime: float | None = None) -> Path:
    d = server / folder
    d.mkdir(exist_ok=True)
    path = d / name
    path.write_text(f"=====\n== DayZServer\n{line}\n\nVersion 1.29.163709\n")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def _unit(directory: Path, name: str, server: Path, exec_line: str = UNIT_EXEC, extra: str = "") -> Path:
    path = directory / name
    path.write_text(f"[Unit]\nDescription=x\n{extra}\n[Service]\nWorkingDirectory={server}\n{exec_line}\n")
    return path


# --- the launch command ------------------------------------------------------


def test_the_rpt_is_read_with_a_space_in_a_mod_name(sample_config, units):
    server = sample_config.server_dir_path
    _rpt(server, "custom_profiles", "DayZServer_x64_2026-10-10_12-00-00.RPT")
    report = adopt.scan(sample_config)
    launch = report.launch
    assert launch.source == adopt.SOURCE_RPT
    assert launch.mods == ["@CF", "@Dabs Framework", "@MyRadZone"]
    assert launch.server_mods == ["@AdminTool"]
    assert launch.values == {
        "config": "serverDZ.cfg", "profiles": "custom_profiles", "battleye": "battleye",
        "port": "2502", "cpu_count": "4",
    }
    assert launch.extra_args == ["-adminLog"]


def test_a_unit_and_a_script_are_read_with_their_quotes(sample_config, units):
    server = sample_config.server_dir_path
    _unit(units["system"], "mydayz.service", server)
    (server / "start.sh").write_text(SCRIPT)
    report = adopt.scan(sample_config)
    unit, script = report.launches
    assert (unit.source, script.source) == (adopt.SOURCE_UNIT, adopt.SOURCE_SCRIPT)
    assert unit.mods == ["@CF", "@Dabs Framework"]
    assert unit.values == {"config": "serverDZ.cfg", "profiles": "unit_profiles", "port": "2402"}
    assert unit.extra_args == ["-doLogs"]
    # The continuation line is joined and the commented line is skipped.
    assert script.mods == ["@CF"]
    assert script.values["profiles"] == "script_profiles"
    assert script.values["port"] == "2302"


def test_a_windows_start_script_is_read(sample_config, units):
    server = sample_config.server_dir_path
    (server / "start.bat").write_text(
        '@echo off\r\nrem DayZServer_x64.exe is started below\r\n'
        'start /wait "" "DayZServer_x64.exe" "-config=serverDZ.cfg" "-mod=@CF;@Dabs Framework" '
        '"-profiles=C:\\DayZ\\profiles" -port=2302\r\n'
    )
    launch = adopt.scan(sample_config).launch
    assert launch.source == adopt.SOURCE_SCRIPT
    assert launch.mods == ["@CF", "@Dabs Framework"]
    assert launch.values["profiles"] == "C:\\DayZ\\profiles"


def test_zsms_own_unit_is_never_the_launch_source(sample_config, units):
    """A mod change installed Zedstead's unit on an
    adopted server, and Adopt then read its empty -mod= as the launch."""
    server = sample_config.server_dir_path
    (server / "start.sh").write_text(SCRIPT)
    _unit(units["system"], "dayz-server.service", server, extra=adopt.ZSM_UNIT_MARKER)
    report = adopt.scan(sample_config)
    assert report.launch.source == adopt.SOURCE_SCRIPT
    assert any(u.zsm for u in report.units)  # still known, for the other checks


def test_the_rpt_beats_a_unit_and_a_unit_beats_a_script(sample_config, units):
    server = sample_config.server_dir_path
    (server / "start.sh").write_text(SCRIPT)
    assert adopt.scan(sample_config).launch.source == adopt.SOURCE_SCRIPT
    _unit(units["user"], "mine.service", server)
    assert adopt.scan(sample_config).launch.source == adopt.SOURCE_UNIT
    # The unit's -profiles= is where the log is looked for.
    _rpt(server, "unit_profiles", "DayZServer_x64_2026-10-10_12-00-00.RPT")
    report = adopt.scan(sample_config)
    assert report.launch.source == adopt.SOURCE_RPT
    assert [l.source for l in report.launches] == ["rpt", "unit", "script"]


def test_the_newest_rpt_wins_across_folders(sample_config, units):
    server = sample_config.server_dir_path
    _rpt(server, "profiles", "old.RPT", line=RPT_LINE.replace("-port=2502", "-port=1111"), mtime=1000)
    newest = _rpt(server, "custom_profiles", "new.rpt", mtime=2000)
    report = adopt.scan(sample_config)
    assert report.rpt == str(newest)
    assert report.launch.values["port"] == "2502"


def test_a_variable_in_a_command_is_flagged(sample_config, units):
    server = sample_config.server_dir_path
    (server / "run.sh").write_text('MODS="@CF"\n./DayZServer "-mod=$MODS" -port=2302\n')
    assert adopt.scan(sample_config).launch.uses_variables


def test_no_launch_command_at_all(sample_config, units):
    report = adopt.scan(sample_config)
    assert report.launch is None and report.launches == []
    assert report.mission == "dayzOffline.chernarusplus"


# --- the @ folders -----------------------------------------------------------


def test_mod_folders_their_ids_and_whether_they_are_loaded(sample_config, units):
    server = sample_config.server_dir_path
    _mod(server, "@CF", "1559212036")
    _mod(server, "@Dabs Framework")  # no meta.cpp; zsm knows the ID
    _mod(server, "@MyRadZone")  # no meta.cpp, unknown: local
    _mod(server, "@Repack", "0")  # a local mod's meta.cpp
    _mod(server, "@UnusedMod", "123")
    _mod(server, "@AdminTool")
    _rpt(server, "custom_profiles", "a.RPT")

    report = adopt.scan(sample_config)
    mods = {m.name: m for m in report.mods}
    assert (mods["@CF"].workshop_id, mods["@CF"].id_source) == ("1559212036", adopt.ID_FROM_META)
    assert mods["@Dabs Framework"].id_source == adopt.ID_KNOWN
    assert mods["@Dabs Framework"].workshop_id
    assert mods["@MyRadZone"].local and mods["@MyRadZone"].workshop_id == ""
    assert mods["@Repack"].local
    assert not mods["@UnusedMod"].loaded and mods["@UnusedMod"].workshop_id == "123"
    assert mods["@AdminTool"].loaded and mods["@AdminTool"].server_only
    assert mods["@CF"].loaded and not mods["@CF"].server_only
    assert report.missing == []
    rows = {r["name"]: r for r in report.to_dict()["mods"]}
    assert rows["@CF"]["local"] is False and rows["@MyRadZone"]["local"] is True


def test_a_folder_the_command_names_but_the_disk_lacks(sample_config, units):
    server = sample_config.server_dir_path
    _mod(server, "@CF", "1559212036")
    _rpt(server, "custom_profiles", "a.RPT")
    assert adopt.scan(sample_config).missing == ["@Dabs Framework", "@MyRadZone", "@AdminTool"]


def test_a_name_zsm_cannot_use_is_reported_not_adopted(sample_config, units):
    server = sample_config.server_dir_path
    _mod(server, "@Bad;Name")
    _mod(server, "@Fine")
    mods = {m.name: m for m in adopt.scan(sample_config).mods}
    assert mods["@Bad;Name"].problem
    assert mods["@Fine"].problem == ""


def test_the_rollback_stash_is_not_a_mod(sample_config, units):
    server = sample_config.server_dir_path
    (server / ".zsm-mod-rollback" / "@CF").mkdir(parents=True)
    assert adopt.scan(sample_config).mods == []


# --- units -------------------------------------------------------------------


def test_a_unit_in_each_scope_and_whose_it_is(sample_config, units):
    from app.server.service_control import TEMPLATE_PATH
    from app.shared.systemd_scope import server_unit

    server = sample_config.server_dir_path
    _unit(units["system"], "mydayz.service", server)
    _unit(units["user"], server_unit(sample_config), server, extra=adopt.ZSM_UNIT_MARKER)
    # Another server's unit, and a unit that runs no DayZ server.
    other = server.parent / "other-server"
    other.mkdir()
    _unit(units["system"], "other.service", other)
    _unit(units["system"], "web.service", server, exec_line="ExecStart=/usr/bin/python -m http.server")

    found = {u.name: u for u in adopt.scan(sample_config).units}
    assert set(found) == {"mydayz.service", server_unit(sample_config)}
    assert found["mydayz.service"].scope == "system"
    assert not found["mydayz.service"].zsm and not found["mydayz.service"].zsm_name
    own = found[server_unit(sample_config)]
    assert own.scope == "user" and own.zsm and own.zsm_name
    # The marker is a line of the template zsm renders.
    assert adopt.ZSM_UNIT_MARKER in TEMPLATE_PATH.read_text()


def test_a_unit_without_working_directory_names_the_binary_by_path(sample_config, units):
    server = sample_config.server_dir_path
    path = units["system"] / "abs.service"
    path.write_text(f"[Service]\nExecStart={server}/DayZServer -config=serverDZ.cfg -port=2302\n")
    assert [u.name for u in adopt.scan(sample_config).units] == ["abs.service"]


def test_units_are_not_looked_for_on_windows(sample_config, units, monkeypatch):
    _unit(units["system"], "mydayz.service", sample_config.server_dir_path)
    monkeypatch.setattr(adopt, "IS_WINDOWS", True)
    assert adopt.scan(sample_config).units == []


# --- reads only --------------------------------------------------------------


def test_the_scan_writes_nothing(sample_config, units):
    server = sample_config.server_dir_path
    _mod(server, "@CF", "1559212036")
    _rpt(server, "custom_profiles", "a.RPT")
    _unit(units["system"], "mydayz.service", server)
    (server / "start.sh").write_text(SCRIPT)

    def tree(root: Path) -> dict:
        return {
            str(p.relative_to(root)): (p.stat().st_mtime_ns, p.read_bytes() if p.is_file() else b"")
            for p in sorted(root.rglob("*"))
        }

    before = tree(server)
    adopt.scan(sample_config)
    assert tree(server) == before


# --- apply (step 2) ----------------------------------------------------------


def _adoptable(sample_config, units) -> Path:
    """Step 0's server: CF with meta.cpp, Dabs without, a local mod, an unused
    folder, a server-side tool, and a log of the run."""
    server = sample_config.server_dir_path
    _mod(server, "@CF", "1559212036")
    _mod(server, "@Dabs Framework")
    _mod(server, "@MyRadZone")
    _mod(server, "@UnusedMod", "123")
    _mod(server, "@AdminTool", "456")
    _rpt(server, "custom_profiles", "a.RPT")
    return server


def test_apply_writes_the_list_in_launch_order(sample_config, units):
    from app.modmgr import modlist
    from app.modmgr.mods import get_resolved_mod_order

    _adoptable(sample_config, units)
    lines = adopt.apply(sample_config, adopt.scan(sample_config))
    ml = modlist.load(sample_config)
    assert ml.names() == ["@CF", "@Dabs Framework", "@MyRadZone", "@AdminTool", "@UnusedMod"]
    rows = {m.name: m for m in ml.mods}
    assert rows["@AdminTool"].server_only and rows["@AdminTool"].enabled
    assert not rows["@UnusedMod"].enabled
    assert rows["@MyRadZone"].local and rows["@MyRadZone"].id == ""
    assert rows["@CF"].id == "1559212036" and not rows["@CF"].local
    assert ml.manual and ml.pinned == ml.names()
    assert get_resolved_mod_order(sample_config) == ["@CF", "@Dabs Framework", "@MyRadZone", "@AdminTool"]
    assert lines[0].startswith("Mod list: 5 mod(s), 4 loaded, from the server log")
    assert any("@MyRadZone" in l and "own" in l for l in lines)


def test_apply_takes_the_launch_fields(sample_config, units):
    _adoptable(sample_config, units)
    adopt.apply(sample_config, adopt.scan(sample_config))
    assert sample_config.profiles_path == "custom_profiles"
    assert sample_config.battleye_path == "battleye"
    assert sample_config.config_path == "serverDZ.cfg"
    assert sample_config.server_port == 2502
    assert sample_config.server_args == "-adminLog -cpuCount=4"
    assert sample_config.cpu_count == 4
    import json

    saved = json.loads(sample_config._file.read_text())
    assert (saved["server_port"], saved["profiles_path"]) == (2502, "custom_profiles")


def test_a_learned_id_is_remembered(sample_config, units):
    from app.modmgr import modlist

    _adoptable(sample_config, units)
    adopt.apply(sample_config, adopt.scan(sample_config))
    modlist.save_installed(sample_config, [])
    assert modlist.workshop_ids(sample_config)["@UnusedMod"] == "123"


def test_apply_refuses_when_the_list_is_not_empty(sample_config, units):
    from app.modmgr import modlist

    _adoptable(sample_config, units)
    modlist.add(sample_config, "@CF", "1559212036")
    with pytest.raises(adopt.AdoptError, match="already holds"):
        adopt.apply(sample_config, adopt.scan(sample_config))
    assert modlist.installed(sample_config) == ["@CF"]


def test_apply_refuses_without_a_launch_command(sample_config, units):
    from app.modmgr import modlist

    _mod(sample_config.server_dir_path, "@CF", "1559212036")
    with pytest.raises(adopt.AdoptError, match="No launch command"):
        adopt.apply(sample_config, adopt.scan(sample_config))
    assert modlist.installed(sample_config) == []


def test_apply_refuses_a_command_built_from_variables(sample_config, units):
    server = sample_config.server_dir_path
    _mod(server, "@CF", "1559212036")
    (server / "run.sh").write_text('MODS="@CF"\n./DayZServer "-mod=$MODS" -port=2302\n')
    with pytest.raises(adopt.AdoptError, match="variables"):
        adopt.apply(sample_config, adopt.scan(sample_config))


def test_a_refused_folder_and_a_missing_one_are_said_not_listed(sample_config, units):
    from app.modmgr import modlist

    server = sample_config.server_dir_path
    _mod(server, "@CF", "1559212036")
    _mod(server, "@Bad;Name")
    (server / "start.sh").write_text('./DayZServer "-mod=@CF;@Gone" -port=2302\n')
    lines = adopt.apply(sample_config, adopt.scan(sample_config))
    assert modlist.installed(sample_config) == ["@CF"]
    assert any("@Gone" in l and "no folder" in l for l in lines)
    assert any("@Bad;Name" in l and "not listed" in l for l in lines)


def test_a_value_the_launch_check_refuses_is_left_as_it_is(sample_config, units):
    server = sample_config.server_dir_path
    _mod(server, "@CF", "1559212036")
    (server / "start.sh").write_text('./DayZServer "-mod=@CF" "-profiles=a$(b)" -port=2302\n')
    report = adopt.scan(sample_config)
    report.launch.uses_variables = False  # the $( is what is under test here
    lines = adopt.apply(sample_config, report)
    assert sample_config.profiles_path == "profiles"
    assert any(l.startswith("profiles_path was left as it is") for l in lines)


def test_apply_writes_nothing_on_the_server(sample_config, units):
    server = _adoptable(sample_config, units)
    _unit(units["system"], "mydayz.service", server)
    files = lambda: {  # noqa: E731
        str(p.relative_to(server)): p.read_bytes() if p.is_file() else None
        for p in sorted(server.rglob("*")) if "config" not in p.relative_to(server).parts
    }
    before = files()
    adopt.apply(sample_config, adopt.scan(sample_config))
    assert files() == before


# --- an update on an adopted server (step 3) ---------------------------------


def _launch_mods(config) -> list[str]:
    from app.server.service_control import build_launch_argv

    return [a for a in build_launch_argv(config) if a.startswith(("-mod=", "-serverMod="))]


def test_an_update_on_an_adopted_server_keeps_every_folder_and_the_mod_line(sample_config, units, tmp_path):
    """Step 0 measured the first Update moving every folder aside and copying
    none back. Adopted, the Workshop mods come back from the cache and the
    local one is never moved."""
    from app.modmgr import modlist
    from app.ops import fileops

    server = _adoptable(sample_config, units)
    (server / "@MyRadZone" / "addons" / "radzone.pbo").write_text("the admin's own build")
    keys = sample_config.keys_dir
    keys.mkdir(exist_ok=True)
    (keys / "radzone.bikey").write_text("key")  # the local mod's key lives only here
    adopt.apply(sample_config, adopt.scan(sample_config))
    before = _launch_mods(sample_config)
    folders = sorted(p.name for p in server.glob("@*"))

    sample_config.workshop_content_dir = str(tmp_path / "workshop" / "content" / "221100")
    for name, mod_id in modlist.workshop_ids(sample_config).items():
        if name in modlist.installed(sample_config):
            item = sample_config.workshop_dir / mod_id / "addons"
            item.mkdir(parents=True)
            (item / "x.pbo").write_text(name)
    lines: list[str] = []
    fileops.remove_server_mods(sample_config, log=lines.append)
    fileops.copy_all_mods_to_server(sample_config, log=lines.append)
    fileops.copy_bikey_files(sample_config, log=lines.append)

    assert sorted(p.name for p in server.glob("@*")) == folders
    assert (server / "@MyRadZone" / "addons" / "radzone.pbo").read_text() == "the admin's own build"
    assert (keys / "radzone.bikey").exists()
    assert _launch_mods(sample_config) == before
    assert any("local mod folder" in l and "@MyRadZone" in l for l in lines)
    assert any("Skipped" in l and "@MyRadZone" in l for l in lines)


# --- mod types pasted into the mission, D3 (step 5) ----------------------------

MISSION_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>
<types>
    <type name="AKM"><nominal>5</nominal></type>
    <!-- pasted from RadZone by hand -->
    <type name="RZ_GeigerCounter"><nominal>3</nominal></type>
    <type name="rz_crate"><nominal>1</nominal></type>
    <!-- START MOD: @Merged -->
    <type name="MergedThing"><nominal>1</nominal></type>
    <!-- END MOD: @Merged -->
</types>
"""


def _pasted_server(sample_config) -> Path:
    server = sample_config.server_dir_path
    db = server / "mpmissions" / "dayzOffline.chernarusplus" / "db"
    db.mkdir(parents=True, exist_ok=True)
    (db / "types.xml").write_text(MISSION_TYPES)
    rz = _mod(server, "@RadZone", "2999999999")
    (rz / "types.xml").write_text(
        '<types><type name="RZ_GeigerCounter"><nominal>3</nominal></type>'
        '<type name="RZ_Crate"><nominal>1</nominal></type>'
        '<type name="RZ_Only"/></types>'
    )
    (rz / "types_Livonia.xml").write_text('<types><type name="AKM"/></types>')
    frag = _mod(server, "@Fragment")
    (frag / "extra_types.xml").write_text('<type name="akm"><nominal>5</nominal></type>\n')
    merged = _mod(server, "@Merged")
    (merged / "types.xml").write_text('<types><type name="MergedThing"/></types>')
    _mod(server, "@NoXml")
    return server


def test_scan_names_each_mod_whose_types_are_already_pasted(sample_config):
    _pasted_server(sample_config)
    report = adopt.scan(sample_config)
    by_mod = {p.mod: p for p in report.pasted}
    assert set(by_mod) == {"@RadZone", "@Fragment"}, "a merged block is the mod's, not pasted"
    assert by_mod["@RadZone"].classes == ["rz_crate", "RZ_GeigerCounter"]
    assert by_mod["@RadZone"].files == ["@RadZone/types.xml"], "another map's variant is not read"
    assert by_mod["@Fragment"].classes == ["AKM"], "a rootless fragment, matched in any case"
    assert report.to_dict()["pasted"][0]["mod"] in by_mod


def test_keeping_the_pasted_entries_turns_the_mods_file_off(sample_config):
    from app.xml.xml_manual import load_source_exclusions

    server = _pasted_server(sample_config)
    before = (server / "mpmissions/dayzOffline.chernarusplus/db/types.xml").read_bytes()
    entry = next(p for p in adopt.scan(sample_config).pasted if p.mod == "@RadZone")

    adopt.keep_pasted(sample_config, entry)

    assert "@RadZone/types.xml" in load_source_exclusions(manual_dir=sample_config.paths.custom_xml_dir)
    assert (server / "mpmissions/dayzOffline.chernarusplus/db/types.xml").read_bytes() == before


def test_removing_the_pasted_entries_leaves_the_rest_and_turns_the_file_back_on(sample_config):
    from app.xml.xml_manual import load_source_exclusions

    server = _pasted_server(sample_config)
    path = server / "mpmissions/dayzOffline.chernarusplus/db/types.xml"
    entry = next(p for p in adopt.scan(sample_config).pasted if p.mod == "@RadZone")
    adopt.keep_pasted(sample_config, entry)

    lines = adopt.remove_pasted(sample_config, "dayzOffline.chernarusplus", entry)

    text = path.read_text()
    assert "RZ_GeigerCounter" not in text and "rz_crate" not in text
    assert 'name="AKM"' in text and "START MOD: @Merged" in text and "MergedThing" in text
    assert "pasted from RadZone by hand" in text
    assert not load_source_exclusions(manual_dir=sample_config.paths.custom_xml_dir)
    assert "removed 2 entries" in lines[0]
    assert not [p for p in adopt.scan(sample_config).pasted if p.mod == "@RadZone"]



STOCK_AND_PASTE = """<types>
    <type name="NBCGlovesYellow"><nominal>4</nominal><lifetime>7200</lifetime></type>
    <type name="NBCHoodYellow"><nominal>4</nominal></type>
    <type name="AKM"><nominal>5</nominal></type>
    <type name="NBCGlovesYellow">
        <nominal>0</nominal>
        <lifetime>3600</lifetime>
    </type>
    <type name="RZ_Pills"><nominal>2</nominal></type>
</types>
"""


def test_a_class_the_mod_redefines_keeps_the_stock_entry(sample_config):
    """Step 9 on the container: RadZone redefines Bohemia's yellow NBC gear,
    and removing by name took the stock entries out with the pasted ones."""
    server = sample_config.server_dir_path
    path = server / "mpmissions" / "dayzOffline.chernarusplus" / "db" / "types.xml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(STOCK_AND_PASTE)
    rz = _mod(server, "@RadZone", "2999999999")
    (rz / "types.xml").write_text(
        '<types><type name="NBCGlovesYellow"><nominal>0</nominal><lifetime>3600</lifetime></type>'
        '<type name="NBCHoodYellow"><nominal>0</nominal></type>'
        '<type name="RZ_Pills"><nominal>2</nominal></type></types>'
    )

    entry = next(p for p in adopt.scan(sample_config).pasted if p.mod == "@RadZone")
    assert entry.classes == ["NBCGlovesYellow", "RZ_Pills"], "stock's hood alone is not a paste"
    assert entry.changed == ["NBCHoodYellow"], "but it is named: it may be a paste the admin changed"

    lines = adopt.remove_pasted(sample_config, "dayzOffline.chernarusplus", entry)

    text = path.read_text()
    assert text.count('name="NBCGlovesYellow"') == 1 and "<lifetime>7200</lifetime>" in text
    assert 'name="NBCHoodYellow"' in text and "RZ_Pills" not in text
    assert "removed 2 entries" in lines[0]
    assert "NBCHoodYellow" in lines[1]
    assert lines[-1].endswith("zsm run xml-only")


# --- findings and fixes, D6 (step 6) -------------------------------------------


def _findings_server(sample_config, units) -> Path:
    """Step 0's server with its three faults: Dabs' key missing from keys/,
    an unloaded folder, and RadZone's types pasted into the mission."""
    server = _adoptable(sample_config, units)
    (server / "@CF" / "keys").mkdir()
    (server / "@CF" / "keys" / "CF.bikey").write_bytes(b"cf-mod")
    (server / "@Dabs Framework" / "Keys").mkdir()
    (server / "@Dabs Framework" / "Keys" / "Dabs.bikey").write_bytes(b"dabs")
    (server / "keys").mkdir(exist_ok=True)
    (server / "keys" / "CF.bikey").write_bytes(b"cf-already")
    (server / "@MyRadZone" / "types.xml").write_text('<types><type name="RZ_Geiger"/></types>')
    db = server / "mpmissions" / "dayzOffline.chernarusplus" / "db"
    db.mkdir(parents=True, exist_ok=True)
    (db / "types.xml").write_text('<types><type name="rz_geiger"/><type name="AKM"/></types>')
    return server


@pytest.fixture()
def doctor_rows(monkeypatch):
    """Doctor's rows, without running Doctor's subprocesses; records the config
    it was given."""
    from app.commands import doctor

    seen = {}

    def _rows(config, **_kw):
        seen["config"] = config
        return [
            ("Server directory", True, "ok"),
            ("Mod list", True, "No mods installed (vanilla)"),
            ("RPT log scan", True, "Clean"),
            ("Bikey signatures", True, "All present"),
            ("Service", True, "Installed (system)"),
        ], None

    monkeypatch.setattr(doctor, "_run_all_checks", _rows)
    return seen


def test_findings_run_against_the_servers_own_launch_values(sample_config, units, doctor_rows):
    _findings_server(sample_config, units)
    before = (sample_config.profiles_path, sample_config.server_port)

    result = adopt.findings(sample_config, adopt.scan(sample_config))

    preview = doctor_rows["config"]
    assert (preview.profiles_path, preview.server_port) == ("custom_profiles", 2502)
    assert (sample_config.profiles_path, sample_config.server_port) == before, "nothing saved"
    assert {s["label"] for s in result["skipped"]} == {"Mod list", "Bikey signatures", "Service"}
    assert all(s["reason"] for s in result["skipped"])
    assert result["log"]["latest_log"] == "a.RPT"


def test_findings_report_the_three_faults_each_with_its_fix(sample_config, units, doctor_rows):
    _findings_server(sample_config, units)
    rows = {c["label"]: c for c in adopt.findings(sample_config, adopt.scan(sample_config))["checks"]}

    keys = rows["Mod keys"]
    assert not keys["ok"] and keys["fix"] == adopt.FIX_COPY_KEYS
    assert "Dabs.bikey" in keys["detail"] and "CF.bikey" not in keys["detail"]
    assert "@MyRadZone has no .bikey" in keys["detail"]
    assert rows["Mod folders not loaded"]["ok"] and "@UnusedMod" in rows["Mod folders not loaded"]["detail"]
    pasted = rows["Mod types in the mission"]
    assert not pasted["ok"] and pasted["fix"] == adopt.FIX_PASTED_TYPES and "@MyRadZone" in pasted["detail"]


def test_with_no_server_dir_adopts_own_rows_are_skipped_not_green(sample_config, units, doctor_rows, tmp_path):
    """On exp, whose server dir does not exist, Mod
    keys, Mod folders not loaded and Mod types in the mission all showed green."""
    sample_config.server_dir = str(tmp_path / "server-exp")
    result = adopt.findings(sample_config, adopt.scan(sample_config))
    labels = {c["label"] for c in result["checks"]}
    assert not labels & {"Mod keys", "Mod folders not loaded", "Mod types in the mission"}
    skipped = {s["label"]: s["reason"] for s in result["skipped"]}
    assert skipped["Mod keys"] == "no server directory"


def test_apply_alone_fixes_nothing(sample_config, units, doctor_rows):
    server = _findings_server(sample_config, units)
    adopt.apply(sample_config, adopt.scan(sample_config))
    assert not (server / "keys" / "Dabs.bikey").exists()
    assert [p.mod for p in adopt.scan(sample_config).pasted] == ["@MyRadZone"]


def test_copying_the_missing_keys_adds_and_never_replaces(sample_config, units):
    server = _findings_server(sample_config, units)
    lines = adopt.copy_missing_keys(sample_config, adopt.scan(sample_config))
    assert (server / "keys" / "Dabs.bikey").read_bytes() == b"dabs"
    assert (server / "keys" / "CF.bikey").read_bytes() == b"cf-already"
    assert lines == ["@Dabs Framework: copied Dabs.bikey into keys/."]
    assert adopt.missing_keys(server, adopt.scan(sample_config))[0] == {}


# --- the surfaces, D4 (step 7) ---------------------------------------------------


def _adopt_cli(monkeypatch, cfg, *args, ni=True):
    from click.testing import CliRunner

    from app.commands import adopt as adopt_cmd_mod

    monkeypatch.setattr(adopt_cmd_mod, "load_context", lambda ctx: (cfg, ni))
    return CliRunner().invoke(adopt_cmd_mod.adopt_cmd, list(args), obj={})


def test_zsm_adopt_reports_and_writes_nothing(sample_config, units, doctor_rows, monkeypatch):
    from app.modmgr import modlist

    _findings_server(sample_config, units)
    result = _adopt_cli(monkeypatch, sample_config)

    assert result.exit_code == 0, result.output
    assert "@CF" in result.output and "Mod keys" in result.output
    assert "--copy-keys" in result.output and "--remove-pasted" in result.output
    assert modlist.installed(sample_config) == []


def test_zsm_adopt_apply_writes_the_list(sample_config, units, monkeypatch):
    from app.modmgr import modlist

    _findings_server(sample_config, units)
    result = _adopt_cli(monkeypatch, sample_config, "--apply")

    assert result.exit_code == 0, result.output
    assert "@CF" in modlist.installed(sample_config)
    assert "Adopted" in result.output


def test_zsm_adopt_fixes_run_only_when_named(sample_config, units, monkeypatch):
    from app.modmgr import modlist

    server = _findings_server(sample_config, units)
    result = _adopt_cli(monkeypatch, sample_config, "--copy-keys")

    assert result.exit_code == 0, result.output
    assert (server / "keys" / "Dabs.bikey").exists()
    assert modlist.installed(sample_config) == [], "a fix is not an Apply"


def test_disable_unit_refuses_zsms_own(sample_config, units):
    report = adopt.scan(sample_config)
    with pytest.raises(adopt.AdoptError):
        adopt.disable_unit(report, "dayz-server.service")


@pytest.mark.parametrize("answer", [True, False])
def test_init_offers_adopting_once(sample_config, units, monkeypatch, answer):
    from app.commands import adopt as adopt_cmd_mod
    from app.commands import init as init_mod
    from app.modmgr import modlist

    _findings_server(sample_config, units)
    asked = []
    monkeypatch.setattr(adopt_cmd_mod, "confirm", lambda msg, **k: asked.append(msg) or answer)

    init_mod._offer_adopt(sample_config)

    assert len(asked) == 1
    assert bool(modlist.installed(sample_config)) is answer


def test_init_does_not_offer_over_a_list_zsm_keeps(sample_config, units, monkeypatch):
    from app.commands import adopt as adopt_cmd_mod
    from app.commands import init as init_mod
    from app.modmgr import modlist

    _findings_server(sample_config, units)
    modlist.update(sample_config, lambda ml: ml.mods.append(modlist.Mod(name="@CF", id="1559212036")))
    monkeypatch.setattr(adopt_cmd_mod, "confirm", lambda *a, **k: pytest.fail("asked"))

    init_mod._offer_adopt(sample_config)


# --- after the browser pass (step 7) -----------------------------------------------


def test_a_server_side_mod_needs_no_key(sample_config, units):
    _findings_server(sample_config, units)
    _missing, keyless = adopt.missing_keys(sample_config.server_dir_path, adopt.scan(sample_config))
    assert "@AdminTool" not in keyless


def test_the_mod_list_names_where_the_command_came_from(sample_config, units):
    _findings_server(sample_config, units)
    lines = adopt.apply(sample_config, adopt.scan(sample_config))
    assert "from the server log" in lines[0]


@pytest.mark.parametrize("unit, says", [
    ("dayz-server.service", "service install --replace"),
    ("mydayz.service", "--disable-unit"),
    (None, "Nothing Zedstead wrote starts the server"),
])
def test_apply_says_what_still_starts_the_server(sample_config, units, unit, says):
    server = _findings_server(sample_config, units)
    if unit:
        _unit(units["system"], unit, server)
    lines = adopt.apply(sample_config, adopt.scan(sample_config))
    assert says in lines[-1]


def test_nothing_is_said_when_zsms_unit_runs_the_server(sample_config, units):
    from app.server.unit_guard import ZSM_UNIT_MARKER

    server = _findings_server(sample_config, units)
    _unit(units["system"], "dayz-server.service", server, extra=ZSM_UNIT_MARKER)
    assert adopt.handover(sample_config, adopt.scan(sample_config)) == ""


# --- found by the step 9 run on the container ---------------------------------


def test_a_unit_that_runs_a_start_script_is_the_servers(sample_config, units):
    server = sample_config.server_dir_path
    script = server / "start.sh"
    script.write_text("#!/bin/bash\nexec ./DayZServer -config=serverDZ.cfg -port=2602\n")
    _unit(units["system"], "mydayz.service", server, exec_line=f"ExecStart={script}")
    _unit(units["system"], "backup.service", server, exec_line="ExecStart=/usr/bin/tar -czf /tmp/b.tgz .")
    names = [u.name for u in adopt.scan(sample_config).units]
    assert names == ["mydayz.service"]


def test_a_script_outside_the_server_dir_is_not_the_servers(sample_config, units, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    script = elsewhere / "start.sh"
    script.write_text("exec ./DayZServer\n")
    _unit(units["system"], "other.service", sample_config.server_dir_path, exec_line=f"ExecStart={script}")
    assert adopt.scan(sample_config).units == []


def test_the_hints_name_the_instance(sample_config, units, monkeypatch):
    _findings_server(sample_config, units)
    monkeypatch.setattr(sample_config, "instance_id", "adopt9", raising=False)
    found = {"checks": [{"label": "Mod keys", "ok": False, "detail": "x", "fix": adopt.FIX_COPY_KEYS}], "skipped": [], "log": {}}
    monkeypatch.setattr(adopt, "findings", lambda c, r: found)
    out = _adopt_cli(monkeypatch, sample_config).output
    assert "zsm --instance adopt9 adopt --apply" in out
    assert "zsm --instance adopt9 adopt --copy-keys" in out
