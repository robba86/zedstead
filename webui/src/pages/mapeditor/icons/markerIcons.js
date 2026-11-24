// Marker glyphs, by the names iconRules.js uses.
//
// Every glyph is from Game-icons (game-icons.net, CC BY 3.0), taken through
// react-icons/gi, so only the ones imported here reach the bundle. CC BY asks
// for the authors' names: webui/licenses/game-icons.txt lists who drew each
// one, and the build copies it into third-party-licenses.txt. A glyph added
// here needs its line there.
//
// Only import this from pages/mapeditor/: it is part of the editor's lazy
// chunk, which no other page pays for.

import {
  GiAk47, GiAirplane, GiBattleTank, GiBearHead, GiBiohazard, GiBoar, GiBus,
  GiCampfire, GiCarWheel, GiCash, GiChicken, GiCityCar, GiCow, GiDeer,
  GiDutchBike, GiFire, GiFishingBoat, GiFox, GiGoat, GiGoose, GiHamburger, GiHelicopter, GiHut, GiJeep,
  GiMechanicGarage, GiMedicalPack, GiPineTree, GiPlanks, GiPoliceCar,
  GiPoliceOfficerHead, GiPresent, GiPrisoner, GiRabbit, GiRaiseZombie,
  GiShamblingZombie, GiSheep, GiShirt, GiShop, GiSteamLocomotive, GiToolbox,
  GiTruck, GiWell, GiWolfHead,
} from 'react-icons/gi'
// Game-icons has no motorcycle: this one is Phosphor (MIT, webui/licenses/phosphor.txt).
import { PiMotorcycleFill } from 'react-icons/pi'
import { escapeHtml } from '../leaflet/html'

const ICONS = {
  'animals-bear': GiBearHead,
  'animals-boar': GiBoar,
  'animals-chicken': GiChicken,
  'animals-cow': GiCow,
  'animals-deer': GiDeer,
  'animals-fox': GiFox,
  'animals-goat': GiGoat,
  'animals-goose': GiGoose,
  'animals-hare': GiRabbit,
  'animals-sheep': GiSheep,
  'animals-wolf': GiWolfHead,
  'vehicles-bicycle': GiDutchBike,
  'vehicles-motorbike': PiMotorcycleFill,
  'vehicles-sedan': GiCityCar,
  'vehicles-hatchback': GiCityCar,
  'vehicles-offroad': GiJeep,
  'vehicles-truck': GiTruck,
  'vehicles-boat': GiFishingBoat,
  'transit-bus': GiBus,
  'satic-train': GiSteamLocomotive,
  'static-heli-crash': GiHelicopter,
  'static-santa-crash': GiPresent,
  'static-ariplane-crate': GiAirplane,
  'infected': GiShamblingZombie,
  'infected-city': GiRaiseZombie,
  'infected-police': GiPoliceOfficerHead,
  'infected-firefighter': GiFire,
  'infected-medical': GiMedicalPack,
  'infected-prisoner': GiPrisoner,
  'static-contaminated-area': GiBiohazard,
  'static-military-convoy': GiBattleTank,
  'static-police-car': GiPoliceCar,
  'item-planks': GiPlanks,
  'static-bonfire': GiCampfire,
  'static-christmastree': GiPineTree,
  'trader-barricade': GiShop,
  'trader-kevin-weapon-trader': GiAk47,
  'trader-bob-tool-trader': GiToolbox,
  'trader-john-clothes-trader': GiShirt,
  'trader-fastfood': GiHamburger,
  'trader-atm': GiCash,
  'trader-land-wreck': GiCarWheel,
  'trader-garage': GiMechanicGarage,
  'trader-land-misc-trailroof-small': GiHut,
  'trader-land-misc-well-pump-yellow': GiWell,
}

const kebab = (name) => name.replace(/[A-Z]/g, (c) => '-' + c.toLowerCase())

// A react-icons component called as a plain function returns its element
// tree without rendering anything: the <svg> carries `attr.viewBox`, and its
// children are the shapes, each with its attributes as props.
function markup(nodes) {
  return [].concat(nodes ?? []).map((node) => {
    const { children, ...attrs } = node.props
    const attrText = Object.entries(attrs)
      .map(([k, v]) => ' ' + kebab(k) + '="' + escapeHtml(v) + '"')
      .join('')
    return '<' + node.type + attrText + '>' + markup(children) + '</' + node.type + '>'
  }).join('')
}

const cache = new Map()

/** The glyph component for a marker name, or the component itself (nameIcons.js
 * hands one over), or null for a name with no glyph. */
export function markerIconComponent(nameOrIcon) {
  return typeof nameOrIcon === 'function' ? nameOrIcon : ICONS[nameOrIcon] ?? null
}

/** `{viewBox, svg}` for a marker name or a glyph component (nameIcons.js
 * hands one over), or null for a name with no glyph. */
export function markerIcon(nameOrIcon) {
  const Icon = markerIconComponent(nameOrIcon)
  if (!Icon) return null
  if (!cache.has(Icon)) {
    const tree = Icon({})
    cache.set(Icon, { viewBox: tree.props.attr.viewBox, svg: markup(tree.props.children) })
  }
  return cache.get(Icon)
}
