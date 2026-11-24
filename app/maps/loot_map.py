"""The loot layer: which buildings an item can spawn in (plans/82).

Joins three of the mission's own files: every ``<type>`` the economy loads
(``db/types.xml``, then each live ``<file type="types">`` registered in
cfgeconomycore.xml, the last definition of a classname winning), the building
prototypes in ``mapgroupproto.xml`` (which usages a building has, and what each
of its containers takes) and the building positions in ``mapgrouppos.xml``
(`app.maps.building_map`).

Bohemia does not document the loot rules; `container_matches` is the one place
the rule lives, and it says which parts are unverified. What cannot be computed
at all is the value tier: tiers come from the value areas painted into
``areaflags.map``, an undocumented binary file nothing in Zedstead reads. So the
answer is an upper bound, "buildings where this can spawn by usage and
container; its value tiers are not checked", and every surface says so.

Left out on purpose: attachments and cargo (cfgspawnabletypes.xml,
cfgrandompresets.xml), dynamic event loot (``deloot``), the
``mapgroupcluster*.xml`` positions (wrecks and trees), and items the CE never
places (``nominal`` 0, ``crafted``).

Every file here is a third party's (a map's mission, a mod's types), so each
is read within a size limit and through `xml_utils.load_xml` (defusedxml), and
past a limit it reads as unreadable with a reason, never an exception.
"""

from __future__ import annotations

import logging
import threading
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from app.maps.building_map import (
    MAX_MAPGROUPPOS_BYTES,
    building_groups,
    mapgrouppos_path,
    mission_file,
)
from app.ops.mission_baseline import MISSION, owner_label, registered_sources

log = logging.getLogger(__name__)

MAX_PROTO_BYTES = 64 * 1024 * 1024
MAX_TYPES_FILE_BYTES = 32 * 1024 * 1024
MAX_GROUPS = 50_000
MAX_CONTAINERS_PER_GROUP = 256
MAX_FLAGS_PER_ELEMENT = 64

#: At most this many building kinds in a `match` answer.
MAX_GROUPS_SHOWN = 100

_TYPES = "db/types.xml"
_PROTO = "mapgroupproto.xml"


class LootFileError(ValueError):
    """A file the loot layer cannot use; the message is the reason."""


@dataclass(frozen=True)
class Container:
    name: str
    categories: frozenset[str]
    tags: frozenset[str]
    values: frozenset[str]
    points: int


@dataclass(frozen=True)
class Group:
    name: str
    usages: frozenset[str]
    values: frozenset[str]
    containers: tuple[Container, ...]


@dataclass(frozen=True)
class TypeInfo:
    name: str
    owner: str
    nominal: int
    category: str
    tag: str
    usages: frozenset[str]
    values: frozenset[str]
    crafted: bool
    deloot: bool
    defined_by: tuple[str, ...]


@dataclass
class LootIndex:
    mission: str
    groups: dict[str, Group]
    positions: dict[str, list[tuple[int, int]]]
    usage_to_groups: dict[str, set[str]]
    types: dict[str, TypeInfo]
    unreadable: list[str] = field(default_factory=list)
    #: Why the layer cannot draw anything, or None: ``no_mapgroupproto``,
    #: ``no_mapgrouppos`` or ``unreadable: <file>``.
    problem: str | None = None
    #: Group key -> the type keys that can spawn in it, built on first use by
    #: `items_by_group`. The index is replaced, never changed, so it stays valid.
    by_group: dict[str, list[str]] | None = None


# ── Reading ──────────────────────────────────────────────────────────────


def mapgroupproto_path(mission_dir: Path) -> Path | None:
    """``<mission>/mapgroupproto.xml``, matched case-insensitively; never a subfolder."""
    return mission_file(mission_dir, _PROTO)


def _check_size(path: Path, limit: int) -> None:
    size = path.stat().st_size
    if size > limit:
        raise LootFileError(f"{size} bytes, over the {limit // (1024 * 1024)} MB limit")


