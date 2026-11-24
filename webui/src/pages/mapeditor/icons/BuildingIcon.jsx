// A small glyph before a building kind in the Loot finder: the one its name's
// words ask for (components/nameIcons.js: Land_Wreck_Caravan is a caravan),
// else one per usage (mapgroupproto.xml's <usage>).
//
// The glyphs are Game-icons (game-icons.net, CC BY 3.0) through react-icons/gi,
// like the item icons (components/ItemIcon.jsx): webui/licenses/game-icons.txt
// credits each author, and a glyph added here needs its line there.

import {
  GiBarn, GiBiohazard, GiCarousel, GiDeerTrack, GiDesk, GiFactory, GiFireAxe,
  GiGreekTemple, GiHospitalCross, GiHouse, GiHut, GiLighthouse, GiMilitaryFort,
  GiModernCity, GiPoliceBadge, GiPresent, GiPrisoner, GiSchoolBag,
} from 'react-icons/gi'
import { nameIcon } from '../../../components/nameIcons.js'

const BY_USAGE = {
  farm: GiBarn,
  industrial: GiFactory,
  village: GiHouse,
  town: GiModernCity,
  hunting: GiDeerTrack,
  coast: GiLighthouse,
  military: GiMilitaryFort,
  seasonalevent: GiPresent,
  police: GiPoliceBadge,
  medic: GiHospitalCross,
  office: GiDesk,
  firefighter: GiFireAxe,
  school: GiSchoolBag,
  prison: GiPrisoner,
  historical: GiGreekTemple,
  lunapark: GiCarousel,
  contaminatedarea: GiBiohazard,
}

/** The usage's own glyph, or the hut for none or an unknown one. */
export function usageIcon(usage) {
  return BY_USAGE[String(usage || '').toLowerCase()] ?? GiHut
}

/** A building kind's glyph: its name's, else its first usage's. */
export function buildingIcon(name, usages) {
  return nameIcon(name) ?? usageIcon(usages?.[0])
}

export default function BuildingIcon({ name, usages, usage }) {
  const Icon = usage !== undefined ? usageIcon(usage) : buildingIcon(name, usages)
  return <Icon className="item-icon" aria-hidden="true" />
}
