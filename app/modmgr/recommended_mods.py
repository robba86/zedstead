"""The mods Quick setup can offer, and the sets of them that passed a boot test.

This module is data and one random pick. It holds a Workshop ID, a name and
Zedstead's own sentence for each mod, and no file, price, text or image from a mod.
The admin's own Steam account downloads each mod from the Workshop at run time.

A mod enters ``RECOMMENDED_MODS`` only after a read of its author's terms shows
that any server can use it. A random mix cannot promise a working server, so a
roll picks only from ``TESTED_SETS``. A set enters that list after one boot and
one client join on each map it names, and leaves it when it stops booting.
"""

from __future__ import annotations

import os
import random
from dataclasses import dataclass

#: Set this variable on a test server to offer ``CANDIDATE_SETS`` too. A new set
#: needs a boot test through a real Apply before it can enter ``TESTED_SETS``.
CANDIDATES_ENV = "ZSM_QUICK_SETUP_CANDIDATES"

_TERMS_READ = "Workshop page, 2026-11-09"


@dataclass(frozen=True)
class RecommendedMod:
    workshop_id: str
    name: str  # the mod's name in the mod list, "@Name"
    blurb: str  # Zedstead's own sentence, never the author's text
    slot: str  # at most one mod per slot in a set
    terms_checked: str  # date and source of the licence read


@dataclass(frozen=True)
class ModSet:
    mods: tuple[str, ...]  # Workshop IDs in load order, without dependencies
    maps: tuple[str, ...]  # names from app/maps/registry.py where it passed
    dayz_version: str = ""  # empty on a candidate, which has no test yet
    tested_on: str = ""  # the test date, empty on a candidate


TRADERPLUS = "2458896948"
RADZONE = "2294323732"
TOXICZONE = "1752669393"
AIRDROP_UPGRADED = "1870524790"
MORTYS_WEAPONS = "2155726353"

RECOMMENDED_MODS: tuple[RecommendedMod, ...] = (
    RecommendedMod(
        TRADERPLUS, "@TraderPlus",
        "Adds traders where players buy and sell gear.",
        "trader", _TERMS_READ,
    ),
    RecommendedMod(
        RADZONE, "@RadZone",
        "Makes the northwest and northeast airfields radiation zones that need protective gear.",
        "hazard", _TERMS_READ,
    ),
    RecommendedMod(
        TOXICZONE, "@ToxicZone",
        "Makes Kamensk military base and Tisy toxic gas zones that need protective gear.",
        "hazard", _TERMS_READ,
    ),
    RecommendedMod(
        AIRDROP_UPGRADED, "@Airdrop-Upgraded",
        "Drops loot containers from the air at places across the map.",
        "event", _TERMS_READ,
    ),
    RecommendedMod(
        MORTYS_WEAPONS, "@Mortys Weapons",
        "Adds more guns to the loot. It is a large download, about 2.2 GB.",
        "weapons", _TERMS_READ,
    ),
)

#: Sets that passed a boot and a join. The first test results fill this list.
TESTED_SETS: tuple[ModSet, ...] = ()

#: The sets to test next. The default zones of RadZone and ToxicZone are places
#: on Chernarus. Airdrop-Upgraded asks that loot mods load before it.
CANDIDATE_SETS: tuple[ModSet, ...] = (
    ModSet((TRADERPLUS, AIRDROP_UPGRADED), ("Chernarus",)),
    ModSet((TRADERPLUS, RADZONE), ("Chernarus",)),
    ModSet((TRADERPLUS, TOXICZONE), ("Chernarus",)),
    ModSet((MORTYS_WEAPONS, TRADERPLUS, AIRDROP_UPGRADED), ("Chernarus",)),
)

_BY_ID = {mod.workshop_id: mod for mod in RECOMMENDED_MODS}


def get_mod(workshop_id: str) -> RecommendedMod | None:
    """The list entry for *workshop_id*, or None when it is not recommended."""
    return _BY_ID.get(workshop_id)


def sets_for_map(map_name: str | None) -> list[ModSet]:
    """The sets a roll can pick on *map_name*, tested sets first."""
    sets = list(TESTED_SETS)
    if os.environ.get(CANDIDATES_ENV):
        sets += [s for s in CANDIDATE_SETS if s not in sets]
    return [s for s in sets if map_name in s.maps]


def find_set(workshop_ids: list[str], map_name: str | None) -> ModSet | None:
    """The set for *map_name* that holds exactly *workshop_ids*, in any order."""
    wanted = set(workshop_ids)
    return next((s for s in sets_for_map(map_name) if set(s.mods) == wanted), None)


def roll(seed: int, map_name: str | None, *, previous: ModSet | None = None) -> ModSet | None:
    """One set for *map_name*, the same one for the same *seed*. It writes nothing.

    With *previous*, the roll picks another set when one exists, so that the
    Pick other mods button always shows a change.
    """
    sets = sets_for_map(map_name)
    others = [s for s in sets if s != previous]
    choices = others or sets
    return random.Random(seed).choice(choices) if choices else None