def _flag_names(elem: ET.Element, tag: str) -> frozenset[str]:
    names = [
        n for c in elem
        if isinstance(c.tag, str) and c.tag == tag and (n := (c.get("name") or "").strip())
    ]
    if len(names) > MAX_FLAGS_PER_ELEMENT:
        raise LootFileError(f"more than {MAX_FLAGS_PER_ELEMENT} <{tag}> in one element")
    return frozenset(names)


def parse_proto(path: Path) -> dict[str, Group]:
    """Lower-cased group name -> `Group`, from a mapgroupproto.xml.

    Raises `LootFileError` past a limit, `ET.ParseError` on malformed or
    unsafe XML (entity expansion is refused by defusedxml), `OSError` when it
    cannot be read.
    """
    from app.xml.xml_utils import load_xml

    _check_size(path, MAX_PROTO_BYTES)
    root = load_xml(path).getroot()
    groups: dict[str, Group] = {}
    for count, g in enumerate(root.iter("group"), start=1):
        if count > MAX_GROUPS:
            raise LootFileError(f"more than {MAX_GROUPS} groups")
        name = (g.get("name") or "").strip()
        if not name:
            continue
        containers: list[Container] = []
        for c in g:
            if not isinstance(c.tag, str) or c.tag != "container":
                continue
            if len(containers) >= MAX_CONTAINERS_PER_GROUP:
                raise LootFileError(
                    f"group {name!r} has more than {MAX_CONTAINERS_PER_GROUP} containers"
                )
            containers.append(Container(
                name=c.get("name") or "",
                categories=_flag_names(c, "category"),
                tags=_flag_names(c, "tag"),
                values=_flag_names(c, "value"),
                points=sum(1 for p in c if isinstance(p.tag, str) and p.tag == "point"),
            ))
        groups[name.lower()] = Group(
            name=name,
            usages=_flag_names(g, "usage"),
            values=_flag_names(g, "value"),
            containers=tuple(containers),
        )
    return groups


def _rel(mission_dir: Path, path: Path) -> str:
    return str(path.relative_to(mission_dir)) if path.is_relative_to(mission_dir) else str(path)


def _user_groups(mission_dir: Path, unreadable: list[str]) -> tuple[dict, dict]:
    """``(usage groups, value groups)``: user group name -> its member names."""
    from app.xml import limitsdefinition_xml

    try:
        path = limitsdefinition_xml.get_limitsdefinitionuser_path(mission_dir)
    except OSError:
        return {}, {}
    if path is None:
        return {}, {}
    try:
        _check_size(path, MAX_TYPES_FILE_BYTES)
        parsed = limitsdefinition_xml.parse_limitsdefinitionuser(path)
    except (OSError, ET.ParseError, ValueError, UnicodeDecodeError) as exc:
        unreadable.append(f"{_rel(mission_dir, path)}: {exc}")
        return {}, {}
    return (
        {g.name: g.members for g in parsed.usage_groups},
        {g.name: g.members for g in parsed.value_groups},
    )


def _expand(elem: ET.Element, tag: str, user_groups: dict[str, list[str]]) -> frozenset[str]:
    """The ``name=`` flags of *tag* children, with each ``user=`` group expanded."""
    out: set[str] = set()
    for c in elem.findall(tag):
        if name := (c.get("name") or "").strip():
            out.add(name)
        elif user := (c.get("user") or "").strip():
            out.update(user_groups.get(user, ()))
    return frozenset(out)


