"""GET /api/search-index — what the Ctrl+K palette searches (plans/75 P1).

Built from text zsm already has, never from the live files: the settings
registry, the editor registry, each editor's own field help and the static
field lists behind its form, the map editor's guide and the Mods page's tab
hints. So the index is the same on every install and is built once per
process; only the role filter runs per request.

Every entry is ``{kind, title, sub, text, to, min_role}``. ``to`` is a panel
path without the instance prefix (the router's basename adds it). The
frontend adds its own static sources (pages, tabs, cross-links, guide steps;
``webui/src/searchSources.js``) in the same shape.

A field link opens the editor's Edit tab with ``?q=<field key>``: FormEditor
filters its fields by label, key and help, so the key finds its own field. A
table's ``?q=`` filters rows, not columns, so a column links to the editor
without one.
"""

from __future__ import annotations

import functools
import re
from collections.abc import Callable, Iterable
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, Request

from app.web.deps import require_session
from app.web.editors._help import strip_markup
from app.web.users import role_at_least

router = APIRouter(prefix="/api", tags=["search"], dependencies=[Depends(require_session)])

# The roles the routes in webui/src/routes.js give each target. The API
# refuses regardless; this only keeps a viewer's palette free of pages they
# would be refused.
_CONFIGS_ROLE = "viewer"  # /configs/:id
_SETTINGS_ROLE = "admin"  # /settings
_MAP_EDITOR_ROLE = "admin"  # /map-editor
_MODS_ROLE = "admin"  # /mods


# The Mods page's Install subtabs (webui/src/pageTabs.js MODS_INSTALL_SUBTABS);
# every other key in GET /api/mods/help's `tabs` is a top-level tab. A test
# keeps the two in step.
MODS_INSTALL_SUBTABS = ("Supported", "By Name", "By ID", "Workshop", "Search", "Expansion")


def _entry(kind: str, title: str, sub: str, text: str, to: str, min_role: str) -> dict:
    return {
        "kind": kind,
        "title": title,
        "sub": sub,
        "text": strip_markup(text or ""),
        "to": to,
        "min_role": min_role,
    }


def editor_path(editor_id: str, *, tab: str | None = None, q: str | None = None) -> str:
    """``/configs/<id>`` with optional ``tab``/``q`` — the backend twin of
    crossLinks.js's ``editorLink``."""
    path = f"/configs/{quote(editor_id, safe='')}"
    params = {k: v for k, v in (("tab", tab), ("q", q)) if v}
    return f"{path}?{urlencode(params)}" if params else path


# ── Field sources, one per editor ─────────────────────────────────────────
#
# Each returns (key, label, help) triples. A form's triples become links that
# filter to the field; a table's become links to the table.

Field = tuple[str, str, str]


def _from_map(desc: dict[str, str]) -> Callable[[], list[Field]]:
    return lambda: [(k, k, v) for k, v in desc.items()]


def _from_maps(*maps: dict[str, str]) -> Callable[[], list[Field]]:
    def fields() -> list[Field]:
        seen: dict[str, Field] = {}
        for m in maps:
            for k, v in m.items():
                seen.setdefault(k, (k, k, v))
        return list(seen.values())

    return fields


def _serverdz() -> list[Field]:
    from app.commands.config_cmd import _CFG_CATEGORIES
    from app.parsers.servercfg import FIELD_DESCRIPTIONS

    return [
        (key, label or key, FIELD_DESCRIPTIONS.get(key, ""))
        for items in _CFG_CATEGORIES.values()
        for label, key, _kind in items
    ]


def _battleye() -> list[Field]:
    from app.web.editors.server_forms import _BATTLEYE_FIELDS, _BATTLEYE_HELP

    return [(key, label, _BATTLEYE_HELP.get(key, "")) for label, key, _kind in _BATTLEYE_FIELDS] + _zsm_settings(
        "battleye"
    )()


def _zsm_settings(editor_id: str) -> Callable[[], list[Field]]:
    """The registry settings a Configs editor shows (editors/zsm_forms.py)."""

    def fields() -> list[Field]:
        from app.web.editors.zsm_forms import editor_settings

        return [(s.key, s.key, s.description) for s in editor_settings(editor_id)]

    return fields


