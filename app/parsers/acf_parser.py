"""Valve Data Format (VDF/ACF) parser for SteamCMD workshop metadata.

Parses ``appworkshop_221100.acf`` to extract local mod timestamps,
allowing comparison with remote Steam Web API data without relying
on filesystem mtime.

Also writes it back: deleting a workshop item's folder without dropping it
from the ACF leaves SteamCMD believing the item is still installed, and it
then tries to *reuse* that item's chunks for the next download — the missing
files abort the whole transfer with ``Missing game files`` and a bare
``ERROR! Download item <id> failed (Failure).``  One deleted mod therefore
breaks every later workshop download for the app until the ACF is repaired.
"""

from __future__ import annotations

from pathlib import Path

from app.shared.json_utils import atomic_write_text


def parse_acf(text: str) -> dict:
    """Parse a VDF/ACF text string into a nested dict.

    VDF is a simple recursive key-value format::

        "key" "value"
        "key" { ... }

    Handles both quoted and unquoted keys/values.
    """
    tokens = _tokenize(text)
    result: dict = {}
    _parse_block(tokens, result)
    return result


def parse_acf_file(path: Path) -> dict:
    """Parse a VDF/ACF file from disk."""
    return parse_acf(path.read_text(encoding="utf-8"))


def get_installed_timestamps(acf_data: dict) -> dict[str, int]:
    """Extract mod_id -> timeupdated from parsed ACF data.

    Looks in ``AppWorkshop.WorkshopItemsInstalled.<mod_id>.timeupdated``
    and falls back to ``AppWorkshop.WorkshopItemDetails.<mod_id>.timeupdated``.
    """
    app = acf_data.get("AppWorkshop", acf_data)
    timestamps: dict[str, int] = {}
    if not isinstance(app, dict):
        return timestamps

    for section_key in ("WorkshopItemsInstalled", "WorkshopItemDetails"):
        # A manifest cut off mid-write parses its last key as a string.
        section = app.get(section_key)
        if not isinstance(section, dict):
            continue
        for mod_id, data in section.items():
            if mod_id in timestamps:
                continue
            if isinstance(data, dict):
                ts = data.get("timeupdated", "0")
                try:
                    timestamps[mod_id] = int(ts)
                except (ValueError, TypeError):
                    pass

    return timestamps


_ITEM_SECTIONS = (
    "WorkshopItemsInstalled",
    "WorkshopItemDetails",
    "WorkshopItemsPendingUpdate",
)


def dump_acf(data: dict, indent: int = 0) -> str:
    """Serialize a parsed ACF dict back to VDF text (tabs, Valve style)."""
    pad = "\t" * indent
    out: list[str] = []
    for key, value in data.items():
        if isinstance(value, dict):
            out.append(f'{pad}"{key}"\n{pad}{{\n')
            out.append(dump_acf(value, indent + 1))
            out.append(f"{pad}}}\n")
        else:
            out.append(f'{pad}"{key}"\t\t"{value}"\n')
    return "".join(out)


def remove_workshop_items(acf_path: Path, mod_ids: list[str] | set[str]) -> bool:
    """Drop *mod_ids* from the workshop ACF so SteamCMD forgets them.

    Returns True when the file was rewritten (or removed). A parse/write
    failure falls back to deleting the ACF outright: SteamCMD regenerates it
    on the next download, which is a slower but always-correct repair — and
    far better than leaving stale entries that break every future download.
    """
    if not acf_path.is_file():
        return False
    wanted = {str(m) for m in mod_ids}
    if not wanted:
        return False

    try:
        data = parse_acf_file(acf_path)
        app = data.get("AppWorkshop")
        if not isinstance(app, dict):
            raise ValueError("no AppWorkshop block")

        removed = False
        for section_key in _ITEM_SECTIONS:
            section = app.get(section_key)
            if not isinstance(section, dict):
                continue
            for mod_id in wanted & set(section):
                del section[mod_id]
                removed = True

        if not removed:
            return False

        # SizeOnDisk/NeedsUpdate counters no longer match; SteamCMD recomputes
        # them on the next run, so leaving them is harmless.
        atomic_write_text(acf_path, dump_acf(data))
        return True
    except Exception:
        try:
            acf_path.unlink()
            return True
        except OSError:
            return False


# ---------------------------------------------------------------------------
# Tokenizer / parser internals
# ---------------------------------------------------------------------------


def _tokenize(text: str) -> list[str]:
    """Split VDF text into tokens (quoted strings, braces)."""
    tokens: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
        elif c == "/" and i + 1 < n and text[i + 1] == "/":
            # line comment
            while i < n and text[i] != "\n":
                i += 1
        elif c == '"':
            # quoted string
            j = i + 1
            while j < n and text[j] != '"':
                if text[j] == "\\" and j + 1 < n:
                    j += 2
                else:
                    j += 1
            tokens.append(text[i + 1 : j])
            i = j + 1
        elif c in "{}":
            tokens.append(c)
            i += 1
        else:
            # unquoted token
            j = i
            while j < n and text[j] not in ' \t\r\n"{}':
                j += 1
            tokens.append(text[i:j])
            i = j
    return tokens


def _parse_block(tokens: list[str], result: dict) -> None:
    """Consume tokens into a dict. Modifies the token list in place."""
    while tokens:
        token = tokens.pop(0)
        if token == "}":
            return
        if not tokens:
            break
        next_token = tokens[0]
        if next_token == "{":
            tokens.pop(0)
            child: dict = {}
            _parse_block(tokens, child)
            result[token] = child
        else:
            tokens.pop(0)
            result[token] = next_token
