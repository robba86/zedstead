"""Small readers every pre-configured mod's checks share (plans/47, plans/48).

A pre-configured mod is found by workshop ID, keeps its settings as JSON under
the server profile, and is reported on with paths relative to the server dir.
``dynamic_ai.py`` grew these first; ``zone_mods.py`` needed the same three, so
they live here rather than twice.
"""

from __future__ import annotations

import json
from pathlib import Path


def installed_mod_name(config, mod_name: str, workshop_id: str) -> str | None:
    """The active mod-list entry that is this mod, matched by workshop ID so a
    copy installed under another folder name is still found. None when it is
    not installed or is disabled."""
    from app.modmgr.mod_ops import resolve_names_to_ids
    from app.modmgr import modlist

    active = modlist.active(config)
    if mod_name in active:
        return mod_name
    ids = resolve_names_to_ids(active, modlist.workshop_ids(config))
    return next((name for name, wid in ids.items() if wid == workshop_id), None)


def display_path(config, path: Path) -> str:
    """*path* relative to the server dir when it is under it, as written."""
    try:
        return path.relative_to(config.server_dir_path).as_posix()
    except ValueError:
        return str(path)


def read_json(path: Path) -> tuple[dict | None, str | None]:
    """(document, error). Both None when the file does not exist.

    Every way the file can fail to read is an error string, never an
    exception: these files come from a mod's defaults or an admin's editor,
    and a page built on them must report one, not answer 500 (plans/68 CON-11).
    """
    if not path.is_file():
        return None, None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as e:
        return None, f"line {e.lineno}, column {e.colno}: {e.msg}"
    except UnicodeDecodeError as e:
        return None, f"not UTF-8 text (byte {e.start}); save it as UTF-8"
    except RecursionError:
        return None, "nested too deeply to read"
    except OSError as e:
        return None, str(e)
    if not isinstance(data, dict):
        return None, "the top level is not a JSON object"
    return data, None


def json_number(value, default=None):
    """A JSON number as a float, or *default* for anything else (bools too)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value)


def doctor_verdict(label: str, findings: list[dict], ok_parts=()) -> tuple[str, bool, str]:
    """A pre-configured mod's one Doctor row, from its findings.

    Errors and warnings fail the row and the first three are named; notices
    ride along on a passing row after *ok_parts* (e.g. "last boot: enabled").
    """
    problems = [f for f in findings if f["severity"] in ("error", "warning")]
    if not problems:
        notices = [f["title"] for f in findings if f["severity"] == "notice"]
        return label, True, " · ".join(["Setup OK", *ok_parts, *notices])
    shown = [f"{f['title']} — {f['detail']}" for f in problems[:3]]
    if len(problems) > 3:
        shown.append(f"+{len(problems) - 3} more")
    return label, False, "; ".join(shown)
