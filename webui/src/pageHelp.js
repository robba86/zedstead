// pageHelp.js
//
// Feature descriptions so each web view explains what it does: PAGE_HELP per
// page, SECTION_HELP per section.
//
// EDITORS was rewritten in 2026 Q3, sourced from dayz-docs/ (the vendored
// wiki + Bohemia's vanilla missions) — see the note above the map itself.
//
// All consumers look these up defensively — a missing key renders nothing, so
// section-title drift never breaks a page. Mods tab hints come from the backend
// (GET /api/mods/help → app/shared/mods_help.py) and are not duplicated here.

// Per-page intro, keyed by React Router path.
export const PAGE_HELP = {
  '/':
    'Start, stop or restart the DayZ server, and see its status and launch command. Drag a tile by its grip to move it, drag its right edge to resize it, hide it with ✕, and bring it back from "Add tile".',
  // Keyed on the old /wizard path: the guide is a drawer, and the path is what
  // still opens it (routes.js, guide.jsx::GuideRoute).
  '/wizard':
    'A guided tour of a first-time setup, in the order that works. Each step says what a page does and links to it, and the guide stays open beside that page.',
  '/doctor':
    'Health diagnostics for your server — config validation, mod conflicts, server info, disk usage, and log analysis.',
  '/update':
    "Updates DayZ through SteamCMD, then your mods, their bikeys and their XML, then the service files. Stop the server first, because the update does not stop it. Tick \"Start the server afterwards\" to start it and watch the boot.",
  '/mods':
    "Install, update and uninstall your server's Steam Workshop mods, set the order they load in, and check them for problems.",
  '/mods/:id':
    "Everything Zedstead knows about one mod, and every action that applies to it now. Browse the mod's own folder below, where XML, JSON, TXT and MD files open read-only.",
  '/players':
    'Online player list, activity history, and search — kick, message, ban, and manage the priority queue via RCON.',
  '/backups':
    'Four ways to keep a copy of your server. Quick saves the world and the players, Full the whole server folder, and Bundles your setup without the world. Offsite copies backups to another machine.',
  '/scheduler':
    "Recurring jobs on a cron schedule: restarts, server + mod updates, backups, mod update checks, announcements and RCon commands. Times are this machine's local time. The in-game warnings before a restart are in Configs → Restart warnings.",
  '/settings':
    'Core paths and global toggles applied across the tool. Path edits do not migrate existing files — they only change where future operations look.',
  '/cleanup':
    'Destructive maintenance actions: wipe server, migrate players, clear logs, reset mpmission, remove workshop mods, reinstall DayZ server / SteamCMD, clear XML backups, reset TraderPlus. ⚠ Most actions are irreversible — back up first.',
  '/configs':
    "Every config file Zedstead can edit, one card each, for the server, the mission and the mods. Click a card to open its file beside the list: Edit is the form or table, and Raw is the file text. Every save keeps the old version.",
  '/configs/mod-xml':
    "Mods ship XML for loot, events and spawn points, and Zedstead puts it into your mission on every install and update. See where each file went, turn one off, or take it over to edit it. Manual XML files are your own, and they win over every mod.",
  '/configs/types':
    "types.xml decides how much of each item exists in the world, one row per item. Nominal is the number the server keeps alive, min starts a refill, and a nominal of 0 takes an item out. This page also edits the mods' custom types files.",
  '/configs/tp-price':
    "The TraderPlus price list: every category, its items, and their buy and sell prices. A price of -1 turns that direction off. A category reaches a trader only after you assign it to the trader's ID on the TraderPlus IDs page.",
  '/adopt':
    "For a server that ran before Zedstead: this page reads its launch command, mod folders, units and mission, and runs the checks. Apply writes only Zedstead's own mod list and launch settings, and changes no file on the server.",
  '/quick-setup':
    'The short way to a first server. Pick a card, read what it does, and press Apply. Zedstead installs the server, adds the mods you chose, sets the difficulty, and can start the server.',
  '/my-server':
    'The settings most admins change first, as plain questions. Press Save when you are done. Each question links to the page that holds every setting of its kind.',
  '/presets':
    'Change gameplay in plain terms, not raw config values. A built-in preset sets every value any preset manages, so a switch leaves nothing of the last one behind. Preview first, and save your own setup as a custom preset.',
  '/maps':
    'Switch the active map. Each map brings its own mods, dependencies and mission template. Download fetches a community map without a switch, so you can look at it in the Map Editor first.',
  // No '/map-editor' entry: that page is full-bleed and explains itself
  // through its own ? guide (app/data/map_editor_help.json), so there is no
  // .page-header for a <PageIntro> to sit under.
  '/analysis':
    'Deep-dive analytics — economy score, server log findings, config diffs, version history, and performance trends.',
  '/help':
    'The documentation lives on zedstead.com: getting started, every page of the app, troubleshooting and every command.',
}

