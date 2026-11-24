"""whitelist_ops — read/write/CRUD for whitelist.txt.

``enableWhitelist = 1`` in serverDZ.cfg makes the engine refuse anyone who is
not in ``whitelist.txt``: kick code ``0x0004000C - NOT_WHITELISTED``, *"Player
is not present in whitelist.txt"*
(``dayz-docs/wiki/pages/error-codes.wiki:219``). zsm could already turn that
switch on and had no way to put a single name in the file, which is a locked
door with the key thrown away.

**The format is the file Bohemia ships.** The wiki names the file and the
kick, nothing else, but every server install carries a stock whitelist.txt
whose header is the documentation: CRLF lines, ``//`` comments (also *after*
an ID, "to easily mark it"), one ID per line, and a 44-character player ID
from the .ADM as the example. So this module **edits lines, it never
regenerates the file**: adding appends one line, removing drops the line that
holds the ID, and every comment, blank line, line ending and ID it does not
understand (the 44-character form) is left exactly where it was. The first
version rebuilt the file from the Steam64 IDs it could parse, which erased
the header and silently deleted every 44-character ID — on a server with the
switch on, that kicks those players.

Both kinds of ID are listed, added and removed: a Steam64 ID and the
44-character player ID the stock header asks for (2026 Q4; until then
only Steam64 was listed, and the ID Bohemia's own file asks for could not
be added). A file without a final newline keeps none after an add or a
remove, as Bohemia's has none.

A file an earlier zsm wrote as ``SteamId;SteamId`` (the ``priority.txt``
convention it borrowed) is still read, and still appended to with ``;``.
The line editor is ``priority_ops``' (``add_id_line``, ``remove_id_line``),
which priority.txt uses the same way.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.ops.priority_ops import add_id_line, id_comments, is_valid_steam64, listed_ids, remove_id_line

# The player ID from the .ADM: "add his player ID (44 characters long ID)"
# (the stock whitelist.txt's header). It is the base64 of a hash, written in
# the standard or the URL-safe alphabet; fullmatch, as for Steam64.
_PLAYER_ID_RE = re.compile(r"[A-Za-z0-9+/=_-]{44}")

__all__ = [
    "WHITELIST_FILENAME",
    "add_whitelist_id",
    "is_valid_steam64",
    "is_whitelist_id",
    "read_whitelist",
    "whitelist_comments",
    "remove_whitelist_id",
    "whitelist_path",
]

WHITELIST_FILENAME = "whitelist.txt"


def is_whitelist_id(value: str) -> bool:
    """A Steam64 ID or a 44-character player ID."""
    return is_valid_steam64(value) or bool(_PLAYER_ID_RE.fullmatch(value))


def whitelist_path(config) -> Path:
    """Where the engine looks: the root of the server install, like priority.txt."""
    return config.server_dir_path / WHITELIST_FILENAME


def read_whitelist(path: Path) -> list[str]:
    """Parse whitelist.txt → IDs (Steam64 or 44-character) in file order.

    Missing or empty file → ``[]``. BOM tolerated, ``//`` comments skipped.
    Tokens that are neither are not listed, but they stay in the file, see
    the module docstring.
    """
    return listed_ids(path, is_whitelist_id)


def whitelist_comments(path: Path) -> dict[str, str]:
    """The comment on each listed ID's line, for the Whitelist tab: the stock
    file's example ID says "Example of a character ID" only there."""
    return id_comments(path, is_whitelist_id)


def add_whitelist_id(path: Path, steam_id: str) -> bool:
    """Append *steam_id*. Returns ``False`` on duplicate.

    Raises ``ValueError`` if *steam_id* is neither a Steam64 ID nor a
    44-character player ID.
    """
    if not is_whitelist_id(steam_id):
        raise ValueError(
            f"Not a Steam64 ID or a 44-character player ID from the .ADM: {steam_id!r}"
        )
    if steam_id in read_whitelist(path):
        return False
    add_id_line(path, steam_id, join_single=False)
    return True


def remove_whitelist_id(path: Path, steam_id: str) -> bool:
    """Remove *steam_id*. Returns ``False`` if it was not there.

    A line left with no ID goes, comment and all — the comment was that
    player's label. Every other line is written back byte for byte.
    """
    if steam_id not in read_whitelist(path):
        return False
    remove_id_line(path, steam_id)
    return True