def resolve_types(mission_dir: Path) -> tuple[dict[str, TypeInfo], list[str]]:
    """Lower-cased classname -> the `TypeInfo` the economy ends up with, and
    the files it could not read (``"<file>: <reason>"``).

    ``db/types.xml`` first (owner `MISSION`), then each live registered types
    file in `registered_sources` order; the last definition of a classname
    wins, and ``defined_by`` keeps every owner that defined it.
    """
    from app.xml import types_xml

    unreadable: list[str] = []
    usage_groups, value_groups = _user_groups(mission_dir, unreadable)
    files: list[tuple[str, Path]] = [(MISSION, mission_dir / _TYPES)]
    files.extend(registered_sources(mission_dir).of("types"))

    seen: set[Path] = set()
    types: dict[str, TypeInfo] = {}
    for owner, path in files:
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        if resolved in seen:
            continue
        seen.add(resolved)
        try:
            _check_size(path, MAX_TYPES_FILE_BYTES)
            parsed = types_xml.parse_types_file(path)
        except (OSError, ET.ParseError, ValueError, UnicodeDecodeError) as exc:
            unreadable.append(f"{_rel(mission_dir, path)}: {exc}")
            continue
        for entry in parsed.entries:
            name = entry.name.strip()
            if not name:
                continue
            key = name.lower()
            before = types.get(key)
            defined_by = before.defined_by if before else ()
            if owner not in defined_by:
                defined_by = (*defined_by, owner)
            types[key] = TypeInfo(
                name=name,
                owner=owner,
                nominal=entry.nominal,
                category=entry.category,
                tag=entry.tag,
                usages=_expand(entry.element, "usage", usage_groups),
                values=_expand(entry.element, "value", value_groups),
                crafted=entry.flags.get("crafted") == 1,
                deloot=entry.flags.get("deloot") == 1,
                defined_by=defined_by,
            )
    return types, unreadable


def build_index(mission_dir: Path) -> LootIndex:
    """The uncached `LootIndex` for *mission_dir*. See `index_for`."""
    types, unreadable = resolve_types(mission_dir)
    problem: str | None = None
    groups: dict[str, Group] = {}

    proto = mapgroupproto_path(mission_dir)
    if proto is None:
        problem = "no_mapgroupproto"
    else:
        try:
            groups = parse_proto(proto)
        except (OSError, ET.ParseError, ValueError, UnicodeDecodeError) as exc:
            log.warning("Could not read %s: %s", proto, exc)
            unreadable.append(f"{proto.name}: {exc}")
            problem = f"unreadable: {proto.name}"

    positions: dict[str, list[tuple[int, int]]] = {}
    pos_path = mapgrouppos_path(mission_dir)
    if pos_path is None:
        problem = problem or "no_mapgrouppos"
    else:
        try:
            too_big = pos_path.stat().st_size > MAX_MAPGROUPPOS_BYTES
        except OSError:
            too_big = True
        if too_big:
            unreadable.append(f"{pos_path.name}: over the size limit")
            problem = problem or f"unreadable: {pos_path.name}"
        else:
            positions = building_groups(mission_dir)

    usage_to_groups: dict[str, set[str]] = {}
    for key, group in groups.items():
        for usage in group.usages:
            usage_to_groups.setdefault(usage, set()).add(key)

    return LootIndex(
        mission=mission_dir.name,
        groups=groups,
        positions=positions,
        usage_to_groups=usage_to_groups,
        types=types,
        unreadable=unreadable,
        problem=problem,
    )


# ── Cache ────────────────────────────────────────────────────────────────
#
# Keyed on every input's (mtime_ns, size): the fixed files, every entry in
# custom/, and every types file the last build read (a foreign <ce> folder's
# files are outside custom/). An edit, a merge or a map switch is picked up on
# the next request.

_cache: dict[Path, tuple[tuple, LootIndex, list[Path]]] = {}
_cache_lock = threading.Lock()


def _inputs(mission_dir: Path) -> list[Path]:
    paths = [
        mission_dir / _TYPES,
        mission_dir / "cfgeconomycore.xml",
        mapgroupproto_path(mission_dir) or mission_dir / _PROTO,
        mapgrouppos_path(mission_dir) or mission_dir / "mapgrouppos.xml",
        mission_file(mission_dir, "cfglimitsdefinitionuser.xml")
        or mission_dir / "cfglimitsdefinitionuser.xml",
    ]
    custom = mission_dir / "custom"
    try:
        paths.extend(sorted(custom.iterdir()) if custom.is_dir() else [])
    except OSError:
        pass
    return paths


