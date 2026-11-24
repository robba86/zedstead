"""Field-help sanitizer for the web editors.

The help text in the source maps carries Rich console markup — ``[bold]``,
``[dim]``, and in a few strings ``[@click=...]...[/]`` — and embedded newlines.
The browser renders plain text, so strip the markup here at load time (the
source maps in ``app/xml``/``app/parsers``/``app/traderplus`` keep their markup
untouched). Newlines are preserved; the frontend `<Tooltip>` renders
them with ``white-space: pre-line``.
"""

from __future__ import annotations

import re

# [bold] [/bold] [dim] [bold underline] [@click=screen.open_run_command] [/]
_TAG = re.compile(r"\[/?[^\]]*\]")
_HSPACE = re.compile(r"[ \t]{2,}")


def strip_markup(text: str) -> str:
    """Return `text` with Rich console markup removed and whitespace tidied."""
    s = _TAG.sub("", text or "")
    s = _HSPACE.sub(" ", s)
    s = "\n".join(line.strip() for line in s.split("\n"))
    return s.strip()


def described_choices(values, meanings: dict[str, str]) -> list[dict]:
    """*values* as picker options, each with its meaning as `description`
    (the line under it in the open list, webui `Select`)."""
    return [{"value": v, "description": meanings[v]} if v in meanings else {"value": v} for v in values]


def field_help(desc_map: dict[str, str], key: str) -> str:
    """Look up `key` in `desc_map` and return its sanitized help (``""`` if absent).

    Falls back to the segment after ``:`` and the last ``.``-delimited segment so
    prefixed/dotted web keys (e.g. ``state:foo``, ``a.b.leaf``) still resolve.
    """
    for candidate in (key, key.split(":")[-1], key.rsplit(".", 1)[-1]):
        text = strip_markup(desc_map.get(candidate, ""))
        if text:
            return text
    return ""


def unit_attr(units: dict[str, str], key: str) -> dict[str, str]:
    """``{"unit": ...}`` for a field whose number has a unit in *units*, else
    ``{}``, to spread into the field dict. The web form shows it in the box
    (webui UnitInput.jsx)."""
    unit = units.get(key)
    return {"unit": unit} if unit else {}


# A unit a label writes in brackets at its end ("Change time (s)"), and the
# word the form's box shows for it (webui UnitInput.jsx).
_LABEL_UNITS = {
    "s": "seconds", "min": "minutes", "hours": "hours", "m": "meters", "m/s": "m/s",
    "rad": "rad", "degrees": "deg", "deg": "deg",
}


def unit_from_label(label: str) -> tuple[str, dict[str, str]]:
    """*label* without its trailing unit, and ``{"unit": ...}`` to spread into
    the field, so the box shows "seconds" where the label said "(s)". A
    bracket that is no unit, such as a range "(0-1)", stays in the label and
    gives ``{}``. The CLI keeps the full label, since it draws no box."""
    match = re.fullmatch(r"(.*\S)\s*\(([^()]+)\)", label)
    if match and match.group(2) in _LABEL_UNITS:
        return match.group(1), {"unit": _LABEL_UNITS[match.group(2)]}
    return label, {}


def label_without_unit(label: str, unit: str | None) -> str:
    """*label* without a trailing "(<unit>)", for a field whose box already
    shows *unit*: "Interval (seconds)" beside a "seconds" box says it twice.
    The CLI keeps the full label, since it draws no box."""
    suffix = f"({unit})" if unit else ""
    if suffix and label.lower().endswith(suffix.lower()):
        return label[: -len(suffix)].rstrip() or label
    return label


def inject_help(payload: dict, desc_map: dict[str, str]) -> dict:
    """Set `help` on every form field / table column in `payload` that lacks one,
    looking the key up in `desc_map`. Mutates and returns `payload`."""
    if payload.get("kind") == "form":
        for section in payload.get("sections", []):
            for f in section.get("fields", []):
                if not f.get("help"):
                    text = field_help(desc_map, f["key"])
                    if text:
                        f["help"] = text
    elif payload.get("kind") == "table":
        for col in payload.get("columns", []):
            if not col.get("help"):
                text = field_help(desc_map, col["key"])
                if text:
                    col["help"] = text
    return payload
