"""Starter XML for the manual-link builder.

The Manual XML Links card used to hand the user an empty box. These are the
"Start from:" buttons above it — a commented, ready-to-edit skeleton per link
kind, plus one per free-form merge target whose shape is worth shipping.

Written in Python rather than as data files so the `types` starters can build
their hint comments from `types_xml.KNOWN_CATEGORIES` / `KNOWN_USAGES` /
`KNOWN_VALUES` / `FLAG_NAMES` — a starter that names a category the rest of the
app doesn't know is worse than no starter. Imports only those constants; the
kind/target resolution belongs to the router, which asks `xml_manual`.

Four rules every template keeps (`tests/test_xml_templates.py` enforces each):

1. Starts with an ``<?xml ...?>`` declaration, like `capture_event_positions`'s
   generated links.
2. **Wraps its content in the target file's root element.** Not cosmetic:
   `xml_manual._merge_free_form` appends *the children of* the fragment root,
   so an unwrapped single nested element would merge its grandchildren.
3. Already `xml_text.format_xml_text`-clean, so the Format button is a no-op on
   a fresh starter.
4. Explanatory comments live *inside* the root, never above it — and are kept
   short for the CE kinds, whose file is copied into ``custom/`` verbatim
   (comments and all). A merged kind's comments never reach the mission:
   `splice_mod_files` / `_merge_free_form` load the link without
   ``preserve_comments``, so they stay in ``<custom_xml_dir>/<id>.xml``
   where the user edits them.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.xml.economy_xml import SYSTEM_DESCRIPTIONS
from app.xml.globals_xml import VAR_DESCRIPTIONS
from app.xml.types_xml import FLAG_NAMES, KNOWN_CATEGORIES, KNOWN_USAGES, KNOWN_VALUES
from app.xml.xml_text import XML_DECLARATION as _DECL


@dataclass(frozen=True)
class XmlTemplate:
    """One "Start from:" button."""

    id: str
    label: str        # button text
    summary: str      # one sentence, rendered as the button's title=
    content: str
    kind: str = ""    # manual-link kind it belongs to
    target: str = ""  # mission-relative file, for free-form merges


_FLAGS = " ".join(f'{name}="0"' for name in FLAG_NAMES)
_CATEGORIES = ", ".join(KNOWN_CATEGORIES)
_USAGES = ", ".join(KNOWN_USAGES)
_VALUES = ", ".join(KNOWN_VALUES)
_ECONOMY_SYSTEMS = ", ".join(SYSTEM_DESCRIPTIONS)
# Wrapped, because globals.xml has ~40 variables and one line of them would run
# far past the editor's width.
_GLOBALS_VARS = "\n\t     ".join(
    ", ".join(chunk)
    for chunk in (
        list(VAR_DESCRIPTIONS)[i:i + 4] for i in range(0, len(VAR_DESCRIPTIONS), 4)
    )
)


TEMPLATES: tuple[XmlTemplate, ...] = (
    # -- types ------------------------------------------------------------
    XmlTemplate(
        id="types-item",
        label="Item",
        summary="A full central-economy entry for one item classname.",
        kind="types",
        content=f"""{_DECL}
<types>
\t<!-- One <type> per item classname. nominal is what the economy aims to have
\t     in the world; min is when it starts restocking towards nominal. -->
\t<type name="YourClassName">
\t\t<nominal>10</nominal>
\t\t<min>5</min>
\t\t<!-- lifetime = seconds an untouched item stays before cleanup.
\t\t     restock = seconds before a consumed one comes back (0 = at once). -->
\t\t<lifetime>3888000</lifetime>
\t\t<restock>0</restock>
\t\t<!-- quantmin/quantmax are percent fill for ammo, food and liquids.
\t\t     Use -1 -1 for anything that has no quantity. -->
\t\t<quantmin>-1</quantmin>
\t\t<quantmax>-1</quantmax>
\t\t<cost>100</cost>
\t\t<flags {_FLAGS} />
\t\t<!-- Names come from the mission's cfglimitsdefinition.xml. Stock ones:
\t\t     category: {_CATEGORIES} -->
\t\t<category name="tools" />
\t\t<!-- usage (where it spawns), repeatable: {_USAGES} -->
\t\t<usage name="Town" />
\t\t<!-- value (loot tier), repeatable: {_VALUES} (Livonia has no Tier4) -->
\t\t<value name="Tier1" />
\t</type>
</types>
""",
    ),
    XmlTemplate(
        id="types-disable",
        label="Turn an item off",
        summary="Stop an item spawning without editing the mod's own types.xml.",
        kind="types",
        content=f"""{_DECL}
