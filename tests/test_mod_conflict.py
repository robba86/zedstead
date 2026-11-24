"""Tests for cli/mod_conflict.py — mod conflict detection."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from tests.conftest import write_test_ids, write_test_mods

from app.modmgr.mod_conflict import (
    _CLASSNAME_CACHE_FILE,
    ConflictItem,
    ScanResult,
    _extract_addon_name_binary,
    _extract_class_names_binary,
    _extract_required_addons_binary,
    _read_pbo_entry,
    check_missing_bikeys,
    check_missing_deps,
    check_required_addons,
    check_rpt_errors,
    extract_mod_classnames_cached,
    resolve_mod_load_order,
    scan_conflicts,
)


# ── check_rpt_errors ────────────────────────────────────────────────


class TestCheckRptErrors:
    def test_no_rpt_files_returns_empty(self, tmp_path: Path):
        items, name = check_rpt_errors(tmp_path)
        assert items == []
        assert name == ""

    def test_nonexistent_dir_returns_empty(self, tmp_path: Path):
        items, name = check_rpt_errors(tmp_path / "nope")
        assert items == []
        assert name == ""

    def test_detects_script_compile_error(self, tmp_path: Path):
        rpt = tmp_path / "server.RPT"
        rpt.write_text("10:00:00 Cannot compile script module\n")
        items, name = check_rpt_errors(tmp_path)
        assert len(items) == 1
        assert items[0].severity == "error"
        assert items[0].message == "Script compile error"
        assert name == "server.RPT"

    def test_detects_missing_addon_dep(self, tmp_path: Path):
        rpt = tmp_path / "server.RPT"
        rpt.write_text("10:00:00 Addon 'ModA' requires addon 'ModB'\n")
        items, _ = check_rpt_errors(tmp_path)
        assert len(items) == 1
        assert items[0].severity == "error"
        assert items[0].message == "Missing addon dependency"

    def test_detects_duplicate_addon(self, tmp_path: Path):
        rpt = tmp_path / "server.RPT"
        rpt.write_text("10:00:00 Duplicate addon detected\n")
        items, _ = check_rpt_errors(tmp_path)
        assert len(items) == 1
        assert items[0].severity == "warning"
        assert items[0].message == "Duplicate addon conflict"

    def test_detects_cannot_create_type(self, tmp_path: Path):
        rpt = tmp_path / "server.RPT"
        rpt.write_text("10:00:00 cannot create type SomeClass\n")
        items, _ = check_rpt_errors(tmp_path)
        assert len(items) == 1
        assert items[0].severity == "error"
        assert items[0].message == "Class conflict"

    def test_detects_script_variable_error(self, tmp_path: Path):
        rpt = tmp_path / "server.RPT"
        rpt.write_text("10:00:00 C42: can't find variable 'myVar'\n")
        items, _ = check_rpt_errors(tmp_path)
        assert len(items) == 1
        assert items[0].severity == "error"
        assert items[0].message == "Script variable error"

    def test_extracts_mod_name_from_context(self, tmp_path: Path):
        rpt = tmp_path / "server.RPT"
        rpt.write_text(
            "10:00:00 Loading @TestMod/scripts/config.cpp\n"
            "10:00:01 Cannot compile script module\n"
        )
        items, _ = check_rpt_errors(tmp_path)
        assert len(items) == 1
        assert items[0].mod_name == "@TestMod"

    def test_deduplicates_same_error(self, tmp_path: Path):
        rpt = tmp_path / "server.RPT"
        lines = "10:00:00 Cannot compile script module\n" * 10
        rpt.write_text(lines)
        items, _ = check_rpt_errors(tmp_path)
        # Same category + same mod ("") = 1 item
        assert len(items) == 1

    def test_ignores_non_matching_lines(self, tmp_path: Path):
        rpt = tmp_path / "server.RPT"
        rpt.write_text("10:00:00 Server started successfully\n10:00:01 Player connected\n")
        items, _ = check_rpt_errors(tmp_path)
        assert items == []

    def test_uses_newest_rpt(self, tmp_path: Path):
        old = tmp_path / "old.RPT"
        old.write_text("10:00:00 Cannot compile script module\n")
        # Ensure different mtime
        time.sleep(0.05)
        new = tmp_path / "new.RPT"
        new.write_text("10:00:00 Server started successfully\n")
        items, name = check_rpt_errors(tmp_path)
        assert items == []
        assert name == "new.RPT"


# ── check_missing_deps ──────────────────────────────────────────────


class TestCheckMissingDeps:
    def test_no_mods_returns_empty(self, sample_config):
        write_test_mods(sample_config, [])
        items = check_missing_deps(sample_config)
        assert items == []

    def test_all_deps_satisfied(self, sample_config):
        write_test_mods(sample_config, ['@CF', '@TraderPlus'])
        write_test_ids({'@CF': '1559212036', '@TraderPlus': '2458896948'})
        items = check_missing_deps(sample_config)
        assert items == []

    def test_missing_dep_detected(self, sample_config):
        write_test_mods(sample_config, ['@TraderPlus'])
        write_test_ids({'@TraderPlus': '2458896948'})
        items = check_missing_deps(sample_config)
        assert len(items) == 1
        assert items[0].severity == "error"
        assert items[0].category == "missing_dep"
        assert "@CF" in items[0].message

    def test_missing_dep_carries_installable_ref(self, sample_config):
        # The web Conflicts tab installs the dep from these two fields — the
        # message string alone isn't addressable.
        write_test_mods(sample_config, ['@TraderPlus'])
        write_test_ids({'@TraderPlus': '2458896948'})
        items = check_missing_deps(sample_config)
        assert items[0].dep_name == "@CF"
        assert items[0].dep_id == "1559212036"

    def test_unknown_mods_no_issues(self, sample_config):
        write_test_mods(sample_config, ['@SomeRandomMod'])
        write_test_ids({'@SomeRandomMod': '9999999'})
        items = check_missing_deps(sample_config)
        assert items == []

    def test_bicycle_without_survivor_animations_is_reported(self, sample_config):
        # Neither config.cpp names it in requiredAddons; the server segfaults
        # at boot without it (2026 Q4).
        write_test_mods(sample_config, ['@CF', '@DayZ-Bicycle', '@Arma Bicycles'])
        write_test_ids({'@CF': '1559212036', '@DayZ-Bicycle': '2971190303', '@Arma Bicycles': '2971993210'})
        items = check_missing_deps(sample_config)
        assert [(i.dep_id, i.mod_name) for i in items] == [("2918418331", "@DayZ-Bicycle")]


def _workshop_cache(entries: dict) -> None:
    import json

    from app.modmgr.mod_ops import workshop_deps_cache

    path = workshop_deps_cache()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries), encoding="utf-8")


class TestWorkshopRequiredItems:
    def test_a_cached_required_item_that_is_missing_is_reported(self, sample_config):
        write_test_mods(sample_config, ['@Parent'])
        write_test_ids({'@Parent': '9000001'})
        _workshop_cache({"9000001": {"children": ["9000002"], "answered": True},
                         "9000002": {"children": [], "title": "Needed Thing"}})
        items = check_missing_deps(sample_config)
        assert len(items) == 1
        assert (items[0].dep_id, items[0].dep_name, items[0].mod_name) == ("9000002", "@Needed Thing", "@Parent")
        assert items[0].category == "missing_dep"

    def test_an_installed_required_item_is_not_reported(self, sample_config):
        write_test_mods(sample_config, ['@Parent', '@Needed Thing'])
        write_test_ids({'@Parent': '9000001', '@Needed Thing': '9000002'})
        _workshop_cache({"9000001": {"children": ["9000002"], "answered": True}})
        assert check_missing_deps(sample_config) == []

    def test_a_dependency_both_sources_name_is_reported_once(self, sample_config):
        write_test_mods(sample_config, ['@TraderPlus'])
        write_test_ids({'@TraderPlus': '2458896948'})
        _workshop_cache({"2458896948": {"children": ["1559212036"], "answered": True}})
        assert [i.dep_id for i in check_missing_deps(sample_config)] == ["1559212036"]

    def test_the_scan_never_asks_steam_unless_told(self, sample_config, monkeypatch):
        from app.modmgr import mod_ops

        def _no(*a, **k):
            raise AssertionError("asked Steam")

        monkeypatch.setattr(mod_ops, "resolve_workshop_deps", _no)
        sample_config.steam_api_key = "key"
        write_test_mods(sample_config, ['@Parent'])
        write_test_ids({'@Parent': '9000001'})
        assert check_missing_deps(sample_config) == []

    def test_fetch_fills_the_cache_with_a_key(self, sample_config, monkeypatch):
        from app.modmgr import mod_ops, workshop_api

        monkeypatch.setattr(workshop_api, "get_workshop_children", lambda ids, key, timeout=30: {"9000001": ["9000002"]})
        monkeypatch.setattr(workshop_api, "get_workshop_details", lambda ids, key, timeout=30: {})
        sample_config.steam_api_key = "key"
        write_test_mods(sample_config, ['@Parent'])
        write_test_ids({'@Parent': '9000001'})
        items = check_missing_deps(sample_config, fetch_workshop=True)
        assert [(i.dep_id, i.dep_name) for i in items] == [("9000002", "@Mod 9000002")]
        assert mod_ops.workshop_deps_cache().is_file()


# ── check_missing_bikeys ────────────────────────────────────────────


class TestCheckMissingBikeys:
    def test_bikeys_present_no_warning(self, sample_config):
        server = sample_config.server_dir_path
        write_test_mods(sample_config, ['@TestMod'])
        # Mod ships a bikey
        mod_keys = server / "@TestMod" / "Keys"
        mod_keys.mkdir(parents=True)
        (mod_keys / "testmod.bikey").write_text("")
        # Key installed in keys dir
        (server / "keys" / "testmod.bikey").write_text("")
        items = check_missing_bikeys(sample_config)
        assert items == []

    def test_missing_bikey_detected(self, sample_config):
        server = sample_config.server_dir_path
        write_test_mods(sample_config, ['@TestMod'])
        mod_keys = server / "@TestMod" / "Keys"
        mod_keys.mkdir(parents=True)
        (mod_keys / "testmod.bikey").write_text("")
        # keys/ dir exists but bikey not copied
        items = check_missing_bikeys(sample_config)
        assert len(items) == 1
        assert items[0].severity == "warning"
        assert items[0].category == "missing_bikey"
        assert items[0].mod_name == "@TestMod"
        assert "testmod.bikey" in items[0].message

    def test_mod_without_bikeys_skipped(self, sample_config):
        server = sample_config.server_dir_path
        write_test_mods(sample_config, ['@ClientMod'])
        (server / "@ClientMod").mkdir()
        items = check_missing_bikeys(sample_config)
        assert items == []

    def test_missing_mod_dir_skipped(self, sample_config):
        write_test_mods(sample_config, ['@GhostMod'])
        items = check_missing_bikeys(sample_config)
        assert items == []


# ── binary extraction helpers ────────────────────────────────────────


class TestExtractRequiredAddonsBinary:
    def test_extracts_addon_names_rap_format(self):
        # raP format: requiredAddons\x00 + count(compressed int) + elements
        # Each string element: \x00 (type=string) + string\x00
        data = (
            b"\x00\x00requiredAddons\x00"
            b"\x03"  # count = 3
            b"\x00DZ_Data\x00"  # string element
            b"\x00CF\x00"  # string element
            b"\x00MyAddon\x00"  # string element
        )
        result = _extract_required_addons_binary(data)
        assert result == ["DZ_Data", "CF", "MyAddon"]

    def test_does_not_pick_up_sibling_fields(self):
        # After the array elements, there might be other properties
        # They should NOT be extracted
        data = (
            b"requiredAddons\x00"
            b"\x01"  # count = 1
            b"\x00DZ_Data\x00"  # string element
            # Next property (should not be extracted):
            b"\x01units\x00\x00\x00\x00\x00"
        )
        result = _extract_required_addons_binary(data)
        assert result == ["DZ_Data"]
        assert "units" not in result

    def test_returns_empty_for_no_marker(self):
        data = b"\x00\x00some random binary data\x00\x00"
        assert _extract_required_addons_binary(data) == []

    def test_handles_empty_data(self):
        assert _extract_required_addons_binary(b"") == []

    def test_handles_zero_count(self):
        data = b"requiredAddons\x00\x00"  # count = 0
        assert _extract_required_addons_binary(data) == []


class TestExtractAddonNameBinary:
    def test_extracts_cfgpatches_class_name(self):
        data = _build_rap_config("MyModAddon")
        result = _extract_addon_name_binary(data)
        assert result == "MyModAddon"

    def test_returns_empty_for_no_cfgpatches(self):
        data = b"\x00\x00some binary\x00\x00"
        assert _extract_addon_name_binary(data) == ""

    def test_handles_empty_data(self):
        assert _extract_addon_name_binary(b"") == ""


# ── check_required_addons ────────────────────────────────────────────


def _build_pbo(entries: dict[str, bytes]) -> bytes:
    """Build a minimal synthetic PBO file from {filename: content} entries."""
    import struct

    header = b""
    body = b""
    for name, content in entries.items():
        header += name.encode("ascii") + b"\x00"
        # packing=0, original_size, reserved=0, timestamp=0, data_size
        header += struct.pack("<5I", 0, len(content), 0, 0, len(content))
        body += content
    # Empty-name entry marks end of header
    header += b"\x00" + struct.pack("<5I", 0, 0, 0, 0, 0)
    return header + body


def _build_rap_config(addon_name: str, required: list[str] | None = None) -> bytes:
    """Build a minimal raP config.bin with CfgPatches and requiredAddons.

    Produces valid raP binary that the parser can follow:
    - raP header (16 bytes)
    - Root body with 1 entry: CfgPatches class
    - CfgPatches body with 1 child class entry
    - Child body with requiredVersion + requiredAddons array
    """
    import struct

    # We'll build bottom-up, computing offsets

    # Child class body (addon_name's body): inherited\x00 + num_entries + entries
    child_entries = b""
    num_child_entries = 1  # requiredVersion
    # requiredVersion = 0.1 (type=1, name, float value)
    child_entries += b"\x01" + b"requiredVersion\x00" + struct.pack("<f", 0.1)
    if required is not None:
        num_child_entries += 1
        # requiredAddons array (type=2, name, compressed count, elements)
        arr_data = b"\x02" + b"requiredAddons\x00"
        arr_data += bytes([len(required)])
        for r in required:
            arr_data += b"\x00" + r.encode("ascii") + b"\x00"
        child_entries += arr_data
    child_body = b"\x00" + bytes([num_child_entries]) + child_entries

    # CfgPatches body: inherited\x00 + 1 entry (class addon_name)
    # The class entry points to child_body offset (computed below)
    patches_body_content = b"\x00\x01"  # inherited="" + 1 entry

    # Root body: inherited\x00 + 1 entry (class CfgPatches)
    root_body = b"\x00\x01"  # inherited="" + 1 entry

    # raP header: \x00raP + 0(4) + 8(4) + enum_offset(4)
    header = b"\x00raP\x00\x00\x00\x00\x08\x00\x00\x00"

    # Compute offsets:
    # header = 16 bytes (including enum_offset placeholder)
    # root_body starts at 16
    # root entry: type(1) + "CfgPatches\x00"(11) + offset(4) = 16 bytes
    # patches_body starts at: 16 + 2 + 16 = 34
    # patches entry: type(1) + addon_name\x00(len+1) + offset(4)
    patches_entry_size = 1 + len(addon_name) + 1 + 4
    # child_body starts at: 34 + 2 + patches_entry_size
    child_body_offset = 34 + 2 + patches_entry_size
    patches_body_offset = 34

    # enum offset (put at end, we don't use enums)
    total_size = child_body_offset + len(child_body)
    enum_offset = total_size

    header += struct.pack("<I", enum_offset)

    # Root body + CfgPatches entry with offset
    root = root_body + b"\x00" + b"CfgPatches\x00" + struct.pack("<I", patches_body_offset)

    # CfgPatches body + child class entry with offset
    patches = patches_body_content + b"\x00" + addon_name.encode() + b"\x00" + struct.pack("<I", child_body_offset)

    return header + root + patches + child_body


class TestReadPboEntry:
    def test_reads_entry_from_pbo(self, tmp_path: Path):
        content = b"hello world"
        pbo = _build_pbo({"config.bin": content, "data.bin": b"other"})
        pbo_path = tmp_path / "test.pbo"
        pbo_path.write_bytes(pbo)
        assert _read_pbo_entry(pbo_path, "config.bin") == content

    def test_returns_none_for_missing_entry(self, tmp_path: Path):
        pbo = _build_pbo({"data.bin": b"stuff"})
        pbo_path = tmp_path / "test.pbo"
        pbo_path.write_bytes(pbo)
        assert _read_pbo_entry(pbo_path, "config.bin") is None

    def test_returns_none_for_missing_file(self, tmp_path: Path):
        assert _read_pbo_entry(tmp_path / "nope.pbo", "config.bin") is None

    def test_reads_the_header_and_the_entry_never_the_whole_file(self, tmp_path: Path, monkeypatch):
        """plans/68 CON-10: a map PBO runs to a GB or more, and reading all of
        it to find config.cpp cost that much memory after every mod update.
        Here a 4 MiB entry sits before the config, and no single read may be
        anywhere near the size of that entry."""
        import builtins
        import struct

        from app.modmgr import mod_conflict

        config = b"\x00raP" + b"x" * 100
        big = 4 * 1024 * 1024
        pbo_path = tmp_path / "map.pbo"
        with open(pbo_path, "wb") as f:
            f.write(b"\x00" + struct.pack("<5I", 0x56657273, 0, 0, 0, 0))
            f.write(b"prefix\x00map_data\x00\x00")
            f.write(b"data\\terrain.bin\x00" + struct.pack("<5I", 0, big, 0, 0, big))
            f.write(b"config.cpp\x00" + struct.pack("<5I", 0, len(config), 0, 0, len(config)))
            f.write(b"\x00" + struct.pack("<5I", 0, 0, 0, 0, 0))
            f.truncate(f.tell() + big)
            f.seek(0, 2)
            f.write(config)

        reads: list[int] = []

        class _Recording:
            def __init__(self, f):
                self._f = f

            def read(self, *args):
                data = self._f.read(*args)
                reads.append(len(data))
                return data

            def __getattr__(self, name):
                return getattr(self._f, name)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return self._f.__exit__(*exc)

        monkeypatch.setattr(
            mod_conflict, "open", lambda *a, **k: _Recording(builtins.open(*a, **k)), raising=False
        )
        assert _read_pbo_entry(pbo_path, "config.cpp") == config
        assert _read_pbo_entry(pbo_path, "config.bin") is None
        assert reads and max(reads) < big


# ── _extract_class_names_binary ─────────────────────────────────────


def _rap_str(s: str) -> bytes:
    return s.encode("ascii") + b"\x00"


def _rap_array(elements: list) -> bytes:
    """raP array payload: count + typed elements (str, float, int, list = nested array)."""
    import struct

    out = bytes([len(elements)])
    for el in elements:
        if isinstance(el, str):
            out += b"\x00" + _rap_str(el)
        elif isinstance(el, float):
            out += b"\x01" + struct.pack("<f", el)
        elif isinstance(el, int):
            out += b"\x02" + struct.pack("<i", el)
        else:
            out += b"\x03" + _rap_array(el)
    return out


def _build_rap(root: list[tuple]) -> bytes:
    """Serialize a raP config.bin from nested entries.

    ("class", name, [entries]) · ("extern", name) — `class Foo;` · ("delete", name)
    · ("string"|"int"|"float", name, value) · ("array", name, [elements])
    · ("array+=", name, [elements]) — the flagged array form.
    """
    import struct

    def body(entries: list[tuple], at: int) -> bytes:
        # Entry sizes don't depend on offsets, so lay out this body first and
        # place each child body after it, in order.
        parts: list[tuple[bytes, list | None]] = []
        for kind, name, *rest in entries:
            if kind == "class":
                parts.append((b"\x00" + _rap_str(name), rest[0]))
            elif kind == "extern":
                parts.append((b"\x03" + _rap_str(name), None))
            elif kind == "delete":
                parts.append((b"\x04" + _rap_str(name), None))
            elif kind == "string":
                parts.append((b"\x01\x00" + _rap_str(name) + _rap_str(rest[0]), None))
            elif kind == "float":
                parts.append((b"\x01\x01" + _rap_str(name) + struct.pack("<f", rest[0]), None))
            elif kind == "int":
                parts.append((b"\x01\x02" + _rap_str(name) + struct.pack("<i", rest[0]), None))
            elif kind == "array":
                payload = rest[0] if isinstance(rest[0], bytes) else _rap_array(rest[0])
                parts.append((b"\x02" + _rap_str(name) + payload, None))
            elif kind == "array+=":
                parts.append((b"\x05" + struct.pack("<I", 1) + _rap_str(name) + _rap_array(rest[0]), None))
        head = b"\x00" + bytes([len(entries)])
        size = len(head) + sum(len(p) + (4 if kids is not None else 0) for p, kids in parts)
        out, tail, child_at = head, b"", at + size
        for p, kids in parts:
            if kids is None:
                out += p
                continue
            out += p + struct.pack("<I", child_at)
            child = body(kids, child_at)
            tail += child
            child_at += len(child)
        return out + tail

    root_body = body(root, 16)
    enum_offset = 16 + len(root_body)
    return b"\x00raP" + struct.pack("<III", 0, 8, enum_offset) + root_body + b"\x00\x00\x00\x00"


class TestExtractClassNamesBinary:
    def test_forward_declarations_do_not_end_the_scan(self):
        # The shape nearly every mod ships: base classes declared, then defined.
        # Expansion's vehicles_ground_uaz.pbo opens CfgVehicles with 20 of these,
        # and the scan used to stop at the first one and report nothing.
        data = _build_rap([
            ("class", "CfgVehicles", [
                ("extern", "CarScript"),
                ("extern", "Inventory_Base"),
                ("class", "ExpansionUAZ", [("string", "displayName", "UAZ")]),
                ("class", "ExpansionBus", []),
            ]),
        ])
        assert _extract_class_names_binary(data, "CfgVehicles") == ["ExpansionUAZ", "ExpansionBus"]

    def test_values_arrays_and_deletes_between_classes_are_skipped(self):
        data = _build_rap([
            ("class", "CfgVehicles", [
                ("int", "scope", 2),
                ("class", "First", []),
                ("array", "hiddenSelections", ["camo", 1.5, 3, ["nested", 4]]),
                ("array+=", "attachments", ["Hat"]),
                ("delete", "Removed"),
                ("float", "weight", 0.5),
                ("class", "Second", []),
            ]),
        ])
        assert _extract_class_names_binary(data, "CfgVehicles") == ["First", "Second"]

    def test_only_direct_children_are_reported(self):
        data = _build_rap([
            ("class", "CfgVehicles", [
                ("class", "ExpansionUAZ", [("class", "Cargo", []), ("class", "AnimationSources", [])]),
            ]),
        ])
        assert _extract_class_names_binary(data, "CfgVehicles") == ["ExpansionUAZ"]

    def test_section_name_is_case_insensitive(self):
        data = _build_rap([("class", "cfgVehicles", [("class", "ModCar", [])])])
        assert _extract_class_names_binary(data, "CfgVehicles") == ["ModCar"]

    def test_a_longer_name_ending_in_the_section_is_not_the_section(self):
        # A byte search for "CfgVehicles\0" also matches inside "MyCfgVehicles\0".
        data = _build_rap([
            ("class", "MyCfgVehicles", [("class", "Wrong", [])]),
            ("class", "CfgVehicles", [("class", "ModCar", [])]),
        ])
        assert _extract_class_names_binary(data, "CfgVehicles") == ["ModCar"]

    def test_other_sections_can_be_read(self):
        data = _build_rap([
            ("class", "CfgVehicles", [("class", "ModCar", [])]),
            ("class", "CfgWeapons", [("extern", "Rifle_Base"), ("class", "Expansion_M16", [])]),
        ])
        assert _extract_class_names_binary(data, "CfgWeapons") == ["Expansion_M16"]

    def test_missing_section_or_non_rap_data_returns_empty(self):
        assert _extract_class_names_binary(_build_rap([("class", "CfgPatches", [])]), "CfgVehicles") == []
        assert _extract_class_names_binary(b"", "CfgVehicles") == []
        assert _extract_class_names_binary(b"class CfgVehicles { class A {}; };", "CfgVehicles") == []

    def test_truncated_data_never_raises(self):
        data = _build_rap([
            ("class", "CfgVehicles", [
                ("extern", "CarScript"),
                ("array", "list", ["a", ["b", 1]]),
                ("class", "A", [("int", "x", 1)]),
                ("class", "B", []),
            ]),
        ])
        for cut in range(len(data)):
            assert isinstance(_extract_class_names_binary(data[:cut], "CfgVehicles"), list)

    def test_deeply_nested_arrays_are_skipped_without_recursion(self):
        """A mod's config.bin can nest arrays as deep as its bytes allow; 5000
        levels raised RecursionError, which the scan did not catch, and one PBO
        took the class scan of the whole mod list down (plans/68 CON-9)."""
        deep = b"\x01\x03" * 5000 + b"\x01\x00x\x00"  # [[[...["x"]...]]]
        data = _build_rap([
            ("class", "CfgVehicles", [
                ("class", "First", []),
                ("array", "deep", deep),
                ("class", "Second", []),
            ]),
        ])
        assert _extract_class_names_binary(data, "CfgVehicles") == ["First", "Second"]
        # Cut inside the nesting: still no exception, and what came before it is kept.
        assert _extract_class_names_binary(data[: len(data) // 2], "CfgVehicles") == ["First"]


class TestExtractModClassnamesCached:
    def _mod(self, server_dir: Path) -> Path:
        addons = server_dir / "@Mod" / "addons"
        addons.mkdir(parents=True)
        pbo = addons / "mod.pbo"
        config = _build_rap([("class", "CfgVehicles", [("extern", "CarScript"), ("class", "ModCar", [])])])
        pbo.write_bytes(_build_pbo({"config.bin": config}))
        return pbo

    def test_an_entry_cached_by_the_old_parser_is_rescanned(self, tmp_path: Path):
        import json

        pbo = self._mod(tmp_path)
        # What the pre-fix parser left behind: a matching mtime and no classes.
        # Trusted as-is, the fix would never reach an existing install.
        (tmp_path / _CLASSNAME_CACHE_FILE).write_text(
            json.dumps({"@Mod": {"mtime": pbo.stat().st_mtime, "classes": []}})
        )
        assert extract_mod_classnames_cached(tmp_path, ["@Mod"]) == {"@Mod": ["ModCar"]}

    def test_a_current_entry_is_reused(self, tmp_path: Path, monkeypatch):
        import app.modmgr.mod_conflict as mc

        self._mod(tmp_path)
        assert extract_mod_classnames_cached(tmp_path, ["@Mod"]) == {"@Mod": ["ModCar"]}

        def _no_rescan(*_a, **_k):
            raise AssertionError("cache entry should have been reused")

        monkeypatch.setattr(mc, "extract_mod_classnames", _no_rescan)
        assert extract_mod_classnames_cached(tmp_path, ["@Mod"]) == {"@Mod": ["ModCar"]}


class TestCheckRequiredAddons:
    def test_no_mods_returns_empty(self, sample_config):
        write_test_mods(sample_config, [])
        items, debug = check_required_addons(sample_config)
        assert items == []

    def test_no_addons_dir_returns_empty(self, sample_config):
        write_test_mods(sample_config, ['@TestMod'])
        (sample_config.server_dir_path / "@TestMod").mkdir()
        items, debug = check_required_addons(sample_config)
        assert items == []

    def test_detects_unsatisfied_addon(self, sample_config):
        server = sample_config.server_dir_path
        write_test_mods(sample_config, ['@ModB'])
        addons = server / "@ModB" / "addons"
        addons.mkdir(parents=True)
        config_bin = _build_rap_config("ModB_core", ["SomeAddon"])
        (addons / "modb.pbo").write_bytes(_build_pbo({"config.bin": config_bin}))

        items, debug = check_required_addons(sample_config)
        addon_items = [i for i in items if i.category == "required_addon"]
        assert len(addon_items) == 1
        assert "SomeAddon" in addon_items[0].message
        assert addon_items[0].mod_name == "@ModB"
        assert debug.pbos_scanned == 1
        assert debug.configs_found == 1

    def test_satisfied_addon_no_warning(self, sample_config):
        server = sample_config.server_dir_path
        write_test_mods(sample_config, ['@ModA', '@ModB'])
        # ModA provides "ModA_core"
        addons_a = server / "@ModA" / "addons"
        addons_a.mkdir(parents=True)
        (addons_a / "moda.pbo").write_bytes(
            _build_pbo({"config.bin": _build_rap_config("ModA_core")})
        )
        # ModB requires "ModA_core"
        addons_b = server / "@ModB" / "addons"
        addons_b.mkdir(parents=True)
        (addons_b / "modb.pbo").write_bytes(
            _build_pbo({"config.bin": _build_rap_config("ModB_core", ["ModA_core"])})
        )

        items, debug = check_required_addons(sample_config)
        addon_items = [i for i in items if i.category == "required_addon"]
        assert addon_items == []
        assert "moda_core" in debug.provided_addons

    def test_vanilla_addons_skipped(self, sample_config):
        server = sample_config.server_dir_path
        write_test_mods(sample_config, ['@ModA'])
        addons = server / "@ModA" / "addons"
        addons.mkdir(parents=True)
        config_bin = _build_rap_config("ModA_core", ["DZ_Data", "DZ_Weapons"])
        (addons / "moda.pbo").write_bytes(_build_pbo({"config.bin": config_bin}))

        items, debug = check_required_addons(sample_config)
        addon_items = [i for i in items if i.category == "required_addon"]
        assert addon_items == []


# ── scan_conflicts (integration) ────────────────────────────────────


class TestScanConflicts:
    def test_clean_server_returns_no_errors(self, sample_config):
        server = sample_config.server_dir_path
        write_test_mods(sample_config, ['@CF'])
        write_test_ids({'@CF': '1559212036'})
        # Create a clean RPT
        profiles = server / "profiles"
        (profiles / "server.RPT").write_text("10:00:00 Server started\n")
        # Install bikey
        mod_keys = server / "@CF" / "Keys"
        mod_keys.mkdir(parents=True)
        (mod_keys / "cf.bikey").write_text("")
        (server / "keys" / "cf.bikey").write_text("")

        result = scan_conflicts(sample_config)
        assert result.error_count == 0
        assert result.warning_count == 0
        assert result.mods_scanned == 1
        assert result.rpt_file == "server.RPT"
        assert result.scan_duration_ms >= 0

    def test_aggregates_all_checks(self, sample_config):
        server = sample_config.server_dir_path
        # Missing dep: TraderPlus without @CF
        write_test_mods(sample_config, ['@TraderPlus'])
        write_test_ids({'@TraderPlus': '2458896948'})
        # RPT error
        profiles = server / "profiles"
        (profiles / "server.RPT").write_text("10:00:00 Cannot compile script\n")
        # Missing bikey
        mod_keys = server / "@TraderPlus" / "Keys"
        mod_keys.mkdir(parents=True)
        (mod_keys / "trader.bikey").write_text("")

        result = scan_conflicts(sample_config)
        categories = {i.category for i in result.items}
        assert "rpt_error" in categories
        assert "missing_dep" in categories
        assert "missing_bikey" in categories

    def test_no_logs_hint_when_no_rpt(self, sample_config):
        write_test_mods(sample_config, [])
        result = scan_conflicts(sample_config)
        no_log_items = [i for i in result.items if i.category == "no_logs"]
        assert len(no_log_items) == 1
        assert no_log_items[0].severity == "info"

    def test_no_logs_hint_mentions_cleanup(self, sample_config):
        sample_config.log_cleanup_keep = 1
        write_test_mods(sample_config, [])
        result = scan_conflicts(sample_config)
        no_log_items = [i for i in result.items if i.category == "no_logs"]
        assert len(no_log_items) == 1
        assert "cleanup" in no_log_items[0].detail.lower()


# ── resolve_mod_load_order ──────────────────────────────────────────


def test_a_mod_name_that_is_not_one_folder_is_never_read(tmp_path, monkeypatch):
    """mods.txt arrives verbatim in a bundle: a ``..`` or absolute line used to
    be read outside the server directory, by the Mods page of any viewer."""
    from app.modmgr import mod_conflict

    server = tmp_path / "server"
    for folder in (server / "@Good" / "addons", tmp_path / "outside" / "@Evil" / "addons"):
        folder.mkdir(parents=True)
        (folder / "x.pbo").write_bytes(
            _build_pbo({"config.bin": _build_rap_config(f"{folder.parent.name[1:]}_Addon", ["DZ_Data"])})
        )
    opened = []
    real = mod_conflict._read_pbo_entry
    monkeypatch.setattr(mod_conflict, "_read_pbo_entry", lambda pbo, name: (opened.append(pbo), real(pbo, name))[1])

    names = ["@Good", "../outside/@Evil", str(tmp_path / "outside" / "@Evil"), "", ".", ".."]
    provided, _requires, _, _ = mod_conflict.scan_mod_pbos_cached(server, names)

    assert set(provided.values()) == {"@Good"} and "good_addon" in provided
    assert {p.parent.parent.name for p in opened} == {"@Good"}
    assert "evil_addon" not in (server / ".pbo_addon_cache.json").read_text().lower()


class TestResolveModLoadOrder:
    def _setup_mod_pbo(self, server_dir, mod_name, provides, requires_list=None):
        """Helper: create a mod with a PBO containing proper raP config.bin."""
        addons = server_dir / mod_name / "addons"
        addons.mkdir(parents=True, exist_ok=True)
        config_bin = _build_rap_config(provides, requires_list or [])
        pbo = _build_pbo({"config.bin": config_bin})
        (addons / f"{mod_name.lstrip('@').lower()}.pbo").write_bytes(pbo)

    def test_empty_list(self, tmp_path):
        assert resolve_mod_load_order([], tmp_path) == []

    def test_single_mod(self, tmp_path):
        self._setup_mod_pbo(tmp_path, "@CF", "CF")
        result = resolve_mod_load_order(["@CF"], tmp_path)
        assert result == ["@CF"]

    def test_dependency_ordered_before_dependent(self, tmp_path):
        self._setup_mod_pbo(tmp_path, "@CF", "CF")
        self._setup_mod_pbo(tmp_path, "@TraderPlus", "TraderPlus", ["CF"])
        # Input: TraderPlus first (wrong order)
        result = resolve_mod_load_order(["@TraderPlus", "@CF"], tmp_path)
        assert result.index("@CF") < result.index("@TraderPlus")

    def test_chain_a_b_c(self, tmp_path):
        self._setup_mod_pbo(tmp_path, "@ModA", "ModA_core")
        self._setup_mod_pbo(tmp_path, "@ModB", "ModB_core", ["ModA_core"])
        self._setup_mod_pbo(tmp_path, "@ModC", "ModC_core", ["ModB_core"])
        # Input: reverse order
        result = resolve_mod_load_order(["@ModC", "@ModB", "@ModA"], tmp_path)
        assert result == ["@ModA", "@ModB", "@ModC"]

    def test_vanilla_deps_ignored(self, tmp_path):
        self._setup_mod_pbo(tmp_path, "@MyMod", "MyMod_core", ["DZ_Data", "DZ_Weapons"])
        result = resolve_mod_load_order(["@MyMod"], tmp_path)
        assert result == ["@MyMod"]

    def test_mods_without_pbos_use_registry_tiebreaker(self, tmp_path):
        self._setup_mod_pbo(tmp_path, "@CF", "CF")
        # @NoPbo has no addons dir — no PBO dependency data
        (tmp_path / "@NoPbo").mkdir()
        # Registry says @CF before @NoPbo
        result = resolve_mod_load_order(
            ["@NoPbo", "@CF"], tmp_path, registry_order=["@CF", "@NoPbo"],
        )
        assert result == ["@CF", "@NoPbo"]

    def test_registry_order_as_tiebreaker(self, tmp_path):
        # Two independent mods (no deps between them)
        self._setup_mod_pbo(tmp_path, "@ModA", "ModA_core")
        self._setup_mod_pbo(tmp_path, "@ModB", "ModB_core")
        registry = ["@ModB", "@ModA"]  # registry says B before A
        result = resolve_mod_load_order(
            ["@ModA", "@ModB"], tmp_path, registry_order=registry,
        )
        assert result == ["@ModB", "@ModA"]

    def test_multiple_deps_diamond(self, tmp_path):
        # Diamond: D depends on B and C, both depend on A
        self._setup_mod_pbo(tmp_path, "@A", "A_core")
        self._setup_mod_pbo(tmp_path, "@B", "B_core", ["A_core"])
        self._setup_mod_pbo(tmp_path, "@C", "C_core", ["A_core"])
        self._setup_mod_pbo(tmp_path, "@D", "D_core", ["B_core", "C_core"])
        result = resolve_mod_load_order(["@D", "@C", "@B", "@A"], tmp_path)
        assert result.index("@A") < result.index("@B")
        assert result.index("@A") < result.index("@C")
        assert result.index("@B") < result.index("@D")
        assert result.index("@C") < result.index("@D")


# ── registered_factions (plans/47 B) ────────────────────────────────


class TestRegisteredFactions:
    def _mod(self, server_dir: Path, name: str, payload: bytes) -> Path:
        addons = server_dir / name / "addons"
        addons.mkdir(parents=True, exist_ok=True)
        pbo = addons / "scripts.pbo"
        pbo.write_bytes(payload)
        return pbo

    def test_reads_every_registration_across_mods(self, tmp_path: Path):
        from app.modmgr.mod_conflict import registered_factions

        self._mod(tmp_path, "@AI", b"eAIRegisterFaction(eAIFactionEast)\x00eAIRegisterFaction( eAIFactionWest )")
        self._mod(tmp_path, "@More", b"eAIRegisterFaction(eAIFactionPirates)")
        self._mod(tmp_path, "@Plain", b"no factions here")
        assert registered_factions(tmp_path, ["@AI", "@More", "@Plain"]) == {"East", "West", "Pirates"}

    def test_a_registration_split_across_chunks_is_found(self, tmp_path: Path, monkeypatch):
        from app.modmgr import mod_conflict

        monkeypatch.setattr(mod_conflict, "_FACTION_CHUNK", 16)
        self._mod(tmp_path, "@AI", b"x" * 10 + b"eAIRegisterFaction(eAIFactionMercenaries)" + b"y" * 40)
        assert mod_conflict.registered_factions(tmp_path, ["@AI"]) == {"Mercenaries"}

    def test_an_unchanged_mod_is_read_from_the_cache(self, tmp_path: Path, monkeypatch):
        from app.modmgr import mod_conflict

        self._mod(tmp_path, "@AI", b"eAIRegisterFaction(eAIFactionEast)")
        assert mod_conflict.registered_factions(tmp_path, ["@AI"]) == {"East"}

        def _no_rescan(pbo):
            raise AssertionError("an unchanged PBO was read again")

        monkeypatch.setattr(mod_conflict, "_scan_pbo_factions", _no_rescan)
        assert mod_conflict.registered_factions(tmp_path, ["@AI"]) == {"East"}

    def test_a_changed_mod_is_read_again_and_a_removed_one_dropped(self, tmp_path: Path):
        import json
        import os

        from app.modmgr.mod_conflict import _FACTION_CACHE_FILE, registered_factions

        pbo = self._mod(tmp_path, "@AI", b"eAIRegisterFaction(eAIFactionEast)")
        self._mod(tmp_path, "@Gone", b"eAIRegisterFaction(eAIFactionGhost)")
        registered_factions(tmp_path, ["@AI", "@Gone"])
        pbo.write_bytes(b"eAIRegisterFaction(eAIFactionWest)")
        os.utime(pbo, (time.time() + 5, time.time() + 5))
        assert registered_factions(tmp_path, ["@AI"]) == {"West"}
        cache = json.loads((tmp_path / _FACTION_CACHE_FILE).read_text())
        assert set(cache) == {"@AI"}


def test_a_manual_order_that_breaks_a_dependency_is_a_finding(sample_config, monkeypatch):
    """User test in 2026 Q4: Check mods said nothing about a manual order the
    Doctor already flagged. Each break is a warning with a fix hint."""
    from app.modmgr import mod_conflict, mods
    from app.ops.fix_hints import MODS_INSTALLED, conflict_fix
    from tests.conftest import set_test_flags

    monkeypatch.setattr(mods, "load_order_breaks", lambda config: [("@TraderPlus", "@CF")])
    assert mod_conflict.check_load_order(sample_config) == []

    set_test_flags(sample_config, manual=True)
    [item] = mod_conflict.check_load_order(sample_config)
    assert (item.severity, item.category, item.mod_name) == ("warning", "load_order", "@TraderPlus")
    assert item.message == "@TraderPlus loads before @CF, which it requires"
    assert conflict_fix(item)["links"] == [{"label": "Installed mods", "to": MODS_INSTALLED}]
