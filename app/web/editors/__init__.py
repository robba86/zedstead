"""Config-editor registry.

Each `EditorSpec` wraps one config file with a uniform load/save contract so
the frontend needs only 3 generic renderers (form/table/text) instead of one
bespoke page per file. Submodules call `register()` at import time; this
package imports them all below so the registry is fully populated as soon as
`app.web.editors` is imported.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from app.shared.config import DayzConfig
from app.web.editors._raw import RawTarget


class SaveElsewhere(ValueError):
    """A save refused because the change belongs on another page.

    `go` names an entry of webui/src/crossLinks.js's `LINKS`; the editor asks
    whether to open it. The route answers 400 with `go` beside `detail`.
    """

    def __init__(self, message: str, go: str) -> None:
        super().__init__(message)
        self.go = go


@dataclass
class EditorSpec:
    id: str
    title: str
    group: str
    kind: str  # "form" | "table" | "text"
    exists: Callable[[DayzConfig], bool]
    load: Callable[[DayzConfig], dict]
    save: Callable[[DayzConfig, dict], None]
    restore: Callable[[DayzConfig], bool] | None = None
    # True when save() mutates DayzConfig fields (config.json), instead of or
    # as well as writing server files (BattlEye syncs rcon_password) — the
    # router must then run it through state.mutate_config so the
    # save/broadcast invariant holds. Such a save that also writes files
    # returns those writes as a function instead of doing them; mutate_config
    # runs it after the path-lock check, so a refused save writes nothing.
    mutates_app_config: bool = False
    # Resolves the file(s) editable via the generic Raw tab. None => no Raw
    # tab (config.json-backed editors, or editors with no single file).
    raw_targets: Callable[[DayzConfig], list[RawTarget]] | None = None
    # A list editor whose entries are names: reads the names a version of its
    # file lists. Set, it gets a server-side "Recently removed" list built from
    # the versions every save keeps (GET /api/configs/{id}/removed).
    entry_names: Callable[[Path], list[str]] | None = None


_REGISTRY: dict[str, EditorSpec] = {}


def register(spec: EditorSpec) -> None:
    if spec.id in _REGISTRY:
        raise ValueError(f"Duplicate editor id: {spec.id!r}")
    _REGISTRY[spec.id] = spec


def list_editors() -> list[EditorSpec]:
    return list(_REGISTRY.values())


def get_editor(editor_id: str) -> EditorSpec | None:
    return _REGISTRY.get(editor_id)


# Import submodules for their register() side effects.
from app.web.editors import dynamic_ai  # noqa: E402,F401
from app.web.editors import expansion  # noqa: E402,F401
from app.web.editors import expansion_ai  # noqa: E402,F401
from app.web.editors import mission_forms  # noqa: E402,F401
from app.web.editors import mission_tables  # noqa: E402,F401
from app.web.editors import playerspawnpoints  # noqa: E402,F401
from app.web.editors import radzone  # noqa: E402,F401
from app.web.editors import server_forms  # noqa: E402,F401
from app.web.editors import zsm_forms  # noqa: E402,F401  (after server_forms: list order)
from app.web.editors import text_fallback  # noqa: E402,F401
from app.web.editors import toxiczone  # noqa: E402,F401
from app.web.editors import traderplus  # noqa: E402,F401
from app.web.editors import underground  # noqa: E402,F401