def _stat_all(paths: list[Path]) -> tuple:
    sig = []
    for p in paths:
        try:
            st = p.stat()
            sig.append((str(p), st.st_mtime_ns, st.st_size))
        except OSError:
            sig.append((str(p), None, None))
    return tuple(sig)


def _registered_types_files(mission_dir: Path) -> list[Path]:
    return [path for _, path in registered_sources(mission_dir).of("types")]


def index_for(mission_dir: Path) -> LootIndex:
    """The cached `LootIndex` for *mission_dir*, rebuilt when an input changed."""
    fixed = _inputs(mission_dir)
    with _cache_lock:
        hit = _cache.get(mission_dir)
    if hit is not None:
        sig, index, extra = hit
        if sig == (_stat_all(fixed), _stat_all(extra)):
            return index
    extra = _registered_types_files(mission_dir)
    sig = (_stat_all(fixed), _stat_all(extra))
    index = build_index(mission_dir)
    with _cache_lock:
        _cache[mission_dir] = (sig, index, extra)
    return index


# ── Matching ─────────────────────────────────────────────────────────────


def container_matches(item: TypeInfo, c: Container) -> bool:
    """Whether container *c* takes *item*. The one place the rule lives.

    Community knowledge, not documented by Bohemia (plans/82 section 2):
    a container must list the item's category, and its tag when the item has
    one. An item with no category, or no tag, is not limited by it. A
    container's ``<value>`` is matched only when a file has one (stock
    mapgroupproto.xml has none). **Unverified** (plans/82 open questions 1
    and 2): whether a tag is an AND or an OR with the category, and whether a
    container ever carries a value. Flag names compare exactly, as
    `types_xml.undefined_limit_names` does.
    """
    return (
        (not item.category or item.category in c.categories)
        and (not item.tag or item.tag in c.tags)
        and (not c.values or not item.values or bool(item.values & c.values))
    )


def groups_for(item: TypeInfo, index: LootIndex) -> list[str]:
    """The group keys *item* can spawn in, by usage, group value and container.

    An item with no usage is not limited by usage (unverified, plans/82 open
    question 2): every group is a candidate.
    """
    if item.usages:
        candidates: set[str] = set()
        for usage in item.usages:
            candidates |= index.usage_to_groups.get(usage, set())
    else:
        candidates = set(index.groups)
    out = []
    for key in sorted(candidates):
        g = index.groups[key]
        if g.values and item.values and not (g.values & item.values):
            continue
        if any(c.points > 0 and container_matches(item, c) for c in g.containers):
            out.append(key)
    return out


def _never_placed(item: TypeInfo) -> str | None:
    if item.nominal <= 0:
        return "nominal 0"
    if item.crafted:
        return "crafted"
    if item.deloot:
        return "event loot only"
    return None


def _no_match_reason(item: TypeInfo, index: LootIndex, keys: list[str]) -> str:
    if keys:
        return "no building of its kind is on the map"
    if item.usages and not any(index.usage_to_groups.get(u) for u in item.usages):
        return "no building has its usage"
    return "no container takes its category/tag"


#: The owner key the API uses for the mission's own ``db/types.xml``
#: (`MISSION` is ``""``, which a URL cannot carry). No mod dir starts without
#: ``@`` and every other owner holds ``@`` or ``/``, so it cannot collide.
VANILLA_KEY = "vanilla"


def owner_key(owner: str) -> str:
    """*owner* as the API names it."""
    return VANILLA_KEY if owner == MISSION else owner


def owner_from_key(key: str, index: LootIndex) -> str | None:
    """The owner an API key names, matched without regard to case, or None."""
    wanted = key.strip().lower()
    if wanted == VANILLA_KEY:
        return MISSION if any(t.owner == MISSION for t in index.types.values()) else None
    return next((t.owner for t in index.types.values() if t.owner.lower() == wanted), None)


def label_of(owner: str) -> str:
    """How an owner reads in the panel."""
    if owner == MISSION:
        return "Vanilla (db/types.xml)"
    return owner_label(owner)