// Per-config-file intro for /configs/:id, keyed by editor id. Shown three ways:
// clamped to 3 lines on a /configs card, in full behind that card's "?" button,
// and behind the "?" in the editor drawer's header. So the FIRST paragraph has to
// stand alone as the card's summary, and what follows is the part the "?" is
// there to reveal — write it in that order.
//
// Sourced from dayz-docs/ (Bohemia's vendored wiki pages + their vanilla
// mission files) and from each editor's own field list, not from memory: these
// state units, defaults and cross-file rules an admin will act on. Check the
// spec before changing a number here.
// The 30 JSON files DayZ-Expansion writes on its first boot, registered as
// editors in app/web/editors/expansion.py. One sentence each: what the file
// governs. Deliberately no defaults, units or field names — those belong to
// Expansion's own wiki, which zsm does not vendor, and a number quoted here
// would go stale with their next release.
const EXPANSION_EDITORS = {
  'expansion-core': "Expansion's core switches — the settings that belong to no single feature, such as inventory tidy-up and how it treats central-economy item lifetimes.",
  'expansion-general': "General behaviour and the colours Expansion's own HUD and menus use.",
  'expansion-ai': "How Expansion's AI behave: accuracy, reaction time, sight and hearing.",
  'expansion-airdrop': 'Airdrop events — how often they run, how they are announced, and the container list that decides what falls out of the sky.',
  'expansion-book': 'The in-game server book: which categories it shows and the text on each page.',
  'expansion-chat': "Expansion's chat channels and their colours.",
  'expansion-damagesystem': 'Which explosives damage what, and by how much.',
  'expansion-garage': 'The vehicle garage — where players may store vehicles and how many.',
  'expansion-logs': 'Which Expansion actions are written to the server log.',
  'expansion-mission': "The mission system's own switches.",
  'expansion-monitoring': "Expansion's performance monitoring.",
  'expansion-nametags': 'Name tags above players: when they show and how far away they stay readable.',
  'expansion-notification': 'Which events raise an on-screen notification, and how each one looks.',
  'expansion-notificationscheduler': 'Messages Expansion shows on a timer.',
  'expansion-party': 'Player parties: size limits, invites, and what members share with each other.',
  'expansion-playerlist': 'The in-game player list and who may open it.',
  'expansion-quest': "The quest system's switches. The quests themselves are separate files, which Zedstead does not edit here.",
  'expansion-raid': 'Raiding: which tools work on what, the damage multipliers, and the schedule raiding is allowed on.',
  'expansion-socialmedia': 'The news feed and the links Expansion shows in its menus.',
  'expansion-territory': 'Territories — how large they are and how many a player may hold.',
  'expansion-vehicle': 'Expansion vehicles: keys, locks, damage and towing, plus the per-vehicle list.',
  'expansion-ai-locations': 'Where AI roam on this map, and the areas they never enter. Each location is a fold in the list below; the Map Editor moves and resizes them on the map.',
  'expansion-ai-patrols': 'AI patrols placed on this map. The switches are on the form; the patrols themselves are in the Expansion AI Patrol List table, and the load-balancing table is on the Raw tab.',
  'expansion-ai-patrol-list': "One row per AI patrol on this map: faction, loadout, size, speed and load-balancing category. Waypoints are placed on the map editor, and the card above says what Expansion would skip or change at boot.",
  'expansion-basebuilding': 'Base building on this map: what may be built, and the zones it is restricted in.',
  'expansion-hardline': 'The humanity/reputation system and the rarity Expansion assigns to each item.',
  'expansion-map': 'The in-game map and the markers the server puts on it.',
  'expansion-market': 'The market for this mission: currencies, where bought vehicles appear, and the menu colours. The per-category price files are not editable here.',
  'expansion-p2pmarket': 'The player-to-player market and its menu categories.',
  'expansion-safezone': 'Safe zones on this map — the circles, cylinders and polygons where players cannot be harmed. They are also drawn on the map editor.',
  'expansion-spawn': 'Where players spawn on this map, and what they spawn with.',
}

