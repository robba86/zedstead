"""Shared JSON file utilities — validate, load, save."""

from __future__ import annotations

import json
import math
import os
import stat
import struct
import tempfile
import time
from collections.abc import Iterable
from decimal import Decimal
from pathlib import Path

from app.shared.platform import IS_WINDOWS


def atomic_write_text(
    path: Path, text: str, *, newline: str | None = None, owner_only: bool = False
) -> None:
    """Atomically (re)write a text file.

    Writes to a temp file in the same directory, fsyncs it, then ``os.replace``s
    it over the target. The replace is a single rename, so a crash or kill
    mid-write can never leave the file truncated — a reader sees either the old
    complete file or the new one, never a half-written one. The temp lives in
    the same directory to keep the rename on one filesystem (cross-device
    replace fails). Use for in-place rewrites that previously opened the
    destination with ``"w"`` (or bare ``write_text``) and could lose data if
    interrupted partway. ``newline=""`` writes *text*'s line endings verbatim
    instead of translating ``\\n`` to the platform's.

    **A file that exists keeps its permission bits**: the temp file is a
    mkstemp (0600), and renamed over a mission file at 0644 it left that file
    readable by its owner only, so an FTP user or a backup tool running as
    someone else lost it after the first panel save. A new file is 0600.
    ``owner_only=True`` makes the result 0600 whatever the old file was: the
    writers of a secret pass it, so their next save also tightens a file an
    older version left readable (SEC-4).
    """
    _atomic_replace(
        path, text, owner_only=owner_only, mode="w", encoding="utf-8", newline=newline
    )


def read_text_lossy(path: Path, encoding: str = "utf-8") -> str:
    """Read a text file to show it or to take values from it, never to write it back.

    A file a person edits can be saved in another encoding (Windows-1252 from
    an ANSI editor, a ban reason typed into an RCon tool), and a strict read
    raised out of every route that needed one value from it: with such a
    serverDZ.cfg the status, config and maps routes all answered 500. Bytes
    that are not UTF-8 come back as U+FFFD. Code that changes the file and
    writes it back reads it with :func:`read_text_to_rewrite` instead.
    ``utf-8-sig`` as *encoding* drops a byte order mark.
    """
    return path.read_text(encoding=encoding, errors="replace")


class NotUtf8Error(ValueError):
    """*path*, which was about to be rewritten, is not UTF-8 text."""

    def __init__(self, path: Path) -> None:
        super().__init__(
            f"{path.name} is not saved as UTF-8. Zedstead does not rewrite it, because the characters "
            "it cannot read would be lost. Save it as UTF-8 in your editor."
        )


def read_text_to_rewrite(path: Path) -> str:
    """Read a text file whose text is about to be changed and written back.

    A file that is not UTF-8 is refused (``NotUtf8Error``, a ``ValueError``,
    so a route answers it like any refused value): rewritten from a lossy
    read, the characters that could not be read would be gone for good. A
    writer that builds the new file from what a lossy reader returned calls
    this first for the same reason.
    """
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise NotUtf8Error(path) from None


def atomic_write_bytes(path: Path, data: bytes, *, owner_only: bool = False) -> None:
    """:func:`atomic_write_text` for binary content, such as a downloaded image."""
    _atomic_replace(path, data, owner_only=owner_only, mode="wb")


# On Windows a rename over a file fails with PermissionError while another
# handle has it open: a second save of the same file that is still reading it,
# or a virus scanner. Two quick clicks on Save gave "permission denied" in the
# Windows 11 user test (2026 Q4). The rename is tried again for about a
# second before the error stands.
_REPLACE_TRIES = 20
_REPLACE_WAIT = 0.05


def _replace(src: str, dst: Path) -> None:
    for attempt in range(_REPLACE_TRIES):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if not IS_WINDOWS or attempt == _REPLACE_TRIES - 1:
                raise
            time.sleep(_REPLACE_WAIT)


def _keep_mode(path: Path, fd: int) -> None:
    """Give the temp file *fd* the permission bits of the *path* it replaces.

    Only the rwx bits: a setuid bit is never copied onto new content. A file
    that does not exist yet, or one that cannot be read, leaves mkstemp's 0600.
    On Windows chmod only sets the read-only flag, so nothing is copied.
    """
    if IS_WINDOWS:
        return
    try:
        mode = stat.S_IMODE(os.stat(path).st_mode) & 0o777
    except OSError:
        return
    if mode != 0o600:
        try:
            os.fchmod(fd, mode)
        except OSError:
            pass


# Called with the path after every atomic write that landed.
# config_versions.note_written registers here, to record what Zedstead wrote, so
# a later edit made outside Zedstead shows on the Timeline.
_after_write: list = []


def on_atomic_write(fn) -> None:
    """Call *fn(path)* after every atomic write. A failing *fn* never fails the write."""
    _after_write.append(fn)


