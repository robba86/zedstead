"""messages.xml parsing/classification for the web editors, free of CLI/Rich
console helpers."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from xml.etree import ElementTree as ET

from app.modmgr.mods import get_current_mission
from app.shared.config import DayzConfig
from app.shared.json_utils import NotUtf8Error
from app.xml.xml_text import describe_xml_error
from app.xml.xml_utils import load_xml, save_xml, subelement_in_order


def get_messages_path(config: DayzConfig) -> Path | None:
    """Resolve the messages.xml path from the current mission."""
    mission = get_current_mission(config.server_cfg_path)
    if not mission:
        return None
    return config.server_dir_path / "mpmissions" / mission / "db" / "messages.xml"


def read_messages(path: Path) -> tuple[ET.ElementTree | None, list[ET.Element]]:
    """Load messages.xml and return (tree, list of message elements).

    (None, []) only for a file that is not there. One that is there and cannot
    be read raises a ValueError that names it: shown as an empty table, the
    next save would replace it with whatever rows the table then held.
    """
    if not path.exists():
        return None, []
    try:
        # Comments kept: Bohemia's file explains the format in one.
        tree = load_xml(path, preserve_comments=True)
    except UnicodeDecodeError:
        raise NotUtf8Error(path) from None
    except ET.ParseError as e:
        raise ValueError(
            f"{path.name} is not well-formed XML ({describe_xml_error(e).summary}). "
            "Correct it on the Raw tab; this table cannot show or save it as it is."
        ) from None
    return tree, tree.getroot().findall("message")


def load_messages(path: Path) -> tuple[ET.ElementTree | None, list[ET.Element]]:
    """`read_messages` for a caller that only reads: a file that cannot be
    read is (None, []). Never use this before a save."""
    try:
        return read_messages(path)
    except ValueError:
        return None, []


def save_messages(tree: ET.ElementTree, path: Path) -> None:
    save_xml(tree, path, indent="  ")


# Bohemia spells the on-connect flag both ways: `<onconnect>` in the examples
# at the top of the stock messages.xml, `<onConnect>` on the wiki. A file is
# read in either spelling and written back in its own.
ON_CONNECT_TAG = "onConnect"


def _child(parent: ET.Element, tag: str) -> ET.Element | None:
    """The child element named *tag*, in any letter case."""
    elem = parent.find(tag)
    if elem is not None:
        return elem
    wanted = tag.lower()
    for child in parent:
        if isinstance(child.tag, str) and child.tag.lower() == wanted:
            return child
    return None


def elem_text(parent: ET.Element, tag: str) -> str:
    elem = _child(parent, tag)
    return elem.text.strip() if elem is not None and elem.text else ""


# Where a message's children go, in the order of the wiki's example and
# Bohemia's (server-messages.wiki:58-79): times, then flags, then the text.
MESSAGE_CHILD_ORDER = ["delay", "repeat", "deadline", "onconnect", "onConnect", "shutdown", "text"]

# "The maximum length of the message is 160 characters." (server-messages.wiki:26)
MAX_TEXT = 160

# "Every property related to time in the server message is in minutes."
# (server-messages.wiki:33)
TIME_KEYS = ("delay", "repeat", "deadline")


def set_elem_text(parent: ET.Element, tag: str, value: str) -> None:
    """Set the text of a child element, creating it where Bohemia's file
    would have it if needed."""
    elem = _child(parent, tag)
    if elem is None:
        elem = subelement_in_order(parent, tag, MESSAGE_CHILD_ORDER)
    elem.text = value


def message_value_problem(key: str, value: str) -> str | None:
    """What is wrong with one value of a message, or None."""
    if key == "text":
        if len(value) > MAX_TEXT:
            return f"A message is at most {MAX_TEXT} characters; this one is {len(value)}."
        return None
    text = value.strip()
    if text and not (text.isascii() and text.isdigit()):
        return f"{key} is a whole number of minutes, 0 or more, not {value!r}"
    return None


def remove_elem(parent: ET.Element, tag: str) -> None:
    """Remove the child element named *tag* (any letter case), if there is one."""
    elem = _child(parent, tag)
    if elem is not None:
        parent.remove(elem)


def on_connect_tag(messages: list[ET.Element]) -> str:
    """The spelling of the on-connect flag these messages use."""
    for msg in messages:
        elem = _child(msg, ON_CONNECT_TAG)
        if elem is not None:
            return elem.tag
    return ON_CONNECT_TAG


class MessageType(Enum):
    ON_CONNECT = "OnConnect"
    REPEAT = "Repeat"
    SHUTDOWN = "Shutdown"
    OTHER = "Other"


def classify_message(msg: ET.Element) -> MessageType:
    """Classify a message element (priority: onConnect > shutdown > repeat > other)."""
    if elem_text(msg, ON_CONNECT_TAG) == "1":
        return MessageType.ON_CONNECT
    if elem_text(msg, "shutdown") == "1":
        return MessageType.SHUTDOWN
    repeat = elem_text(msg, "repeat")
    if repeat and repeat != "0":
        return MessageType.REPEAT
    return MessageType.OTHER


def group_messages(
    messages: list[ET.Element],
) -> dict[MessageType, list[tuple[int, ET.Element]]]:
    """Group messages by type, preserving original indices."""
    groups: dict[MessageType, list[tuple[int, ET.Element]]] = {t: [] for t in MessageType}
    for i, msg in enumerate(messages):
        groups[classify_message(msg)].append((i, msg))
    return groups
