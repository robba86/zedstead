// Marker icon selection and event categorisation.
//
// Ported from the vanilla editor's icons.js. Icon choice is regex matching on
// the event/trader name against ordered rule tables — first match wins, so
// specific items must stay above their category fallbacks.
//
// Dropped in the port: initTippy() (the app has one shared react-tooltip
// instance) and getLocationIcon/locationMarkerSvg (location markers render no
// icon at all — their permanent tooltip is the visual).

import { markerIcon, markerIconComponent } from './markerIcons'
import { nameIcon } from '../../../components/nameIcons.js'
import { html, raw } from '../leaflet/html'

/** An icon as SVG markup: `icon` is a marker name or a glyph component. */
export function renderSvgIcon(iconName, { fillOpacity = 0.3, size = 14 } = {}) {
  const icon = markerIcon(iconName)
  if (!icon) return fallbackCircle(size)
  return html`<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="${icon.viewBox}" fill="currentColor" fill-opacity="${fillOpacity}" stroke="none">${raw(icon.svg)}</svg>`
}

export function fallbackCircle(size = 14) {
  const r = size / 2
  return html`<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}"><circle cx="${r}" cy="${r}" r="${r - 1.5}" fill="currentColor" fill-opacity="0.4" stroke="currentColor" stroke-width="1.5"/></svg>`
}

// ---------------------------------------------------------------------------
// Spawn markers (item-level matching by event name)
// ---------------------------------------------------------------------------

// A word that starts a name part: after a non-letter ("Old_Bike") or as a
// CamelCase part ("AmbientHen"). Short words that hide inside longer ones
// ("Share", "Kitchen", "motorbike") need it; /i would make every letter a
// word start.
const word = (...words) =>
  new RegExp(
    words
      .map((w) => `(^|[^A-Za-z])${w}|${w[0].toUpperCase()}${w.slice(1)}`)
      .join('|')
  )

const SPAWN_ICON_RULES = [
  // Animals
  [/cow/i, 'animals-cow'],
  [/deer|stag/i, 'animals-deer'],
  [/fox/i, 'animals-fox'],
  [/wolf/i, 'animals-wolf'],
  [/boar|pig|sus/i, 'animals-boar'],
  [/sheep/i, 'animals-sheep'],
  [word('bear'), 'animals-bear'],
  [/goat/i, 'animals-goat'],
  [word('hare', 'rabbit'), 'animals-hare'],
  [/goose/i, 'animals-goose'],
  [word('hen', 'chicken'), 'animals-chicken'],
  [/animal/i, 'animals-cow'],

  // Vehicles. Bicycles first: mod bike events ("Old_Bike", "Lady_Bicycle",
  // "VehicleHunterz_Old_Bike") otherwise fall through to the generic /vehicle/
  // rule and get drawn as a car. The word-start guard keeps "motorbike" out of
  // it: VehicleMotorbike gets its own glyph on the next line.
  [word('bicycle', 'bike'), 'vehicles-bicycle'],
  [/motorbike|motorcycle/i, 'vehicles-motorbike'],
  [/sedan/i, 'vehicles-sedan'],
  // Offroad first: "VehicleOffroadHatchback" is the Ada 4x4, not a city car.
  [/offroad/i, 'vehicles-offroad'],
  [/hatchback/i, 'vehicles-hatchback'],
  [/bus/i, 'transit-bus'],
  [/truck/i, 'vehicles-truck'],
  [/boat/i, 'vehicles-boat'],
  [/train/i, 'satic-train'],
  [/vehicle/i, 'vehicles-sedan'],

  // Crash / static events
  [/heli.*crash|crash.*heli|heli/i, 'static-heli-crash'],
  [/santa.*crash/i, 'static-santa-crash'],
  [/airplane|plane/i, 'static-ariplane-crate'],
  [/crash/i, 'static-heli-crash'],

  // Infected
  [/infected.*police|police.*infected/i, 'infected-police'],
  [/infected.*fire|fire.*infected/i, 'infected-firefighter'],
  [/infected.*medic|medic.*infected/i, 'infected-medical'],
  [/infected.*prison|prison.*infected/i, 'infected-prisoner'],
  [/infected.*city|city.*infected/i, 'infected-city'],
  [/infected|zombie/i, 'infected'],

  [/contam/i, 'static-contaminated-area'],

  // Ambient, above the Static fallback: "StaticBonfire" and
  // "StaticChristmasTree" were drawn as the convoy's tank.
  [/bonfire|campfire/i, 'static-bonfire'],
  [/christmas/i, 'static-christmastree'],
]

