"""Tests for cli.json_utils."""

from __future__ import annotations

import json

import pytest

from app.shared.json_utils import (
    SourceFloat,
    atomic_write_lines,
    dumps_inline_arrays,
    dumps_json,
    dumps_json_over,
    load_json,
    load_json_preserving,
    save_json,
    validate_json_file,
)


def test_load_json_valid(tmp_path):
    p = tmp_path / "ok.json"
    p.write_text('{"key": "value"}', encoding="utf-8")
    assert load_json(p) == {"key": "value"}


def test_load_json_with_bom(tmp_path):
    p = tmp_path / "bom.json"
    p.write_bytes(b"\xef\xbb\xbf" + b'{"bom": true}')
    assert load_json(p) == {"bom": True}


def test_load_json_invalid_raises(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{broken", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError, match="bad.json"):
        load_json(p)


def test_load_json_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_json(tmp_path / "nope.json")


def test_validate_json_file_valid(tmp_path):
    p = tmp_path / "ok.json"
    p.write_text("[1, 2, 3]", encoding="utf-8")
    assert validate_json_file(p) is True


def test_validate_json_file_invalid(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{nope}", encoding="utf-8")
    assert validate_json_file(p) is False


def test_validate_json_file_missing(tmp_path):
    assert validate_json_file(tmp_path / "nope.json") is False


class TestAtomicWriteLines:
    def test_writes_newline_terminated_lines(self, tmp_path):
        p = tmp_path / "out.jsonl"
        atomic_write_lines(p, ["a", "b", "c"])
        assert p.read_text(encoding="utf-8") == "a\nb\nc\n"

    def test_accepts_a_generator(self, tmp_path):
        p = tmp_path / "out.jsonl"
        atomic_write_lines(p, (str(i) for i in range(3)))
        assert p.read_text(encoding="utf-8") == "0\n1\n2\n"

    def test_empty_iterable_truncates_to_empty(self, tmp_path):
        p = tmp_path / "out.jsonl"
        p.write_text("old\ncontent\n", encoding="utf-8")
        atomic_write_lines(p, [])
        assert p.read_text(encoding="utf-8") == ""

    def test_replaces_existing_atomically(self, tmp_path):
        p = tmp_path / "out.jsonl"
        p.write_text("stale\n", encoding="utf-8")
        atomic_write_lines(p, ["fresh"])
        assert p.read_text(encoding="utf-8") == "fresh\n"

    def test_creates_parent_dirs(self, tmp_path):
        p = tmp_path / "nested" / "deep" / "out.jsonl"
        atomic_write_lines(p, ["x"])
        assert p.read_text(encoding="utf-8") == "x\n"

    def test_no_temp_file_left_behind(self, tmp_path):
        p = tmp_path / "out.jsonl"
        atomic_write_lines(p, ["x", "y"])
        leftovers = [f.name for f in tmp_path.iterdir() if f.name != "out.jsonl"]
        assert leftovers == []

    def test_crash_mid_write_leaves_original_intact(self, tmp_path):
        # If the write raises partway, os.replace never runs, so the original
        # file must survive untouched and no temp file is left behind.
        p = tmp_path / "out.jsonl"
        p.write_text("original\n", encoding="utf-8")

        def boom():
            yield "partial"
            raise RuntimeError("disk full")

        with pytest.raises(RuntimeError, match="disk full"):
            atomic_write_lines(p, boom())

        assert p.read_text(encoding="utf-8") == "original\n"
        leftovers = [f.name for f in tmp_path.iterdir() if f.name != "out.jsonl"]
        assert leftovers == []


class TestSaveJson:
    def test_writes_pretty_json_with_trailing_newline(self, tmp_path):
        p = tmp_path / "config.json"
        save_json(p, {"b": 1, "a": 2})
        text = p.read_text(encoding="utf-8")
        assert text.endswith("\n")
        assert json.loads(text) == {"b": 1, "a": 2}
        assert '    "b": 1' in text  # default indent=4

    def test_sort_keys(self, tmp_path):
        p = tmp_path / "config.json"
        save_json(p, {"b": 1, "a": 2}, sort_keys=True)
        text = p.read_text(encoding="utf-8")
        assert text.index('"a"') < text.index('"b"')

    def test_leaves_no_temp_file(self, tmp_path):
        p = tmp_path / "config.json"
        save_json(p, {"x": [1, 2, 3]})
        leftovers = [f.name for f in tmp_path.iterdir() if f.name != "config.json"]
        assert leftovers == []

    def test_crash_mid_write_leaves_original_intact(self, tmp_path, monkeypatch):
        p = tmp_path / "config.json"
        p.write_text('{"keep": true}\n', encoding="utf-8")

        monkeypatch.setattr(
            "app.shared.json_utils.atomic_write_text",
            lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
        )
        with pytest.raises(OSError):
            save_json(p, {"new": "data"})

        assert p.read_text(encoding="utf-8") == '{"keep": true}\n'
        leftovers = [f.name for f in tmp_path.iterdir() if f.name != "config.json"]
        assert leftovers == []


class TestDumpsInlineArrays:
    def test_scalar_lists_stay_on_one_line(self):
        out = dumps_inline_arrays({"Position": [1.5, 2, -3], "Name": "x"})
        assert out == '{\n    "Position": [ 1.5, 2, -3 ],\n    "Name": "x"\n}'

    def test_lists_of_objects_are_broken_out(self):
        out = dumps_inline_arrays({"T": [{"a": 1}, {"b": [1, 2]}]})
        assert out == (
            '{\n    "T": [\n        {\n            "a": 1\n        },\n'
            '        {\n            "b": [ 1, 2 ]\n        }\n    ]\n}'
        )

    def test_round_trips_and_keeps_empties_and_unicode(self):
        data = {"a": [], "b": {}, "c": [None, True, "\u00e5"], "d": [[1, 2], [3]]}
        out = dumps_inline_arrays(data)
        assert json.loads(out) == data
        assert "\u00e5" in out and "\\u00e5" not in out  # written as-is, not escaped


class TestDumpsJson:
    """F75 (plans/54 D): a save wrote back numbers nobody touched."""

    # As a mod writes it through Enforce: 17 significant digits, never an
    # exponent. Python's shortest repr of the first is ...625, of the second
    # -3.000000106112566e-06.
    ENFORCE = (
        '{\n    "X": 1647.6500244140626,\n    "Tiny": -0.000003000000106112566,\n'
        '    "Whole": 1.0,\n    "Count": 3,\n    "Pos": [\n        0.30000000000000004,\n'
        '        2.5\n    ]\n}'
    )

    def test_without_floats_it_is_json_dumps(self):
        data = {"a": [], "b": {}, "c": [None, True, "\u00e5", 3], "d": [[1, 2], [{"e": "f"}]]}
        assert dumps_json(data) == json.dumps(data, indent=4, ensure_ascii=False)

    def test_an_untouched_file_is_written_back_byte_for_byte(self, tmp_path):
        p = tmp_path / "zone.json"
        p.write_text(self.ENFORCE, encoding="utf-8")
        assert dumps_json(load_json_preserving(p)) == self.ENFORCE

    def test_a_changed_number_is_written_fresh(self, tmp_path):
        p = tmp_path / "zone.json"
        p.write_text(self.ENFORCE, encoding="utf-8")
        data = load_json_preserving(p)
        data["X"] = round(data["X"] + 10, 2)
        out = dumps_json(data)
        assert '"X": 1657.65,' in out
        assert '"Tiny": -0.000003000000106112566,' in out

    def test_no_float_is_ever_written_in_exponent_notation(self):
        out = dumps_json([-3.000000106112566e-06, 1e20, 1.5e-7, 123.25, -0.0])
        assert "e" not in out
        assert json.loads(out) == [-3.000000106112566e-06, 1e20, 1.5e-7, 123.25, -0.0]
        assert "-0.000003000000106112566" in out and "100000000000000000000.0" in out

    def test_an_equal_value_sent_back_keeps_the_files_text(self, tmp_path):
        """A popup sends every field back; an untouched one is a plain float."""
        p = tmp_path / "zone.json"
        p.write_text(self.ENFORCE, encoding="utf-8")
        previous = load_json_preserving(p)
        data = json.loads(self.ENFORCE)  # every number a plain float now
        assert dumps_json(data, previous=previous) == self.ENFORCE
        data["X"] = 1647.66
        assert '"X": 1647.66,' in dumps_json(data, previous=previous)

    def test_over_a_missing_or_broken_file_it_still_writes(self, tmp_path):
        broken = tmp_path / "broken.json"
        broken.write_text("{ not json", encoding="utf-8")
        for path in (tmp_path / "missing.json", broken):
            assert dumps_json_over(path, {"a": 1.5}) == '{\n    "a": 1.5\n}\n'

    def test_over_a_file_with_no_final_newline_it_adds_none(self, tmp_path):
        """ToxicZone's writer ends the file on ``}``; adding a newline made
        the last line a diff of its own."""
        p = tmp_path / "zone.json"
        p.write_text(self.ENFORCE, encoding="utf-8")
        assert dumps_json_over(p, load_json_preserving(p)) == self.ENFORCE
        p.write_text(self.ENFORCE + "\n", encoding="utf-8")
        assert dumps_json_over(p, load_json_preserving(p)) == self.ENFORCE + "\n"

    def test_inline_arrays_keep_the_files_text_too(self):
        data = {"Position": [SourceFloat("9663.460000000001"), 211.88, SourceFloat("1e-07")]}
        assert dumps_inline_arrays(data) == '{\n    "Position": [ 9663.460000000001, 211.88, 1e-07 ]\n}'

    def test_a_source_float_is_a_float_and_survives_a_copy(self):
        import copy

        v = SourceFloat("1647.6500244140626")
        assert v == 1647.6500244140625 and type(v + 0) is float
        assert copy.deepcopy(v).text == "1647.6500244140626"


# ---------------------------------------------------------------------------
# A file saved in another encoding: read for display, never rewritten
# ---------------------------------------------------------------------------

GUID = "a" * 32
STEAM = "76561198000000001"
OTHER = "76561198000000002"
CFG = 'hostname = "Café";\nclass Missions\n{\n\tclass DayZ\n\t{\n\t\ttemplate = "dayzOffline.chernarusplus";\n\t};\n};\n'


def _servercfg(path):
    from app.parsers import servercfg

    assert servercfg.read_cfg_value(path, "hostname") == "Caf\ufffd"
    assert servercfg.read_mission_template(path) == "dayzOffline.chernarusplus"
    return [
        lambda: servercfg.write_cfg_value(path, "hostname", "New"),
        lambda: servercfg.remove_cfg_key(path, "hostname"),
        lambda: servercfg.set_mission_template(path, "dayzOffline.enoch"),
    ]


def _beserver(path):
    from app.parsers import battleye_cfg

    assert battleye_cfg.read_beserver_cfg(path) == {"RConPassword": "caf\ufffd", "RConPort": "2305"}
    return [lambda: battleye_cfg.write_beserver_cfg(path, {"RConPassword": "new", "RConPort": "2305"})]


def _bans(path):
    from app.ops import ban_ops

    assert [(b.identifier, b.reason) for b in ban_ops.read_bans(path)] == [(GUID, "trich\ufffd")]
    return [
        lambda: ban_ops.add_ban(path, "b" * 32, "griefing", metadata_path=path.with_name("meta.json")),
        lambda: ban_ops.remove_ban(path, GUID),
    ]


def _whitelist(path):
    from app.ops import whitelist_ops

    assert whitelist_ops.read_whitelist(path) == [STEAM]
    return [
        lambda: whitelist_ops.add_whitelist_id(path, OTHER),
        lambda: whitelist_ops.remove_whitelist_id(path, STEAM),
    ]


def _priority(path):
    from app.ops import priority_ops

    assert priority_ops.read_priority_list(path) == [STEAM]
    return [
        lambda: priority_ops.add_priority_id(path, OTHER),
        lambda: priority_ops.remove_priority_id(path, STEAM),
    ]


@pytest.mark.parametrize("name, text, check", [
    ("serverDZ.cfg", CFG, _servercfg),
    ("beserver_x64.cfg", "RConPassword café\nRConPort 2305\n", _beserver),
    ("bans.txt", f"{GUID} -1 triché\n", _bans),
    ("whitelist.txt", f"{STEAM}\t//André\n", _whitelist),
    ("priority.txt", f"{STEAM};é\n", _priority),
])
def test_a_file_saved_in_another_encoding_is_read_and_never_rewritten(tmp_path, name, text, check):
    """Windows-1252 from an ANSI editor, or a ban reason typed into an RCon
    tool. A strict read raised out of every route that needed one value from
    the file (with such a serverDZ.cfg the status, config and maps routes all
    answered 500); a rewrite from a lossy read would lose what it could not
    read. So the reader replaces it and every writer refuses."""
    from app.shared.json_utils import NotUtf8Error

    path = tmp_path / name
    data = text.encode("cp1252")
    path.write_bytes(data)
    for write in check(path):
        with pytest.raises(NotUtf8Error, match=f"^{name} is not saved as UTF-8"):
            write()
    assert path.read_bytes() == data
    assert [f.name for f in tmp_path.iterdir()] == [name]


def test_the_two_readers_agree_on_a_utf8_file(tmp_path):
    from app.shared.json_utils import read_text_lossy, read_text_to_rewrite

    path = tmp_path / "x.txt"
    path.write_text("Café ☕\r\nsecond\n", encoding="utf-8", newline="")
    assert read_text_lossy(path) == read_text_to_rewrite(path) == "Café ☕\nsecond\n"
    path.write_bytes(b"\xef\xbb\xbfa;b\n")
    assert read_text_lossy(path, "utf-8-sig") == "a;b\n"


def test_a_busy_target_is_renamed_again_on_windows(tmp_path, monkeypatch):
    """User test in 2026 Q4 (Windows 11): two quick saves of one file gave
    "permission denied", because Windows refuses a rename over a file another
    handle has open. The rename is tried again; elsewhere the error stands."""
    from app.shared import json_utils

    target = tmp_path / "types.xml"
    target.write_text("old", encoding="utf-8")
    real = json_utils.os.replace
    calls = []

    def flaky(src, dst):
        calls.append(dst)
        if len(calls) < 3:
            raise PermissionError("in use")
        real(src, dst)

    monkeypatch.setattr(json_utils.os, "replace", flaky)
    monkeypatch.setattr(json_utils, "_REPLACE_WAIT", 0)
    monkeypatch.setattr(json_utils, "IS_WINDOWS", True)
    json_utils.atomic_write_text(target, "new")
    assert target.read_text(encoding="utf-8") == "new" and len(calls) == 3

    calls.clear()
    monkeypatch.setattr(json_utils, "IS_WINDOWS", False)
    import pytest

    with pytest.raises(PermissionError):
        json_utils.atomic_write_text(target, "newer")
    assert target.read_text(encoding="utf-8") == "new"
    assert list(tmp_path.glob("*.tmp")) == []


@pytest.mark.skipif(__import__("os").name == "nt", reason="POSIX modes")
@pytest.mark.parametrize("mode", [0o755, 0o644, 0o640, 0o600])
def test_a_rewrite_keeps_the_file_mode(tmp_path, mode):
    """Every panel save left economy.xml (0755 as
    SteamCMD ships it) at 0600, because the mkstemp temp file was renamed over
    it, and an FTP user or backup tool running as someone else lost it."""
    import os
    import stat

    from app.shared.json_utils import atomic_write_bytes, atomic_write_text

    target = tmp_path / "economy.xml"
    target.write_text("old", encoding="utf-8")
    os.chmod(target, mode)
    atomic_write_text(target, "new")
    assert stat.S_IMODE(target.stat().st_mode) == mode
    atomic_write_bytes(target, b"newer")
    assert stat.S_IMODE(target.stat().st_mode) == mode
    assert target.read_bytes() == b"newer"


@pytest.mark.skipif(__import__("os").name == "nt", reason="POSIX modes")
def test_a_new_file_and_an_owner_only_write_are_0600(tmp_path):
    """A secret's writer passes owner_only, so its next save tightens a file
    an older version left readable (SEC-4); a new file starts 0600."""
    import os
    import stat

    from app.shared.json_utils import atomic_write_text

    fresh = tmp_path / "new.json"
    old = os.umask(0o022)
    try:
        atomic_write_text(fresh, "{}")
        secret = tmp_path / "rclone.conf"
        secret.write_text("x", encoding="utf-8")
        os.chmod(secret, 0o644)
        atomic_write_lines(secret, ["[remote]"], owner_only=True)
    finally:
        os.umask(old)
    assert stat.S_IMODE(fresh.stat().st_mode) == 0o600
    assert stat.S_IMODE(secret.stat().st_mode) == 0o600
