"""No new row, item, count or badge class family in the web UI's CSS.

Plan 89 gave the panel one design system (`webui/DESIGN.md`): a list row is
`ListRow` (`.list-row`), a count is `CountBadge` (`.count-badge`), and so on.
Before it, every Map Editor tab drew its own `.me-*-row` and `.me-*-count`,
and each looked a little different. This check keeps that from growing back.

It reads every CSS file under `webui/src/` and collects the class names that
end in `-row`, `-item`, `-count` or `-badge`. A name outside the lists below
fails. The fix is to use the shared part. If a class really is a new part
kind, add it to `DESIGN.md` and to `PARTS` in the same change.

`LEGACY` is the set that existed when the check was written (2026-11-11).
Remove a name from it when its last rule goes; the test asks for that too, so
the list only shrinks.
"""

from __future__ import annotations

import re
from pathlib import Path

WEBUI = Path(__file__).resolve().parent.parent / "webui" / "src"

# The shared parts (webui/DESIGN.md).
PARTS = frozenset({"list-row", "count-badge", "menu-item"})

# Classes from before plan 89. Never add to this list.
LEGACY = frozenset(
    {
        "bar-chart-row",
        "beta-badge",
        "branch-badge",
        "breadcrumb-item",
        "command-palette-input-row",
        "command-palette-item",
        "dash-add-item",
        "dash-item",
        "form-row",
        "instance-badge",
        "instance-menu-item",
        "json-count",
        "kv-row",
        "list-field-item",
        "load-order-item",
        "login-badge",
        "map-badge",
        "map-switch-row",
        "me-add-mode-count",
        "me-detail-field-row",
        "me-loot-filter-row",
        "me-mod-item-row",
        "me-object-row",
        "msg-repeat-row",
        "nav-edit-row",
        "popup-row",
        "spawn-test-count",
        "table-nested-row",
        "timeline-item",
        "tip-item",
        "trash-tray-item",
    }
)

_FAMILY = re.compile(r"\.([a-zA-Z][\w-]*-(?:row|item|count|badge))(?![\w-])")


def _css_classes() -> dict[str, list[str]]:
    """Each matching class name with the CSS files that declare it."""
    found: dict[str, list[str]] = {}
    for path in sorted(WEBUI.rglob("*.css")):
        for name in set(_FAMILY.findall(path.read_text(encoding="utf-8"))):
            found.setdefault(name, []).append(str(path.relative_to(WEBUI)))
    return found


def test_no_new_row_item_count_or_badge_class():
    new = {
        name: files
        for name, files in _css_classes().items()
        if name not in PARTS and name not in LEGACY
    }
    assert not new, (
        "New class family in the web UI CSS. Use the shared part from "
        f"webui/DESIGN.md (ListRow, CountBadge, RowTag, .status-pill): {new}"
    )


def test_legacy_list_only_shrinks():
    gone = sorted(LEGACY - set(_css_classes()))
    assert not gone, f"These classes have no rule left; remove them from LEGACY: {gone}"


def test_every_component_is_on_the_design_page():
    """The Components tab of /design (`pages/DesignComponents.jsx`) draws every
    generic component, or names it in `FEATURES` when it needs live data. A new
    file in `components/` has to pick one, so the page stays the full list."""
    page = (WEBUI / "pages" / "DesignComponents.jsx").read_text(encoding="utf-8")
    drawn = set(re.findall(r"from '\.\./components/([\w]+)\.jsx'", page))
    features = page[page.index("export const FEATURES = {") :]
    features = features[: features.index("\n}")]
    listed = set(re.findall(r"'([A-Z]\w+)'", features))
    # The Parts tab (pages/DesignPage.jsx) draws the parts from DESIGN.md.
    parts = set(re.findall(r"from '\.\./components/([\w]+)\.jsx'", (WEBUI / "pages" / "DesignPage.jsx").read_text(encoding="utf-8")))
    files = {p.stem for p in (WEBUI / "components").glob("*.jsx")}
    missing = sorted(files - drawn - listed - parts)
    assert not missing, f"Draw these on /design or name them in FEATURES: {missing}"
    gone = sorted((drawn | listed) - files)
    assert not gone, f"No such component any more: {gone}"
