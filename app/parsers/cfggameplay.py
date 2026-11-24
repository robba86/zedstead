"""cfggameplay.json reader/writer — manages gameplay configuration."""

from __future__ import annotations

import json
import math
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.shared.json_utils import _auto_snapshot, _dump_float, atomic_write_text
from app.xml.xml_utils import backup_file, restore_file


# ---------------------------------------------------------------------------
# DayZ default cfggameplay.json structure
# ---------------------------------------------------------------------------

DEFAULT_CFGGAMEPLAY: dict[str, Any] = {
    "version": 123,
    "GeneralData": {
        "disableBaseDamage": False,
        "disableContainerDamage": False,
        "disableRespawnDialog": False,
        "disableRespawnInUnconsciousness": False,
    },
    "PlayerData": {
        "disablePersonalLight": False,
        "StaminaData": {
            "sprintStaminaModifierErc": 1.0,
            "sprintStaminaModifierCro": 1.0,
            "staminaWeightLimitThreshold": 6000.0,
            "staminaMax": 100.0,
            "staminaKgToStaminaPercentPenalty": 1.75,
            "staminaMinCap": 5.0,
            "sprintSwimmingStaminaModifier": 1.0,
            "meleeStaminaModifier": 1.0,
            "obstacleTraversalStaminaModifier": 1.0,
            "holdBreathStaminaModifier": 1.0,
            "sprintLadderStaminaModifier": 1.0,
        },
        "ShockHandlingData": {
            "shockRefillSpeedConscious": 5.0,
            "shockRefillSpeedUnconscious": 1.0,
            "allowRefillSpeedModifier": True,
        },
        "MovementData": {
            "timeToStrafeJog": 0.1,
            "rotationSpeedJog": 0.15,
            "timeToSprint": 0.45,
            "timeToStrafeSprint": 0.3,
            "rotationSpeedSprint": 0.15,
            "allowStaminaAffectInertia": True,
        },
        "DrowningData": {
            "staminaDepletionSpeed": 10.0,
            "healthDepletionSpeed": 10.0,
            "shockDepletionSpeed": 10.0,
        },
        "WeaponObstructionData": {
            "staticMode": 1,
            "dynamicMode": 1,
        },
    },
    "WorldsData": {
        "lightingConfig": 0,
        "environmentMinTemps": [-3, -2, 0, 4, 9, 14, 18, 17, 13, 11, 9, 0],
        "environmentMaxTemps": [3, 5, 7, 14, 19, 24, 26, 25, 18, 14, 10, 5],
        "wetnessWeightModifiers": [1.0, 1.0, 1.33, 1.66, 2.0],
    },
    "BaseBuildingData": {
        "HologramData": {
            "disableIsCollidingBBoxCheck": False,
            "disableIsCollidingPlayerCheck": False,
            "disableIsClippingRoofCheck": False,
            "disableIsBaseViableCheck": False,
            "disableIsCollidingGPlotCheck": False,
            "disableIsPlacementPermittedCheck": False,
            "disableHeightPlacementCheck": False,
            "disableIsUnderwaterCheck": False,
            "disableIsInTerrainCheck": False,
            "disableIsCollidingAngleCheck": False,
            "disableColdAreaBuildingCheck": True,
            "disallowedTypesInUnderground": ["FenceKit", "TerritoryFlagKit", "WatchtowerKit"],
        },
        "ConstructionData": {
            "disablePerformRoofCheck": False,
            "disableIsCollidingCheck": False,
            "disableDistanceCheck": False,
            "disableItemFalling": False,
            "disableDropAttachmentsOnFall": False,
            "disableRefundMaterialsOnFall": False,
        },
    },
    "UIData": {
        "use3DMap": False,
        "HitIndicationData": {
            "hitDirectionOverrideEnabled": False,
            "hitDirectionBehaviour": 1,
            "hitDirectionStyle": 0,
            "hitDirectionIndicatorColorStr": "0xffbb0a1e",
            "hitDirectionMaxDuration": 2.0,
            "hitDirectionBreakPointRelative": 0.2,
            "hitDirectionScatter": 10.0,
            "hitIndicationPostProcessEnabled": True,
        },
    },
    "MapData": {
        "ignoreMapOwnership": False,
        "ignoreNavItemsOwnership": False,
        "displayPlayerPosition": False,
        "displayNavInfo": True,
    },
    "VehicleData": {
        "boatDecayMultiplier": 1,
        "canDetachAttachedCarWheels": True,
        "canDamageAttachedCarWheels": True,
        "canDetachAttachedBikeWheels": True,
        "canDamageAttachedBikeWheels": True,
    },
}


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def get_cfggameplay_path(mission_dir: Path) -> Path:
    """Return the cfggameplay.json path inside a mission directory."""
    return mission_dir / "cfggameplay.json"


