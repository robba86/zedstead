"""BEServer_x64.cfg reader/writer — space-separated key-value format."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from app.modmgr.mods import breaks_a_line
from app.shared.json_utils import (
    NotUtf8Error,
    atomic_write_text,
    join_edited_lines,
    lines_with_endings,
    read_text_lossy,
)

if TYPE_CHECKING:
    from app.shared.config import DayzConfig

# `Key` or `Key value`, the only shape the file has (zsm's own writer emits a
# bare key for an empty value, e.g. `RConIP `).
_BE_LINE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(?:\s+(?!=)\S.*)?\s*$")

# BattlEye's own copy while the server runs. Measured on a Linux 1.29 server
# (2026 Q3): starting the server renames beserver_x64.cfg to
# beserver_x64_active_<hex>.cfg (`beserver_x64_active_770d861c.cfg`), and
# stopping it renames that back over beserver_x64.cfg, mtime unchanged. So
# while the server runs there is no beserver_x64.cfg at all, and a file
# written there is overwritten at the next stop. Windows is believed to use
# `BEServer_x64_active_<hex>.cfg` but that is not measured, hence the
# case-insensitive match.
_ACTIVE_CFG = re.compile(r"^beserver_x64_active_[0-9a-f]+\.cfg$", re.IGNORECASE)


def live_beserver_cfg(config: DayzConfig) -> Path:
    """The file BattlEye is using: its active copy if one exists, else the cfg.

    Every reader that means "what BattlEye runs with" goes through this; a
    writer still writes ``config.beserver_cfg_path`` and refuses while the
    server runs (``refuse_while_running``). With several active copies (a crash
    can leave one behind, which is not measured) the newest by mtime wins.
    """
    try:
        active = [
            p for p in config.battleye_dir.iterdir()
            if _ACTIVE_CFG.match(p.name) and p.is_file()
        ]
    except OSError:
        active = []
    if active:
        return max(active, key=lambda p: (p.stat().st_mtime, p.name))
    return config.beserver_cfg_path


def live_file(config: DayzConfig, path: Path) -> Path:
    """Where *path*'s current content is read from: ``live_beserver_cfg`` when
    *path* is beserver_x64.cfg, else *path* itself. For readers that take any
    config file, such as the versions list and diff."""
    return live_beserver_cfg(config) if path == config.beserver_cfg_path else path


class BeserverCfgInUse(RuntimeError):
    """A write to beserver_x64.cfg refused while the server runs or is still
    stopping. Routes map it to 409, the CLI prints it."""

    def __init__(self, name: str = "beserver_x64.cfg") -> None:
        super().__init__(
            f"The server is running or still stopping. BattlEye keeps its own copy "
            f"of {name} while it runs and puts it back when the server stops, so a "
            "change saved now would be lost. Stop the server first. If it is "
            "stopping, wait until it has stopped."
        )


def refuse_while_running(config: DayzConfig) -> None:
    """Raise ``BeserverCfgInUse`` while the server process may be alive. Call
    it before anything is snapshotted or written.

    That is ``service_control.is_server_process_alive``, not
    ``is_server_running``: BattlEye puts its copy back when the process exits,
    so a stop that has not finished (the unit is ``deactivating``) refuses
    too. An active file on its own does not refuse; a copied server folder
    can carry a stale one, and it must not block saves on a stopped server.
    """
    from app.server import service_control

    if service_control.is_server_process_alive(config):
        raise BeserverCfgInUse(config.beserver_cfg_path.name)


def read_beserver_cfg(path: Path) -> dict[str, str]:
    """Parse BEServer_x64.cfg into a dict. Returns empty dict if missing."""
    if not path.exists():
        return {}
    return parse_beserver_text(read_text_lossy(path))


def parse_beserver_text(text: str) -> dict[str, str]:
    """The ``Key value`` pairs of a beserver cfg's text, the last one of a key
    winning. The one parser for the file on disk and for a Raw save's text."""
    # "\n" only, as BattlEye reads the file: str.splitlines would also break
    # at a Unicode separator inside a value and read a key BattlEye does not
    # see (plans/68 LB-5). strip() takes the "\r" of a CRLF file.
    pairs = (_key_value(line) for line in text.lstrip("﻿").split("\n"))
    return dict(kv for kv in pairs if kv is not None)


class BeserverValueError(ValueError):
    """A key or value ``write_beserver_cfg`` refuses. Routes answer it with a
    400; the message names the key and never quotes the value, which may be
    the RCon password."""


