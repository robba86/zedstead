"""Tests for app/xml/messages_xml.py (extracted from app.commands.config_cmd)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import CE_1_29, bohemia_ce, ce_lines

from app.xml.messages_xml import (
    MessageType,
    classify_message,
    elem_text,
    group_messages,
    load_messages,
    on_connect_tag,
    set_elem_text,
)

SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<messages>
    <message>
        <deadline>600</deadline>
        <shutdown>1</shutdown>
        <text>Server restarting soon.</text>
    </message>
    <message>
        <repeat>15</repeat>
        <text>Welcome to the server.</text>
    </message>
    <message>
        <delay>2</delay>
        <onConnect>1</onConnect>
        <text>Hello #name</text>
    </message>
</messages>
"""


def test_load_messages(tmp_path: Path):
    path = tmp_path / "messages.xml"
    path.write_text(SAMPLE)
    tree, messages = load_messages(path)
    assert tree is not None
    assert len(messages) == 3


def test_load_messages_missing_file(tmp_path: Path):
    tree, messages = load_messages(tmp_path / "nope.xml")
    assert tree is None
    assert messages == []


def test_classify_message(tmp_path: Path):
    path = tmp_path / "messages.xml"
    path.write_text(SAMPLE)
    _tree, messages = load_messages(path)
    assert classify_message(messages[0]) == MessageType.SHUTDOWN
    assert classify_message(messages[1]) == MessageType.REPEAT
    assert classify_message(messages[2]) == MessageType.ON_CONNECT


def test_group_messages(tmp_path: Path):
    path = tmp_path / "messages.xml"
    path.write_text(SAMPLE)
    _tree, messages = load_messages(path)
    groups = group_messages(messages)
    assert len(groups[MessageType.SHUTDOWN]) == 1
    assert len(groups[MessageType.REPEAT]) == 1
    assert len(groups[MessageType.ON_CONNECT]) == 1
    assert len(groups[MessageType.OTHER]) == 0


def test_elem_text_and_set_elem_text(tmp_path: Path):
    path = tmp_path / "messages.xml"
    path.write_text(SAMPLE)
    _tree, messages = load_messages(path)
    msg = messages[0]
    assert elem_text(msg, "text") == "Server restarting soon."
    set_elem_text(msg, "text", "New text")
    assert elem_text(msg, "text") == "New text"
    set_elem_text(msg, "newtag", "value")
    assert elem_text(msg, "newtag") == "value"


# The shape of the examples at the top of Bohemia's stock messages.xml, which
# spell the flag `onconnect`; the wiki spells it `onConnect`.
LOWERCASE_FLAG = """<?xml version="1.0" encoding="UTF-8"?>
<messages>
    <message>
        <delay>10</delay>
        <repeat>50</repeat>
        <onconnect>1</onconnect>
        <text>Greets, then repeats.</text>
    </message>
    <message>
        <delay>2</delay>
        <onconnect>1</onconnect>
        <text>Greets once.</text>
    </message>
</messages>
"""


def test_the_on_connect_flag_is_read_in_either_spelling(tmp_path: Path):
    path = tmp_path / "messages.xml"
    path.write_text(LOWERCASE_FLAG)
    _tree, messages = load_messages(path)
    assert [classify_message(m) for m in messages] == [MessageType.ON_CONNECT] * 2
    assert on_connect_tag(messages) == "onconnect"


def test_setting_a_flag_keeps_the_spelling_the_message_has(tmp_path: Path):
    path = tmp_path / "messages.xml"
    path.write_text(LOWERCASE_FLAG)
    _tree, messages = load_messages(path)
    set_elem_text(messages[0], "onConnect", "0")
    assert [child.tag for child in messages[0]].count("onconnect") == 1
    assert messages[0].find("onConnect") is None
    assert elem_text(messages[0], "onConnect") == "0"


def test_a_file_without_the_flag_gets_the_wiki_spelling(tmp_path: Path):
    path = tmp_path / "messages.xml"
    path.write_text(SAMPLE.replace("<onConnect>1</onConnect>", ""))
    _tree, messages = load_messages(path)
    assert on_connect_tag(messages) == "onConnect"


# ---------------------------------------------------------------------------
# A messages.xml that cannot be read is refused, never shown as empty
# ---------------------------------------------------------------------------

UNREADABLE_MESSAGES = {
    "malformed": b"<messages><message><text>Hi</text></messages>",
    "cp1252": b"<messages><message><text>Caf\xe9</text></message></messages>",
}


@pytest.mark.parametrize("content", UNREADABLE_MESSAGES.values(), ids=UNREADABLE_MESSAGES.keys())
def test_an_unreadable_file_is_refused_on_load_and_on_save(tmp_path, content: bytes):
    from app.web.editors.ce_tables import messages_load, messages_save

    path = tmp_path / "messages.xml"
    path.write_bytes(content)

    with pytest.raises(ValueError, match="messages.xml"):
        messages_load(path)
    with pytest.raises(ValueError, match="messages.xml"):
        messages_save(path, [{"values": {"type": "Repeat", "text": "New", "repeat": "5"}}])
    assert path.read_bytes() == content
    assert load_messages(path) == (None, [])


