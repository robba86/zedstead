"""Where TraderPlus and Expansion Market meet on the map.

Both mods are trading systems, and a server can have both installed: the
editor draws TraderPlus traders (gold) and safe zones (green) from
`TraderPlusGeneralConfig.json` / `TraderPlusSafeZoneConfig.json`, and Expansion
trading zones (violet) from `<mission>/expansion/traderzones/`. Nothing merges
them — they are separate files, separate prices and separate NPCs, and zsm
edits each on its own terms.

What this module adds is the one thing a map can answer for both at once:
**where they sit in the same place**. A TraderPlus NPC standing inside an
Expansion trading zone, or two zones whose circles overlap, is a spot where
both mods are live at once — which is worth knowing before wondering why a
price or a safe-zone rule is not the one that was configured.

Deliberately *not* here: a verdict on whether running both works. That is the
mods' business and neither one's documentation covers the other, so the editor
reports the measured overlap and leaves the decision to the admin.

Everything is a plain function over the payload dicts the three states already
serialize, so it is testable without a mission folder.
"""

from __future__ import annotations

from math import hypot
from typing import Any

# A long list is a wall of text in a sidebar panel; the count is reported in
# full either way.
MAX_REPORTED = 50


def _distance(a: dict[str, Any], b: dict[str, Any]) -> float:
    """Metres between two entries on the ground plane (y is ignored)."""
    return hypot(
        float(a.get("game_x", 0.0)) - float(b.get("game_x", 0.0)),
        float(a.get("game_z", 0.0)) - float(b.get("game_z", 0.0)),
    )


def find_overlaps(
    traders: list[dict], safezones: list[dict], exp_zones: list[dict]
) -> list[dict]:
    """Every TraderPlus entry that shares ground with an Expansion zone.

    A trader is a point, so it overlaps when it is *inside* the zone; a
    TraderPlus safe zone is a circle, so it overlaps when the two circles
    touch. Sorted closest-first — the tightest overlap is the one to look at.
    """
    found: list[dict] = []
    for zone in exp_zones:
        radius = float(zone.get("radius", 0.0) or 0.0)
        zone_name = zone.get("name") or zone.get("id") or "trader zone"
        for trader in traders:
            distance = _distance(trader, zone)
            if distance > radius:
                continue
            found.append(
                {
                    "kind": "trader",
                    "expansion_zone": zone_name,
                    "traderplus": trader.get("given_name")
                    or trader.get("name")
                    or f"trader #{trader.get('index', 0)}",
                    "distance": round(distance),
                    "radius": round(radius),
                }
            )
        for safezone in safezones:
            reach = radius + float(safezone.get("radius", 0.0) or 0.0)
            distance = _distance(safezone, zone)
            if distance > reach:
                continue
            found.append(
                {
                    "kind": "safezone",
                    "expansion_zone": zone_name,
                    "traderplus": safezone.get("name") or "TraderPlus safe zone",
                    "distance": round(distance),
                    "radius": round(reach),
                }
            )
    found.sort(key=lambda entry: entry["distance"])
    return found


def analyze(
    traders: list[dict], safezones: list[dict], exp_zones: list[dict]
) -> dict[str, Any]:
    """The `/trader-systems` payload: what each side has, and where they meet."""
    overlaps = find_overlaps(traders, safezones, exp_zones)
    return {
        "traderplus": bool(traders or safezones),
        "expansion": bool(exp_zones),
        "both": bool((traders or safezones) and exp_zones),
        "traderplus_traders": len(traders),
        "traderplus_safezones": len(safezones),
        "expansion_zones": len(exp_zones),
        "overlap_count": len(overlaps),
        "overlaps": overlaps[:MAX_REPORTED],
    }