def _runcommand() -> list[Field]:
    from app.web.editors.server_forms import _RUNCOMMAND_HELP, _RUNCOMMAND_SECTIONS

    return [
        (key, label, _RUNCOMMAND_HELP.get(key, ""))
        for _title, items in _RUNCOMMAND_SECTIONS
        for label, key, _kind in items
    ]


def _coresettings() -> list[Field]:
    from app.web.editors.server_forms import _CORE_DESC, _CORESETTINGS_SECTIONS

    # The form's key is `<prefix>--<attr>`; the attribute alone is what a
    # person searches for, and it is a substring of the key.
    return [
        (attr, attr, _CORE_DESC.get(attr, ""))
        for _title, _prefix, attrs in _CORESETTINGS_SECTIONS
        for attr in attrs
    ]


def _weather() -> list[Field]:
    from app.xml.weather_xml import PARAM_DESCRIPTIONS, STATUS_DESCRIPTIONS

    return [
        ("enabled", "Weather system enabled", STATUS_DESCRIPTIONS.get("sw-enabled", "")),
        ("reset", "Reset each restart", STATUS_DESCRIPTIONS.get("sw-reset", "")),
        *((k, k, v) for k, v in PARAM_DESCRIPTIONS.items()),
    ]


def _mod_form(module: str, attr: str) -> Callable[[], list[Field]]:
    def fields() -> list[Field]:
        import importlib

        form = getattr(importlib.import_module(module), attr)
        return [(f.key, f.label, f.help) for _title, group in form.sections for f in group]

    return fields


def _mod_table(module: str, attr: str) -> Callable[[], list[Field]]:
    def fields() -> list[Field]:
        import importlib

        table = getattr(importlib.import_module(module), attr)
        return [(f.key, f.label, f.help) for f in table.columns]

    return fields


def _dynai_table(table_id: str) -> Callable[[], list[Field]]:
    def fields() -> list[Field]:
        from app.web.editors.dynamic_ai import TABLES

        table = next(t for t in TABLES if t.id == table_id)
        return [(f.key, f.label, f.help) for f in table.columns]

    return fields


def _lazy_map(module: str, attr: str) -> Callable[[], list[Field]]:
    def fields() -> list[Field]:
        import importlib

        return _from_map(getattr(importlib.import_module(module), attr))()

    return fields


def _lazy_maps(*refs: tuple[str, str]) -> Callable[[], list[Field]]:
    def fields() -> list[Field]:
        import importlib

        return _from_maps(*(getattr(importlib.import_module(m), a) for m, a in refs))()

    return fields


def _lazy_fn(module: str, attr: str) -> Callable[[], list[Field]]:
    def fields() -> list[Field]:
        import importlib

        return getattr(importlib.import_module(module), attr)()

    return fields