// Dynamic AI (plans/47 C). The card above each editor lists what the mod would
// skip or change in these files, and what it logged on its last boot.
const DYNAMIC_AI_EDITORS = {
  'dynai-settings':
    "Dynamic AI's main settings: how often it checks for players, how far away it spawns, who gets AI and when.\n\nThe mod corrects out-of-range values on load and says so only in the Expansion log, so the Dynamic AI card below lists them here. Two things are not on this form. The Group, Point, Location and Audio lists have their own editors. Version is only on the Raw tab, because the mod switches itself off for a version it does not know.",
  'dynai-groups':
    'The groups spawned around players: faction, loadout, how many and how likely. The mod skips a group whose counts are impossible or whose loadout file is missing.',
  'dynai-points':
    'Zones with their own spawns, or no spawns at all when marked Safe. Positions are edited on the map; new points are made by duplicating one.',
  'dynai-locations':
    'Places that spawn AI when a player enters the trigger radius. Positions are edited on the map; new locations are made by duplicating one.',
  'dynai-audio':
    'Like locations, but triggered by noise. Positions are edited on the map; new entries are made by duplicating one.',
  'dynai-notifications':
    'Messages players get about AI, and, when ActiveHoursEnabled is 2 or 3, which groups may spawn: a group no entry names never does.',
}

// RadZone (plans/48 B). The card above each editor lists what looks off in the
// three files: zones off the map, corners unset, crates outside their zone.
const RADZONE_EDITORS = {
  'radzone-settings':
    "RadZone's main settings: whether the zones are on, how radiation sickness hurts, and how much protective clothing a player needs.\n\nThe zones themselves are in the Zones editor. RadZone writes all three files on its first boot, with zones for Chernarus' two airfields whatever the map.",
  'radzone-zones':
    'The radiation zones: a circle (centre and radius) or, with IsSquareZone on, a rectangle between two corners. Y is metres north, which the game calls Z. New zones are made by duplicating one.',
  'radzone-crates':
    'The loot crates spawned inside the zones: whether they spawn at all, and how often they are replaced. The loot lists themselves are on the Raw tab.',
  'radzone-crate-areas':
    "Which zone each group of crates belongs to, and how many crates spawn at a time. The crates' positions and the creatures guarding them are on the Raw tab and kept as they are when you save here.",
  'radzone-clothes':
    'Which clothes protect against radiation, per clothing slot, and how much.',
}

