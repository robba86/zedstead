// One table of "which view edits this thing", shared across the whole app.
//
// Why it lives client-side (and not next to app/ops/doctor_report.py's route
// constants or configs.py::_RUNCOMMAND_LINKS): those payloads are *generated*
// server-side from server-side facts, so their routes have to travel with the
// data. Nothing here does — this is pure frontend route knowledge (editor ids,
// map-editor tab names). Same call ModXmlPage's TARGET_ROUTES already made.
//
// Why it is app-wide and not pages/mapeditor/links.js: the map editor is the
// first consumer, not the only planned one. Every entry therefore carries human
// copy (`tip`, a sentence — not a label) so the help view, a setup wizard and
// any future "where do I edit X?" surface can render from this table instead of
// each re-deriving the routes.
//
// The per-mod page (/mods/:id) lives here too, as `modLink()` at the bottom.

// Every cross-link target, keyed by a stable slug. For config editors the slug
// IS the /configs/:id editor id, so there is one name for the thing everywhere.
import { CONFIGS_MOD_XML_TAB } from './pageTabs.js'

export const LINKS = {
  // --- app pages -----------------------------------------------------------
  maps: { to: '/maps', label: 'Maps', tip: 'Switch the mission map.' },
  mods: { to: '/mods', label: 'Mods', tip: 'Install, enable and reorder mods.' },
  configs: { to: '/configs', label: 'All config editors', tip: 'Every editable server and mission file.' },
  analysis: {
    to: '/analysis',
    label: 'Analysis',
    tip: 'Economy analysis, server logs and config version history.',
  },
  'mod-xml': {
    to: '/configs?tab=Custom',
    label: 'Custom config',
    tip: 'Where each mod XML file ended up, and why some were skipped.',
  },
  'manual-xml-links': {
    to: '/configs?tab=Custom#manual-files',
    label: 'Manual XML files',
    tip: 'Your own XML fragments, kept outside the mission and re-applied on every merge.',
  },

  // --- server files ---------------------------------------------------------
  battleye: {
    to: '/configs/battleye',
    label: 'BattlEye',
    tip: 'The RCon password and port the server reads, which Zedstead uses to connect.',
  },

  // --- mission files the editor writes ------------------------------------
  eventspawns: {
    to: '/configs/eventspawns',
    label: 'cfgeventspawns.xml',
    tip: 'The raw spawn positions behind the map markers.',
  },
  events: {
    to: '/configs/events',
    label: 'events.xml',
    tip: 'Event timing, limits and radii.',
  },
  playerspawnpoints: {
    to: '/configs/playerspawnpoints',
    label: 'cfgPlayerSpawnPoints.xml',
    tip: 'The raw fresh/hop/travel spawn points and their parameters.',
  },
  types: {
    to: '/configs/types',
    label: 'Types.xml',
    tip: 'Loot table for every item classname an event can spawn.',
  },
  economycore: {
    to: '/configs/economycore',
    label: 'cfgeconomycore.xml',
    tip: 'Which XML files the mission loads, including each mod’s.',
  },
  globals: {
    to: '/configs/globals',
    label: 'globals.xml',
    tip: 'Engine-wide variables — zombie counts, cleanup timers, view distance.',
  },
  economy: {
    to: '/configs/economy',
    label: 'economy.xml',
    tip: 'Master on/off switches for each central-economy system.',
  },
  messages: {
    to: '/configs/messages',
    label: 'messages.xml',
    tip: 'Server broadcasts shown to players in chat.',
  },
  spawnabletypes: {
    to: '/configs/spawnabletypes',
    label: 'cfgspawnabletypes.xml',
    tip: 'What spawns inside an item when it spawns — attachments and cargo.',
  },
  eventgroups: {
    to: '/configs/eventgroups',
    label: 'cfgeventgroups.xml',
    tip: 'The object groups an event spawns as one cluster.',
  },
  randompresets: {
    to: '/configs/randompresets',
    label: 'cfgrandompresets.xml',
    tip: 'Named random loot presets that spawnabletypes reference.',
  },
  limitsdefinition: {
    to: '/configs/limitsdefinition',
    label: 'cfglimitsdefinition.xml',
    tip: 'The usage, value and tag flags every types entry may use.',
  },
  limitsdefinitionuser: {
    to: '/configs/limitsdefinitionuser',
    label: 'cfglimitsdefinitionuser.xml',
    tip: 'User-defined groupings of the limits flags.',
  },

  // --- TraderPlus (only meaningful when the mod is installed) --------------
  'tp-general': {
    to: '/configs/tp-general',
    label: 'TraderPlus General',
    tip: 'Trader NPCs and trade objects.',
    traderplus: true,
  },
  'tp-safezone': {
    to: '/configs/tp-safezone',
    label: 'TraderPlus SafeZone',
    tip: 'Safe zone positions and radii.',
    traderplus: true,
  },
  'tp-garage': {
    to: '/configs/tp-garage',
    label: 'TraderPlus Garage',
    tip: 'Garage NPCs and parking spots.',
    traderplus: true,
  },
  'tp-ids': {
    to: '/configs/tp-ids',
    label: 'TraderPlus IDs',
    tip: 'The trader id registry — NPCs sharing an id share stock.',
    traderplus: true,
  },
  'tp-price': {
    to: '/configs/tp-price',
    label: 'TraderPlus Prices',
    tip: 'The trader price database.',
    traderplus: true,
  },
  'tp-banking': {
    to: '/configs/tp-banking',
    label: 'TraderPlus Banking',
    tip: 'Bank accounts, fees and limits.',
    traderplus: true,
  },
  'tp-vehicles': {
    to: '/configs/tp-vehicles',
    label: 'TraderPlus Vehicles',
    tip: 'Vehicle spawn settings for trader-sold vehicles.',
    traderplus: true,
  },
  'tp-insurance': {
    to: '/configs/tp-insurance',
    label: 'TraderPlus Insurance',
    tip: 'Insurance prices per vehicle.',
    traderplus: true,
  },
  'tp-beans': {
    to: '/configs/tp-beans',
    label: 'TraderPlus Beans Launcher',
    tip: 'The DayZ Beans Launcher badge in the trader menu: partner code and hide switch.',
    traderplus: true,
  },
}