# ---------------------------------------------------------------------------
# Read / write
# ---------------------------------------------------------------------------

class GameplayFileError(ValueError):
    """cfggameplay.json exists but can't be read, so nothing may be written over it."""

    def __init__(self, path: Path, problem: str) -> None:
        super().__init__(
            f"{path.name} can't be read ({problem}). Fix it on the Gameplay editor's Raw tab; "
            "Zedstead does not write over a file it cannot read."
        )


def _read_text(path: Path) -> str:
    """The file's text exactly, line endings and a byte order mark included."""
    try:
        with open(path, encoding="utf-8", newline="") as f:
            return f.read()
    except UnicodeDecodeError:
        raise GameplayFileError(path, "it is not saved as UTF-8") from None
    except OSError as exc:
        raise GameplayFileError(path, exc.strerror or str(exc)) from None


def _parse(path: Path, text: str) -> dict[str, Any]:
    try:
        data = json.loads(text.lstrip("﻿"))
    except json.JSONDecodeError as exc:
        raise GameplayFileError(path, f"line {exc.lineno}, column {exc.colno}: {exc.msg}") from None
    if not isinstance(data, dict):
        raise GameplayFileError(path, "it is not a JSON object")
    return data


def read_cfggameplay(path: Path) -> dict[str, Any]:
    """Read cfggameplay.json from disk.

    Bohemia's defaults only when the file is missing. A file that is there but
    can't be read (not JSON, not UTF-8, not an object) raises
    :class:`GameplayFileError`: returning the defaults made the editor show
    them, and its next save or a preset wrote them over the admin's file.
    """
    if not path.is_file():
        return json.loads(json.dumps(DEFAULT_CFGGAMEPLAY))  # deep copy
    return _parse(path, _read_text(path))


def _whole_file(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=4, ensure_ascii=False) + "\n"


def write_cfggameplay(path: Path, data: dict[str, Any]) -> bool:
    """Write *data* to cfggameplay.json, changing only the values that differ.

    An existing file is patched as text (:func:`patch_text`): a value that
    did not change keeps its exact text, and so do the file's tabs, inline
    lists, line endings and key order. Bohemia's stock files are laid out by
    hand, and the full rewrite this replaced made every save a whole-file
    diff. A file with nothing to change is not written at all. A missing
    file, or a change a patch cannot make (a key added or removed), is written
    whole. Returns whether the file was written.
    """
    if path.is_file():
        text = _read_text(path)
        current = _parse(path, text)
        if same_value(current, data):
            return False
        new_text = patch_text(text, current, data)
        if new_text is None:
            new_text = _whole_file(data)
        _auto_snapshot(path)
    else:
        new_text = _whole_file(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, new_text, newline="")
    return True


# ---------------------------------------------------------------------------
# Text patching: a save rewrites only the text of the values it changes
# ---------------------------------------------------------------------------

_WS = re.compile(r"[ \t\n\r]*")
_DECODER = json.JSONDecoder()


def same_value(a: Any, b: Any) -> bool:
    """Equal as DayZ reads them: ``1`` and ``1.0`` are one number, ``1`` and ``true`` are not."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same_value(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(same_value(x, y) for x, y in zip(a, b))
    return type(a) is type(b) and a == b


def _scan(text: str, pos: int, path: tuple, spans: dict[tuple, tuple[int, int]]) -> int:
    """Record where the value at *pos*, and everything inside it, is in *text*."""
    pos = _WS.match(text, pos).end()
    start = pos
    opener = text[pos:pos + 1]
    if opener in ("{", "["):
        close = "}" if opener == "{" else "]"
        pos = _WS.match(text, pos + 1).end()
        index = 0
        while text[pos:pos + 1] != close:
            if opener == "{":
                if text[pos:pos + 1] != '"':
                    raise ValueError("expected a key")
                key, pos = json.decoder.scanstring(text, pos + 1)
                pos = _WS.match(text, pos).end()
                if text[pos:pos + 1] != ":":
                    raise ValueError("expected ':'")
                pos = _scan(text, pos + 1, path + (key,), spans)
            else:
                pos = _scan(text, pos, path + (index,), spans)
                index += 1
            pos = _WS.match(text, pos).end()
            if text[pos:pos + 1] == ",":
                pos = _WS.match(text, pos + 1).end()
            elif text[pos:pos + 1] != close:
                raise ValueError(f"expected ',' or '{close}'")
        pos += 1
    else:
        _value, pos = _DECODER.raw_decode(text, pos)
    spans[path] = (start, pos)
    return pos


def _number_text(new: int | float, old_text: str | None) -> str:
    """A number in the file's style: whole where the file wrote this value whole."""
    if isinstance(new, int):
        return str(new)
    whole_before = old_text is not None and not any(c in old_text for c in ".eE")
    if new == int(new) and whole_before:
        return str(int(new))
    return _dump_float(new)  # a SourceFloat from `gameplay_value` writes the text typed