# Every registered editor has an entry (tests/test_web_search.py). None means
# the editor has no static field list (its fields come from the file, or it is
# a text editor); its editor entry is still in the index.
FIELD_SOURCES: dict[str, Callable[[], list[Field]] | None] = {
    # Server
    "serverdz": _serverdz,
    "battleye": _battleye,
    "runcommand": _runcommand,
    "updates": _zsm_settings("updates"),
    "crash-recovery": _zsm_settings("crash-recovery"),
    "restart-warnings": _zsm_settings("restart-warnings"),
    "coresettings": _coresettings,
    # Mission forms
    "globals": _lazy_map("app.xml.globals_xml", "VAR_DESCRIPTIONS"),
    "weather": _weather,
    "gameplay": _lazy_map("app.parsers.cfggameplay", "FIELD_DESCRIPTIONS"),
    # Mission tables
    "events": _lazy_maps(
        ("app.xml.events_xml", "FIELD_DESCRIPTIONS"), ("app.web.editors.ce_tables", "EVENTS_COL_HELP")
    ),
    "spawnabletypes": _lazy_maps(
        ("app.xml.spawnabletypes_xml", "FIELD_DESCRIPTIONS"),
        ("app.web.editors.ce_tables", "SPAWNABLE_COL_HELP"),
    ),
    "economy": _lazy_maps(
        ("app.xml.economy_xml", "FLAG_DESCRIPTIONS"), ("app.web.editors.ce_tables", "ECONOMY_COL_HELP")
    ),
    "messages": _lazy_map("app.web.editors.ce_tables", "MESSAGES_COL_HELP"),
    "eventspawns": _lazy_map("app.web.editors.mission_tables", "_EVENTSPAWNS_COL_HELP"),
    "eventgroups": _lazy_map("app.web.editors.mission_tables", "_EVENTGROUPS_COL_HELP"),
    "ignorelist": _lazy_map("app.web.editors.mission_tables", "_IGNORELIST_COL_HELP"),
    "limitsdefinition": _lazy_map("app.xml.limitsdefinition_xml", "FIELD_DESCRIPTIONS"),
    "limitsdefinitionuser": _lazy_map("app.web.editors.mission_tables", "_LIMITSUSER_COL_HELP"),
    "randompresets": _lazy_map("app.xml.randompresets_xml", "FIELD_DESCRIPTIONS"),
    "effectarea": _lazy_map("app.parsers.effectarea_json", "FIELD_DESCRIPTIONS"),
    "economycore": None,
    "environment": None,
    "playerspawnpoints": _lazy_fn("app.web.editors.playerspawnpoints", "search_fields"),
    "undergroundtriggers": None,
    # TraderPlus
    "tp-banking": _lazy_map("app.traderplus.traderplus_banking_json", "FIELD_DESCRIPTIONS"),
    "tp-general": _lazy_map("app.traderplus.traderplus_general_json", "FIELD_DESCRIPTIONS"),
    "tp-safezone": _lazy_map("app.traderplus.traderplus_safezone_json", "FIELD_DESCRIPTIONS"),
    "tp-garage": _lazy_map("app.traderplus.traderplus_garage_json", "FIELD_DESCRIPTIONS"),
    "tp-vehicles": _lazy_map("app.traderplus.traderplus_vehicles_json", "FIELD_DESCRIPTIONS"),
    "tp-insurance": _lazy_map("app.traderplus.traderplus_insurance_json", "FIELD_DESCRIPTIONS"),
    "tp-ids": _lazy_map("app.web.editors.traderplus", "_IDS_COL_HELP"),
    "tp-beans": _mod_form("app.web.editors.traderplus", "BEANS_FORM"),
    # Dynamic AI, RadZone, ToxicZone, Expansion AI (editors/_mod_json.py)
    "dynai-settings": _mod_form("app.web.editors.dynamic_ai", "FORM"),
    "dynai-groups": _dynai_table("dynai-groups"),
    "dynai-points": _dynai_table("dynai-points"),
    "dynai-locations": _dynai_table("dynai-locations"),
    "dynai-audio": _dynai_table("dynai-audio"),
    "dynai-notifications": _dynai_table("dynai-notifications"),
    "radzone-settings": _mod_form("app.web.editors.radzone", "RADIATION_FORM"),
    "radzone-crates": _mod_form("app.web.editors.radzone", "CRATES_FORM"),
    "radzone-zones": _mod_table("app.web.editors.radzone", "ZONES"),
    "radzone-crate-areas": _mod_table("app.web.editors.radzone", "CRATE_AREAS"),
    "radzone-clothes": _mod_table("app.web.editors.radzone", "CLOTHES"),
    "toxiczone-settings": _mod_form("app.web.editors.toxiczone", "GENERAL_FORM"),
    "toxiczone-static": _mod_form("app.web.editors.toxiczone", "STATIC_FORM"),
    "toxiczone-moving": _mod_form("app.web.editors.toxiczone", "MOVING_FORM"),
    "toxiczone-loot": _mod_form("app.web.editors.toxiczone", "LOOT_FORM"),
    "toxiczone-protection": _mod_form("app.web.editors.toxiczone", "PROTECTION_FORM"),
    "toxiczone-static-zones": _mod_table("app.web.editors.toxiczone", "STATIC_ZONES"),
    "toxiczone-moving-zones": _mod_table("app.web.editors.toxiczone", "MOVING_ZONES"),
    "toxiczone-clothes": _mod_table("app.web.editors.toxiczone", "CLOTHES"),
    "expansion-ai-patrol-list": _mod_table("app.web.editors.expansion_ai", "PATROLS"),
}

# The 30 Expansion editors (editors/expansion.py) build their form from the
# file's own JSON, so none has a static field list.
_NO_STATIC_FIELDS_PREFIX = "expansion-"