// Mod XML wiring, both directions of the report's rows (app/xml/xml_report.py).
//
// A classified kind (app/xml/xml_classify.py) names the mission file that owns
// that content; a destination filename names the file a fragment was spliced
// into. Both resolve to a LINKS slug so the Mod XML page never carries routes
// of its own — it used to keep a private TARGET_ROUTES map.
export const MODXML_KIND_SLUGS = {
  types: 'types',
  events: 'events',
  spawnabletypes: 'spawnabletypes',
  globals: 'globals',
  economy: 'economy',
  messages: 'messages',
  cfgeventspawns: 'eventspawns',
  cfgeventgroups: 'eventgroups',
  cfgrandompresets: 'randompresets',
  cfglimitsdefinition: 'limitsdefinition',
  cfglimitsdefinitionuser: 'limitsdefinitionuser',
}

export const MISSION_FILE_SLUGS = {
  'cfgeconomycore.xml': 'economycore',
  'cfgeventspawns.xml': 'eventspawns',
  'cfgeventgroups.xml': 'eventgroups',
  'cfgrandompresets.xml': 'randompresets',
  'cfglimitsdefinition.xml': 'limitsdefinition',
  'cfglimitsdefinitionuser.xml': 'limitsdefinitionuser',
}

// The map editor's sidebar "Related" block: shown on every tab, plus per-tab.
export const RELATED_COMMON = ['maps', 'mods', 'configs', 'analysis']

export const RELATED_BY_TAB = {
  spawns: ['eventspawns', 'events', 'types', 'economycore'],
  'player-spawns': ['playerspawnpoints'],
  traders: ['tp-general', 'tp-safezone', 'tp-garage', 'tp-ids', 'tp-price', 'tp-banking'],
}

