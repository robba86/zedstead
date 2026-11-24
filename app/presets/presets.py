"""Server presets — data structures, labels, and built-in preset definitions."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from enum import Enum
from typing import Any



# ---------------------------------------------------------------------------
# Label system
# ---------------------------------------------------------------------------

class PresetLabel(str, Enum):
    """Human-friendly label for a parameter value."""
    NONE = "None"
    LOW = "Low"
    NORMAL = "Normal"
    BOOSTED = "Boosted"
    HIGH = "High"
    INSANE = "Insane"


@dataclass
class ParameterScale:
    """Maps labels → numeric values for a specific parameter.

    Each parameter has its own scale, e.g.::

        ParameterScale(
            name="ZombieMaxCount",
            mapping={
                PresetLabel.NONE: 0,
                PresetLabel.LOW: 200,
                PresetLabel.NORMAL: 500,
                PresetLabel.BOOSTED: 800,
                PresetLabel.HIGH: 1200,
                PresetLabel.INSANE: 2000,
            },
        )
    """
    name: str
    mapping: dict[PresetLabel, float | int]


def label_for_value(scale: ParameterScale, value: float | int) -> PresetLabel | None:
    """Reverse-lookup: find the closest label for a numeric value.

    Returns None if the scale is empty.
    """
    if not scale.mapping:
        return None

    best_label: PresetLabel | None = None
    best_distance = float("inf")

    for lbl, mapped_value in scale.mapping.items():
        distance = abs(float(mapped_value) - float(value))
        if distance < best_distance:
            best_distance = distance
            best_label = lbl

    return best_label


# ---------------------------------------------------------------------------
# Parameter scale registry
# ---------------------------------------------------------------------------

PARAMETER_SCALES: dict[str, ParameterScale] = {
    # globals.xml — zombie/animal counts
    "ZombieMaxCount": ParameterScale("ZombieMaxCount", {
        PresetLabel.NONE: 0, PresetLabel.LOW: 200, PresetLabel.NORMAL: 500,
        PresetLabel.BOOSTED: 800, PresetLabel.HIGH: 1200, PresetLabel.INSANE: 2000,
    }),
    "AnimalMaxCount": ParameterScale("AnimalMaxCount", {
        PresetLabel.NONE: 0, PresetLabel.LOW: 100, PresetLabel.NORMAL: 200,
        PresetLabel.BOOSTED: 400, PresetLabel.HIGH: 600, PresetLabel.INSANE: 1000,
    }),
    # events.xml — spawn multipliers (as float multipliers)
    "infected_nominal": ParameterScale("infected_nominal", {
        PresetLabel.NONE: 0.0, PresetLabel.LOW: 0.5, PresetLabel.NORMAL: 1.0,
        PresetLabel.BOOSTED: 1.5, PresetLabel.HIGH: 2.0, PresetLabel.INSANE: 3.0,
    }),
    "wildlife_nominal": ParameterScale("wildlife_nominal", {
        PresetLabel.NONE: 0.0, PresetLabel.LOW: 0.5, PresetLabel.NORMAL: 1.0,
        PresetLabel.BOOSTED: 1.5, PresetLabel.HIGH: 2.0, PresetLabel.INSANE: 3.0,
    }),

    # types.xml — loot multipliers (as float multipliers)
    "loot_nominal": ParameterScale("loot_nominal", {
        PresetLabel.LOW: 0.5, PresetLabel.NORMAL: 1.0,
        PresetLabel.BOOSTED: 1.5, PresetLabel.HIGH: 2.0, PresetLabel.INSANE: 3.0,
    }),
    "loot_lifetime": ParameterScale("loot_lifetime", {
        PresetLabel.LOW: 0.5, PresetLabel.NORMAL: 1.0,
        PresetLabel.BOOSTED: 1.5, PresetLabel.HIGH: 2.0, PresetLabel.INSANE: 3.0,
    }),
}


# ---------------------------------------------------------------------------
# Layer dataclasses — what each preset can control
# ---------------------------------------------------------------------------

@dataclass
class ServerCfgLayer:
    """Values to set in serverDZ.cfg."""
    maxPlayers: int | None = None
    # 0 off, 1 on, 2 only in a vehicle (DayZ 1.30). A write takes the old
    # disable3rdPerson out of the file (servercfg.REPLACED_CFG_KEYS).
    thirdPersonView: int | None = None
    disableCrosshair: int | None = None
    enableCfgGameplayFile: int | None = None
    respawnTime: int | None = None


@dataclass
class GameplayJsonLayer:
    """Sections to merge into cfggameplay.json."""
    PlayerData: dict[str, Any] | None = None
    WorldsData: dict[str, Any] | None = None
    GeneralData: dict[str, Any] | None = None
    UIData: dict[str, Any] | None = None
    MapData: dict[str, Any] | None = None


@dataclass
class EventsLayer:
    """Multipliers for events.xml categories."""
    infected_nominal: float | None = None   # multiplier for infected event nominals
    wildlife_nominal: float | None = None   # multiplier for wildlife event nominals


@dataclass
class TypesLayer:
    """Multipliers for types.xml loot values."""
    loot_nominal: float | None = None       # multiplier for item nominal counts
    loot_min: float | None = None           # multiplier for item min counts
    loot_lifetime: float | None = None      # multiplier for item lifetimes
    loot_restock: float | None = None       # multiplier for item restock times


@dataclass
class GlobalsLayer:
    """Values to set in globals.xml."""
    values: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Preset definition
# ---------------------------------------------------------------------------

@dataclass
class PresetDefinition:
    """A complete server preset."""
    name: str
    description: str
    icon: str = ""
    server_cfg: ServerCfgLayer = field(default_factory=ServerCfgLayer)
    gameplay_json: GameplayJsonLayer = field(default_factory=GameplayJsonLayer)
    events: EventsLayer = field(default_factory=EventsLayer)
    types: TypesLayer = field(default_factory=TypesLayer)
    globals: GlobalsLayer = field(default_factory=GlobalsLayer)
    recommended_mods: list[str] = field(default_factory=list)
    is_builtin: bool = True


# ---------------------------------------------------------------------------
# Map type detection
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Built-in presets
# ---------------------------------------------------------------------------

# Shared with the Quick Tweaks cards: loot is scaled in db/types.xml only,
# never in the mods' custom/ files, which every merge rebuilds.
STOCK_ITEMS_ONLY = "Stock items only — mod items are not changed."

BUILTIN_PRESETS: dict[str, PresetDefinition] = {
    "beginner_friendly": PresetDefinition(
        name="Beginner Friendly",
        description=(
            "Easy survival for new players: fewer zombies, more loot, longer item lifetimes, "
            f"3rd person enabled. {STOCK_ITEMS_ONLY}"
        ),
        icon="[green]★[/green]",
        server_cfg=ServerCfgLayer(
            maxPlayers=40,
            thirdPersonView=1,
            disableCrosshair=0,
        ),
        gameplay_json=GameplayJsonLayer(
            PlayerData={
                "sprintStaminaModifierErc": 0.8,
                "sprintStaminaModifierCro": 0.7,
                "staminaDepletionSpeed": 8.0,
                "healthDepletionSpeed": 8.0,
                "shockDepletionSpeed": 8.0,
            },
        ),
        events=EventsLayer(
            infected_nominal=0.5,
            wildlife_nominal=1.5,
        ),
        types=TypesLayer(
            loot_nominal=2.0,
            loot_min=2.0,
            loot_lifetime=1.5,
            loot_restock=0.7,
        ),
        globals=GlobalsLayer(values={
            "ZombieMaxCount": "200",
            "AnimalMaxCount": "400",
            "CleanupLifetimeDeadPlayer": "3600",
            "CleanupLifetimeDeadInfected": "300",
        }),
    ),
    # Nothing of its own: every value it writes comes from STOCK_PRESET_VALUES.
    "vanilla_balanced": PresetDefinition(
        name="Vanilla Balanced",
        description=(
            "Stock DayZ values for everything a preset changes: 60 players, "
            "third person on, the stock zombie cap. Loot and spawn counts go back to "
            "what they were before the last preset."
        ),
        icon="[yellow]◆[/yellow]",
    ),
    "hardcore_survival": PresetDefinition(
        name="Hardcore Survival",
        description=(
            "Punishing survival: 1st person only, scarce loot, many zombies, harsh environment. "
            f"{STOCK_ITEMS_ONLY}"
        ),
        icon="[red]☠[/red]",
        server_cfg=ServerCfgLayer(
            maxPlayers=50,
            thirdPersonView=0,
            disableCrosshair=1,
        ),
        gameplay_json=GameplayJsonLayer(
            PlayerData={
                "disablePersonalLight": True,
                "sprintStaminaModifierErc": 1.2,
                "sprintStaminaModifierCro": 1.1,
                "staminaDepletionSpeed": 13.0,
                "healthDepletionSpeed": 12.0,
                "shockDepletionSpeed": 12.0,
            },
            GeneralData={
                "disableRespawnDialog": True,
            },
        ),
        events=EventsLayer(
            infected_nominal=1.5,
            wildlife_nominal=0.8,
        ),
        types=TypesLayer(
            loot_nominal=0.5,
            loot_min=0.5,
            loot_lifetime=0.7,
            loot_restock=1.5,
        ),
        globals=GlobalsLayer(values={
            "ZombieMaxCount": "1200",
            "AnimalMaxCount": "150",
            "CleanupLifetimeDeadPlayer": "1800",
            "CleanupLifetimeDeadInfected": "120",
            "TimePenalty": "600",
        }),
    ),
    "pvp_competitive": PresetDefinition(
        name="PvP Competitive",
        description=(
            "Fast-paced PvP: high loot, few zombies, 3rd person allowed, short restocking. "
            f"{STOCK_ITEMS_ONLY}"
        ),
        icon="[cyan]⚔[/cyan]",
        server_cfg=ServerCfgLayer(
            maxPlayers=60,
            thirdPersonView=1,
            disableCrosshair=0,
            respawnTime=5,
        ),
        gameplay_json=GameplayJsonLayer(
            PlayerData={
                "sprintStaminaModifierErc": 0.7,
                "sprintStaminaModifierCro": 0.6,
                "staminaDepletionSpeed": 6.0,
                "healthDepletionSpeed": 6.0,
                "shockDepletionSpeed": 6.0,
            },
            GeneralData={
                "disableBaseDamage": True,
                "disableContainerDamage": True,
            },
        ),
        events=EventsLayer(
            infected_nominal=0.3,
            wildlife_nominal=0.5,
        ),
        types=TypesLayer(
            loot_nominal=2.5,
            loot_min=2.5,
            loot_lifetime=0.8,
            loot_restock=0.4,
        ),
        globals=GlobalsLayer(values={
            "ZombieMaxCount": "100",
            "AnimalMaxCount": "100",
            "CleanupLifetimeDeadPlayer": "600",
            "CleanupLifetimeDeadInfected": "60",
            "TimePenalty": "300",
        }),
        recommended_mods=[
            "@SNAFU Weapons",
            "@SFE-Special Force Weapon",
            "@FOX WEAPONS 2.0",
            "@MMG - Mightys Military Gear",
        ],
    ),
}


# ---------------------------------------------------------------------------
# Stock values — what a built-in preset writes for a key it doesn't name
# ---------------------------------------------------------------------------
#
# Built-in presets are absolute (plans/50 C, decision 1): each writes every key
# that any built-in preset manages, so applying one after another leaves
# nothing of the first behind. The managed set is exactly the union of the keys
# the built-ins name; tests/test_preset_absolute.py fails if a preset gains a
# key without a stock value here, or if a value here stops matching the source.
#
# Sources (identical on all three stock maps, checked in 2026 Q3):
#   serverDZ.cfg      dayz-docs/wiki/pages/server-configuration.wiki
#   globals.xml       dayz-docs/vanilla/*/db/globals.xml
#   cfggameplay.json  dayz-docs/vanilla/*/cfggameplay.json
#                     (drowning speeds are "per second while drowning", stock
#                     10.0: dayz-docs/wiki/pages/gameplay-settings.wiki)
#   types / events    multipliers, so stock is 1.0
STOCK_PRESET_VALUES = PresetDefinition(
    name="Stock DayZ",
    description="The stock value of every key a built-in preset manages.",
    server_cfg=ServerCfgLayer(
        maxPlayers=60,
        thirdPersonView=1,
        disableCrosshair=0,
        respawnTime=5,
    ),
    gameplay_json=GameplayJsonLayer(
        PlayerData={
            "disablePersonalLight": False,
            "sprintStaminaModifierErc": 1.0,
            "sprintStaminaModifierCro": 1.0,
            "staminaDepletionSpeed": 10.0,
            "healthDepletionSpeed": 10.0,
            "shockDepletionSpeed": 10.0,
        },
        GeneralData={
            "disableRespawnDialog": False,
            "disableBaseDamage": False,
            "disableContainerDamage": False,
        },
    ),
    events=EventsLayer(infected_nominal=1.0, wildlife_nominal=1.0),
    types=TypesLayer(loot_nominal=1.0, loot_min=1.0, loot_lifetime=1.0, loot_restock=1.0),
    globals=GlobalsLayer(values={
        "ZombieMaxCount": "1000",
        "AnimalMaxCount": "200",
        "CleanupLifetimeDeadPlayer": "3600",
        "CleanupLifetimeDeadInfected": "330",
        "TimePenalty": "20",
    }),
)


def _own_or_stock(layer: Any, stock: Any) -> Any:
    """A flat layer with every field the preset left ``None`` taken from stock."""
    return type(layer)(**{
        f.name: getattr(stock, f.name) if getattr(layer, f.name) is None else getattr(layer, f.name)
        for f in dataclasses.fields(layer)
    })


def absolute(preset: PresetDefinition) -> PresetDefinition:
    """*preset* with every managed key filled in: its own value, else stock.

    Built-in presets only. A custom preset is a snapshot of one server and
    applies as it is (the keys it holds, no multipliers). The definitions above
    stay readable as "what this preset is about"; this is what gets written.
    """
    if not preset.is_builtin:
        return preset
    stock = STOCK_PRESET_VALUES
    gameplay = GameplayJsonLayer(**{
        f.name: ({**(getattr(stock.gameplay_json, f.name) or {}), **(getattr(preset.gameplay_json, f.name) or {})} or None)
        for f in dataclasses.fields(GameplayJsonLayer)
    })
    return dataclasses.replace(
        preset,
        server_cfg=_own_or_stock(preset.server_cfg, stock.server_cfg),
        gameplay_json=gameplay,
        events=_own_or_stock(preset.events, stock.events),
        types=_own_or_stock(preset.types, stock.types),
        globals=GlobalsLayer(values={**stock.globals.values, **preset.globals.values}),
    )