def _atomic_replace(
    path: Path, content: str | bytes, *, owner_only: bool = False, **open_kwargs
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name + ".", suffix=".tmp",
    )
    try:
        if not owner_only:
            _keep_mode(path, fd)
        with os.fdopen(fd, **open_kwargs) as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        _replace(tmp_name, path)
    except BaseException:
        # Never leave the temp file behind if the replace didn't happen.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    notify_written(path)


def notify_written(path: Path) -> None:
    """Run the :func:`on_atomic_write` hooks for *path*. A writer that does not
    go through ``_atomic_replace`` calls this, so the Timeline does not show
    Zedstead's own write as a change made outside Zedstead."""
    for fn in _after_write:
        try:
            fn(path)
        except Exception:  # noqa: BLE001 — a record of the write never fails it
            pass


def copy_file(source: Path, dest: Path) -> None:
    """Copy *source* over the file *dest* (``shutil.copy2``) and record it as
    Zedstead's write. Use it for every copy onto a file a person also edits."""
    import shutil

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)
    notify_written(dest)


def atomic_write_lines(path: Path, lines: Iterable[str], *, owner_only: bool = False) -> None:
    """Atomically (re)write a text file as newline-terminated lines.

    Thin wrapper over :func:`atomic_write_text` — see it for the crash-safety
    guarantee. Materialising the lines first means an exception raised while
    iterating ``lines`` happens before any temp file is created.
    """
    atomic_write_text(path, "".join(f"{line}\n" for line in lines), owner_only=owner_only)


def lines_with_endings(text: str) -> list[list[str]]:
    """*text* as ``[line, ending]`` pairs, split at ``"\\n"`` only (as
    BattlEye reads its files; ``str.splitlines`` also breaks at U+2028).
    The ending is ``"\\r\\n"``, ``"\\n"``, or ``""`` for a last line with no
    final newline. ``"".join(a + b for a, b in pairs) == text``."""
    pieces = text.split("\n")
    pairs = [[p[:-1], "\r\n"] if p.endswith("\r") else [p, "\n"] for p in pieces[:-1]]
    if pieces[-1]:
        pairs.append([pieces[-1], ""])
    return pairs


def join_edited_lines(pairs: list[list[str]], *, eol: str, final_newline: bool) -> str:
    """Join ``[line, ending]`` pairs after lines were removed or appended
    (an appended pair's ending may be ``""``): every line but the last ends
    with its own ending or *eol*, and the last has a newline only when the
    file had one (*final_newline*)."""
    for pair in pairs[:-1]:
        pair[1] = pair[1] or eol
    if pairs:
        pairs[-1][1] = (pairs[-1][1] or eol) if final_newline else ""
    return "".join(line + end for line, end in pairs)


class SourceFloat(float):
    """A float read from a file, carrying the literal it was written as.

    DayZ mods write their JSON through Enforce, which prints 17 significant
    digits: ``1647.6500244140626``, ``-0.000003000000106112566``. Python's
    shortest ``repr`` of the same double differs in the last digit and turns
    small values into ``-3.000000106112566e-06``, so one drag on the map
    rewrote 38 numbers nobody touched (F75, plans/54 D). ``dumps_json``
    writes ``.text`` back. Arithmetic returns a plain ``float``, so a value
    the editor changed is written fresh.
    """

    __slots__ = ("text",)

    def __new__(cls, text: str) -> "SourceFloat":
        self = super().__new__(cls, text)
        self.text = text
        return self


def float32_equal(a: float, b: float) -> bool:
    """True when *a* and *b* are the same 32-bit float, the precision Enforce
    holds a mod's ``float`` in: ``0.05`` and the file's ``0.05000000074505806``
    are one value to the game."""
    try:
        return struct.pack("<f", a) == struct.pack("<f", b)
    except (OverflowError, struct.error):
        return False


def _dump_float(value: float) -> str:
    """``repr``, but never exponent notation: nothing verifies that Enforce's
    ``JsonFileLoader`` reads ``e-06``, and a file zsm writes must not be the
    first place it is tried."""
    if isinstance(value, SourceFloat):
        return value.text
    text = repr(value)
    if "e" not in text or not math.isfinite(value):
        return json.dumps(value)
    text = format(Decimal(text), "f")
    return text if "." in text else text + ".0"