def test_a_table_save_keeps_the_file_s_comments(tmp_path):
    from app.web.editors.ce_tables import messages_load, messages_save

    path = tmp_path / "messages.xml"
    path.write_text(
        "<messages>\n  <!-- how the file works -->\n"
        "  <message><repeat>5</repeat><text>Hi</text></message>\n</messages>\n",
        encoding="utf-8",
    )
    messages_save(path, messages_load(path)["rows"])
    assert "<!-- how the file works -->" in path.read_text(encoding="utf-8")


def _rows(path):
    from app.web.editors.ce_tables import messages_load

    return messages_load(path)["rows"]


def test_a_repeat_on_an_other_row_is_written(tmp_path):
    """The Other card offers a Repeat box, and the save dropped what was typed
    in it: only non-Other rows had their repeat written. DayZ reads a repeat
    above 0 as the Repeat flag, so the row comes back as a Repeat message."""
    from app.web.editors.ce_tables import messages_save

    path = tmp_path / "messages.xml"
    path.write_text("<messages>\n  <message><text>Hi</text></message>\n</messages>\n", encoding="utf-8")
    messages_save(path, [{"id": "0", "values": {"type": "Other", "text": "Hi", "repeat": "20"}}])

    (row,) = _rows(path)
    assert row["values"]["repeat"] == "20"
    assert row["values"]["type"] == "Repeat"


def test_a_type_the_flags_cannot_hold_is_refused(tmp_path):
    """A type that is none of the four came back as Repeat, and a Repeat row
    without an interval came back as Other."""
    from app.web.editors.ce_tables import messages_save

    path = tmp_path / "messages.xml"
    path.write_text(SAMPLE, encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="not a message type"):
        messages_save(path, [{"id": "1", "values": {"type": "Countdown", "text": "x", "repeat": "15"}}])
    with pytest.raises(ValueError, match="repeat interval"):
        messages_save(path, [{"id": "1", "values": {"type": "Repeat", "text": "x", "repeat": ""}}])
    assert path.read_bytes() == before


def test_a_row_keeps_the_values_it_does_not_carry(tmp_path):
    """The Messages cards sent only what each card shows: any save dropped a
    Shutdown message's delay and repeat, and a Repeat or OnConnect message's
    deadline. A row with an id starts from that message."""
    from app.web.editors.ce_tables import messages_save

    path = tmp_path / "messages.xml"
    path.write_text(
        """<messages>
  <message>
    <delay>3</delay>
    <repeat>30</repeat>
    <deadline>600</deadline>
    <shutdown>1</shutdown>
    <text>Restart in #tmin</text>
  </message>
  <message>
    <repeat>15</repeat>
    <deadline>45</deadline>
    <text>Repeats</text>
  </message>
  <message>
    <delay>2</delay>
    <deadline>90</deadline>
    <onConnect>1</onConnect>
    <extra>keep</extra>
    <text>Hello</text>
  </message>
</messages>
""",
        encoding="utf-8",
    )
    # What the cards used to send: each only the keys it shows.
    messages_save(
        path,
        [
            {"id": "2", "values": {"type": "OnConnect", "text": "Hello", "delay": "2"}},
            {"id": "1", "values": {"type": "Repeat", "text": "Repeats", "repeat": "15", "delay": ""}},
            {"id": "0", "values": {"type": "Shutdown", "text": "Restart in #tmin", "deadline": "600"}},
        ],
    )
    values = [r["values"] for r in _rows(path)]
    assert values == [
        {"type": "OnConnect", "text": "Hello", "delay": "2", "repeat": "", "deadline": "90"},
        {"type": "Repeat", "text": "Repeats", "delay": "", "repeat": "15", "deadline": "45"},
        {"type": "Shutdown", "text": "Restart in #tmin", "delay": "3", "repeat": "30", "deadline": "600"},
    ]
    assert "<extra>keep</extra>" in path.read_text(encoding="utf-8")


def test_a_value_sent_empty_is_cleared_and_a_type_change_moves_the_flag(tmp_path):
    from app.web.editors.ce_tables import messages_save

    path = tmp_path / "messages.xml"
    path.write_text(SAMPLE, encoding="utf-8")
    messages_save(
        path,
        [
            {"id": "0", "values": {"type": "Other", "text": "Countdown", "deadline": "600", "repeat": ""}},
            {"id": "2", "values": {"type": "Shutdown", "text": "Hello #name", "delay": "", "deadline": "30"}},
        ],
    )
    values = [r["values"] for r in _rows(path)]
    assert values == [
        {"type": "Other", "text": "Countdown", "delay": "", "repeat": "", "deadline": "600"},
        {"type": "Shutdown", "text": "Hello #name", "delay": "", "repeat": "", "deadline": "30"},
    ]
    text = path.read_text(encoding="utf-8")
    assert text.count("<shutdown>1</shutdown>") == 1
    assert "onConnect" not in text


