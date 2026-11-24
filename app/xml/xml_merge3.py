"""Three-way merge of a stock mission XML file (plans/71).

*base* is the stock file the admin's version was made from, *mine* the admin's
version and *theirs* DayZ's new stock file. The admin's changes are worked out
per entry and per value against *base* and replayed onto *theirs*, so a DayZ
update keeps every new item while the admin keeps every edit. Only a value both
sides changed, or an entry one side removed while the other changed it, is
reported as a row.

An element is matched across the three versions by a key among its siblings,
never by position. The first rule that fits the element's tag decides:

1. A per-file rule (`_FILE_RULES`): territories have no name, so a territory is
   keyed by where its zones are and a zone by its name and position. A
   cfgeventgroups `<child>` is keyed by its position: a group repeats types
   (decals), and DayZ 1.30 swaps a convoy's wreck type at the same spot.
2. The tag, when it occurs at most once among the siblings in every version
   (`<nominal>`, `<flags>`, cfgweather's singletons). Comments never use it.
3. The first of `name`, `type`, `folder` that every same-tag sibling carries.
   Elements that share a value form one entry, compared as a whole: Livonia's
   cfgspawnabletypes.xml has a type twice. Leaves with repeated names (the
   `<group>` lines of mapgrouppos.xml) use rule 4 instead.
4. A leaf (no children), or a comment: the whole element is the key, so it can
   be added or removed but never "changed". This is what keeps the
   ``<!-- DISABLED @Mod: … -->`` wrappers of cfgeconomycore.xml: only the
   admin's version has them. An edit is a removal plus an addition here, so
   when both sides replaced the same leaf with different ones, nothing says
   which addition stands for which: the parent is then compared as one value
   (at the root, the file is unmergeable), never both kept.
5. Nothing fits: the parent is compared as one value. cfgspawnabletypes.xml's
   `<attachments>` groups have no name, so each `<type>` is one value there. At
   the root this means the file cannot be merged (`Unmergeable`).

The result is written the way `xml_utils.save_xml` writes and read back; if it
does not read back as the tree that was built, the file is unmergeable.
"""

from __future__ import annotations

import fnmatch
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Callable

BOTH_CHANGED = "both_changed"
DAYZ_REMOVED = "dayz_removed"
YOU_REMOVED = "you_removed"

_COMMENT = "#comment"
_KEY_ATTRS = ("name", "type", "folder")

# (file name pattern, tag) -> key of one element. Only where rules 2-4 fail.
_FILE_RULES: dict[tuple[str, str], Callable[[ET.Element], tuple]] = {
    ("*_territories.xml", "territory"): lambda el: tuple(sorted(
        (zone.get("x", ""), zone.get("z", "")) for zone in el if _tag(zone) == "zone"
    )),
    ("*_territories.xml", "zone"): lambda el: (el.get("name", ""), el.get("x", ""), el.get("z", "")),
    ("cfgeventgroups.xml", "child"): lambda el: (el.get("x", ""), el.get("z", "")),
}

# Called for a value both sides changed: (keys, target, base, mine, theirs) ->
# the value to use instead of a row, or None. Quick tweaks use it (D4).
Resolver = Callable[[list, list, "str | None", "str | None", "str | None"], "str | None"]


class Unmergeable(Exception):
    """The file cannot be merged safely; the caller keeps the whole-file choice."""


@dataclass
class ValueRow:
    """One value both sides touched. ``keys`` and ``target`` find it again in
    the live file (JSON-able); the rest is for people."""

    entry: str
    field: str
    base: str | None
    mine: str | None
    theirs: str | None
    kind: str
    live: str  # "mine" or "dayz"
    keys: list
    target: list  # ["attr", name] | ["text"] | ["entry"]


@dataclass
class XmlMerge:
    text: str
    rows: list[ValueRow] = field(default_factory=list)
    dayz: int = 0  # changes taken from DayZ's new file
    yours: int = 0  # the admin's changes kept
    resolved: int = 0  # values both changed that the resolver settled