def match(index: LootIndex, names: list[str]) -> dict:
    """Where the items *names* (lower-cased classnames in *index*) can spawn.

    Matched once per distinct ``(usages, category, tag, values)`` signature,
    since a mod's items mostly share a few. ``points`` is the union of the
    matching items' building cells, flat ``[x0, z0, …]`` and sorted by x.
    """
    memo: dict[tuple, list[str]] = {}
    items: list[dict] = []
    cells: set[tuple[int, int]] = set()
    group_hits: dict[str, set[str]] = {}  # group key -> container names
    tiers_unchecked = False

    for key in names:
        item = index.types[key]
        row = {
            "name": item.name,
            "owner": owner_key(item.owner),
            "owner_label": label_of(item.owner),
            "nominal": item.nominal,
            "category": item.category,
            "tag": item.tag,
            "usages": sorted(item.usages),
            "values": sorted(item.values),
            "buildings": 0,
            "kinds": 0,
        }
        reason = _never_placed(item)
        if reason is None:
            sig = (item.usages, item.category, item.tag, item.values)
            if sig not in memo:
                memo[sig] = groups_for(item, index)
            keys = memo[sig]
            placed = [k for k in keys if index.positions.get(k)]
            item_cells: set[tuple[int, int]] = set()
            for k in placed:
                item_cells.update(index.positions[k])
                hit = group_hits.setdefault(k, set())
                hit.update(c.name for c in index.groups[k].containers
                           if c.points > 0 and container_matches(item, c))
                if item.values and not index.groups[k].values:
                    tiers_unchecked = True
            row["buildings"] = len(item_cells)
            row["kinds"] = len(placed)
            cells |= item_cells
            if not placed:
                reason = _no_match_reason(item, index, keys)
        if reason is not None:
            row["reason"] = reason
        items.append(row)

    groups = []
    for k, containers in group_hits.items():
        placed = index.positions[k]
        groups.append({
            "name": index.groups[k].name,
            "usages": sorted(index.groups[k].usages),
            "buildings": len(placed),
            "first": list(placed[0]),
            "containers": sorted(containers),
        })
    groups.sort(key=lambda g: (-g["buildings"], g["name"].lower()))

    return {
        "items": items,
        "matched": sum(1 for row in items if row["buildings"]),
        "count": len(cells),
        "points": [v for point in sorted(cells) for v in point],
        "kinds": len(groups),
        "groups": groups[:MAX_GROUPS_SHOWN],
        "tiers_unchecked": tiers_unchecked,
    }


def owners(index: LootIndex) -> list[dict]:
    """Every owner of a type the economy ends up with: ``{key, label, count}``,
    the key through `owner_key`.

    The mission's own first, then by label. ``count`` is the types whose last
    definition is that owner's.
    """
    counts: dict[str, int] = {}
    for item in index.types.values():
        counts[item.owner] = counts.get(item.owner, 0) + 1
    rows = [{"key": owner_key(k), "label": label_of(k), "count": n} for k, n in counts.items()]
    rows.sort(key=lambda r: (r["key"] != VANILLA_KEY, r["label"].lower()))
    return rows


def catalog(index: LootIndex) -> dict:
    """Every type the economy loads, for the panel's picker.

    ``items`` is ``[[classname, owner, category, usages, nominal, never], …]``
    sorted by name: ``owner`` an index into ``owners``, ``category`` one into
    ``categories`` (-1 for none), ``usages`` indexes into ``usages``, and
    ``never`` why the CE never places it (`_never_placed`) or ``""``.
    """
    rows = owners(index)
    position = {row["key"]: i for i, row in enumerate(rows)}
    types = sorted(index.types.values(), key=lambda info: info.name.lower())
    categories = sorted({t.category for t in types if t.category}, key=str.lower)
    usages = sorted({u for t in types for u in t.usages}, key=str.lower)
    cat_pos = {c: i for i, c in enumerate(categories)}
    use_pos = {u: i for i, u in enumerate(usages)}
    return {
        "owners": rows,
        "categories": categories,
        "usages": usages,
        "items": [
            [
                info.name,
                position[owner_key(info.owner)],
                cat_pos.get(info.category, -1),
                sorted(use_pos[u] for u in info.usages),
                info.nominal,
                _never_placed(info) or "",
            ]
            for info in types
        ],
    }