def dumps_json(
    data: object,
    *,
    indent: int = 4,
    inline_scalar_lists: bool = False,
    previous: object = None,
) -> str:
    """``json.dumps(indent=...)`` that writes every number the way the file had it.

    - A :class:`SourceFloat` is written as its source text, and so is any
      other float equal to the ``SourceFloat`` at the same place in
      *previous* (the file as it was, read with :func:`load_json_preserving`):
      a popup sends back every field, and an untouched one arrives as a plain
      float. Carrying text over is safe whenever the values are equal, since
      the text parses to that same double.
    - Any other float is its ``repr``, without exponent notation.
    - ``inline_scalar_lists`` keeps a list of scalars on one line, the way
      Bohemia hand-writes ``"Position": [ x, y, z ]``; plain ``indent=4``
      puts every number on its own line, and one edited value produced a
      1287-line diff (measured in 2026 Q3, plans/46 C). Lists holding an
      object or a list are still broken out, one item per line.

    With no floats and ``inline_scalar_lists=False`` the output is exactly
    ``json.dumps(data, indent=indent, ensure_ascii=False)``.
    """
    def fmt(value: object, level: int, prev: object) -> str:
        pad, inner = " " * (indent * level), " " * (indent * (level + 1))
        if isinstance(value, dict):
            if not value:
                return "{}"
            before = prev if isinstance(prev, dict) else {}
            items = (
                f"{inner}{json.dumps(k, ensure_ascii=False)}: {fmt(v, level + 1, before.get(k))}"
                for k, v in value.items()
            )
            return "{\n" + ",\n".join(items) + "\n" + pad + "}"
        if isinstance(value, list):
            if not value:
                return "[]"
            before = prev if isinstance(prev, list) else []
            parts = [fmt(v, level + 1, before[i] if i < len(before) else None) for i, v in enumerate(value)]
            if inline_scalar_lists and not any(isinstance(v, (dict, list)) for v in value):
                return "[ " + ", ".join(parts) + " ]"
            return "[\n" + ",\n".join(inner + p for p in parts) + "\n" + pad + "]"
        if isinstance(value, float):
            if (
                not isinstance(value, SourceFloat)
                and isinstance(prev, SourceFloat)
                and prev == value
            ):
                return prev.text
            return _dump_float(value)
        return json.dumps(value, ensure_ascii=False)

    return fmt(data, 0, previous)


def dumps_inline_arrays(data: object, *, indent: int = 4) -> str:
    """:func:`dumps_json` with lists of scalars kept on one line."""
    return dumps_json(data, indent=indent, inline_scalar_lists=True)


def dumps_json_over(path: Path, data: object, **kwargs) -> str:
    """:func:`dumps_json` of *data* that is about to replace *path*.

    Reads the file as it is now for ``previous``, and ends the text with a
    newline unless the file did not (the mods' own writer adds none, so the
    last line was a diff of its own). A file that is missing or unreadable
    only means there is nothing to keep; it never blocks the save.
    """
    try:
        text = path.read_text(encoding="utf-8-sig") if path.is_file() else None
        previous = json.loads(text, parse_float=SourceFloat) if text is not None else None
    except (OSError, ValueError):
        text = previous = None
    end = "" if text and not text.endswith("\n") else "\n"
    return dumps_json(data, previous=previous, **kwargs) + end


def save_json(
    path: Path, data: dict | list, *, indent: int = 4, sort_keys: bool = False,
) -> None:
    """Write JSON data to disk with automatic versioning snapshot.

    Atomic (temp file + os.replace): every TraderPlus/gameplay config editor
    routes through here, so a crash mid-write must not truncate a live config.
    The serialized output is identical to the old bare write_text.
    """
    _auto_snapshot(path)
    atomic_write_text(
        path,
        json.dumps(data, indent=indent, ensure_ascii=False, sort_keys=sort_keys) + "\n",
    )


def _auto_snapshot(path: Path) -> None:
    """Create a versioned snapshot if the file exists and has changed."""
    if not path.is_file():
        return
    try:
        from app.shared.config_versions import snapshot
        snapshot(path, trigger="auto_save")
    except Exception:
        pass  # never block a save because of versioning


def validate_json_file(path: str | Path) -> bool:
    """Return True if the file exists and contains valid JSON."""
    try:
        load_json(path)
        return True
    except (json.JSONDecodeError, FileNotFoundError):
        return False


def load_json(
    path: str | Path, *, encoding: str = "utf-8-sig", parse_float=None,
) -> dict | list:
    """Read and parse a JSON file, raising clear errors on failure.

    Uses ``utf-8-sig`` by default to handle BOM (common in DayZ/Windows files).
    """
    p = Path(path)
    text = p.read_text(encoding=encoding)
    try:
        return json.loads(text, parse_float=parse_float)
    except json.JSONDecodeError as exc:
        raise json.JSONDecodeError(
            f"{p.name}: {exc.msg}", exc.doc, exc.pos
        ) from exc


def load_json_preserving(path: str | Path) -> dict | list:
    """:func:`load_json`, every float a :class:`SourceFloat` holding its text,
    so :func:`dumps_json` can write back exactly what it did not change."""
    return load_json(path, parse_float=SourceFloat)