<types>
\t<!-- Manual links are applied last, after every mod, so this entry wins by
\t     DayZ's last-definition-wins rule. Set nominal and min to 0 and the item
\t     stops spawning without touching the mod's own file, which the next
\t     SteamCMD update would overwrite anyway. -->
\t<type name="YourClassName">
\t\t<nominal>0</nominal>
\t\t<min>0</min>
\t</type>
</types>
""",
    ),
    XmlTemplate(
        id="types-vehicle",
        label="Vehicle",
        summary="A vehicle entry — pair it with an event and its spawn positions.",
        kind="types",
        content=f"""{_DECL}
<types>
\t<!-- A vehicle needs three things: this entry, an <event> in events.xml, and
\t     positions in cfgeventspawns.xml. This is only the first one. -->
\t<type name="CivilianSedan">
\t\t<nominal>0</nominal>
\t\t<min>0</min>
\t\t<lifetime>3888000</lifetime>
\t\t<restock>1800</restock>
\t\t<quantmin>-1</quantmin>
\t\t<quantmax>-1</quantmax>
\t\t<cost>100</cost>
\t\t<flags {_FLAGS} />
\t</type>
</types>
""",
    ),
    # -- events -----------------------------------------------------------
    XmlTemplate(
        id="events-vehicle",
        label="Vehicle event",
        summary="A dynamic event that spawns vehicles. Positions live in cfgeventspawns.xml.",
        kind="events",
        content=f"""{_DECL}
<events>
\t<!-- The engine reads the name prefix, so keep one of: Vehicle, Animal,
\t     Infected, Loot, Static, Item, Trap. This file says how many and how
\t     often; where they spawn is cfgeventspawns.xml. -->
\t<event name="VehicleYourCar">
\t\t<nominal>10</nominal>
\t\t<min>5</min>
\t\t<max>12</max>
\t\t<lifetime>1800</lifetime>
\t\t<restock>0</restock>
\t\t<!-- saferadius: no spawn if a player is this close.
\t\t     distanceradius: minimum spacing between two of these.
\t\t     cleanupradius: how far an untouched one is removed from. -->
\t\t<saferadius>500</saferadius>
\t\t<distanceradius>500</distanceradius>
\t\t<cleanupradius>500</cleanupradius>
\t\t<flags deletable="0" init_random="0" remove_damaged="0" />
\t\t<position>fixed</position>
\t\t<limit>mixed</limit>
\t\t<active>1</active>
\t\t<children>
\t\t\t<!-- Each child is a classname that also needs a types.xml entry. -->
\t\t\t<child lootmax="0" lootmin="0" max="5" min="2" type="CivilianSedan" />
\t\t</children>
\t</event>
</events>
""",
    ),
    XmlTemplate(
        id="events-loot",
        label="Loot / static event",
        summary="A static event such as a crashed heli or a loot pile.",
        kind="events",
        content=f"""{_DECL}
<events>
\t<!-- A Static* event spawns its children at the positions listed for it in
\t     cfgeventspawns.xml. lifetime is how long it stays once spawned;
\t     restock is the gap before the next one. -->
\t<event name="StaticYourCrash">
\t\t<nominal>3</nominal>
\t\t<min>1</min>
\t\t<max>4</max>
\t\t<lifetime>1800</lifetime>
\t\t<restock>1800</restock>
\t\t<saferadius>500</saferadius>
\t\t<distanceradius>1000</distanceradius>
\t\t<cleanupradius>1000</cleanupradius>
\t\t<flags deletable="1" init_random="0" remove_damaged="1" />
\t\t<position>fixed</position>
\t\t<limit>mixed</limit>
\t\t<active>1</active>
\t\t<children>
\t\t\t<child lootmax="0" lootmin="0" max="1" min="1" type="Wreck_UH1Y" />
\t\t</children>
\t</event>
</events>
""",
    ),
    # -- spawnabletypes ---------------------------------------------------
    XmlTemplate(
        id="spawnabletypes-loadout",
        label="Loadout",
        summary="Attachments and cargo an item spawns with.",
        kind="spawnabletypes",
        content=f"""{_DECL}
