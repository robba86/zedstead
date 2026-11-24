"""Every map-editor broadcast has to reach an open map.

A state class broadcasts a bare type (`hazards_changed`); `to_web_message`
namespaces it through `_WEB_MESSAGE_TYPES` and **drops** any type missing from
that table; `MapEditorProvider.jsx` reloads a resource per namespaced type. A
gap anywhere is silent: the write lands on disk, the map that made it moved its
own shape, and every other open map (or the same one, after an edit elsewhere)
keeps showing the old data. The Hazard zones layer shipped with exactly that
gap (plans/48 C), and it was found only by restoring a file under an open map.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.spawn_editor.state import _WEB_MESSAGE_TYPES

ROOT = Path(__file__).resolve().parents[1]
SPAWN_EDITOR = ROOT / "app" / "spawn_editor"
PROVIDER = ROOT / "webui" / "src" / "pages" / "mapeditor" / "state" / "MapEditorProvider.jsx"

_SENT = (
    re.compile(r'broadcast_type\s*=\s*"([a-z_]+)"'),
    re.compile(r'broadcast\(\s*\{\s*"type":\s*"([a-z_]+)"'),
)


def _sent_types() -> set[str]:
    found: set[str] = set()
    for path in SPAWN_EDITOR.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for pattern in _SENT:
            found.update(pattern.findall(text))
    return found


def test_the_scan_finds_the_broadcasts():
    """If this drops, the patterns stopped matching and the tests below pass vacuously."""
    sent = _sent_types()
    assert {"full_reload", "events_changed", "hazards_changed", "underground_triggers_changed"} <= sent
    assert len(sent) >= 14


def test_every_broadcast_type_survives_to_the_web_socket():
    missing = sorted(_sent_types() - set(_WEB_MESSAGE_TYPES))
    assert not missing, f"to_web_message drops these; add them to _WEB_MESSAGE_TYPES: {missing}"


def test_every_web_type_reloads_something_in_the_map_editor():
    provider = PROVIDER.read_text(encoding="utf-8")
    missing = sorted(t for t in _WEB_MESSAGE_TYPES.values() if f"{t}:" not in provider)
    assert not missing, f"MapEditorProvider's reloaders have no entry for: {missing}"
