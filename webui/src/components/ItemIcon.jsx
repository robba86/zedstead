// A small glyph before an item's classname, one per item category, so a long
// item list reads at a glance. The glyphs are Game-icons (game-icons.net,
// CC BY 3.0) through react-icons/gi: webui/licenses/game-icons.txt credits
// each author, and a glyph added here needs its line there.
//
// A name whose own words name a thing (Barrel_Green, SeaChest) gets that
// thing's glyph first, from components/nameIcons.js; the category's glyph is
// the fallback.
//
// The category comes from GET /api/types/categories (economy_analyzer.
// icon_category), fetched once per page load and shared by every icon. It
// files medicine as medical, where types.xml says tools. A row may also pass
// `category`: the fallback for a name the live files no longer hold, such
// as a vanilla item an admin removed.

import { useEffect, useState } from 'react'
import {
  GiAk47, GiAlarmClock, GiAmmoBox, GiArmBandage, GiBackpack, GiBaseballBat, GiBayonet, GiBearFace, GiBinoculars,
  GiBookCover, GiBrickWall, GiBrokenBone, GiBullets, GiCampfire, GiCardboardBox,
  GiCaptainHatProfile, GiCarWheel, GiCityCar, GiCrossbow, GiCrosshair, GiDeer, GiFedora, GiFireworkRocket, GiFishingBoat,
  GiFishingHook, GiGasMask, GiGloves, GiBoots, GiTrousers, GiGrenade, GiGunStock, GiMachineGunMagazine, GiMedicalPack, GiMushroom,
  GiOpenedFoodCan, GiPistolGun, GiPlainDagger, GiShamblingZombie, GiShinyApple, GiShirt,
  GiShotgun, GiSpray, GiSunglasses, GiSteamLocomotive, GiSyringe, GiToolbox, GiWatchtower, GiWaterBottle,
  GiWoodenFence, GiCash, GiWallet, GiLockPicking, GiTicket, GiCargoCrate,
} from 'react-icons/gi'
import { api } from '../api'
import { nameIcon } from './nameIcons.js'
import { humanizeKey } from '../fieldLabel.js'
import { tooltipAttrs } from './Tooltip.jsx'

const ICONS = {
  weapons: GiAk47,
  food: GiOpenedFoodCan,
  clothing: GiShirt,
  containers: GiBackpack,
  tools: GiToolbox,
  vehicles: GiCarWheel,
  lootdispatch: GiCarWheel,
  explosives: GiGrenade,
  medical: GiMedicalPack,
  books: GiBookCover,
  infected: GiShamblingZombie,
  animals: GiDeer,
  boats: GiFishingBoat,
  world: GiBrickWall,
  // Finer kinds inside a category (economy_analyzer._ICON_KINDS).
  bone: GiBrokenBone,
  knife: GiPlainDagger,
  clock: GiAlarmClock,
  fireworks: GiFireworkRocket,
  injector: GiSyringe,
  spray: GiSpray,
  bait: GiFishingHook,
  bat: GiBaseballBat,
  binoculars: GiBinoculars,
  bayonet: GiBayonet,
  magazine: GiMachineGunMagazine,
  ammo: GiBullets,
  buttstock: GiGunStock,
  crossbow: GiCrossbow,
  shotgun: GiShotgun,
  optic: GiCrosshair,
  pistol: GiPistolGun,
  mushroom: GiMushroom,
  apple: GiShinyApple,
  gloves: GiGloves,
  glasses: GiSunglasses,
  cap: GiCaptainHatProfile, // Game-icons has no baseball cap: a peaked cap reads closest
  hat: GiFedora,
  armband: GiArmBandage, // Game-icons has no armband: a band round an arm reads closest
  shoes: GiBoots,
  pants: GiTrousers,
  mask: GiGasMask,
  ammobox: GiAmmoBox,
  teddy: GiBearFace, // Game-icons has no teddy bear: the round face reads closest
  train: GiSteamLocomotive,
  car: GiCityCar,
  bonfire: GiCampfire,
  fence: GiWoodenFence,
  bottle: GiWaterBottle,
  watchtower: GiWatchtower,
  // TraderPlus's items (economy_analyzer._ICON_TRADERPLUS).
  money: GiCash,
  wallet: GiWallet,
  lockpick: GiLockPicking,
  receipt: GiTicket, // Game-icons has no receipt: a paper slip reads closest
  cargo: GiCargoCrate,
}

let lookup = null

function loadLookup() {
  lookup ??= api
    .get('/api/types/categories')
    .then((res) => res.categories)
    .catch(() => {
      lookup = null // a failed fetch is tried again by the next page
      return {}
    })
  return lookup
}

/** The item's icon category: the looked-up one, else the one given. */
export function useItemCategory(name, given) {
  const [found, setFound] = useState(null)
  useEffect(() => {
    let live = true
    loadLookup().then((map) => live && setFound(map[name.toLowerCase()] ?? given ?? 'other'))
    return () => {
      live = false
    }
  }, [name, given])
  return found
}

/** The whole name → icon category map (lowercased names), null while it loads. */
export function useItemCategories() {
  const [map, setMap] = useState(null)
  useEffect(() => {
    let live = true
    loadLookup().then((found) => live && setMap(found))
    return () => {
      live = false
    }
  }, [])
  return map
}

