"""Raw file editing shared by the generic Raw editor tab
(`routers/configs.py`) and the `kind="text"` adapters (`text_fallback.py`).

Every save validates (defusedxml for XML, json for JSON), snapshots the
existing file, then writes. `cfg`/`txt` have no language-wide check; the two
`.cfg` files that matter carry their own through `RawTarget.validate`
(serverDZ.cfg's grammar and mission, beserver_x64.cfg's `Key value` lines —
see `server_forms`). Before plans/51 C a `.cfg` was not looked at at all, and
an Upload of `garbage garbage` replaced the live serverDZ.cfg.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.shared.config import DayzConfig
from app.shared.config_versions import snapshot
from app.shared.json_utils import atomic_write_text, read_text_lossy
from app.xml.xml_text import XmlSyntaxError, check_xml_text


@dataclass
class RawTarget:
    """One editable file behind an editor's Raw tab.

    ``name`` is a stable, path-free label (the file's basename). Callers match
    the request's ``target`` param against ``name`` — it is never used to build
    a filesystem path, so it can't be abused for traversal.
    """

    name: str
    path: Path
    language: str  # "xml" | "json" | "cfg" | "txt"
    #: Extra check a file needs beyond being well-formed, run before anything
    #: is written. Raises with a message the admin can act on (plans/47 C:
    #: Dynamic AI refuses a decimal in a field DayZ loads as a whole number).
    validate: Callable[[str], None] | None = None
    #: Refuses a save for a reason that is not in the content, before it is
    #: checked, snapshotted or written. BattlEye's cfg while the server runs.
    guard: Callable[[], None] | None = None
    #: Where the current text is read from (Raw tab, preview, Download) when
    #: that is not ``path``: BattlEye renames beserver_x64.cfg while running.
    read_path: Path | None = None
    #: Copies what the saved text sets into zsm's own config, which the route
    #: then saves with the file (``WebState.mutate_config``). BattlEye's cfg
    #: carries the RCon pair zsm dials with.
    adopt: Callable[[DayzConfig, str], None] | None = None

    @property
    def source(self) -> Path:
        """The file whose text the Raw tab and a Download show."""
        return self.read_path or self.path


def refuse_if(check: Callable[[str], str | None]) -> Callable[[str], None]:
    """A ``RawTarget.validate`` from a check that returns what is wrong."""

    def _validate(content: str) -> None:
        problem = check(content)
        if problem:
            raise ValueError(problem)

    return _validate


def load_raw(path: Path) -> str:
    """Return the file's text, or "" if it doesn't exist yet.

    A file that is not UTF-8 is shown with U+FFFD where it could not be read,
    so the Raw tab is where it can be put right; a strict read answered 500.
    """
    return read_text_lossy(path) if path.is_file() else ""


def validate_raw(content: str, language: str) -> None:
    """Raise if ``content`` isn't well-formed for a validated language.

    XML goes through the shared checker so the 400 the routers build from
    ``str(e)`` names the line and says what's wrong in English, and so an
    entity-expansion payload reports as malformed XML rather than leaking a
    stringified `DefusedXmlException`.
    """
    if language == "xml":
        problem = check_xml_text(content)
        if problem is not None:
            raise XmlSyntaxError(problem)
    elif language == "json":
        json.loads(content)


def save_raw(
    path: Path,
    language: str,
    content: str,
    validate: Callable[[str], None] | None = None,
    guard: Callable[[], None] | None = None,
) -> None:
    """Refuse (``guard``), validate, snapshot the current file, then write."""
    check_raw(language, content, validate=validate, guard=guard)
    write_raw(path, content)


def check_raw(
    language: str,
    content: str,
    validate: Callable[[str], None] | None = None,
    guard: Callable[[], None] | None = None,
) -> None:
    """The refusals of ``save_raw``, for a caller that writes later."""
    if guard is not None:
        guard()
    validate_raw(content, language)
    if validate is not None:
        validate(content)


def write_raw(path: Path, content: str) -> None:
    """Snapshot the current file, then write *content* (checked already)."""
    if path.is_file():
        snapshot(path, trigger="editor")
    # Never `write_text`: it empties the file before it encodes the text, so
    # content that cannot be encoded left a file of 0 bytes behind the error.
    atomic_write_text(path, content)


def raw_targets_from(
    *specs: tuple | Callable[[DayzConfig], RawTarget | None],
) -> Callable[[DayzConfig], list[RawTarget]]:
    """Build a ``raw_targets`` callable from ``(path_getter, language)`` pairs,
    or ``(path_getter, language, validate)`` for a file with its own check, or
    a ``config -> RawTarget | None`` function for a target that needs more
    (BattlEye's cfg: where it is read from and when a save is refused).

    Targets whose path can't be resolved (getter returns None — e.g. no mission
    folder) are dropped; each target's name is the file's basename.
    """

    def _targets(config: DayzConfig) -> list[RawTarget]:
        out: list[RawTarget] = []
        for spec in specs:
            if callable(spec):
                target = spec(config)
                if target is not None:
                    out.append(target)
                continue
            getter, language, *rest = spec
            p = getter(config)
            if p is not None:
                out.append(RawTarget(
                    name=p.name, path=p, language=language,
                    validate=rest[0] if rest else None,
                ))
        return out

    return _targets