// ToxicZone (plans/48, ToxicZone pass B). Same author and shape as RadZone, plus
// moving zones: the card above each editor lists what looks off in the five
// files, including a moving zone whose path no longer matches its ends.
const TOXICZONE_EDITORS = {
  'toxiczone-settings':
    "ToxicZone's sickness settings: how much toxicity a zone gives, how fast a sick player bleeds, and what toxic grenades do to players and gas masks.\n\nThe zones are in the Static Zones and Moving Zones editors. ToxicZone writes all five files on its first boot, with zones for Chernarus whatever the map.",
  'toxiczone-static':
    'The switch for every static toxic zone at once. The zones themselves are in the Static Zones editor.',
  'toxiczone-static-zones':
    'The static toxic zones: a circle (centre and radius) or, with IsSquareZone on, a rectangle between two corners, each with its own messages and loot crates. Y is metres north, which the game calls Z. New zones are made by duplicating one.',
  'toxiczone-moving':
    "The switch for the moving zones, and AdminUID: the Steam IDs that get each moving zone's position in chat. A fresh install lists only the mod author's ID.",
  'toxiczone-moving-zones':
    'Zones that travel from a start point to an end point and then start again. Change the two points and save: Zedstead works out the path the mod needs (Coeff, YtoOrigin, Norme) and which way the zone moves. The path must run east or west, not straight north or south.',
  'toxiczone-loot':
    'The loot crates in the static zones: whether they spawn at all, and how often they are replaced. The loot lists themselves are on the Raw tab.',
  'toxiczone-protection':
    'How many protection points a player needs to be fully protected. What each piece of clothing gives is in the Clothes Protection editor.',
  'toxiczone-clothes':
    'Which clothes protect against the toxic zones, per clothing slot, and how many points each gives.',
}