def beserver_value_problem(value: str) -> str | None:
    """Why *value* cannot be one key or value of beserver_x64.cfg, or ``None``.

    Each ``Key value`` pair is one line. A character ``str.splitlines`` breaks
    at (``mods.breaks_a_line``) would start a directive of its own, or one
    that only a reader splitting that way sees (plans/68 LB-5). A tab stays
    allowed.
    """
    for ch in value:
        if ch != "\t" and breaks_a_line(ch):
            shown = {"\n": "a newline", "\r": "a carriage return"}.get(ch, f"the character U+{ord(ch):04X}")
            return f"contains {shown}, which would break its line in the BattlEye cfg"
    return None


def check_beserver_values(data: dict[str, str], labels: dict[str, str] | None = None) -> None:
    """Raise ``BeserverValueError`` for the first key or value of *data* that
    ``beserver_value_problem`` refuses, naming it by *labels* when given."""
    for key, value in data.items():
        problem = beserver_value_problem(key)
        if problem:
            raise BeserverValueError(f"A key in the BattlEye cfg {problem}.")
        problem = beserver_value_problem(str(value))
        if problem:
            raise BeserverValueError(f"{(labels or {}).get(key, key)} {problem}.")


def _key_value(line: str) -> tuple[str, str] | None:
    """A line's key and value as `read_beserver_cfg` reads them, or None for
    a blank or comment line."""
    line = line.strip()
    if not line or line.startswith("#") or line.startswith("//"):
        return None
    parts = line.split(None, 1)
    return parts[0], parts[1] if len(parts) == 2 else ""


def write_beserver_cfg(path: Path, data: dict[str, str]) -> None:
    """Write key-value pairs to BEServer_x64.cfg. Creates parent dirs.

    An existing file is edited, not regenerated (2026 Q4): ``#`` and
    ``//`` comment lines, blank lines, a key written twice, the line endings
    and the final newline all stay. A key whose value is unchanged keeps
    every line of it byte for byte; a changed key has each of its lines set
    to the new value (which of two lines BattlEye takes is not documented);
    a key no longer in *data* loses its lines; a new key is appended. The
    rewrite this replaced dropped the comments and collapsed a repeated key.

    Refuses (``BeserverValueError``) before anything is written when a key or
    value would not stay on its own line (``check_beserver_values``), and
    (``NotUtf8Error``) when the file there is not UTF-8: *data* starts from
    `read_beserver_cfg`, which reads such a file with replacement characters,
    and a password written back from that would no longer be the password.
    """
    check_beserver_values(data)
    if not path.is_file():
        atomic_write_text(path, "".join(f"{key} {value}\n" for key, value in data.items()))
        return
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise NotUtf8Error(path) from None
    bom = "\ufeff" if text.startswith("\ufeff") else ""
    text = text[len(bom):]
    eol = "\r\n" if "\r\n" in text else "\n"
    lines = lines_with_endings(text)
    final_newline = not lines or lines[-1][1] != ""
    current: dict[str, str] = {}
    for line, _end in lines:
        kv = _key_value(line)
        if kv:
            current[kv[0]] = kv[1]  # the last wins, as in read_beserver_cfg
    out = []
    for line, end in lines:
        kv = _key_value(line)
        if kv is None:
            out.append([line, end])
        elif kv[0] not in data:
            continue  # removed
        elif data[kv[0]] == current[kv[0]]:
            out.append([line, end])
        else:
            out.append([f"{kv[0]} {data[kv[0]]}", end])
    out += [[f"{key} {value}", eol] for key, value in data.items() if key not in current]
    atomic_write_text(path, bom + join_edited_lines(out, eol=eol, final_newline=final_newline), newline="")


def check_beserver_cfg(text: str) -> str | None:
    """The first line that isn't ``Key value``, or ``None`` (plans/51 C).

    Comment lines (``#``, ``//``) and blank lines are skipped, as the reader
    above skips them. This catches the wrong file in the wrong place, such as
    a serverDZ.cfg (``hostname = …``), JSON or XML uploaded here. It cannot
    tell a real key from a made-up one: BattlEye adds keys of its own, so a
    list would go stale. ``garbage garbage`` is therefore valid for this format.
    """
    # By "\n", as read_beserver_cfg and BattlEye read the file.
    for number, line in enumerate(text.lstrip("\ufeff").split("\n"), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "//")):
            continue
        if not _BE_LINE.match(stripped):
            # The line itself is not quoted back: it may hold RConPassword.
            return f"Line {number}: expected 'Key value', e.g. 'RConPort 2306'."
    return None