<spawnabletypes>
\t<!-- chance is 0.00 to 1.00. attachments go on the item, cargo goes inside
\t     it. The item itself still needs a types.xml entry. -->
\t<type name="YourClassName">
\t\t<!-- Optional: spawn it damaged. 0.0 is pristine, 1.0 is ruined. -->
\t\t<damage min="0.0" max="0.4" />
\t\t<attachments chance="1.00">
\t\t\t<item name="YourAttachment" chance="0.50" />
\t\t</attachments>
\t\t<cargo chance="0.30">
\t\t\t<item name="YourCargoItem" chance="1.00" />
\t\t</cargo>
\t</type>
</spawnabletypes>
""",
    ),
    XmlTemplate(
        id="spawnabletypes-preset",
        label="Use a preset",
        summary="Point a loadout at a named preset from cfgrandompresets.xml.",
        kind="spawnabletypes",
        content=f"""{_DECL}
<spawnabletypes>
\t<!-- The preset name must exist in cfgrandompresets.xml or the whole entry is
\t     ignored at load. Use a preset when several items share one loadout. -->
\t<type name="YourClassName">
\t\t<attachments chance="0.35" preset="yourOpticsPreset" />
\t\t<cargo chance="0.50" preset="yourFoodPreset" />
\t</type>
</spawnabletypes>
""",
    ),
    # -- cfgeventspawns ---------------------------------------------------
    XmlTemplate(
        id="cfgeventspawns-positions",
        label="Event positions",
        summary="Where an event spawns. The event itself must exist in events.xml.",
        kind="cfgeventspawns",
        content=f"""{_DECL}
<eventposdef>
\t<!-- x and z are world metres, a is the yaw in degrees. The name must match
\t     an <event> in events.xml or nothing spawns here.
\t     The Map Editor writes this file for you: place the positions on
\t     the map, then use "Make permanent" on the event. -->
\t<event name="StaticYourCrash">
\t\t<pos x="7500.0" z="7500.0" a="0.0" />
\t\t<pos x="7600.0" z="7600.0" a="90.0" />
\t</event>
</eventposdef>
""",
    ),
    # -- cfgeventgroups ---------------------------------------------------
    XmlTemplate(
        id="cfgeventgroups-group",
        label="Event group",
        summary="A cluster of objects that spawn together at one event position.",
        kind="cfgeventgroups",
        content=f"""{_DECL}
<eventgroupdef>
\t<!-- x, y and z are offsets RELATIVE to the group's spawn position, not
\t     world coordinates. Reference the group from an <event> as its child
\t     type in events.xml. -->
\t<group name="YourGroupName">
\t\t<child type="Wreck_UH1Y" deloot="1" lootmax="0" lootmin="0" x="0.0" y="0.0" z="0.0" a="0.0" />
\t\t<child type="LandMineTrap" deloot="0" lootmax="0" lootmin="0" x="3.0" y="0.0" z="1.5" a="45.0" />
\t</group>
</eventgroupdef>
""",
    ),
    # -- cfgrandompresets -------------------------------------------------
    XmlTemplate(
        id="cfgrandompresets-cargo",
        label="Cargo preset",
        summary="A named bundle of items a spawnabletypes cargo entry can reuse.",
        kind="cfgrandompresets",
        content=f"""{_DECL}
<randompresets>
\t<!-- Reference this from cfgspawnabletypes.xml as
\t     <cargo chance="1.00" preset="yourFoodPreset" />. -->
\t<cargo chance="0.30" name="yourFoodPreset">
\t\t<item name="TacticalBaconCan" chance="0.40" />
\t\t<item name="SardinesCan" chance="0.60" />
\t</cargo>
</randompresets>
""",
    ),
    XmlTemplate(
        id="cfgrandompresets-attachments",
        label="Attachment preset",
        summary="A named bundle of attachments a spawnabletypes entry can reuse.",
        kind="cfgrandompresets",
        content=f"""{_DECL}
<randompresets>
\t<!-- Reference this from cfgspawnabletypes.xml as
\t     <attachments chance="0.35" preset="yourOpticsPreset" />. -->
\t<attachments chance="0.35" name="yourOpticsPreset">
\t\t<item name="M68Optic" chance="0.30" />
\t\t<item name="ACOGOptic" chance="0.10" />
\t</attachments>
</randompresets>
""",
    ),
    # -- cfglimitsdefinition* ---------------------------------------------
    XmlTemplate(
        id="cfglimitsdefinitionuser-group",
        label="Usage / value group",
        summary="Group existing usages or tiers under one name you can use in types.xml.",
        kind="cfglimitsdefinitionuser",
        content=f"""{_DECL}