def merge_xml3(
    base: str, mine: str, theirs: str, *, filename: str,
    prefer: str = "mine", resolver: Resolver | None = None,
) -> XmlMerge:
    """Merge *mine* into *theirs* against *base*. *prefer* ("mine" or "dayz")
    settles a value both sides changed; an entry DayZ removed always goes.

    Raises `Unmergeable` when a version does not parse, the roots differ, or
    the entries cannot be keyed.
    """
    from app.xml.xml_merge import strip_mod_blocks_text

    if prefer not in ("mine", "dayz"):
        raise ValueError(f"prefer must be 'mine' or 'dayz', not {prefer!r}")
    roots = [parse(text) for text in (base, strip_mod_blocks_text(mine)[0], theirs)]
    if len({root.tag for root in roots}) != 1:
        raise Unmergeable("the root element differs between the versions")
    merger = _Merger(filename, prefer, resolver)
    if merger.plans(*roots) is None:
        raise Unmergeable("the file's entries have no key")
    (root,) = merger.element(*roots, keys=[], label="")
    text = serialize(root)
    if _canon(parse(text)) != _canon(root):
        raise Unmergeable("the merged file did not read back as built")
    return XmlMerge(text=text, rows=merger.rows, dayz=merger.dayz, yours=merger.yours, resolved=merger.resolved)


# --- finding a value again (applying one side later) ------------------------


def find_element(root: ET.Element, keys: list, filename: str) -> list[ET.Element]:
    """The element(s) *keys* names in a single tree, or [] when it is gone.

    Each key is matched among one parent's children the way it was made, so
    no plan over three versions is needed.
    """
    nodes = [root]
    for key in keys:
        if len(nodes) != 1:
            return []
        nodes = [child for child in nodes[0] if _matches(child, key, filename)]
        if not nodes:
            return []
        if key[0] == "leaf":
            occurrence = key[2]
            nodes = nodes[occurrence - 1:occurrence]
    return nodes


def value_at(root: ET.Element, keys: list, target: list, filename: str) -> str | None:
    """A row's value in a single tree, shown the way the row shows it; None
    when it is gone."""
    found = find_element(root, keys, filename)
    if target[:1] == ["entry"]:
        return _shown(found) if found else None
    if len(found) != 1:
        return None
    if target[:1] == ["attr"]:
        return found[0].get(target[1])
    return _text(found[0])


def find_parent(root: ET.Element, keys: list, filename: str) -> ET.Element | None:
    found = find_element(root, keys[:-1], filename) if keys[:-1] else [root]
    return found[0] if len(found) == 1 else None


def _matches(el: ET.Element, key: list, filename: str) -> bool:
    kind, tag = key[0], key[1]
    if kind == "leaf":
        return _leaf_id(el) == tag
    if _tag(el) != tag:
        return False
    if kind == "tag":
        return True
    if kind == "attr":
        return el.get(key[2]) == key[3]
    rule = _file_rule(filename, tag)
    return rule is not None and _jsonable(rule(el)) == key[2]


# --- the merge ---------------------------------------------------------------


