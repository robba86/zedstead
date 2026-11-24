"""Reading a binarised config (``config.bin``, the raP format).

Bohemia's games store a PBO's ``config.cpp`` binarised. The mod conflict scan
(`app.modmgr.mod_conflict`) reads a mod's classes and ``requiredAddons`` with
it, and the map editor reads an official map's place names from its world
config (`app.maps.world_names`).

A config.bin comes from a mod or a server install, so it is untrusted input:
every reader here walks iteratively or with a fixed depth, and truncated or
unknown data raises IndexError, ValueError or struct.error for the caller to
catch, never a recursion error.
"""

from __future__ import annotations

import struct

# raP header: "\0raP", two reserved uint32s, the enum offset — the root class body follows.
RAP_ROOT_OFFSET = 16
RAP_MAGIC = b"\x00raP"


def read_compressed_int(data: bytes, pos: int) -> tuple[int, int]:
    """Read a rapified compressed integer. Returns (value, new_pos)."""
    if pos >= len(data):
        return 0, pos
    b = data[pos]
    if b < 128:
        return b, pos + 1
    # Multi-byte: value = (b0 & 0x7F) | (b1 << 7) | ...
    value = b & 0x7F
    shift = 7
    pos += 1
    while pos < len(data) and shift < 32:
        b = data[pos]
        value |= (b & 0x7F) << shift
        shift += 7
        pos += 1
        if b < 128:
            break
    return value, pos


def skip_array(data: bytes, pos: int) -> int:
    """Return the position just past the raP array (count + typed elements) at *pos*.

    Iterative: a nested array keeps its parent's remaining count on a list, so
    how deep a config.bin nests is limited by its bytes, not by Python's
    recursion limit, which 5000 nested arrays exceeded (plans/68 CON-9).
    """
    remaining, pos = read_compressed_int(data, pos)
    parents: list[int] = []
    while True:
        while remaining > 0:
            remaining -= 1
            elem_type = data[pos]
            pos += 1
            if elem_type in (0, 4):  # string, variable name
                pos = data.index(b"\x00", pos) + 1
            elif elem_type in (1, 2):  # float, int32
                pos += 4
            elif elem_type == 6:  # int64
                pos += 8
            elif elem_type == 3:  # nested array
                parents.append(remaining)
                remaining, pos = read_compressed_int(data, pos)
            else:
                raise ValueError(f"unknown raP array element type {elem_type}")
        if not parents:
            return pos
        remaining = parents.pop()


def _scalar(data: bytes, pos: int, kind: int) -> tuple[object, int]:
    """One raP value of *kind* (0 string, 1 float, 2 int32, 4 variable, 6 int64) at *pos*."""
    if kind in (0, 4):
        end = data.index(b"\x00", pos)
        return data[pos:end].decode("utf-8", errors="replace"), end + 1
    if kind == 1:
        return struct.unpack_from("<f", data, pos)[0], pos + 4
    if kind == 2:
        return struct.unpack_from("<i", data, pos)[0], pos + 4
    if kind == 6:
        return struct.unpack_from("<q", data, pos)[0], pos + 8
    raise ValueError(f"unknown raP value type {kind}")


def flat_array(data: bytes, pos: int) -> list | None:
    """The scalars of the raP array at *pos* (``position[] = {x, z}``), or None
    when it holds a nested array: a flat reader never recurses."""
    count, pos = read_compressed_int(data, pos)
    out = []
    for _ in range(count):
        kind = data[pos]
        if kind == 3:
            return None
        value, pos = _scalar(data, pos + 1, kind)
        out.append(value)
    return out


def class_values(data: bytes, offset: int):
    """Yield ``(entry_type, name, body_offset, value)`` for the raP class body at *offset*.

    Entry types: 0 class (the only one with a body offset), 1 value, 2 array,
    3 forward declaration (``class Foo;``), 4 delete, 5 flagged array (``+=``).
    *value* is the scalar of a value entry and the position of an array entry
    (read it with `flat_array`), else None.
    Raises IndexError / ValueError / struct.error on truncated or unknown data.
    """
    pos = data.index(b"\x00", offset) + 1  # inherited class name
    count, pos = read_compressed_int(data, pos)
    for _ in range(count):
        entry_type = data[pos]
        pos += 1
        subtype = None
        if entry_type == 1:
            subtype = data[pos]
            pos += 1
        elif entry_type == 5:
            pos += 4  # flags
        end = data.index(b"\x00", pos)
        name = data[pos:end].decode("ascii", errors="replace")
        pos = end + 1

        body_offset = None
        value = None
        if entry_type == 0:
            body_offset = struct.unpack_from("<I", data, pos)[0]
            pos += 4
        elif entry_type == 1:
            if subtype in (0, 1, 2, 4, 6):
                value, pos = _scalar(data, pos, subtype)
            else:  # a 4-byte value of a kind nothing here reads
                pos += 4
        elif entry_type in (2, 5):
            value = pos
            pos = skip_array(data, pos)
        elif entry_type not in (3, 4):
            raise ValueError(f"unknown raP entry type {entry_type}")
        yield entry_type, name, body_offset, value


def class_entries(data: bytes, offset: int):
    """Yield ``(entry_type, name, body_offset)`` for the raP class body at *offset*
    (`class_values` without the values)."""
    for entry_type, name, body_offset, _value in class_values(data, offset):
        yield entry_type, name, body_offset


def class_parent(data: bytes, offset: int) -> str:
    """The class the raP class body at *offset* inherits from (``class M134 :
    Rifle_Base``), or "" for none: the null-terminated name that opens every
    body, which `class_values` skips."""
    end = data.index(b"\x00", offset)
    return data[offset:end].decode("ascii", errors="replace")


def class_scope(data: bytes, offset: int) -> int | None:
    """The ``scope`` value the raP class body at *offset* sets itself, or None
    when it inherits one. 2 is an item the game can spawn, 0 and 1 a base."""
    for entry_type, name, _body, value in class_values(data, offset):
        if entry_type == 1 and name.lower() == "scope" and isinstance(value, (int, float)):
            return int(value)
    return None


def find_class(data: bytes, path: list[str]) -> int | None:
    """The body offset of the class at *path* from the root (``["CfgWorlds",
    "ChernarusPlus", "Names"]``), each step matched without regard to case as
    the engine does, or None."""
    offset = RAP_ROOT_OFFSET
    for step in path:
        want = step.lower()
        offset = next(
            (o for kind, name, o in class_entries(data, offset) if kind == 0 and name.lower() == want),
            None,
        )
        if offset is None:
            return None
    return offset
