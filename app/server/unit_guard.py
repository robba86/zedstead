"""May zsm replace the server's installed unit? (plans/78 D2, step 4)

Step 0 of plans/78 measured the first Update on a server zsm did not install
overwriting its hand-written unit without a word: the new unit had no `-mod=`
and another `-profiles=`, so the next start ran without mods. Every path that
installs the unit asks `check` first.

The rule:

- A unit without `ZSM_UNIT_MARKER`, a line only zsm's template carries, is
  **foreign**. Nothing replaces it; the caller says so.
- A unit with the marker is zsm's. If its text is what zsm last installed
  (`record` keeps that text's sha256 per instance) or what zsm renders now, it
  is replaced as before. Otherwise it was **edited** by hand: the CLI shows the
  difference and asks, `--yes` does not count as the answer, and a path that
  cannot ask (a job, a web route) refuses.
- With the marker and no record (an install from before this), the marker
  decides: the unit is zsm's, as it always was.

`zsm service install --replace` is the one deliberate way past a refusal.
"""

from __future__ import annotations

import difflib
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.shared.config import DayzConfig

#: A line of the template only a unit zsm rendered carries
#: (`app/templates/dayz-server.service.j2`; `tests/test_adopt.py` pins it).
ZSM_UNIT_MARKER = "# zsm's own start/restart runs `reset-failed` first"

ABSENT = "absent"
OWN = "own"
EDITED = "edited"
FOREIGN = "foreign"
UNREADABLE = "unreadable"


@dataclass(frozen=True)
class UnitCheck:
    state: str
    path: Path
    current: str = ""

    @property
    def may_replace(self) -> bool:
        return self.state in (ABSENT, OWN)

    def refusal(self, config: DayzConfig) -> str:
        """The sentence a path that cannot ask gives instead of installing."""
        from app.shared.systemd_scope import zsm_command

        install = zsm_command(config, "service install")
        if self.state == FOREIGN:
            return (
                f"{self.path} was not written by Zedstead, so it was left as it is. "
                f"Run '{install} --replace' to replace it with Zedstead's unit, "
                "or keep starting the server with your own."
            )
        if self.state == EDITED:
            return (
                f"{self.path} was edited by hand since Zedstead installed it, so it was "
                f"left as it is. Run '{install}' to see the difference and choose."
            )
        if self.state == UNREADABLE:
            return f"{self.path} could not be read, so it was left as it is."
        return ""


class UnitNotOurs(ValueError):
    """The installed unit may not be replaced; the message says why."""


def guard(config: DayzConfig, rendered: str) -> None:
    """Raise `UnitNotOurs` unless *rendered* may replace the installed unit."""
    unit_check = check(config, rendered)
    if not unit_check.may_replace:
        raise UnitNotOurs(unit_check.refusal(config))


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _record_path(config: DayzConfig) -> Path:
    return config.paths.installed_unit_hash


def installed_path(config: DayzConfig) -> Path:
    from app.shared.systemd_scope import get_scope, server_unit

    return get_scope(config).unit_dir / server_unit(config)


def is_zsm_unit(text: str) -> bool:
    return ZSM_UNIT_MARKER in text


def check(config: DayzConfig, rendered: str, path: Path | None = None) -> UnitCheck:
    """Classify the installed unit against *rendered*, the text about to go in."""
    path = path or installed_path(config)
    if not path.exists():
        return UnitCheck(ABSENT, path)
    try:
        current = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return UnitCheck(UNREADABLE, path)
    if not is_zsm_unit(current):
        return UnitCheck(FOREIGN, path, current)
    if current == rendered:
        return UnitCheck(OWN, path, current)
    try:
        recorded = _record_path(config).read_text(encoding="utf-8").strip()
    except OSError:
        recorded = ""
    if not recorded or recorded == _sha(current):
        return UnitCheck(OWN, path, current)
    return UnitCheck(EDITED, path, current)


def record(config: DayzConfig, installed: str) -> None:
    """Remember what zsm just installed, so a later edit can be told apart."""
    from app.shared.json_utils import atomic_write_text

    path = _record_path(config)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, _sha(installed) + "\n")
    except OSError:
        pass  # without a record the marker decides, as before


def diff(unit_check: UnitCheck, rendered: str) -> str:
    """The installed unit against what would replace it, as a unified diff."""
    return "".join(
        difflib.unified_diff(
            unit_check.current.splitlines(keepends=True),
            rendered.splitlines(keepends=True),
            fromfile=f"{unit_check.path} (installed)",
            tofile="what Zedstead would install",
        )
    )