class _Merger:
    def __init__(self, filename: str, prefer: str, resolver: Resolver | None):
        self.filename = filename
        self.prefer = prefer
        self.resolver = resolver
        self.rows: list[ValueRow] = []
        self.dayz = 0
        self.yours = 0
        self.resolved = 0

    # Keying -----------------------------------------------------------------

    def plans(self, *parents: ET.Element) -> dict[str, Callable[[ET.Element], tuple]] | None:
        """tag -> key function for the children of *parents*, or None when a
        tag has no key (the parent is then compared as one value)."""
        tags: dict[str, list[list[ET.Element]]] = {}
        for i, parent in enumerate(parents):
            for child in parent:
                groups = tags.setdefault(_tag(child), [[] for _ in parents])
                groups[i].append(child)
        plans = {}
        for tag, groups in tags.items():
            plan = self._plan(tag, groups)
            if plan is None:
                return None
            plans[tag] = plan
        return plans

    def _plan(self, tag: str, groups: list[list[ET.Element]]) -> Callable[[ET.Element], tuple] | None:
        everyone = [el for group in groups for el in group]
        rule = _file_rule(self.filename, tag)
        if rule is not None:
            return lambda el: ("custom", tag, rule(el))
        if tag == _COMMENT:
            return lambda el: ("leaf", _leaf_id(el))
        if all(len(group) <= 1 for group in groups):
            return lambda el: ("tag", tag)
        leaves = all(len(el) == 0 for el in everyone)
        for attr in _KEY_ATTRS:
            if all(el.get(attr) is not None for el in everyone):
                unique = all(len({el.get(attr) for el in group}) == len(group) for group in groups)
                if unique or not leaves:
                    return lambda el, attr=attr: ("attr", tag, attr, el.get(attr))
                break
        if leaves:
            return lambda el: ("leaf", _leaf_id(el))
        return None

    def _keyed(self, parent: ET.Element, plans) -> tuple[dict[tuple, list[ET.Element]], list[tuple]]:
        groups: dict[tuple, list[ET.Element]] = {}
        order: list[tuple] = []
        seen_leaf: dict[tuple, int] = {}
        for child in parent:
            key = plans[_tag(child)](child)
            if key[0] == "leaf":
                seen_leaf[key] = seen_leaf.get(key, 0) + 1
                key = (*key, seen_leaf[key])
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(child)
        return groups, order

    # Merging ----------------------------------------------------------------

    def element(self, b: ET.Element, m: ET.Element, t: ET.Element, *, keys: list, label: str) -> list[ET.Element]:
        plans = self.plans(b, m, t)
        if plans is not None and _both_replaced_a_leaf(b, m, t, plans, self._keyed):
            if not keys:
                raise Unmergeable("both versions replaced the same entry, and the entries have no key")
            plans = None
        if plans is None:
            return self._whole([b], [m], [t], keys, label)
        new = ET.Element(t.tag)
        for name in [*t.attrib, *(a for a in m.attrib if a not in t.attrib)]:
            value = self._value(b.get(name), m.get(name), t.get(name), keys, label, ["attr", name])
            if value is not None:
                new.set(name, value)
        text = self._value(_text(b), _text(m), _text(t), keys, label, ["text"])
        new.text = text or None
        new.extend(self._children(b, m, t, plans, keys, label))
        return [new]

    def _value(self, b, m, t, keys, label, target):
        if m == b:
            if t != b:
                self.dayz += 1
            return t
        if t == b:
            self.yours += 1
            return m
        if m == t:
            return m
        if self.resolver is not None:
            settled = self.resolver(keys, target, b, m, t)
            if settled is not None:
                self.resolved += 1
                return settled
        self._row(keys, label, target, b, m, t, BOTH_CHANGED, self.prefer)
        return m if self.prefer == "mine" else t

    def _children(self, b, m, t, plans, keys, label) -> list[ET.Element]:
        (kb, b_order), (km, m_order), (kt, t_order) = (self._keyed(p, plans) for p in (b, m, t))
        result: dict[tuple, list[ET.Element]] = {}
        for key in dict.fromkeys([*t_order, *m_order, *kb]):
            bl, ml, tl = kb.get(key, []), km.get(key, []), kt.get(key, [])
            child_keys = [*keys, _jsonable(key)]
            child_label = label or _label(key)
            if bl and ml and tl:
                if len(bl) == len(ml) == len(tl) == 1 and key[0] != "leaf":
                    result[key] = self.element(bl[0], ml[0], tl[0], keys=child_keys, label=child_label)
                else:
                    result[key] = self._whole(bl, ml, tl, child_keys, child_label)
            elif not bl:
                kept = self._whole([], ml, tl, child_keys, child_label)
                if kept:
                    result[key] = kept
            elif not ml and not tl:
                continue
            elif not ml:
                if _canons(tl) == _canons(bl):
                    self.yours += 1
                else:
                    self._row(child_keys, child_label, ["entry"], bl, None, tl, YOU_REMOVED, self.prefer)
                    if self.prefer == "dayz":
                        result[key] = tl
            elif not tl:
                if _canons(ml) == _canons(bl):
                    self.dayz += 1
                else:
                    self._row(child_keys, child_label, ["entry"], bl, ml, None, DAYZ_REMOVED, "dayz")
        if _reordered(m_order, b_order) and not _reordered(t_order, b_order):
            order = _order(result, primary=m_order, other=t_order)
        else:
            order = _order(result, primary=t_order, other=m_order)
        return [el for key in order for el in result[key]]

    def _whole(self, bl, ml, tl, keys, label) -> list[ET.Element]:
        """Entries compared as one value each side: added, changed or gone."""
        cb, cm, ct = _canons(bl), _canons(ml), _canons(tl)
        if cm == cb:
            if ct != cb:
                self.dayz += 1
            return tl
        if ct == cb:
            self.yours += 1
            return ml
        if cm == ct:
            return ml
        self._row(keys, label, ["entry"], bl or None, ml or None, tl or None, BOTH_CHANGED, self.prefer)
        return ml if self.prefer == "mine" else tl

    def _row(self, keys, label, target, b, m, t, kind, live) -> None:
        self.rows.append(ValueRow(
            entry=label or "(file)",
            field=_field(keys, target),
            base=_shown(b), mine=_shown(m), theirs=_shown(t),
            kind=kind, live=live, keys=keys, target=target,
        ))


# --- helpers -----------------------------------------------------------------


