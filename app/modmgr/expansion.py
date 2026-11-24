"""The DayZ-Expansion module catalog — which modules exist, what each one needs.

Pure data plus one graph walk: no config, no network, no filesystem, so it is
fully unit-testable. The IDs here are **not** a second registry — every module is
keyed by the workshop ID it already has in
:data:`app.shared.seed_data.WORKSHOP_MOD_IDS`, and :func:`module_name` reads the
name back out of it, so a mod's name lives in exactly one place
(``tests/test_expansion_catalog.py`` fails if any catalog ID is missing there).

The dependency edges were measured in 2026 Q3 from the installed PBOs'
``requiredAddons`` (plan 36), not read off a wiki page:

- everything needs ``-Core``, except ``-Animations``, ``-Licensed`` and
  ``-Map-Assets``, which need nothing;
- ``-Core`` needs ``@CF`` and ``@Dabs Framework``;
- ``-Vehicles`` also needs ``-Licensed``.

That is a **floor, not the whole truth**: Steam's own "Required items" list may
name more, and could not be read while the catalog was written (the Workshop
rate-limited every fetch). The install path unions this closure with
``mod_ops.get_missing_deps_ex``, so an extra Workshop-declared dependency is an
addition, never a contradiction.

``@DayZ-Expansion`` (2116151222) is **a content module, not a container** — it
defines 105 classes of its own and requires only Core. The all-in-one Bundle is a
separate Workshop item and is deliberately not offered here.

Beside the measured graph there is :data:`PAIRINGS`, which is a different kind of
fact and is kept apart for that reason: Expansion's own installation cheatsheet
(Steam Workshop item 2430317812) groups some modules into *packages* —
BaseBuilding with Licensed and Book, Groups with Book, Missions with Licensed —
and footnotes that the main module's mapping needs Map-Assets. **None of those
are in ``requiredAddons``**, so the server loads without them and what suffers is
the feature, not the boot. They are therefore shown as a hint next to the
checkbox and **never selected for you** — a dependency and "the authors ship
these together" are not the same claim, and the picker must not blur them.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

CF_ID = "1559212036"
DABS_ID = "2545327648"
CORE_ID = "2291785308"
LICENSED_ID = "2116157322"


@dataclass(frozen=True)
class ExpansionModule:
    """One installable item: an Expansion module, or a framework it needs.

    ``deps`` holds workshop IDs of *direct* dependencies only — the transitive
    closure is :func:`required_for`'s job. ``classes`` is the number of
    CfgVehicles ∪ CfgWeapons ∪ CfgMagazines classes the module defines (measured
    on the 2026 Q3 install); 0 means the module ships behaviour, not content,
    which is worth showing next to a checkbox.
    """

    id: str
    label: str
    blurb: str
    deps: tuple[str, ...] = ()
    classes: int = 0
    framework: bool = False


# @CF and @Dabs Framework are not Expansion modules and are never optional —
# they are listed apart so the picker can show them as what they are.
FRAMEWORKS: tuple[ExpansionModule, ...] = (
    ExpansionModule(
        id=CF_ID,
        label="Community Framework",
        blurb="Community Framework (CF). Required by Expansion Core; also used by many other mods.",
        framework=True,
    ),
    ExpansionModule(
        id=DABS_ID,
        label="Dabs Framework",
        blurb="Dabs Framework. Required by Expansion Core.",
        framework=True,
    ),
)

MODULES: tuple[ExpansionModule, ...] = (
    ExpansionModule(
        id=CORE_ID,
        label="Core",
        blurb="The settings system, notifications and the shared UI every other module builds on. "
        "Always installed.",
        deps=(CF_ID, DABS_ID),
        classes=198,
    ),
    ExpansionModule(
        id=LICENSED_ID,
        label="Licensed",
        blurb="Licensed content: helicopters, spray cans, bus/tractor/Vodnik parts and "
        "base-building kits. 172 of Chernarus' 484 template types are Licensed classes, so "
        "Vehicles, Weapons and BaseBuilding content refers to it constantly.",
        classes=362,
    ),
    ExpansionModule(
        id="2116151222",
        label="Expansion (main)",
        blurb="The original Expansion content pack — 105 classes of its own. This is a content "
        "module, not an all-in-one bundle.",
        deps=(CORE_ID,),
        classes=105,
    ),
    ExpansionModule(
        id="2792982069",
        label="AI",
        blurb="AI patrols, guards and bandits, placed from the AI settings files.",
        deps=(CORE_ID,),
        classes=69,
    ),
    ExpansionModule(
        id="2793893086",
        label="Animations",
        blurb="The animation set the other modules play. Needs nothing itself.",
        classes=32,
    ),
    ExpansionModule(
        id="2792982513",
        label="Base Building",
        blurb="Expansion base building: codelocks, territories and the raid system.",
        deps=(CORE_ID,),
        classes=220,
    ),
    ExpansionModule(
        id="2572324799",
        label="Book",
        blurb="The in-game server book — rules, players, territories and quests in one UI.",
        deps=(CORE_ID,),
    ),
    ExpansionModule(
        id="2792982897",
        label="Chat",
        blurb="Extra chat channels (global, transport, party) and chat colours.",
        deps=(CORE_ID,),
    ),
    ExpansionModule(
        id="2792983364",
        label="Groups",
        blurb="Player parties: invites, group markers and shared map markers.",
        deps=(CORE_ID,),
    ),
    ExpansionModule(
        id="2828487396",
        label="Hardline",
        blurb="The humanity/reputation system and its rarity tiers.",
        deps=(CORE_ID,),
        classes=10,
    ),
    ExpansionModule(
        id="2792983824",
        label="Map Assets",
        blurb="986 buildings and props the Expansion mission templates place on the map.",
        classes=986,
    ),
    ExpansionModule(
        id="2572328470",
        label="Market",
        blurb="The trader/market system: trader zones, ATM and the player-to-player market. "
        "Every class in the template's trader maps is a Market class.",
        deps=(CORE_ID,),
        classes=231,
    ),
    ExpansionModule(
        id="2792984177",
        label="Missions",
        blurb="The mission system — airdrops and contaminated areas.",
        deps=(CORE_ID,),
        classes=36,
    ),
    ExpansionModule(
        id="2576460232",
        label="Name Tags",
        blurb="Name tags above players, configurable per distance.",
        deps=(CORE_ID,),
    ),
    ExpansionModule(
        id="2792984722",
        label="Navigation",
        blurb="The 2D map, GPS and 3D world markers.",
        deps=(CORE_ID,),
        classes=1,
    ),
    ExpansionModule(
        id="2828486817",
        label="Quests",
        blurb="Quests and quest NPCs, with objectives configured per quest file.",
        deps=(CORE_ID,),
        classes=144,
    ),
    ExpansionModule(
        id="2804241648",
        label="Spawn Selection",
        blurb="The spawn-point selection screen players get on joining.",
        deps=(CORE_ID,),
    ),
    ExpansionModule(
        id="2291785437",
        label="Vehicles",
        blurb="Expansion vehicles, car keys, and the vehicle lock/damage systems. Its parts and "
        "wrecks are Licensed classes, so Licensed comes with it.",
        deps=(CORE_ID, LICENSED_ID),
        classes=549,
    ),
    ExpansionModule(
        id="2792985069",
        label="Weapons",
        blurb="Expansion weapons, magazines and attachments.",
        deps=(CORE_ID,),
        classes=442,
    ),
)

@dataclass(frozen=True)
class ExpansionPairing:
    """A cheatsheet package: the IDs it names, and the reason to read."""

    with_ids: tuple[str, ...]
    note: str


#: Which modules Expansion's installation cheatsheet packages together, keyed by
#: the module the hint hangs off. Source: the "EXPANSION INSTALLATION CHEATSHEET"
#: image on Steam Workshop item 2430317812 (the official installation guide),
#: read in 2026 Q3. **Not dependencies** — see the module docstring.
PAIRINGS: dict[str, ExpansionPairing] = {
    "2792982513": ExpansionPairing(  # Base Building
        (LICENSED_ID, "2572324799"),
        "Expansion ships Base Building as a package with Licensed and Book — "
        "the kits are Licensed classes and territories are administered from the Book.",
    ),
    "2792983364": ExpansionPairing(  # Groups
        ("2572324799",),
        "Expansion ships Groups as a package with Book, which is where a party is managed.",
    ),
    "2792984177": ExpansionPairing(  # Missions
        (LICENSED_ID,),
        "Expansion ships Missions as a package with Licensed — the airdrop and crash "
        "content is Licensed.",
    ),
    "2116151222": ExpansionPairing(  # Expansion (main)
        ("2792983824",),
        "Its mapping feature needs Map-Assets, per the cheatsheet's own footnote.",
    ),
}

ALL: tuple[ExpansionModule, ...] = MODULES + FRAMEWORKS

_BY_ID: dict[str, ExpansionModule] = {m.id: m for m in ALL}


def get(mod_id: str) -> ExpansionModule | None:
    """The catalog entry for *mod_id*, or None when it is not an Expansion item."""
    return _BY_ID.get(str(mod_id))


def module_name(mod_id: str) -> str:
    """``@DayZ-Expansion-Core`` for a catalog ID — read from the seed registry.

    Empty for an ID the registry does not know, which is a bug the catalog test
    catches rather than something a caller should paper over.
    """
    from app.shared.seed_data import parse_workshop_mod_ids

    return parse_workshop_mod_ids().get(str(mod_id), "")


def required_for(selection: Iterable[str]) -> set[str]:
    """The transitive dependency closure of *selection*, excluding the selection.

    Selecting Vehicles alone returns ``{Licensed, Core, @CF, @Dabs Framework}``.
    IDs that are not in the catalog are ignored — they carry no edges here, and
    the install path resolves their deps through ``MOD_DEPS``/Workshop anyway.
    """
    chosen = {str(i) for i in selection}
    needed: set[str] = set()
    queue = [i for i in chosen if i in _BY_ID]
    while queue:
        entry = _BY_ID[queue.pop()]
        for dep in entry.deps:
            if dep not in needed:
                needed.add(dep)
                if dep in _BY_ID:
                    queue.append(dep)
    return needed - chosen


def pairing(mod_id: str) -> ExpansionPairing | None:
    """The cheatsheet package for *mod_id*, or None.

    Separate from :func:`requires` on purpose: the picker renders this as text
    and never ticks anything from it.
    """
    return PAIRINGS.get(str(mod_id))


def requires(mod_id: str) -> list[str]:
    """:func:`required_for` for one module, in catalog order (frameworks first).

    The picker renders and locks rows off this list, so the graph walk happens
    here and never a second time in JavaScript.
    """
    reqs = required_for([mod_id])
    return [m.id for m in FRAMEWORKS + MODULES if m.id in reqs]