def _literal(new: Any, old: Any, old_text: str | None,
             items: list[str] | None = None, layout: tuple[str, str, str] | None = None) -> str:
    """*new* as the file writes it, in the style of *old* (whose text is *old_text*)."""
    if isinstance(new, bool):
        if isinstance(old, int) and not isinstance(old, bool):
            return "1" if new else "0"  # this file writes the switch as 0/1
        return "true" if new else "false"
    if isinstance(new, (int, float)):
        return _number_text(new, old_text)
    if isinstance(new, list):
        old_list = old if isinstance(old, list) else []
        items = items or []
        parts = []
        for i, item in enumerate(new):
            if i < len(old_list) and same_value(item, old_list[i]):
                parts.append(items[i])  # untouched: exactly as the file had it
                continue
            j = min(i, len(old_list) - 1)
            parts.append(_literal(item, old_list[j] if j >= 0 else None, items[j] if j >= 0 else None))
        if not parts:
            return "[]"
        before, sep, after = layout or ("", ", ", "")
        return "[" + before + sep.join(parts) + after + "]"
    return json.dumps(new, ensure_ascii=False)


def _list_layout(text: str, spans: dict, path: tuple, length: int) -> tuple[str, str, str]:
    """The space inside a list's brackets and between its items, as the file has it."""
    if length == 0:
        return ("", ", ", "")
    start, end = spans[path]
    first, last = spans[path + (0,)], spans[path + (length - 1,)]
    sep = text[first[1]:spans[path + (1,)][0]] if length > 1 else ", "
    return (text[start + 1:first[0]], sep, text[last[1]:end - 1])


def _changed_leaves(old: Any, new: Any, path: tuple, out: list[tuple]) -> bool:
    """Collect the paths of the values that changed; False when a key was added or removed."""
    if isinstance(old, dict) or isinstance(new, dict):
        if not (isinstance(old, dict) and isinstance(new, dict)) or old.keys() != new.keys():
            return False
        return all(_changed_leaves(old[k], new[k], path + (k,), out) for k in new)
    if not same_value(old, new):
        out.append(path)
    return True


def patch_text(text: str, current: dict[str, Any], data: dict[str, Any]) -> str | None:
    """*text* (which parses to *current*) with every value *data* changes rewritten in place.

    None when *data* adds or removes a key, which only a whole write can do.
    """
    changed: list[tuple] = []
    if not _changed_leaves(current, data, (), changed):
        return None
    spans: dict[tuple, tuple[int, int]] = {}
    try:
        _scan(text, 1 if text.startswith("﻿") else 0, (), spans)
    except ValueError:
        return None
    edits = []
    for path in changed:
        old, new = current, data
        for part in path:
            old, new = old[part], new[part]
        start, end = spans[path]
        if isinstance(old, list) and isinstance(new, list):
            items = [text[slice(*spans[path + (i,)])] for i in range(len(old))]
            layout = _list_layout(text, spans, path, len(old))
            literal = _literal(new, old, text[start:end], items, layout)
        else:
            literal = _literal(new, old, text[start:end])
        edits.append((start, end, literal))
    for start, end, literal in sorted(edits, reverse=True):
        text = text[:start] + literal + text[end:]
    return text


# ---------------------------------------------------------------------------
# Backup / restore
# ---------------------------------------------------------------------------

def backup_cfggameplay(path: Path) -> Path:
    """Create .backup copy. Returns backup path."""
    return backup_file(path)