def _order(result: dict, *, primary: list, other: list) -> list:
    """*primary*'s order, with each entry only *other* has placed after its
    nearest preceding sibling in *other*. DayZ's order is primary unless only
    the admin's version moved entries around."""
    placed = [key for key in primary if key in result]
    in_primary = set(placed)
    after: dict[tuple | None, list] = {}
    anchor = None
    for key in other:
        if key in in_primary:
            anchor = key
        elif key in result:
            after.setdefault(anchor, []).append(key)
    out = list(after.get(None, []))
    for key in placed:
        out.append(key)
        out.extend(after.get(key, []))
    return out


def _reordered(order: list, b_order: list) -> bool:
    """Whether *order* moved entries it shares with *b_order* around."""
    shared = set(order) & set(b_order)
    return [k for k in order if k in shared] != [k for k in b_order if k in shared]


def _tag(el: ET.Element) -> str:
    return _COMMENT if el.tag is ET.Comment else el.tag


def _text(el: ET.Element) -> str:
    return (el.text or "").strip()


def _both_replaced_a_leaf(b, m, t, plans, keyed) -> bool:
    """A leaf of *b* gone from both *m* and *t*, which each added a leaf the
    other does not have: both changed it, differently (an edit of a leaf is a
    removal plus an addition, rule 4)."""
    kb, km, kt = (keyed(p, plans)[0] for p in (b, m, t))
    if not any(k[0] == "leaf" and k not in km and k not in kt for k in kb):
        return False
    added_m = {k for k in km if k[0] == "leaf" and k not in kb and k not in kt}
    added_t = {k for k in kt if k[0] == "leaf" and k not in kb and k not in km}
    return bool(added_m and added_t)


def _leaf_id(el: ET.Element) -> str:
    attrs = " ".join(f'{k}="{v}"' for k, v in sorted(el.attrib.items()))
    return f"{_tag(el)}|{attrs}|{_text(el)}"


def _canon(el: ET.Element) -> tuple:
    return (_tag(el), tuple(sorted(el.attrib.items())), _text(el), tuple(_canon(c) for c in el))


def _canons(els: list[ET.Element]) -> tuple:
    return tuple(_canon(el) for el in els)


def _file_rule(filename: str, tag: str):
    name = filename.rsplit("/", 1)[-1].lower()
    for (pattern, rule_tag), rule in _FILE_RULES.items():
        if rule_tag == tag and fnmatch.fnmatch(name, pattern):
            return rule
    return None


def _jsonable(value):
    if isinstance(value, tuple):
        return [_jsonable(v) for v in value]
    return value


def _label(key: tuple) -> str:
    if key[0] == "attr":
        return key[3]
    if key[0] == "tag":
        return key[1]
    if key[0] == "custom":
        return key[1]
    return key[1].split("|", 1)[0]


def _field(keys: list, target: list) -> str:
    parts = []
    for key in keys[1:]:
        if key[0] == "attr":
            parts.append(f"{key[1]}[{key[3]}]")
        elif key[0] == "leaf":
            parts.append(key[1].split("|", 1)[0])
        else:
            parts.append(key[1])
    if target[0] == "attr":
        parts.append(f"@{target[1]}")
    elif target[0] == "entry" and not parts:
        parts.append("(whole entry)")
    return "/".join(parts) or "text"


def _shown(value) -> str | None:
    if value is None or isinstance(value, str):
        return value
    return "\n".join(_compact(el) for el in value)


def _compact(el: ET.Element) -> str:
    copy = _clone(el)
    ET.indent(copy, space="\t")
    return ET.tostring(copy, encoding="unicode").strip()


def _clone(el: ET.Element) -> ET.Element:
    copy = ET.Element(el.tag, dict(el.attrib)) if el.tag is not ET.Comment else ET.Comment(el.text)
    copy.text = el.text if el.tag is ET.Comment else (_text(el) or None)
    for child in el:
        copy.append(_clone(child))
    return copy


def parse(text: str) -> ET.Element:
    from app.xml.xml_text import parse_xml_text

    try:
        return parse_xml_text(text, preserve_comments=True).getroot()
    except ET.ParseError as exc:
        raise Unmergeable(f"a version does not parse: {exc}") from exc


def serialize(root: ET.Element) -> str:
    """What `xml_utils.save_xml` writes for *root*."""
    tree = ET.ElementTree(root)
    ET.indent(tree, space="\t")
    text = ET.tostring(root, encoding="unicode", xml_declaration=True)
    return text if text.endswith("\n") else text + "\n"