// Reverse direction: config editor id -> the map editor, set up to show that
// file's data. `tab=spawns` is intentionally omitted — urlState's applyTab
// treats it as the default and drops it, so emitting it round-trips to nothing.
export const MAP_EDITOR_LINKS = {
  eventspawns: '/map-editor?layers=spawns',
  events: '/map-editor?layers=spawns',
  playerspawnpoints: '/map-editor?tab=player-spawns&layers=playerspawns',
  'tp-general': '/map-editor?tab=traders&layers=traders',
  'tp-safezone': '/map-editor?tab=traders&layers=traders,zones',
  'tp-garage': '/map-editor?tab=traders&layers=traders',
  'tp-ids': '/map-editor?tab=traders&layers=traders',
  // Expansion's four map-backed settings files. The spawn one lands on the
  // Players tab, where both spawn systems are listed side by side; the rest
  // have no tab of their own — you edit them by clicking their features on the
  // map — so they just switch their own layer on.
  'expansion-spawn': '/map-editor?tab=player-spawns&layers=expspawns',
  'expansion-safezone': '/map-editor?layers=expsafezones',
  // AILocationSettings.json and AIPatrolSettings.json. The locations one is a
  // text-kind editor, so until these entries existed its only surface was a
  // textarea — for a file the map editor has full CRUD on (2026 Q3, H4).
  'expansion-ai-locations': '/map-editor?layers=expairoaming',
  'expansion-ai-patrols': '/map-editor?layers=expaipatrols',
  'expansion-ai-patrol-list': '/map-editor?layers=expaipatrols',
  // Position lists the Configs editors can only show on their Raw tab
  // (TODO H4, 2026 Q3); the rest of each file stays a form there.
  'expansion-market': '/map-editor?layers=expmarket',
  'expansion-map': '/map-editor?layers=expmarkers',
  'expansion-basebuilding': '/map-editor?layers=expbuildzones',
  // Vanilla map data, not a mod's: the trigger boxes and their breadcrumbs.
  undergroundtriggers: '/map-editor?layers=underground',
  // Dynamic AI's positions (plans/47 D). The tables keep positions out; the map
  // is where they are edited.
  'dynai-points': '/map-editor?layers=dynaipoints',
  'dynai-locations': '/map-editor?layers=dynailocations',
  'dynai-audio': '/map-editor?layers=dynailocations,dynaiaudio',
}

// Config editor -> the other views that own part of that file, rendered by
// ConfigEditorDrawer as ghost buttons in the header. cfgeconomycore.xml is written from
// two directions: the mod XML merge generates its `custom` <ce> block, and a
// manual XML file is how a user adds one of their own — neither is editable in
// the table below, so both have to be reachable from the page.
export const EDITOR_LINKS = {
  economycore: ['mod-xml', 'manual-xml-links'],
}

// Config editor -> where its *generated* (readonly) table rows are really
// managed. TableEditor renders it beside every greyed-out row.
export const EDITOR_READONLY_LINKS = {
  economycore: 'manual-xml-links',
}

// The optional preselect params ConfigEditorDrawer reads, shared by both builders below:
//   tab   — which editor tab to open (interactive|basic|raw). The app-wide tab
//           key, same as every other tab bar (urlTabs.js) — this builder used to
//           write `?view=` here, which was the same idea under a second name.
//           ConfigEditorDrawer still *reads* ?view= for old links; nothing writes it.
//   group — select a TableEditor filter_key group (e.g. one event). Two-way:
//           the picker rewrites it, so the link also works when the target page
//           is already open.
//   q     — prefill the search box (the app-wide search-prefill key)
//   file  — the file to open on a page with a file picker (the Types page)
//   owner — show only the rows a START MOD block of this owner holds (a mod,
//           or `@user:<id>` for a manual XML file; TableEditor, plans/92)
function withParams(to, { tab, group, q, file, owner } = {}) {
  const params = new URLSearchParams()
  if (tab) params.set('tab', tab)
  if (group) params.set('group', group)
  if (q) params.set('q', q)
  if (file) params.set('file', file)
  if (owner) params.set('owner', owner)
  const qs = params.toString()
  return qs ? `${to}?${qs}` : to
}

// The /configs/:id URL for an editor id, LINKS entry or not.
//
// configLink() below stays the *cross-page* builder: it returns null for a slug
// with no LINKS entry, and its callers filter on that, so it can't be the one
// that answers for every editor (10 of the 28 registry ids have no entry, and
// nor do the bespoke pages). This one is for surfaces that address editors by
// id — the Configs listing. Never hand-concatenate `/configs/…` anywhere.
/**
 * The Settings page showing one section whole, by its title (the page's filter
 * box shows a section alone when the filter is its name): `RCon` → /settings?q=RCon.
 */