def restore_cfggameplay(path: Path) -> bool:
    """Restore from .backup. Returns True if restored."""
    return restore_file(path)


# ---------------------------------------------------------------------------
# Deep merge
# ---------------------------------------------------------------------------

def merge_sections(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge overrides into base, only touching preset-controlled keys.

    Nested dicts are merged recursively. Non-dict values are overwritten.
    Keys not in overrides are left unchanged.
    """
    result = dict(base)
    for key, value in overrides.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = merge_sections(result[key], value)
        else:
            result[key] = value
    return result


# Field help text (shared with the web UI, which strips the Rich
# console markup; see app/web/editors/_help.py).
FIELD_DESCRIPTIONS: dict[str, str] = {
    "disableBaseDamage": "Disable damage/destruction on all base-building structures (walls, fences, gates, watchtowers, ramps, stairs). Tents/barrels are governed by Disable container damage below.",
    "disableContainerDamage": "Disable damage/destruction of tents, barrels, crates, seachests.",
    "disableRespawnDialog": "Disable the post-death respawn-type chooser (random vs spawn-select). New characters always spawn as random.",
    "disableRespawnInUnconsciousness": "Disable the Respawn button in the pause menu while the player is unconscious \u2014 they must wake or die.",
    "sprintStaminaModifierErc": "Stamina drain multiplier when sprinting upright. Default: 1.0.",
    "sprintStaminaModifierCro": "Stamina drain multiplier when sprinting crouched. Default: 1.0.",
    "staminaWeightLimitThreshold": "Stamina points that do not count towards the stamina deduction for the weight carried, divided by 1000 (Bohemia's wiki); not grams. Default: 6000.",
    "staminaMax": "Maximum stamina pool size. Default: 100. Bohemia warns: setting to 0 may produce unexpected results.",
    "staminaKgToStaminaPercentPenalty": "Multiplier on the stamina-cap reduction per kg above the weight threshold. Default: 1.75. Set to 0 to disable weight-based stamina penalties entirely.",
    "staminaMinCap": "Floor for the dynamic stamina cap \u2014 under heavy load the cap shrinks but never below this. Default: 5. Bohemia warns: do not set to 0.",
    "sprintSwimmingStaminaModifier": "Stamina drain multiplier when sprint swimming. Default: 1.0.",
    "meleeStaminaModifier": "Stamina drain multiplier for melee attacks. Default: 1.0.",
    "obstacleTraversalStaminaModifier": "Stamina drain multiplier for climbing/vaulting. Default: 1.0.",
    "holdBreathStaminaModifier": "Stamina drain multiplier when holding breath (aiming). Default: 1.0.",
    "sprintLadderStaminaModifier": "Stamina drain multiplier when sprinting on ladders. Default: 1.0.",
    "shockRefillSpeedConscious": "Shock recovery rate while conscious (points/sec). Default: 5.0.",
    "shockRefillSpeedUnconscious": "Shock recovery rate while unconscious (points/sec). Default: 1.0.",
    "allowRefillSpeedModifier": "Allow item/state modifiers (e.g. medical items, in-shock multiplier) to affect the shock refill speed.",
    "timeToStrafeJog": "Time in seconds to reach full strafe speed when jogging. Default: 0.1. Lowest value: 0.01.",
    "rotationSpeedJog": "Rotation speed of the character while jogging. Bohemia's wiki gives 0.15 as the default; the three stock missions ship 0.3 in DayZ 1.29 and 0.15 from 1.30. Lowest value: 0.01.",
    "timeToSprint": "Time in seconds to reach full sprint speed. Default: 0.45. Lowest value: 0.01.",
    "timeToStrafeSprint": "Time in seconds to reach full strafe speed when sprinting. Default: 0.3. Lowest value: 0.01.",
    "rotationSpeedSprint": "Rotation responsiveness while sprinting (lower = snappier turns). Default: 0.15. Lowest value: 0.01.",
    "allowStaminaAffectInertia": "When on, low stamina makes the character feel heavier in turns and direction changes.",
    "staminaDepletionSpeed": "Stamina drain while drowning (points/sec). Default: 10.0.",
    "healthDepletionSpeed": "Health drain while drowning (points/sec). Default: 10.0.",
    "shockDepletionSpeed": "Shock drain while drowning (points/sec). Default: 10.0.",
    "staticMode": "Weapon obstruction against walls/terrain. 0 = off (never obstruct), 1 = obstruct then auto-lift, 2 = always obstruct (no auto-lift). Default: 1.",
    "dynamicMode": "Weapon obstruction against vehicles, players, and animals. 0 = off, 1 = obstruct then auto-lift, 2 = always obstruct. Default: 1.",
    "disablePersonalLight": "Disable the omnipresent ambient halo around every player at night. Does NOT affect held flashlights/torches/headlamps. Off = pitch-black nights for unequipped players.",
    "lightingConfig": "Night-time lighting. 0 = bright night, 1 = dark night (Bohemia's wiki, whose default is 1); 2 = Sakhal-specific lighting (the wiki's serverDZ.cfg page). The stock files ship 0 on Chernarus and Livonia and 2 on Sakhal. Overrides serverDZ.cfg's lightingConfig while enableCfgGameplayFile is on.",
    "use3DMap": "Use the engine-rendered 3D terrain map instead of the default 2D image overlay. Default: off.",
    "hitDirectionOverrideEnabled": "Master gate for the hit-indicator override block. When off, all the hit-direction fields below fall back to engine defaults; when on, the values here apply.",
    "hitDirectionBehaviour": "Hit direction indicator behavior. 0 = disabled, 1 = static (vanilla), 2 = dynamic/moving (WIP). Default: 1.",
    "hitDirectionStyle": "Hit direction indicator visual style. 0 = splash (vanilla), 1 = spike, 2 = arrow. Default: 0.",
    "hitDirectionMaxDuration": "Maximum on-screen duration of the hit indicator in seconds. Actual duration is randomized between 0.6\u00d7 and 1.0\u00d7 this value. Default: 2.0.",
    "hitDirectionBreakPointRelative": "Fraction of the duration after which the indicator starts fading out, 0.0 to 1.0: 0 fades from the start, 1 never fades. Default: 0.2.",
    "hitDirectionScatter": "Random angular jitter (degrees) added to the indicator's direction. Default: 10.0.",
    "hitIndicationPostProcessEnabled": "Enable the legacy red-flash hit screen effect alongside the directional indicator.",
    "ignoreMapOwnership": "Players can open the map (M) without holding a paper map item. Vanilla default: off.",
    "ignoreNavItemsOwnership": "Players see compass/GPS info on the map without holding those items.",
    "displayPlayerPosition": "Show a red 'you are here' marker on the map (with facing direction). iZurvive-cheat-style live position. Default: off.",
    "displayNavInfo": "Show GPS/compass info in the map legend (when player has those items). Vanilla default: on. Off hides the legend entries even when items are in inventory.",
    "boatDecayMultiplier": "Multiplier on the base boat decay rate (governed by globals.xml CleanupLifetimeDeadVehicle / persistence rules). 1.0 = vanilla rate. 0 = disable decay; >1 = faster decay.",
    # Added in DayZ 1.30 (Experimental, 2026 Q3). Bohemia has not documented
    # them yet; the names and the true defaults are from the 1.30 server's own
    # cfggameplay.json (plans/66 Step 4).
    "canDetachAttachedCarWheels": "DayZ 1.30+. Players can take a wheel off a car. False keeps attached car wheels on. Bohemia's 1.30 default: on.",
    "canDamageAttachedCarWheels": "DayZ 1.30+. Wheels on a car take damage. False stops attached car wheels being damaged. Bohemia's 1.30 default: on.",
    "canDetachAttachedBikeWheels": "DayZ 1.30+. Players can take a wheel off a motorbike. False keeps attached motorbike wheels on. Bohemia's 1.30 default: on.",
    "canDamageAttachedBikeWheels": "DayZ 1.30+. Wheels on a motorbike take damage. False stops attached motorbike wheels being damaged. Bohemia's 1.30 default: on.",
    "disableIsCollidingBBoxCheck": "Allow placement preview to turn green when the ghost intersects another object's bounding box.",
    "disableIsCollidingPlayerCheck": "Allow placement preview to turn green when a player is standing in the spot.",
    "disableIsClippingRoofCheck": "Allow placement preview to turn green when the ghost clips into a roof above.",
    "disableIsBaseViableCheck": "Allow placement on dynamic/unstable surfaces \u2014 vehicles, tents, etc. that the engine normally rejects as 'not a viable base'.",
    "disableIsCollidingGPlotCheck": "Allow garden-plot placement on otherwise-incompatible surfaces (asphalt, concrete).",
    "disableIsCollidingAngleCheck": "Allow placement on steep slopes (skips the roll/pitch/yaw limit check).",
    "disableIsPlacementPermittedCheck": "Bypass the engine's general 'is this spot permitted?' check at placement preview.",
    "disableHeightPlacementCheck": "Allow placement at any height above terrain (skips the float-fortress prevention check).",
    "disableIsUnderwaterCheck": "Allow placement of items underwater.",
    "disableIsInTerrainCheck": "Allow placement when the ghost intersects terrain geometry.",
    "disableColdAreaBuildingCheck": "Allow garden plots to be placed on frozen ground. This is the name in Chernarus' and Livonia's stock files; Bohemia's wiki and Sakhal's stock file name the key disableColdAreaPlacementCheck. Zedstead keeps the name your file has.",
    "disableColdAreaPlacementCheck": "Allow garden plots to be placed on frozen ground (Bohemia's wiki; Sakhal's stock file uses this name, Chernarus' and Livonia's use disableColdAreaBuildingCheck). Zedstead keeps the name your file has.",
    "disablePerformRoofCheck": "Bypass the roof-clipping check at the construction step (after placement preview).",
    "disableIsCollidingCheck": "Bypass the collision check at the construction step (after placement preview). The placement preview's collision is governed by HologramData fields above.",
    "disableDistanceCheck": "Bypass the player-to-target distance check at construction time (allows building from further away).",
    # Added in DayZ 1.30 (stable, 2026 Q4). Bohemia has not documented them
    # yet; the names and the defaults are from the 1.30 stock files, and each
    # text says no more than the name and the default do.
    "disableItemFalling": "DayZ 1.30+. Construction setting; Bohemia has not documented it yet. Read from its name: true stops items falling off a built part. Bohemia's 1.30 default: false.",
    "disableDropAttachmentsOnFall": "DayZ 1.30+. Construction setting; Bohemia has not documented it yet. Read from its name: true stops a falling part dropping its attachments. Bohemia's 1.30 default: false.",
    "disableRefundMaterialsOnFall": "DayZ 1.30+. Construction setting; Bohemia has not documented it yet. Read from its name: true stops a falling part refunding its materials. Bohemia's 1.30 default: false.",
    "bunkerBroadcastEnabled": "DayZ 1.30+, in Badlands' stock file only. Turns the bunker broadcasts on or off; their schedule and codes are in the mission's cfgbunkerbroadcast.json. Bohemia's Badlands default: true.",
    "protectionCountStageOne": "DayZ 1.30+, in Badlands' stock file only (ExternalLockData). Bohemia has not documented it yet. Badlands' default: 3, the same value as in the mission's cfgexternallocks.json.",
    "protectionCountStageTwo": "DayZ 1.30+, in Badlands' stock file only (ExternalLockData). Bohemia has not documented it yet. Badlands' default: 1, the same value as in the mission's cfgexternallocks.json.",
    "protectionTime": "DayZ 1.30+, in Badlands' stock file only (ExternalLockData). Bohemia has not documented it or its unit yet. Badlands' default: 30, the same value as in the mission's cfgexternallocks.json.",
    "protectionResetTime": "DayZ 1.30+, in Badlands' stock file only (ExternalLockData). Bohemia has not documented it or its unit yet. Badlands' default: 3600, the same value as in the mission's cfgexternallocks.json.",
    "sandstormFrequency": "DayZ 1.30+, in Badlands' stock file only. How often sandstorms come; Bohemia has not documented the scale yet. Badlands' default: 1.0.",
    "hitDirectionIndicatorColorStr": "Colour of the hit indicator, written 0xAARRGGBB (alpha, red, green, blue in hex, any letter case). Default: 0xffbb0a1e (vanilla red).",
    "environmentMinTemps": "12 monthly minimum air temperatures (\u00b0C, Jan\u2013Dec). Used for ambient temperature simulation.",
    "environmentMaxTemps": "12 monthly maximum air temperatures (\u00b0C, Jan\u2013Dec). Used for ambient temperature simulation.",
    "wetnessWeightModifiers": "Carry-weight multipliers for the 5 clothing wetness states: dry, damp, wet, soaked, drenched. Vanilla: 1.0, 1.0, 1.33, 1.66, 2.0.",
    "disallowedTypesInUnderground": "Item types blocked from being built inside underground areas (Livonia bunker, Sakhal military complex \u2014 1.21+ feature). Vanilla: FenceKit, TerritoryFlagKit, WatchtowerKit.",
}


# ---------------------------------------------------------------------------
# What each value is, from Bohemia's wiki
# (dayz-docs/wiki/pages/gameplay-settings.wiki, its "Type" column)
# ---------------------------------------------------------------------------

#: Values the wiki types ``float``. A file may write one whole (the stock
#: ``"boatDecayMultiplier": 1``), and the editor must still take ``1.5``.
FLOAT_KEYS = frozenset({
    "boatDecayMultiplier",
    "sprintStaminaModifierErc", "sprintStaminaModifierCro", "staminaWeightLimitThreshold",
    "staminaMax", "staminaKgToStaminaPercentPenalty", "staminaMinCap",
    "sprintSwimmingStaminaModifier", "sprintLadderStaminaModifier", "meleeStaminaModifier",
    "obstacleTraversalStaminaModifier", "holdBreathStaminaModifier",
    "shockRefillSpeedConscious", "shockRefillSpeedUnconscious",
    "timeToStrafeJog", "rotationSpeedJog", "timeToSprint", "timeToStrafeSprint", "rotationSpeedSprint",
    "hitDirectionMaxDuration", "hitDirectionBreakPointRelative", "hitDirectionScatter",
    "staminaDepletionSpeed", "healthDepletionSpeed", "shockDepletionSpeed",
    # Not on the wiki yet: typed from Badlands' 1.30 stock file, which writes it 1.0.
    "sandstormFrequency",
})

#: Values the wiki types ``int``.
INT_KEYS = frozenset({
    "version", "lightingConfig", "hitDirectionBehaviour", "hitDirectionStyle", "staticMode", "dynamicMode",
    # Not on the wiki yet: Badlands' 1.30 stock file writes them whole.
    "protectionCountStageOne", "protectionCountStageTwo", "protectionTime", "protectionResetTime",
})

#: ``float array`` values and how many items each has: "12 values exactly"
#: for the temperatures (:410-418), one per wetness level for the weights (:62).
FLOAT_LIST_SIZES = {"environmentMinTemps": 12, "environmentMaxTemps": 12, "wetnessWeightModifiers": 5}

#: ``string array`` values.
STR_LIST_KEYS = frozenset({
    "spawnGearPresetFiles", "objectSpawnersArr", "playerRestrictedAreaFiles", "disallowedTypesInUnderground",
})

#: Values with only these settings (0, 1 or 2 for each: :315, :320, :430, :435).
CHOICES = {
    key: ("0", "1", "2")
    for key in ("hitDirectionBehaviour", "hitDirectionStyle", "staticMode", "dynamicMode")
}

#: Known values the form offers while it accepts a typed one. lightingConfig
#: 2 is Sakhal's, and a terrain mod can bring its own (Namalsk: 222, 223).
OPEN_CHOICES = {"lightingConfig": ("0", "1", "2")}

#: What each value of a CHOICES or OPEN_CHOICES field means, shown under it
#: in the web form's picker (the FIELD_DESCRIPTIONS above, split per value).
_OBSTRUCTION = {
    "0": "Off: the weapon is never obstructed.",
    "1": "Obstructed, then lifted by itself.",
    "2": "Always obstructed, never lifted by itself.",
}
CHOICE_HELP: dict[str, dict[str, str]] = {
    "hitDirectionBehaviour": {"0": "Disabled.", "1": "Static (vanilla).", "2": "Dynamic, moving (work in progress)."},
    "hitDirectionStyle": {"0": "Splash (vanilla).", "1": "Spike.", "2": "Arrow."},
    "staticMode": _OBSTRUCTION,
    "dynamicMode": _OBSTRUCTION,
    "lightingConfig": {"0": "Bright night.", "1": "Dark night.", "2": "Sakhal lighting."},
}

#: (lowest, highest) a value may take. The five movement times: "min possible
#: value 0.01" (:189-209). hitDirectionBreakPointRelative is a fraction of the
#: indicator's duration, 0.0 to 1.0 (:335).
# The unit of a cfggameplay.json number, shown in the web form's box beside
# the value (webui UnitInput.jsx). Taken from FIELD_DESCRIPTIONS above.
UNITS: dict[str, str] = {
    **dict.fromkeys(("timeToStrafeJog", "timeToSprint", "timeToStrafeSprint", "hitDirectionMaxDuration"), "seconds"),
    **dict.fromkeys((
        "shockRefillSpeedConscious", "shockRefillSpeedUnconscious",
        "staminaDepletionSpeed", "healthDepletionSpeed", "shockDepletionSpeed",
    ), "points/s"),
    "hitDirectionScatter": "deg",
}

RANGES: dict[str, tuple[float | None, float | None]] = {
    **{key: (0.01, None) for key in (
        "timeToStrafeJog", "rotationSpeedJog", "timeToSprint", "timeToStrafeSprint", "rotationSpeedSprint",
    )},
    "hitDirectionBreakPointRelative": (0.0, 1.0),
}

#: ``0xAARRGGBB``, in any letter case (wiki "ARGB Format", :439-450).
ARGB_COLOR = re.compile(r"0[xX][0-9a-fA-F]{8}")


def value_kind(key: str, current: Any) -> str:
    """``bool``, ``int``, ``float``, ``str``, ``float list``, ``str list`` or ``list``.

    From the wiki where it names the key, else from the value the file holds
    (a key DayZ added since, such as 1.30's wheel switches).
    """
    if key in FLOAT_LIST_SIZES:
        return "float list"
    if key in STR_LIST_KEYS:
        return "str list"
    if key in FLOAT_KEYS:
        return "float"
    if key in INT_KEYS:
        return "int"
    if isinstance(current, bool):
        return "bool"
    if isinstance(current, int):
        return "int"
    if isinstance(current, float):
        return "float"
    if isinstance(current, list):
        return "list"
    return "str"


def finite_number(raw: Any) -> float:
    """*raw* (a number or its text) as a finite float; ValueError otherwise."""
    if isinstance(raw, bool) or raw is None:
        raise ValueError("needs a number")
    text = str(raw).strip()
    if not text:
        raise ValueError("needs a number and can't be left empty")
    try:
        value = float(text)
    except ValueError:
        raise ValueError(f"{text!r} is not a number") from None
    if not math.isfinite(value):
        raise ValueError(f"{text!r} is not a number")
    return value


def _typed_float(raw: Any, number: float) -> float:
    """*number*, carrying the decimals typed (``1.50`` stays ``1.50``; ``1e-3`` is ``0.001``)."""
    from app.shared.json_utils import SourceFloat

    if not isinstance(raw, str):
        return number
    text = format(Decimal(raw.strip()), "f")
    return SourceFloat(text) if "." in text else number


def scalar_value(key: str, raw: Any, current: Any) -> Any:
    """The form's *raw* value for *key*, as cfggameplay.json holds it.

    Raises ValueError (the route's 400) for a value DayZ would not read: a
    number that is not one or not finite (``1e999``), a decimal where the
    wiki says int, a value outside :data:`RANGES` or :data:`CHOICES`, a
    colour that is not ``0xAARRGGBB``. A switch takes true/false, 1/0 or
    their text, nothing else.
    """
    kind = value_kind(key, current)
    if kind == "str" and current is None and isinstance(raw, bool):
        # A switch the file does not hold yet (1.30's wheel switches added to
        # a 1.29 file): the form's own true/false says what it is, and the
        # text "True" would be a string DayZ does not read as a switch.
        kind = "bool"
    if kind == "bool":
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        if text in ("true", "1"):
            return True
        if text in ("false", "0"):
            return False
        raise ValueError(f"{raw!r} is not on or off (true or false)")
    if kind in ("int", "float"):
        number = finite_number(raw)
        if kind == "int" and number != int(number):
            raise ValueError(f"{raw!r} is not a whole number, and DayZ reads this one as a whole number")
        if key in CHOICES and str(int(number)) not in CHOICES[key]:
            raise ValueError(f"{raw!r} is not one of {', '.join(CHOICES[key])}")
        low, high = RANGES.get(key, (None, None))
        if low is not None and number < low:
            raise ValueError(f"{raw!r} is below {low:g}, the lowest value DayZ takes here")
        if high is not None and number > high:
            raise ValueError(f"{raw!r} is above {high:g}, the highest value DayZ takes here")
        return int(number) if kind == "int" else _typed_float(raw, number)
    value = "" if raw is None else str(raw)
    if key == "hitDirectionIndicatorColorStr":
        value = value.strip()
        if not ARGB_COLOR.fullmatch(value):
            raise ValueError(f"{value!r} is not a colour written 0xAARRGGBB (for example 0xffbb0a1e)")
    return value
