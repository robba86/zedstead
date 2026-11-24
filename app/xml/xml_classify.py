"""Content-based classification of mod XML files + map-variant filename filter.

Mods ship their central-economy fragments under wildly inconsistent filenames
and root tags: the same *kind* of file turns up as ``events.xml``,
``MyMod_Events.xml`` or ``cfgeventspawns_Chernarus.xml``, wrapped in
``<events>``, ``<eventposdef>`` or nothing at all. Classifying by filename
substring alone (the original approach) silently mis-buckets files and, worse,
silently merges nothing when the root tag doesn't match what the merge config
expected.

`classify_mod_xml` parses the file and decides from the actual element tree,
falling back to ``None`` when the file is a bare fragment (no single root) or
isn't a CE file at all — callers then keep their filename-based behaviour.

`file_matches_terrain` drops the *other* maps' variants of a map-specific file
so a Livonia spawn table never lands in a Chernarus mission.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from xml.etree import ElementTree as ET

from app.maps.registry import KNOWN_MAPS, terrain_from_mission  # noqa: F401  (re-exported)
from app.xml.xml_utils import load_xml

# Kinds returned by classify_mod_xml. The first six are the CE-linkable kinds
# (cfgeconomycore `<ce><file type=...>`); the rest are direct merge targets keyed
# by their ALL_MERGE_CONFIGS name.
KIND_TYPES = "types"
KIND_EVENTS = "events"
KIND_SPAWNABLETYPES = "spawnabletypes"
KIND_GLOBALS = "globals"
KIND_ECONOMY = "economy"
KIND_MESSAGES = "messages"
KIND_EVENTSPAWNS = "cfgeventspawns"
KIND_EVENTGROUPS = "cfgeventgroups"
KIND_RANDOMPRESETS = "cfgrandompresets"
KIND_LIMITSDEFINITION = "cfglimitsdefinition"
KIND_LIMITSDEFINITIONUSER = "cfglimitsdefinitionuser"

# The six values DayZ accepts in `<file type="...">`, in the order they are
# written into the generated `<ce folder="custom">` block:
#   * economy/globals first — they are engine settings, not content, and the CE
#     reads them as it initialises.
#   * types before spawnabletypes/events, which reference its classnames.
#   * messages last: per the spec it is the one type that is *always appended
#     and never overrides*, so its position carries no override meaning.
CE_TYPES: tuple[str, ...] = (
    KIND_ECONOMY,
    KIND_GLOBALS,
    KIND_TYPES,
    KIND_SPAWNABLETYPES,
    KIND_EVENTS,
    KIND_MESSAGES,
)
CE_KINDS = frozenset(CE_TYPES)
CE_TYPE_ORDER: dict[str, int] = {ce_type: i for i, ce_type in enumerate(CE_TYPES)}

# What the *merge* links off a mod dir, which is deliberately smaller than
# CE_KINDS. A mod ships example/sample mission files as documentation, and
# `iter_mod_xml_files` rglobs the whole mod dir — auto-linking a mod's
# messages.xml would broadcast its example messages to real players. It is
# still a first-class kind everywhere else (manual XML files, custom/ editors,
# the report); the admin opts in per file with "Set as manual".
AUTO_LINK_KINDS = CE_KINDS - {KIND_MESSAGES}

# `type=` is NOT the root tag for every kind: globals.xml's root is <variables>.
# Single source for both `_wrap_with_root_if_needed` (mod files) and
# `xml_manual.root_tag_for` (manual files).
CE_ROOT_TAGS: dict[str, str] = {
    KIND_TYPES: "types",
    KIND_SPAWNABLETYPES: "spawnabletypes",
    KIND_EVENTS: "events",
    KIND_GLOBALS: "variables",
    KIND_ECONOMY: "economy",
    KIND_MESSAGES: "messages",
}


def ce_root_tag(kind: str) -> str:
    """Root element a fragment of *kind* has to be wrapped in to be loadable."""
    return CE_ROOT_TAGS[kind]


# ---------------------------------------------------------------------------
# Map-variant filtering
# ---------------------------------------------------------------------------

# terrain (mission suffix, lowercase) -> filename tokens that mean "this map".
# Mods label variants with the map's *display* name, not the terrain class, so
# both spellings have to be accepted — which is exactly what `app.maps.registry`
# derives from the map catalog. Adding a map there is enough; don't re-list it
# here.
TERRAIN_FILENAME_TOKENS: dict[str, set[str]] = {
    entry.terrain: set(entry.tokens) for entry in KNOWN_MAPS
}

# Map tokens that never belong to a terrain zsm knows about — still enough to
# recognise "this file is for some other map" and skip it.
FOREIGN_MAP_TOKENS: frozenset[str] = frozenset(
    {
        "chiemsee",
        "melkart",
        "valning",
        "iztek",
        "eden",
        "alteria",
        "rostow",
        "pripyat",
        "swansisland",
        "tarkov",
        "utes",
    }
)

_ALL_MAP_TOKENS: frozenset[str] = frozenset(
    tok for toks in TERRAIN_FILENAME_TOKENS.values() for tok in toks
) | FOREIGN_MAP_TOKENS


# A map name this short is a piece of ordinary words (``utes`` in Parachutes,
# ``eden`` in Sweden), so it names a map only as a word of its own. A longer
# one is found anywhere, since mods also glue it on (``typeslivonia.xml``).
_WHOLE_WORD_TOKEN_LEN = 4


def _starts_a_word(stem: str, i: int) -> bool:
    return i == 0 or not stem[i - 1].isalpha() or (stem[i].isupper() and stem[i - 1].islower())


def _ends_a_word(stem: str, j: int) -> bool:
    return j == len(stem) or not stem[j].isalpha() or (stem[j].isupper() and stem[j - 1].islower())


def _map_tokens_in(stem: str, tokens: Iterable[str]) -> set[str]:
    """The *tokens* that *stem* names, leaving out one a longer one covers
    (``banov`` where the name says ``banovfrost``)."""
    low = stem.lower()
    spans: list[tuple[int, int, str]] = []
    for tok in tokens:
        start = low.find(tok)
        while start != -1:
            end = start + len(tok)
            if len(tok) > _WHOLE_WORD_TOKEN_LEN or (
                _starts_a_word(stem, start) and _ends_a_word(stem, end)
            ):
                spans.append((start, end, tok))
            start = low.find(tok, start + 1)
    return {
        tok
        for start, end, tok in spans
        if not any(s <= start and end <= e and e - s > end - start for s, e, _ in spans)
    }


def file_matches_terrain(filename: str, terrain: str | None) -> bool:
    """False when *filename* is a map variant for a map other than *terrain*.

    A file with no recognisable map token in its name always matches — most mod
    XMLs are map-agnostic and must not be filtered out. When a file carries
    both a matching and a foreign token (``cfgeventspawns_ChernarusLivonia``,
    contrived), the matching token wins.
    """
    if not terrain:
        return True
    stem = Path(filename).stem.replace("-", "_")
    own = TERRAIN_FILENAME_TOKENS.get(terrain, {terrain})
    found = _map_tokens_in(stem, _ALL_MAP_TOKENS | own)
    return not found or bool(found & own)


# ---------------------------------------------------------------------------
# Content classification
# ---------------------------------------------------------------------------

def _classify_events_like(root: ET.Element, filename: str) -> str:
    """Tell an economy events.xml apart from a cfgeventspawns.xml.

    Both use ``<event name="...">``; only the spawn table carries ``<pos>``
    children, and only the economy file carries ``<nominal>``/``<children>``.
    """
    events = list(root.iter("event"))
    if any(ev.find("pos") is not None for ev in events):
        return KIND_EVENTSPAWNS
    if any(
        ev.find(tag) is not None
        for ev in events
        for tag in ("nominal", "lifetime", "children", "limit")
    ):
        return KIND_EVENTS
    # Empty or unrecognised — fall back to the filename.
    return KIND_EVENTSPAWNS if "eventspawns" in filename.lower() else KIND_EVENTS


def _classify_lists_like(root: ET.Element) -> str | None:
    """``<lists>`` is shared by cfglimitsdefinition.xml and its *user* twin.

    Only the user file nests ``<user name="...">`` inside the flag sections.
    """
    for section in ("usageflags", "valueflags"):
        elem = root.find(section)
        if elem is not None and elem.find("user") is not None:
            return KIND_LIMITSDEFINITIONUSER
    if root.find("user") is not None:
        return KIND_LIMITSDEFINITIONUSER
    if any(root.find(s) is not None for s in ("categories", "tags", "usageflags", "valueflags")):
        return KIND_LIMITSDEFINITION
    return None


def _classify_globals_like(root: ET.Element) -> str | None:
    """``<variables>`` is globals.xml's root — but only with ``<var>`` children.

    A mod is free to call its own settings file's root ``<variables>``; the
    engine only reads a globals file made of ``<var name= type= value=>``.
    """
    return KIND_GLOBALS if root.find("var") is not None else None


def _classify_economy_like(root: ET.Element) -> str | None:
    """``<economy>``'s children are *system names* (``<dynamic>``, ``<animals>``,
    …), so the shape to key on is the flag attributes, not the tag.

    Keeps a trader mod's own ``<economy><item price="5"/></economy>``
    unclassified — which is the point, since ``economy`` is a legal CE type and
    mislinking one would rewrite the mission's economy systems.
    """
    return (
        KIND_ECONOMY
        if any(
            any(child.get(flag) is not None for flag in _ECONOMY_FLAGS)
            for child in root
            if isinstance(child.tag, str)
        )
        else None
    )


_ECONOMY_FLAGS = ("init", "load", "respawn", "save")

# Root tags that map straight to a kind. Deliberately NOT seeded from
# CE_ROOT_TAGS: `events` has to keep going through `_classify_events_like` (an
# `<events>` root with `<pos>` children is a cfgeventspawns table, not economy
# events), and `variables`/`economy` need their content guards above.
# `test_ce_root_tags_round_trip` is what keeps the two in sync.
_ROOT_TAG_KINDS: dict[str, str] = {
    "types": KIND_TYPES,
    "type": KIND_TYPES,
    "spawnabletypes": KIND_SPAWNABLETYPES,
    "messages": KIND_MESSAGES,
    "eventposdef": KIND_EVENTSPAWNS,
    "eventgroupdef": KIND_EVENTGROUPS,
    "randompresets": KIND_RANDOMPRESETS,
}


def classify_mod_xml(path: str | Path) -> str | None:
    """Return the CE file kind of *path*, or None if it can't be determined.

    None means "not a recognisable CE file" *or* "unparseable" (a bare fragment
    with no single root element is the common case). Callers that have to tell
    those two apart — the merge does, since only the second may fall back to
    filename heuristics — use `classify_mod_file` instead.
    """
    return classify_mod_file(path)[0]


def classify_mod_file(path: str | Path) -> tuple[str | None, str | None]:
    """``(kind, root_tag)`` for *path*; ``root_tag`` is None when it won't parse.

    The second value is what `classify_mod_xml`'s bare ``None`` collapses away,
    and the merge needs it twice over: a file with **no** root element is a bare
    fragment, the only case where guessing the kind from the filename is
    legitimate, while a file that parses fine with a root the central economy
    doesn't know is simply not a CE file — treating the second as the first is
    what registered mods' private XML as ``type="types"``. The tag itself then
    goes in the "skipped, and here is why" log line.
    """
    p = Path(path)
    try:
        root = load_xml(p).getroot()
    except (ET.ParseError, FileNotFoundError, OSError, UnicodeDecodeError):
        return None, None
    return _classify_root(root, p.name), str(root.tag)


def _classify_root(root: ET.Element, filename: str) -> str | None:
    tag = root.tag.lower()
    if tag in _ROOT_TAG_KINDS:
        return _ROOT_TAG_KINDS[tag]
    if tag in ("events", "event"):
        return _classify_events_like(root, filename)
    # `user_lists` is the root of Bohemia's own cfglimitsdefinitionuser.xml.
    if tag in ("lists", "user", "user_lists"):
        return _classify_lists_like(root)
    if tag == "variables":
        return _classify_globals_like(root)
    if tag == "economy":
        return _classify_economy_like(root)
    return None


# Child tag -> the kind a *rootless* fragment of those elements belongs to.
# Only consulted when the text has no single root element to key on, which is
# exactly what a user pastes into the manual-link form ("<type>…</type>" copied
# out of a mod's types.xml).
_CHILD_TAG_KINDS: dict[str, str] = {
    "type": KIND_TYPES,       # ambiguous with spawnabletypes — resolved below
    "event": KIND_EVENTS,     # ambiguous with cfgeventspawns — resolved below
    "group": KIND_EVENTGROUPS,
    "cargo": KIND_RANDOMPRESETS,
    "attachments": KIND_RANDOMPRESETS,
    "pos": KIND_EVENTSPAWNS,
    "var": KIND_GLOBALS,
    "message": KIND_MESSAGES,
    "usageflags": KIND_LIMITSDEFINITION,
    "valueflags": KIND_LIMITSDEFINITION,
    "categories": KIND_LIMITSDEFINITION,
    "tags": KIND_LIMITSDEFINITION,
    "user": KIND_LIMITSDEFINITIONUSER,
}


def classify_xml_text(content: str, filename: str = "") -> str | None:
    """Classify a *string* of XML — the paste box's counterpart to
    `classify_mod_xml`.

    Accepts a bare fragment (several sibling elements, no single root), which is
    the normal shape of hand-copied XML and the shape `_wrapped_copy` /
    `_wrap_with_root_if_needed` already handle at merge time. A fragment is
    wrapped and classified from its children instead of its root; `<type>` and
    `<event>` children stay ambiguous on their own, so they go through the same
    content checks a rooted file does.
    """
    from defusedxml.ElementTree import fromstring as safe_fromstring

    from app.xml.xml_text import strip_declaration

    # A declaration is legal only at the very start, so not inside the wrapper.
    text = strip_declaration(content).strip()
    if not text:
        return None

    rooted: str | None = None
    try:
        root = safe_fromstring(text)
    except Exception:
        pass
    else:
        # A *single* pasted element parses as a rooted document even though it
        # is really a fragment (`<type name="X"/>`), and its root tag is then a
        # child tag — which loses the type/spawnabletypes and
        # limitsdefinition/user distinctions. Only trust the rooted answer when
        # the root is a genuine file root.
        rooted = _classify_root(root, filename)
        if rooted is not None and root.tag.lower() not in _CHILD_TAG_KINDS:
            return rooted

    try:
        wrapper = safe_fromstring(f"<fragment>{text}</fragment>")
    except Exception:
        return rooted

    tags = [child.tag.lower() for child in wrapper if isinstance(child.tag, str)]
    if not tags:
        return rooted
    if "event" in tags:
        # <pos> means spawn positions, <nominal>/<lifetime> means economy — the
        # same test a rooted <events> file gets.
        return _classify_events_like(wrapper, filename)
    if "type" in tags:
        # A spawnabletypes <type> carries loadout children; an economy type
        # carries counts. Nothing else tells them apart.
        if any(
            child.find(tag) is not None
            for child in wrapper
            for tag in ("cargo", "attachments", "hoarder")
        ):
            return KIND_SPAWNABLETYPES
        return KIND_TYPES
    if any(tag in ("usageflags", "valueflags", "categories", "tags", "user") for tag in tags):
        return _classify_lists_like(wrapper)
    # Economy systems are named by their *tag* (<dynamic>, <animals>, a mod's
    # own system), so no tag map can reach them — the flag attributes are the
    # only shape. Checked before the generic map so a system that happens to be
    # named after a mapped tag can't be misread.
    economy = _classify_economy_like(wrapper)
    if economy is not None:
        return economy
    for tag in tags:
        if tag in _CHILD_TAG_KINDS:
            return _CHILD_TAG_KINDS[tag]
    return rooted