export function settingsSectionLink(title) {
  return `/settings?q=${encodeURIComponent(title)}`
}

// The START MOD owner a manual XML file's merged blocks carry, and back.
// A hand-kept copy of app/xml/xml_manual.py MANUAL_MOD_PREFIX.
const MANUAL_PREFIX = '@user:'
export const manualMarker = (id) => `${MANUAL_PREFIX}${id}`
export const manualIdOf = (owner) =>
  owner && owner.startsWith(MANUAL_PREFIX) ? owner.slice(MANUAL_PREFIX.length) : null

/**
 * The Custom config tab with one manual XML file's form open (ModXmlPage reads
 * `?file=`), scrolled to the Manual XML files card.
 */
export function manualFileLink(id) {
  return `${withParams('/configs', { tab: CONFIGS_MOD_XML_TAB, file: id })}#manual-files`
}

export function editorLink(id, opts) {
  return withParams(`/configs/${encodeURIComponent(id)}`, opts)
}

// Editors shown as one, with a switch between them at the top of the drawer:
// what the players read, from the mission's messages.xml and from Zedstead's own
// RCon restart warnings (config.json). Each keeps its own Save and versions.
const EDITOR_SIBLING_GROUPS = [
  [
    { id: 'messages', label: 'messages.xml' },
    { id: 'restart-warnings', label: 'Restart warnings' },
  ],
]

// The group an editor id belongs to, or null.
export function editorSiblings(id) {
  return EDITOR_SIBLING_GROUPS.find((g) => g.some((e) => e.id === id)) || null
}

// Build a /configs/... URL from a cross-page LINKS slug. Null when the slug has
// no entry (callers rely on that to drop the link).
export function configLink(key, opts) {
  const entry = LINKS[key]
  if (!entry) return null
  return withParams(entry.to, opts)
}

// Deep link into the map editor focused on one event (see urlState's ?event=).
export function mapEventLink(name) {
  return `/map-editor?layers=spawns&event=${encodeURIComponent(name)}`
}

// The mod page's spawn test, opened in the map editor to place it on the full
// map. The editor reads the three keys once (urlState.spawnTest) and enters its
// spawn-test mode; `classname` and `count` may be empty, the bar asks for them.
export function spawnTestMapLink({ mod, classname, count }) {
  const query = new URLSearchParams({ spawntest: mod })
  if (classname) query.set('class', classname)
  if (count !== null && count !== undefined && count !== '') query.set('count', String(count))
  return `/map-editor?${query}`
}

// The per-mod page. `ref` is a mod name (`@CF`) OR a workshop id
// (`1559212036`) — GET /api/mods/{ref} resolves either, so callers link with
// whichever they happen to hold. Always build the URL through here; never
// concatenate `/mods/…` by hand.
//
// A workshop id is the canonical, prettiest form (`/mods/1559212036`) and
// ModPage rewrites a name URL to it once it knows one. For a name we drop the
// leading `@` — the backend re-adds it (`ensure_at_prefix`) — so the URL is
// `/mods/CF`, not `/mods/%40CF`. Not dropped when the rest is all digits: a
// mod called `@12345` would then read as a workshop id.
// Custom config with one mod's XML marked (`?mod=`, ModXmlPage). The hash
// scrolls to the card that holds its files: the mod's own files, or the
// manual files when it has only those.
export function modXmlLink(modName, { manualOnly = false } = {}) {
  const hash = manualOnly ? 'manual-files' : 'mod-files'
  return `${LINKS['mod-xml'].to}&mod=${encodeURIComponent(modName)}#${hash}`
}

export function modLink(ref) {
  const s = String(ref ?? '')
  const bare = s.startsWith('@') && !/^\d+$/.test(s.slice(1)) ? s.slice(1) : s
  return `/mods/${encodeURIComponent(bare)}`
}

// The status panel's edit mode: the Dashboard with this query (InfoBar.jsx).
export const STATUS_EDIT_PARAM = 'edit'
export const STATUS_EDIT_VALUE = 'status'
export const STATUS_EDIT_LINK = `/?${STATUS_EDIT_PARAM}=${STATUS_EDIT_VALUE}`