export const EDITORS = {
  ...EXPANSION_EDITORS,
  ...DYNAMIC_AI_EDITORS,
  ...RADZONE_EDITORS,
  ...TOXICZONE_EDITORS,
  serverdz:
    "The server's own settings file, read once when the server starts. It holds the name and description players see in the browser, the player cap, the join and admin passwords, and which mission — and so which map — is loaded.\n\nIt also sets how fast time passes (a multiplier: 24 turns a whole day into one hour, and night has its own multiplier on top of that), whether third person and the crosshair are allowed, voice chat, the login queue, and how often the world is saved.\n\nNothing here reaches a running server — restart for a change to take effect. Two values other pages lean on: the mission template, which the Maps page rewrites when you switch map, and enableCfgGameplayFile, which must be 1 before the Gameplay file is read at all.",
  runcommand:
    "The command line Zedstead builds when it starts the server: the port it listens on, where the config, profiles and BattlEye folders are, and which optional flags are switched on.\n\nThese live in Zedstead's own config.json rather than in serverDZ.cfg, because they are arguments to the server program, not settings the server reads. Relative paths are resolved against the server directory.\n\nThe logging flags fill the files the Analysis page reads, and CPU count limits how many cores the server may use. The mod list is deliberately not here: mods and their load order are managed on the Mods page and added to this line for you.",
  battleye:
    "BattlEye's settings — the anti-cheat and its remote console, RCon. This is the password and port Zedstead uses to talk to a server while it is running. RCon host is Zedstead's own: the address it connects to, this machine unless the server runs elsewhere. It saves while the server runs.\n\nWithout a working RCon there is no live connection: the Players page stays empty, and scheduled announcements, restart warnings and kicks are skipped in silence. The enable switch lives in serverDZ.cfg and everything else in beserver_x64.cfg (BEServer_x64.cfg on Windows — the spelling matters on Linux).\n\nLeave RCon restricted to the local machine unless you truly need it from elsewhere: anyone holding this password can run admin commands on your server.",
  updates:
    "How Zedstead updates this server: the Steam branch it downloads, how many players online hold back a scheduled update, and how long it waits for the server to come up after one.\n\nThese are Zedstead's own values in config.json, not a server file.",
  'crash-recovery':
    "What systemd does when the server stops by itself: whether it restarts it, how long it waits first, and how many restarts in what time before it gives up.\n\nThe service files must be generated again before a change takes effect: Doctor → Service setup, or zsm service install.",
  'restart-warnings':
    "The in-game countdown before a scheduled restart: at which minutes players are warned, the message they see, and when new joins are locked.\n\nThe warnings go out over RCon from the running Zedstead process, not from the task's timer.",
  messages:
    "What the server says to players on its own — a greeting when someone joins, messages on a repeat, and restart countdowns.\n\nA countdown message is broadcast automatically at 90, 60, 45, 30, 20, 15, 10, 5, 2 and 1 minutes before its deadline. Mark one as a shutdown and the server stops itself when the countdown ends — the safe way to restart, since a server killed outright can lose the last few minutes of everyone's progress.\n\nMessages are capped at 160 characters. #name, #port and #tmin are replaced with the server name, its port, and the minutes left on a countdown. Every time in this file is in minutes, delay and deadline included.",
  globals:
    "The world-wide numbers the central economy works from: how many infected and animals may exist at once across the whole map, and how long dead bodies, ruined loot and dropped items last before the server clears them away.\n\nLifetimes are in seconds — 3600 for a dead player, 330 for ruined loot, 45 for anything with no setup of its own. Cleanup skips whatever is close to a player, at the distance set by CleanupAvoidance.\n\nAlso here: login, logout and server-hopper penalties, how much loot spawns on a fresh start and after a restart, how many items may respawn at once, and whether food decays. Every field carries its own help with the vanilla default.",
  spawnabletypes:
    "What a spawned item arrives with — the magazine in a rifle, the contents of a crate, the parts on a car — and in what condition.\n\nEach row is a type from types.xml with optional attachments and cargo, given either as a direct list or as the name of a preset from Random Presets. Damage min and max set the wear it spawns with, where 0 is pristine.\n\nAn item with no row here spawns bare. Nested item groups are shown as a read-only summary — edit those in the raw file.",
  events:
    "Everything the world spawns by itself: helicopter crashes, vehicles, infected hordes, animals and loot. One row per event, with its counts, its timing and a switch to turn it off.\n\nNominal is how many the server aims to have and min the floor that triggers a refill; max is a hard cap; lifetime is how long one lives and restock how long before it may appear again. Times are in seconds. Unticking active disables an event without deleting it, which is far easier to undo.\n\nWhere an event may appear is a separate file, Event Spawns, and multi-part events are built in Event Groups. The event named Loot is the one that puts loot inside buildings — leave that one on.",
  weather:
    "The mission's weather file. It drives overcast, fog, rain, snowfall, wind and storms, either as a fixed starting condition or as a range the server drifts through on its own.\n\nEvery element is described the same way: the value it starts at and how long it holds, the lowest and highest it may reach, how long one change takes, and how big a single change may be. Rain and snow also have thresholds — they only fall while overcast stays inside a given range.\n\nValues run from 0 to 1, except wind, which is in metres per second. The file has a master switch; turn it off and the map falls back to the game's own built-in weather.",
  economycore:
    "The central economy's manifest: which parent classes the economy manages, its default values, how often persistence is backed up, and which extra folders of XML are loaded.\n\nThe backup settings are the ones worth knowing — how many minutes between automatic persistence backups (15 at the least), how many are kept before the oldest is overwritten, and whether one is made at startup. World segments splits the map up for saving and cleanup; the default of 12 is tuned for Chernarus. The log switches make the economy very talkative in the console and are best left off.\n\nThe <ce folder=\"custom\"> entries are not edited here: they are generated by the mod XML merge, which wipes and rebuilds custom/ on every merge, install or mod toggle. Add your own via Manual XML files on the Custom config tab — those are re-applied after every rebuild.",
  randompresets:
    "Named bags of loot that Spawnable Types draws from, so a set of items can be written once and reused everywhere.\n\nA preset holds a list of items, each with its own chance, and the preset itself has a chance of being used at all. Cargo presets fill a container's inventory; attachment presets put things onto a weapon, a vest or a vehicle.\n\nNothing here spawns by itself. A preset only takes effect once an entry in cfgspawnabletypes.xml calls it by name.",
  ignorelist:
    "A list of item names the central economy will not spawn, and wipes from the map at the next restart.\n\nIt is how a class is taken out of the game: listing a container or a base-building piece deletes every one of them on the next restart.\n\nIt is only a list of classnames, one per row, with no numbers attached. Vanilla ships a short list of small items; mods often add their own.",
  environment:
    "The index of animal life on the map: which territory files under env/ are loaded, and which species are grouped into which herd.\n\nA territory file holds the zones one species lives in — where they may appear, how many per zone, and how many may exist at once. This file lists those files, gives each herd a behaviour, and sets the disease agents an animal can carry along with the chance of it.\n\nAnimals are spawned as players come near a zone and removed again once everyone leaves, so the counts here are limits rather than a standing population.",
  effectarea:
    "Contaminated zones — the toxic gas areas that need a mask — each given a centre, a radius and the particles that make it visible.\n\nAreas are created fresh every time the server starts and are never saved, so adding or removing one needs a restart and nothing more. No wipe, no persistence to clear. An empty file means no zones at all.\n\nOnly static zones live here; dynamic contaminated areas are events instead, set up in events.xml. Keep the particle counts modest — they are the expensive part.",
  gameplay:
    "Settings for behaviour that used to need script edits: player movement and stamina, base building, the map and compass, hit indication, and the world's own rules.\n\nIt is only read when serverDZ.cfg contains enableCfgGameplayFile = 1. Without that line the whole file is ignored and every value falls back to the game's default, which is the usual reason a change here seems to do nothing.\n\nIt is also where starting-gear presets and object-spawner files are named, so those two features are switched on from here. The stock files have no PlayerData.spawnGearPresetFiles, and this form shows only the keys a file has, so add that list on the Raw tab; once it is in the file it is edited here.",
  limitsdefinition:
    "The vocabulary the loot system uses. Every category, tag, usage and value flag that types.xml is allowed to mention has to be declared here first.\n\nUsage is the kind of place an item may spawn in (Military, Police, Farm, Coast and so on), value is its tier, category sorts items into groups like weapons or food, and tag says where inside a building it lands — floor, shelves or ground.\n\nThis file is names only; it changes no amounts. A flag used in types.xml but missing here is an error the server reports at startup, which is why mods that invent their own flags need them merged in.",
  limitsdefinitionuser:
    "Shorthand bundles of the flags from Limits Definition, so an entry in types.xml can say Tier1234 instead of listing four values one at a time.\n\nA group is just a name and the flags it stands for: TownVillage means Town and Village, Tier12 means Tier1 and Tier2. It saves repetition and nothing else — the flags themselves still have to exist in cfglimitsdefinition.xml.\n\nMods that ship their own groups are merged in here. One row is one group: its name, which list it belongs to, and the flags it stands for, comma-separated.",
  eventspawns:
    "The map positions each event may use. Events says what spawns and how many; this file says where, one row per point with its coordinates and rotation.\n\nAn event with no positions here never appears, whatever its counts say. Points are grouped under the event name they belong to, so a helicopter crash picks one of its listed spots each time it fires.\n\nMods that ship their own spawn tables have them merged in here, wrapped in START/END MOD comments so they can be taken out again cleanly. The Map Editor edits this same file by clicking on a map, which beats typing coordinates.",
  eventgroups:
    "Multi-part event spawns: a set of objects placed together as one unit, such as a crash site with its wreck, its debris and its loot containers.\n\nEach member is listed with an offset and rotation relative to the group's own position, so events.xml can drop the whole arrangement anywhere as a single event. Per member, deloot says whether it carries loot and the loot min/max say how much.\n\nGroups are called up by name from events.xml; a group nothing refers to is simply never used.",
  playerspawnpoints:
    "Where players arrive in the world: fresh spawns after death, plus separate lists for players hopping in from another server on the same map and for those travelling from a different map.\n\nEach section has positions and a set of rules for turning them into real spawn points. A grid is sampled around every position, and each candidate is scored on its distance from buildings, other players and infected, and on how steep the ground is.\n\nSpawn points can also be gathered into groups the server rotates between, each staying active for a set time or a set number of players. The Map Editor's Players tab edits this same file on a map.",
  undergroundtriggers:
    "How dark it gets underground. Each trigger is a box somewhere in the world \u2014 a position, a size and a rotation \u2014 that decides how much a player standing inside it can see without a flashlight.\n\nThe type is not a field you set; it follows from the rest. A trigger with no breadcrumbs is an outer trigger when its eye accommodation is 1 and an inner one otherwise, and any trigger that has breadcrumbs is a transitional one. Breadcrumbs are the points along the way in: each has a position, a weight and a radius, and together they fade the darkness in and out instead of switching it.\n\nWhat is here depends entirely on the map. Chernarus ships an empty list, Livonia has the Dambog bunker, and Sakhal has thirty triggers. The triggers are edited here as a tree, which keeps every key a trigger has, including the ones that only some terrains use; each trigger's type is listed above the tree. The Map Editor draws the boxes and their breadcrumbs on the map and is the easier way to move one.",
  economy:
    "The master switches for the central economy. Each row is one system — loot, animals, zombies, vehicles, buildings, players — with four boxes deciding what the server does with it.\n\nInit creates it when the server starts, load restores it from the saved world, respawn lets it come back during play, and save writes it back to storage. Untick save for a system and it is gone after the next restart.\n\nThe row named dynamic is loot: switch it off and nothing spawns in any building anywhere on the map. Change these one at a time, and know why.",
  coresettings:
    "Engine settings in three blocks — video, render and the job system. On a dedicated server, only the job system does anything at all.\n\nThe video and render blocks come from the client's version of this file; a headless server draws nothing, so changing them has no effect. For view distance on the server, use serverDZ.cfg instead.\n\nThe job system decides how many threads go to replication, and it only takes effect when serverDZ.cfg has multithreadedReplication = 1.",
  "tp-banking":
    "TraderPlus's bank: what an account starts with, how much it may hold, and what moving money costs.\n\nTransaction fees are taken out of each transfer, and the accepted-currencies list decides which items count as money in the first place. Requiring a credit card means a player has to carry that item before a bank will deal with them.\n\nBanking logs record transactions on the server side, which is what makes a disputed balance answerable afterwards.",
  "tp-general":
    "TraderPlus's own switches: which buttons appear in the trader menu, what the licence system is called, and which item conditions a trader will accept.\n\nAccepted states decide whether worn, damaged or badly damaged goods can be sold at all, and each has a coefficient — a multiplier on the price, so a worn item fetches a fraction of a pristine one. Licences are named permissions a player must hold before certain traders deal with them.\n\nThe receipt options control what is remembered when a vehicle is bought — its lock, its attachments, its cargo — and the convert switches are a one-off import from an older Trader config.",
  "tp-ids":
    "Which goods each trader NPC actually deals in.\n\nEvery trader has a numeric ID. This file maps that ID to the price-list categories they buy and sell, the licences a player needs to trade with them, and optionally a narrower list of currencies they accept. The categories themselves, with their items and prices, are on the TraderPlus Prices page.\n\nAn ID with no categories is a trader with an empty shop, which is the usual explanation for an NPC that does nothing.",
  "tp-safezone":
    "The protected areas around traders: where each zone sits, how big it is, and the messages players see as they cross its edge.\n\nPer zone there is a radius and a countdown, and for the server as a whole an option to disconnect players who idle inside — so a safe zone cannot be used as free parking — plus a timer that clears items left on the ground.\n\nThe lists are the fine print: entities that survive that cleanup, objects to remove outright, admin Steam UIDs the rules do not apply to, and items that may not be kept in a stash inside the zone.",
  "tp-garage":
    "The vehicle garage: how many vehicles a player may store, what parking in and out costs, and what is kept while a vehicle sits in storage.\n\nThe save switches decide whether cargo, damage and fuel survive the stay — without them a vehicle comes back empty or mysteriously repaired. Payment can come from a bank account instead of cash, and there is a window in which a stored vehicle can still be traded in.\n\nThe garage NPCs are positions rather than settings: place and move them in the Map Editor, which writes them back into this same file.",
  "tp-insurance":
    "Optional cover on a bought vehicle, priced per vehicle type.\n\nThe price coefficient sets what insurance costs as a share of the vehicle's own price, and the collateral coefficient sets how much comes back when a claim is made. Only the trader IDs listed as authorized will sell it.\n\nA vehicle with no row here simply cannot be insured.",
  "tp-beans":
    "The small DayZ Beans Launcher badge in the trader menu, added by TraderPlus in its 2026-11-09 defaults.\n\nThe badge opens the launcher's download page. With your own partner code, players who install through the badge earn credits for your server. Keep the default code to support the TraderPlus author, or hide the badge.\n\nThe file is new: a server set up before that version has none until Reset TraderPlus JSON on the Cleanup page copies the defaults.",
  "tp-vehicles":
    "The vehicles TraderPlus knows about, and the parts each one needs before it can be driven away.\n\nThe parts list is what a trader attaches when the vehicle is sold, so a car missing wheels or a battery here is sold undriveable. Height is the spawn height used when it is placed, which is what keeps it from appearing inside the ground.\n\nA modded vehicle has to be listed here before it can be sold or stored in the garage.",
}