# Bohemia's db/messages.xml (dayzOffline.chernarusplus) as it ships, read at
# the pinned 1.29 commit: CRLF, a column-0 comment of examples, a blank line
# before </messages>, no final newline. The comment's examples are cut to the
# first (lines 1-15 and the last three); the layout is the file's own.
def stock_messages() -> str:
    lines = ce_lines(bohemia_ce(CE_1_29, "dayzOffline.chernarusplus/db/messages.xml"))
    return "".join(lines[:15] + lines[-3:])


def test_an_unedited_save_of_the_stock_file_is_byte_identical(tmp_path):
    from app.web.editors.ce_tables import messages_save

    path = tmp_path / "messages.xml"
    path.write_bytes(stock_messages().encode())

    messages_save(path, _rows(path))

    assert path.read_bytes() == stock_messages().encode()


def test_an_added_message_keeps_the_layout_and_bohemias_child_order(tmp_path):
    from app.web.editors.ce_tables import messages_save

    path = tmp_path / "messages.xml"
    path.write_bytes(stock_messages().encode())

    messages_save(path, [{"id": "new-1", "values": {"type": "Shutdown", "text": "Bye", "deadline": "600"}}])

    text = path.read_bytes().decode()
    assert text.startswith(stock_messages()[: stock_messages().index("    -->")])  # comment untouched
    assert text.endswith("</message>\r\n\r\n</messages>")  # the blank line stays last
    assert text.index("<deadline>") < text.index("<shutdown>") < text.index("<text>Bye")


def test_a_comment_between_messages_stays_where_it_was(tmp_path):
    from app.web.editors.ce_tables import messages_save

    path = tmp_path / "messages.xml"
    original = (
        "<messages>\n  <message>\n    <repeat>5</repeat>\n    <text>A</text>\n  </message>\n"
        "  <!-- the second one -->\n"
        "  <message>\n    <repeat>9</repeat>\n    <text>B</text>\n  </message>\n</messages>\n"
    )
    path.write_text(original, encoding="utf-8")

    messages_save(path, _rows(path))

    assert path.read_text(encoding="utf-8") == original


def test_a_message_that_greets_and_shuts_down_keeps_both_flags(tmp_path):
    """It reads as OnConnect, and a save that left the type alone dropped its
    <shutdown>."""
    from app.web.editors.ce_tables import messages_save

    path = tmp_path / "messages.xml"
    path.write_text(
        "<messages>\n  <message>\n    <deadline>60</deadline>\n    <onconnect>1</onconnect>\n"
        "    <shutdown>1</shutdown>\n    <text>Restart in #tmin</text>\n  </message>\n</messages>\n",
        encoding="utf-8",
    )
    (row,) = _rows(path)
    assert row["values"]["type"] == "OnConnect"

    messages_save(path, [{**row, "values": {**row["values"], "text": "Restart soon, #tmin"}}])

    text = path.read_text(encoding="utf-8")
    assert "<shutdown>1</shutdown>" in text and "<onconnect>1</onconnect>" in text

    (row,) = _rows(path)
    messages_save(path, [{**row, "values": {**row["values"], "type": "Repeat", "repeat": "30"}}])
    text = path.read_text(encoding="utf-8")
    assert "shutdown" not in text and "onconnect" not in text  # a changed type moves the flags


@pytest.mark.parametrize("values, words", [
    # "If the Countdown flag is not set, this flag is ignored." (server-messages.wiki:21)
    ({"type": "Shutdown", "text": "Bye"}, "needs a deadline above 0"),
    ({"type": "Shutdown", "text": "Bye", "deadline": "0"}, "needs a deadline above 0"),
    # "The maximum length of the message is 160 characters." (:26)
    ({"type": "Repeat", "text": "x" * 161, "repeat": "5"}, "at most 160 characters"),
    # Every time is in minutes (:33): whole numbers, 0 or more.
    ({"type": "Repeat", "text": "Hi", "repeat": "-5"}, "whole number of minutes"),
    ({"type": "Repeat", "text": "Hi", "repeat": "1.5"}, "whole number of minutes"),
    ({"type": "OnConnect", "text": "Hi", "delay": "soon"}, "whole number of minutes"),
])
def test_a_message_dayz_cannot_use_is_refused(tmp_path, values, words):
    from app.web.editors.ce_tables import messages_save

    path = tmp_path / "messages.xml"
    path.write_bytes(stock_messages().encode())

    with pytest.raises(ValueError, match=words):
        messages_save(path, [{"id": "new-1", "values": values}])
    assert path.read_bytes() == stock_messages().encode()


def test_a_message_of_160_characters_saves(tmp_path):
    from app.web.editors.ce_tables import messages_save

    path = tmp_path / "messages.xml"
    path.write_bytes(stock_messages().encode())

    messages_save(path, [{"id": "new-1", "values": {"type": "Repeat", "text": "x" * 160, "repeat": "5"}}])
    assert "x" * 160 in path.read_text(encoding="utf-8")