<lists>
\t<!-- This only GROUPS flags that already exist in cfglimitsdefinition.xml.
\t     Once defined, use it in types.xml as <usage name="YourAreaGroup" />. -->
\t<usageflags>
\t\t<user name="YourAreaGroup">
\t\t\t<usage name="Town" />
\t\t\t<usage name="Village" />
\t\t</user>
\t</usageflags>
\t<valueflags>
\t\t<user name="YourTierGroup">
\t\t\t<value name="Tier1" />
\t\t\t<value name="Tier2" />
\t\t</user>
\t</valueflags>
</lists>
""",
    ),
    XmlTemplate(
        id="cfglimitsdefinition-flag",
        label="New usage / tier",
        summary="Declare a brand new usage or loot tier flag.",
        kind="cfglimitsdefinition",
        content=f"""{_DECL}
<lists>
\t<!-- A flag nothing references does nothing: after adding it here you still
\t     have to put <usage name="YourUsage" /> on the types entries that should
\t     use it, and the map has to have that area assigned.
\t     This file merges in SECTIONS mode, so keep the
\t     <lists> to <usageflags> nesting exactly as it is. -->
\t<usageflags>
\t\t<usage name="YourUsage" />
\t</usageflags>
\t<valueflags>
\t\t<value name="Tier5" />
\t</valueflags>
</lists>
""",
    ),
    # -- globals ----------------------------------------------------------
    XmlTemplate(
        id="globals-override",
        label="Override a variable",
        summary="Override engine variables from db/globals.xml through cfgeconomycore.",
        kind="globals",
        content=f"""{_DECL}
<variables>
\t<!-- Every attribute must be defined: name, type, value.
\t     type 0 = integer, 1 = float, 2 = string.
\t     globals.xml is a FIXED set of names — inventing one does nothing.
\t     Known: {_GLOBALS_VARS} -->
\t<var name="ZombieMaxCount" type="0" value="800" />
</variables>
""",
    ),
    # -- economy ----------------------------------------------------------
    XmlTemplate(
        id="economy-systems",
        label="System flags",
        summary="Turn central-economy systems on or off (db/economy.xml's flags).",
        kind="economy",
        content=f"""{_DECL}
<economy>
\t<!-- All four flags must be set on every system you list.
\t     Systems: {_ECONOMY_SYSTEMS} -->
\t<dynamic init="1" load="0" respawn="1" save="0" />
\t<animals init="1" load="0" respawn="1" save="0" />
</economy>
""",
    ),
    # -- messages ---------------------------------------------------------
    XmlTemplate(
        id="messages-broadcast",
        label="Server message",
        summary="Add broadcasts to db/messages.xml — messages are only ever appended.",
        kind="messages",
        content=f"""{_DECL}
<messages>
\t<!-- Messages are ALWAYS appended: a linked file can add broadcasts but can
\t     never override or remove one the mission already has.
\t     repeat = minutes between repeats (omit or 0 for a one-off);
\t     onConnect = show to a player as they join. -->
\t<message>
\t\t<delay>0</delay>
\t\t<repeat>30</repeat>
\t\t<text>Welcome to the server.</text>
\t</message>
</messages>
""",
    ),
    # -- free-form merge targets ------------------------------------------
    XmlTemplate(
        id="globals-var",
        label="Variable",
        summary="Override one engine variable in db/globals.xml.",
        target="db/globals.xml",
        content=f"""{_DECL}
<variables>
\t<!-- globals.xml is a FIXED set of engine variables: inventing a name does
\t     nothing. Copy the var you want to change out of the live file (the
\t     button beside this one does that), keep its type, change the value.
\t     type 0 = integer, 1 = float, 2 = string. -->
\t<var name="ZombieMaxCount" type="0" value="800" />
</variables>
""",
    ),
)


def templates_for_kind(kind: str) -> list[XmlTemplate]:
    return [t for t in TEMPLATES if t.kind and t.kind == kind]


def templates_for_target(target: str) -> list[XmlTemplate]:
    """Starters for a free-form merge into *target* (mission-relative)."""
    wanted = target.strip().lower()
    return [t for t in TEMPLATES if t.target and t.target.lower() == wanted]


def get_template(template_id: str) -> XmlTemplate | None:
    return next((t for t in TEMPLATES if t.id == template_id), None)
