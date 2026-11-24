"""Loader for the shared Visual Map Editor guide.

Source: ``app/data/map_editor_help.json``, served over HTTP by the editor's
``GET /help`` route. The browser's ``?`` dialog (``HelpDialog.jsx``) renders it,
and the Ctrl+K search index (``app/web/routers/search.py``) reads it as plain
text.

Its text uses a tiny Markdown subset: ``**bold**``, ``*italic*``, `` `code` ``
and ``[[Key]]`` (a key cap).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_JSON_PATH = Path(__file__).parent.parent / "data" / "map_editor_help.json"


@lru_cache(maxsize=1)
def load_raw() -> dict:
    """Load and cache the raw guide JSON."""
    return json.loads(_JSON_PATH.read_text(encoding="utf-8"))