// Per-section hints, keyed by scope then by the section title as rendered.
// Lookups are defensive: an unmatched title simply renders no hint.
export const SECTION_HELP = {
  settings: {
    Base:
      'Core paths and global toggles applied across the tool. Path edits do not migrate existing files — they only change where future operations look.',
    Backups:
      'Backup destinations and rotation caps. Persistence backups are mission save data; bundle exports package mods + XMLs + settings for sharing. The offsite target itself lives on the Backups page, not here \u2014 its credentials are not stored in this config.',
    Features:
      "Enable/disable specific subsystems. Disabling a feature stops its background work (chat logger, metrics collector, etc.) — it doesn't just hide the menu item.",
    'Web panel':
      'How this app is reached and secured (address, HTTPS, allowed hosts, proxies, the path lock), and whether it runs the background tasks. Most of these apply when zsm web restarts.',
    Notifications:
      'In-app notification toggles. Desktop notifications are switched on per browser and apply at once, without Save. Discord notifications are configured separately below.',
    'zedstead.com':
      'Two opt-ins to the Zedstead website, both off until you turn one on. The listing puts this server on the public server list at https://zedstead.com/servers/. The ping only adds this install to an anonymous count. Only zsm web sends either one.',
    Discord:
      'The webhook URL is the master gate — when blank, every hook below is silently skipped. Each event hook also requires its underlying subsystem to be enabled.',
  },
  'restart-warnings': {
    'Restart warnings':
      "Counted down to the next enabled Server Restart task on the Scheduler, or else to the shutdown deadline in messages.xml. With neither, nothing happens. Zedstead sends the warnings over RCon, so it must run.",
  },
  coresettings: {
    'Video (ignored on dedicated server)':
      'Ignored on dedicated server. Leftovers from the client/Workbench config schema; the headless server has no renderer.',
    'Render (ignored on dedicated server)':
      'Ignored on dedicated server. Client renderer knobs. For server-side view distance, edit serverDZ.cfg (defaultVisibility / defaultObjectViewDistance).',
    'Job System (needs multithreadedReplication=1)':
      'Server replication threading. Only takes effect when serverDZ.cfg has multithreadedReplication=1. The Video and Render sections, on the Client only tab, are inert; this is the only block that changes server behaviour.',
    'Job System — PC':
      'Server replication threading. Only takes effect when serverDZ.cfg has multithreadedReplication=1.',
  },
  runcommand: {
    Paths:
      'Path arguments. Relative paths are resolved against the server directory; absolute paths are used as-is.',
  },
}
