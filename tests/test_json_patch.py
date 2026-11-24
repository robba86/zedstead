"""`app/shared/json_patch.py`: a save changes only what was edited."""

from __future__ import annotations

import copy
import json

import pytest

from tests.conftest import CE_1_29, bohemia_ce, ce_lines, dayz_docs

from app.shared.json_patch import dumps_json_patched, write_json_patched
from app.shared.json_utils import SourceFloat

@pytest.fixture()
def triggers() -> str:
    """The head of Bohemia's Livonia cfgundergroundtriggers.json, read at the
    pinned 1.29 commit: tabs, CRLF, a trailing tab after `{ `, and
    `[ x, y, z]` with no space before `]`. The first trigger whole, the second
    up to its EyeAccommodation and closed with an empty Breadcrumbs list (the
    one line that is the test's own), then the file's last three lines."""
    lines = ce_lines(bohemia_ce(CE_1_29, "dayzOffline.enoch/cfgundergroundtriggers.json"))
    head = lines[: lines.index('\t\t\t"EyeAccommodation": 0,\r\n') + 1]
    return "".join(head + ['\t\t\t"Breadcrumbs": []\r\n'] + lines[-3:])


def _load(text: str):
    return json.loads(text, parse_float=SourceFloat)


def test_a_document_written_back_unchanged_is_byte_identical(triggers):
    assert dumps_json_patched(triggers, _load(triggers), inline_scalar_lists=True) == triggers


def test_equal_numbers_of_another_type_keep_the_file_s_literal(triggers):
    data = _load(triggers)
    data["Triggers"][0]["Size"] = [15.0, 5.6, 10.8]
    data["Triggers"][0]["EyeAccommodation"] = 1.0
    assert dumps_json_patched(triggers, data) == triggers


def test_an_edit_changes_only_its_value(triggers):
    data = _load(triggers)
    data["Triggers"][1]["EyeAccommodation"] = 0.25
    data["Triggers"][0]["Size"][0] = 16.0         # the file wrote 15: stays whole
    out = dumps_json_patched(triggers, data)
    assert out == triggers.replace('"EyeAccommodation": 0,', '"EyeAccommodation": 0.25,').replace(
        '"Size": [ 15, 5.6, 10.8 ],\r\n\t\t\t"EyeAccommodation": 1',
        '"Size": [ 16, 5.6, 10.8 ],\r\n\t\t\t"EyeAccommodation": 1',
    )


def test_a_removed_entry_is_cut_and_nothing_else_moves(triggers):
    data = _load(triggers)
    del data["Triggers"][0]
    out = dumps_json_patched(triggers, data)
    first_end = triggers.index("\t\t},\r\n") + len("\t\t},\r\n")
    assert out == triggers[: triggers.index("\t\t{")] + triggers[first_end:]
    assert json.loads(out) == json.loads(json.dumps(data))


def test_an_added_entry_and_key_take_the_file_s_indent_and_line_ending(triggers):
    data = _load(triggers)
    added = copy.deepcopy(data["Triggers"][1])
    added["Comment"] = "new"
    data["Triggers"].append(added)
    data["Triggers"][0]["Comment"] = "first"
    out = dumps_json_patched(triggers, data, inline_scalar_lists=True)
    assert json.loads(out) == json.loads(json.dumps(data))
    assert "\n" not in out.replace("\r\n", "")
    assert '\t\t\t"AmbientSoundSet": "Underground_SoundSet",\r\n\t\t\t"Comment": "first"\r\n' in out
    assert '\t\t{\r\n\t\t\t"Position": [ 735.0, 533.7, 1229.1 ],' in out
    # The untouched trigger is as it was.
    second = triggers[triggers.rindex("\t\t{ \t\r\n"):triggers.rindex("\t\t}") + 3]
    assert second in out


def test_write_keeps_crlf_on_disk(triggers, tmp_path):
    path = tmp_path / "cfgundergroundtriggers.json"
    path.write_bytes(triggers.encode())
    data = _load(triggers)
    data["Triggers"][1]["EyeAccommodation"] = 0.5
    write_json_patched(path, data, inline_scalar_lists=True)
    raw = path.read_bytes().decode()
    assert raw.count("\r\n") == triggers.count("\r\n")
    assert '"EyeAccommodation": 0.5,' in raw


def test_a_file_that_is_not_json_falls_back_to_a_fresh_write(tmp_path):
    path = tmp_path / "x.json"
    path.write_text("{ broken")
    write_json_patched(path, {"a": 1})
    assert json.loads(path.read_text()) == {"a": 1}


@pytest.mark.parametrize("mission", ["dayzOffline.chernarusplus", "dayzOffline.enoch", "dayzOffline.sakhal"])
@pytest.mark.parametrize("name", ["cfgundergroundtriggers.json", "cfgEffectArea.json", "cfgeffectarea.json"])
def test_every_stock_file_comes_back_byte_identical(mission, name):
    path = dayz_docs("vanilla", mission, name)
    text = path.read_bytes().decode("utf-8")
    assert dumps_json_patched(text, _load(text.lstrip("﻿")), inline_scalar_lists=True) == text