# ── One building ─────────────────────────────────────────────────────────


def items_by_group(index: LootIndex) -> dict[str, list[str]]:
    """Group key -> the type keys that can spawn in it, sorted by classname.

    The reverse of `groups_for`, matched once per signature like `match`, and
    kept on the index. Items the CE never places are left out.
    """
    if index.by_group is not None:
        return index.by_group
    memo: dict[tuple, list[str]] = {}
    out: dict[str, list[str]] = {}
    for key in sorted(index.types, key=lambda k: index.types[k].name.lower()):
        item = index.types[key]
        if _never_placed(item) is not None:
            continue
        sig = (item.usages, item.category, item.tag, item.values)
        if sig not in memo:
            memo[sig] = groups_for(item, index)
        for group in memo[sig]:
            out.setdefault(group, []).append(key)
    index.by_group = out
    return out


def building_kinds(index: LootIndex) -> dict:
    """Every building kind on the map that can hold loot, most common first.

    ``kinds`` has one row per proto group with at least one position and one
    loot point, in the shape `match`'s ``groups`` rows have (the Loot finder
    draws both with one list), plus ``items``: how many types can spawn in it.
    ``points`` is every one of those buildings, flat (``[x0, z0, x1, z1, …]``,
    sorted by x like `/buildings`), and ``point_kinds`` the index into
    ``kinds`` of each, so the map can draw each building's icon.
    """
    by_group = items_by_group(index)
    rows = []
    keys = []
    for key, group in index.groups.items():
        placed = index.positions.get(key)
        containers = sorted(c.name for c in group.containers if c.points > 0)
        if not placed or not containers:
            continue
        rows.append({
            "name": group.name,
            "usages": sorted(group.usages),
            "buildings": len(placed),
            "first": list(placed[0]),
            "containers": containers,
            "items": len(by_group.get(key, ())),
        })
        keys.append(key)
    order = sorted(range(len(rows)), key=lambda i: (-rows[i]["buildings"], rows[i]["name"].lower()))
    kinds = [rows[i] for i in order]
    placed = sorted(
        (cell, rank) for rank, i in enumerate(order) for cell in index.positions[keys[i]]
    )
    return {
        "kinds": kinds,
        "points": [v for cell, _ in placed for v in cell],
        "point_kinds": [rank for _, rank in placed],
    }


def building_at(index: LootIndex, x: float, z: float, radius: float) -> tuple[str, tuple[int, int]] | None:
    """The group key and position of the building nearest to (*x*, *z*)
    within *radius* metres, or None. Only buildings the proto describes."""
    best: tuple[float, str, tuple[int, int]] | None = None
    limit = radius * radius
    for key, cells in index.positions.items():
        if key not in index.groups:
            continue
        for cell in cells:
            d = (cell[0] - x) ** 2 + (cell[1] - z) ** 2
            if d <= limit and (best is None or d < best[0]):
                best = (d, key, cell)
    return (best[1], best[2]) if best else None


def building_report(index: LootIndex, key: str, cell: tuple[int, int]) -> dict:
    """What one building kind holds and every item that can spawn in it."""
    group = index.groups[key]
    items = []
    for name in items_by_group(index).get(key, []):
        info = index.types[name]
        items.append({
            "name": info.name,
            "owner": owner_key(info.owner),
            "owner_label": label_of(info.owner),
            "category": info.category,
            "nominal": info.nominal,
        })
    return {
        "name": group.name,
        "position": list(cell),
        "on_map": len(index.positions.get(key, ())),
        "usages": sorted(group.usages),
        "values": sorted(group.values),
        "containers": [
            {
                "name": c.name,
                "categories": sorted(c.categories),
                "tags": sorted(c.tags),
                "points": c.points,
            }
            for c in group.containers
            if c.points > 0
        ],
        "items": items,
    }