// Below the name's own words (components/nameIcons.js): a word that names a
// thing ("StaticWreckCaravan", "ItemBarrel") wins over these group fallbacks.
const SPAWN_FALLBACK_RULES = [
  // Static / structures
  [/military/i, 'static-military-convoy'],
  [/police/i, 'static-police-car'],
  [/wreck/i, 'trader-land-wreck'],
  // No catch-all for Static: a tank on "StaticTrikes" says more than the
  // plain circle and says it wrong.

  [/loot/i, 'item-planks'],

  [/ambient/i, 'static-bonfire'],

  [/trader/i, 'trader-barricade'],
  [/item/i, 'item-planks'],
]

const firstRule = (rules, name) => rules.find(([pattern]) => pattern.test(name))?.[1] ?? null

/** A spawn event's marker icon: the kind rules, the name's own words, then the group fallbacks. */
function getSpawnIconName(eventName) {
  return (
    firstRule(SPAWN_ICON_RULES, eventName) ??
    nameIcon(eventName) ??
    firstRule(SPAWN_FALLBACK_RULES, eventName)
  )
}

/** The glyph component a spawn event's marker draws, or null for the plain circle. */
export function spawnIconComponent(eventName) {
  return eventName ? markerIconComponent(getSpawnIconName(eventName)) : null
}

export function spawnMarkerSvg(color, eventName) {
  const iconName = eventName ? getSpawnIconName(eventName) : null
  if (iconName) return renderSvgIcon(iconName)
  return fallbackCircle()
}

// ---------------------------------------------------------------------------
// Event categories (sidebar grouping)
// ---------------------------------------------------------------------------

const EVENT_CATEGORY_RULES = [
  [/cow|deer|stag|fox|wolf|boar|pig|sus|sheep|animal|bear|rabbit|hen|goat/i, 'Animals'],
  [
    /sedan|hatchback|offroad|bus|truck|boat|train|vehicle|car|van|suv|jeep|motorcycle|bike|atv|quad|helicopter/i,
    'Vehicles',
  ],
  [/heli.*crash|crash.*heli|santa.*crash|airplane|plane|crash/i, 'Crashes'],
  [
    /infected.*police|police.*infected|infected.*fire|fire.*infected|infected.*medic|medic.*infected|infected.*prison|prison.*infected|infected.*city|city.*infected|infected|zombie/i,
    'Infected',
  ],
  [/contam/i, 'Contamination'],
  [/military|police|static/i, 'Static'],
  [/loot|item/i, 'Loot'],
  [/bonfire|campfire|christmas|ambient/i, 'Ambient'],
  [/trader/i, 'Trader'],
]

export function getEventCategory(eventName) {
  for (const [pattern, category] of EVENT_CATEGORY_RULES) {
    if (pattern.test(eventName)) return category
  }
  return 'Other'
}

// ---------------------------------------------------------------------------
// Player spawn markers (geometric shapes)
// ---------------------------------------------------------------------------

const shape = (fill, path) =>
  html`<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="${fill}" fill-opacity="0.3" stroke="${fill}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">${raw(path)}</svg>`

const DIAMOND = '<polygon points="12 2 22 12 12 22 2 12"/>'
const TRIANGLE = '<path d="M12 2l9 18H3L12 2z"/>'