/** The glyph component for an item: its name's own (nameIcons.js), else its
 * icon category's. The map's canvas badges draw it too. */
export function itemIconComponent(category, name) {
  return nameIcon(name) ?? ICONS[category] ?? GiCardboardBox
}

export default function ItemIcon({ name, category }) {
  const cat = useItemCategory(name, category)
  const named = nameIcon(name)
  // An empty box of the same size while the lookup loads, so names stay put.
  if (!cat && !named) return <span className="item-icon" aria-hidden="true" />
  const Icon = named ?? itemIconComponent(cat)
  return <Icon className="item-icon" aria-label={humanizeKey(cat || 'other')} role="img" />
}

// The colour or material an item's name ends in (AK_WoodBttstck, TShirt_Green,
// Hatchback_02_Door_1_1_BlueRust), as a small square on the icon's bottom right corner. These
// are data colours, so raw hex is right here (webui/CLAUDE.md, Theme and CSS).
// A pattern is a CSS gradient of its main colours, Spotted a field of dots.
const SWATCHES = {
  black: '#1d1d1f', white: '#f2f2f0', grey: '#8a8d91', gray: '#8a8d91',
  darkgrey: '#4b4e52', lightgrey: '#c5c7ca', red: '#b8312f', crimson: '#9e1530',
  wine: '#6e1f33', pink: '#e68fb5', orange: '#e2792a', yellow: '#e3c335',
  lime: '#93d13c', green: '#3f7a3a', olive: '#6b6c30', khaki: '#a99b65',
  blue: '#2f5fb5', lightblue: '#8ac2e8', navy: '#22315c', navyblue: '#22315c',
  violet: '#7a4cb5', purple: '#6a3fa0', brown: '#6b4a2b', darkbrown: '#4a321d', beige: '#d9c8a1',
  tan: '#c8a979', natural: '#cfb78b', wood: '#8b5a2b', birch: '#e6dcc3',
  rust: '#8b3e1a', gold: '#d4af37',
  camo: 'repeating-linear-gradient(135deg, #4b5a2a 0 3px, #8a7b4f 3px 6px, #2e3320 6px 9px)',
  woodland: 'repeating-linear-gradient(135deg, #3f4f2a 0 3px, #6b5536 3px 6px, #1f2a18 6px 9px)',
  desert: 'repeating-linear-gradient(135deg, #c8a979 0 3px, #9c7f55 3px 6px, #e0cfa8 6px 9px)',
  winter: 'repeating-linear-gradient(135deg, #f2f2f0 0 3px, #b9c2cc 3px 6px)',
  autumn: 'repeating-linear-gradient(135deg, #a2522a 0 3px, #c99a3c 3px 6px, #5b3a20 6px 9px)',
  summer: 'repeating-linear-gradient(135deg, #5f8a3a 0 3px, #a8b05a 3px 6px)',
  spring: 'repeating-linear-gradient(135deg, #7ab34a 0 3px, #c7d97a 3px 6px)',
  mossy: 'repeating-linear-gradient(135deg, #4f6b2f 0 3px, #7d8a4a 3px 6px)',
  // Dark spots on white (Animal_BosTaurus_Spotted, a spotted cow or hen).
  spotted: 'radial-gradient(circle, #1d1d1f 0 1.5px, transparent 2px) 0 0 / 5px 5px, #f2f2f0',
}

/** `{label, background}` for the colour or material a name ends in, or null. */
export function swatchFor(name) {
  const part = name.split('_').pop()
  const last = part.toLowerCase()
  // Rust on a car part keeps the paint colour (BlueRust is a rusty blue), and
  // a flare keeps its light's (Ammo_FlareGreen).
  const base = last.replace(/^flare(?=.)/, '').replace(/(?<=.)rust$/, '')
  // A material at the start of the last part: WoodBttstck is a wooden stock.
  const key = SWATCHES[base] ? base : base.startsWith('wood') && base !== 'woodland' ? 'wood' : null
  // The label is the name's own part (BlueRust), or Wood for a wooden part.
  return key && { label: key === base ? part : 'Wood', background: SWATCHES[key] }
}

/** One solid colour for a name's swatch (a canvas badge cannot draw a CSS
 * gradient, so a pattern gives its first colour), or null. */
export function swatchColor(name) {
  const swatch = swatchFor(name)
  return swatch ? swatch.background.match(/#[0-9a-f]{6}/i)[0] : null
}

/** The swatch on the icon's bottom right corner, or nothing. Building rows use it too. */
export function ItemSwatch({ name }) {
  const swatch = swatchFor(name)
  if (!swatch) return null
  return (
    <span
      className="item-swatch"
      style={{ background: swatch.background }}
      role="img"
      aria-label={swatch.label}
      {...tooltipAttrs(swatch.label)}
    />
  )
}

/** An item name with its icon (and swatch) before it, for a table cell or a list line. */
export function ItemName({ name, category, children }) {
  return (
    <span className="item-name">
      <span className="item-icon-wrap">
        <ItemIcon name={name} category={category} />
        <ItemSwatch name={name} />
      </span>
      {children ?? name}
    </span>
  )
}
