"""Reading a TraderPlus config that a map layer patches in place.

The three TraderPlus layers (traders, safe zones, garage NPCs) each keep the
file's document and write it back whole. A file that is there and cannot be
read is shown as empty, and nothing may be added to it: the write would
replace the admin's file with the addition alone.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.shared.json_utils import load_json_preserving

log = logging.getLogger(__name__)


def read_document(path: Path, *list_keys: str) -> dict | None:
    """The file's JSON object, or None when it cannot be used as one.

    None for a file that is not JSON, not an object, or holds something other
    than a list under one of *list_keys* (every reader walks those as lists).
    Never raises: a layer loads while the whole map editor is built.
    """
    try:
        raw = load_json_preserving(path)
    except Exception as e:
        log.warning("Failed to parse %s: %s", path.name, e)
        return None
    if not isinstance(raw, dict) or not all(isinstance(raw.get(key, []), list) for key in list_keys):
        log.warning("%s: %s must be a list", path.name, " and ".join(list_keys))
        return None
    return raw


def refuse_unreadable(unreadable: bool, file_name: str) -> None:
    """Raise the ValueError an add answers with when the file was not read."""
    if unreadable:
        raise ValueError(
            f"{file_name} could not be read, so nothing is added to it: "
            "the file would be replaced. Correct it on the Configs page's Raw tab first."
        )