export const generatorMarkerSvg = () => shape('#2196f3', DIAMOND)
export const freshSpawnMarkerSvg = () => shape('#4caf50', TRIANGLE)
export const hopSpawnMarkerSvg = () => shape('#ff9800', DIAMOND)
export const travelSpawnMarkerSvg = () => shape('#7e57c2', DIAMOND)

// ---------------------------------------------------------------------------
// Location type colours (sidebar dots; map markers are label-only)
// ---------------------------------------------------------------------------

// One per type in app/maps/locations.py LOCATION_TYPES (a test checks).
const LOCATION_COLORS = {
  Airfield: '#90a4ae',
  Camp: '#8d6e27',
  Capital: '#ffd700',
  City: '#ff9800',
  Hill: '#a0845c',
  Local: '#64b5f6',
  Marine: '#29b6f6',
  Military: '#6b8e23',
  RailroadStation: '#ab47bc',
  Ruin: '#b71c1c',
  Village: '#66bb6a',
}
const DEFAULT_LOCATION_COLOR = '#ef5350'

/** Every type a place name can have, for the own-name type picker. */
export const LOCATION_TYPE_NAMES = Object.keys(LOCATION_COLORS)

export function locationTypeColor(type) {
  return LOCATION_COLORS[type] || DEFAULT_LOCATION_COLOR
}

// ---------------------------------------------------------------------------
// Trader markers (TraderPlus NPCs & objects)
// ---------------------------------------------------------------------------

const TRADER_NPC_ICON_RULES = [
  [/weapon/i, 'trader-kevin-weapon-trader'],
  [/tool/i, 'trader-bob-tool-trader'],
  [/cloth/i, 'trader-john-clothes-trader'],
  [/car|vehicle/i, 'vehicles-sedan'],
  [/food/i, 'trader-fastfood'],
  [/atm/i, 'trader-atm'],
]

const TRADER_OBJECT_ICON_RULES = [
  [/land_wreck/i, 'trader-land-wreck'],
  [/land_garage|garage/i, 'trader-garage'],
  [/trailroof/i, 'trader-land-misc-trailroof-small'],
  [/well_pump|pump_yellow/i, 'trader-land-misc-well-pump-yellow'],
  [/atm/i, 'trader-atm'],
]

export function traderNpcMarkerSvg(name) {
  const iconName = name ? firstRule(TRADER_NPC_ICON_RULES, name) : null
  return renderSvgIcon(iconName || 'trader-barricade', { fillOpacity: 0.35, size: 16 })
}

/** A trader object's marker: its name's own words (Land_Wreck_Caravan), then the trader rules. */
export function traderObjectMarkerSvg(name) {
  const iconName = name ? nameIcon(name) ?? firstRule(TRADER_OBJECT_ICON_RULES, name) : null
  return renderSvgIcon(iconName || 'trader-barricade', { fillOpacity: 0.35, size: 14 })
}

export function garageNpcMarkerSvg() {
  return renderSvgIcon('trader-garage', { fillOpacity: 0.5, size: 16 })
}

// ---------------------------------------------------------------------------
// Event colours
// ---------------------------------------------------------------------------

// 16-colour palette, ported from utils.js. Assignment is now a hash of the
// name rather than first-seen order: the old module-global counter handed out
// slots in iteration order, so colours reshuffled whenever the editor
// remounted or the event list came back differently sorted.
const EVENT_PALETTE = [
  '#e57373', '#f06292', '#ba68c8', '#9575cd',
  '#7986cb', '#64b5f6', '#4fc3f7', '#4dd0e1',
  '#4db6ac', '#81c784', '#aed581', '#dce775',
  '#ffd54f', '#ffb74d', '#ff8a65', '#a1887f',
]

export function getEventColor(name) {
  let hash = 0
  for (let i = 0; i < name.length; i++) {
    hash = (hash * 31 + name.charCodeAt(i)) | 0
  }
  return EVENT_PALETTE[Math.abs(hash) % EVENT_PALETTE.length]
}