# The bespoke pages under /configs that carry a field help map of their own.
# Their ?q= filters rows (a classname), so a field links to the page itself.
BESPOKE_FIELD_SOURCES: dict[str, tuple[str, Callable[[], list[Field]]]] = {
    "/configs/types": ("Types.xml", _lazy_map("app.xml.types_xml", "FIELD_DESCRIPTIONS")),
    "/configs/tp-price": (
        "TraderPlus Prices",
        _lazy_map("app.traderplus.traderplus_json", "FIELD_DESCRIPTIONS"),
    ),
}


def field_source(editor_id: str) -> Callable[[], list[Field]] | None:
    """The field source for *editor_id*; KeyError for an editor nobody listed."""
    if editor_id in FIELD_SOURCES:
        return FIELD_SOURCES[editor_id]
    if editor_id.startswith(_NO_STATIC_FIELDS_PREFIX):
        return None
    raise KeyError(editor_id)


# ── Sources ───────────────────────────────────────────────────────────────


def settings_entries() -> list[dict]:
    from app.shared import settings_registry

    return [
        _entry(
            "setting",
            s.key,
            f"Settings · {s.section}",
            s.description,
            f"/settings?{urlencode({'q': s.key})}",
            _SETTINGS_ROLE,
        )
        for s in settings_registry._REGISTRY
        if not s.editor  # its Configs editor lists it (FIELD_SOURCES)
    ]


def editor_entries() -> list[dict]:
    from app.web.editors import list_editors

    out = []
    for spec in list_editors():
        out.append(
            _entry("editor", spec.title, f"Config file · {spec.group}", "", editor_path(spec.id), _CONFIGS_ROLE)
        )
        source = field_source(spec.id)
        if source is None:
            continue
        form = spec.kind == "form"
        for key, label, help_text in source():
            to = editor_path(spec.id, q=key) if form else editor_path(spec.id)
            sub = f"{spec.title} · {key}" if label != key else spec.title
            out.append(_entry("field", label, sub, help_text, to, _CONFIGS_ROLE))
    for path, (title, source) in BESPOKE_FIELD_SOURCES.items():
        for key, label, help_text in source():
            out.append(_entry("field", label, title, help_text, path, _CONFIGS_ROLE))
    return out


_KBD = re.compile(r"\[\[([^\]]+)\]\]")


def _plain(text: str) -> str:
    """The guide's inline Markdown subset (help_content.py) as plain text."""
    return _KBD.sub(r"\1", str(text)).replace("**", "").replace("`", "")


def _help_block_text(block: dict) -> str:
    kind = block.get("type")
    if kind == "paragraph":
        return _plain(block.get("text", ""))
    if kind == "bullets":
        return "\n".join(_plain(b) for b in block.get("items", []))
    if kind == "definitions":
        return "\n".join(f"{_plain(term)}: {_plain(text)}" for term, text in block.get("items", []))
    return ""


def map_editor_entries() -> list[dict]:
    from app.spawn_editor.help_content import load_raw

    data = load_raw()
    return [
        _entry(
            "help",
            section.get("title", ""),
            "Map Editor guide",
            "\n".join(_help_block_text(b) for b in section.get("blocks", [])),
            "/map-editor",
            _MAP_EDITOR_ROLE,
        )
        for section in data.get("sections", [])
    ]


def mods_tab_path(tab: str) -> str:
    if tab in MODS_INSTALL_SUBTABS:
        return f"/mods?{urlencode({'tab': 'Install', 'subtab': tab})}"
    return f"/mods?{urlencode({'tab': tab})}"


def mods_entries() -> list[dict]:
    from app.web.routers.mods import mods_help_payload

    return [
        _entry("help", tab, "Mods", text, mods_tab_path(tab), _MODS_ROLE)
        for tab, text in mods_help_payload()["tabs"].items()
        if text
    ]


SOURCES: dict[str, Callable[[], Iterable[dict]]] = {
    "settings": settings_entries,
    "editors": editor_entries,
    "map_editor": map_editor_entries,
    "mods": mods_entries,
}


@functools.cache
def build_index() -> tuple[dict, ...]:
    """Every entry, for every role. Static, so built once per process."""
    return tuple(entry for source in SOURCES.values() for entry in source())


def index_for(role: str) -> list[dict]:
    return [e for e in build_index() if role_at_least(role, e["min_role"])]


@router.get("/search-index")
async def search_index(request: Request) -> list[dict]:
    """The palette's server-side half, filtered to what the caller's role may open."""
    return index_for(getattr(request.state, "role", "") or "")
