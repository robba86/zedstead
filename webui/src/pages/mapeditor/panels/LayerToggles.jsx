// Layer visibility checkboxes. State lives in the URL (?layers=a,b,c) so a
// view is linkable — the Analysis page deep-links here with layers preset.
//
// Every layer but Locations draws data belonging to the *running mission*, so
// it is disabled while previewing another terrain: those coordinates mean
// nothing on a map they were not written for. The URL keeps the ticks, so
// switching back restores the view you left.

import { Switch } from '../../../components/FlagSwitch.jsx'
import ListSection from '../../../components/ListSection.jsx'
import SectionHeightHandle from './SectionHeightHandle.jsx'
import useStoredOpen from '../state/useStoredOpen.js'
import { useEditor } from '../state/MapEditorProvider'
import { HIDDEN_LAYERS } from '../state/urlState.js'
import CollisionPanel from './CollisionPanel.jsx'
import LootPanel from './LootPanel.jsx'
import OwnMapImage from './OwnMapImage.jsx'
import { ROAD_LEGEND } from '../leaflet/useRoadLayer'
import { AddPlaceNameLink } from './OwnPlaceNames'

// Open/closed is per browser (state/useStoredOpen.js).
const OPEN_KEY = 'zsm.mapeditor.layers.open'
const BASE_OPEN_KEY = 'zsm.mapeditor.basemap.open'

// Tooltip copy is lifted verbatim from the old editor's data-tippy-content.
// Layers that own a sidebar tab: ticking one jumps to the tab that edits it,
// so the layer you just switched on is the one the panel is showing.
const LAYER_TABS = {
  spawns: 'spawns',
  playerspawns: 'player-spawns',
  traders: 'traders',
  objects: 'mod-items',
}

const ALL_LAYERS = [
  {
    key: 'locations',
    label: 'Locations',
    hint: 'Town, city and landmark names: your own, which you add, export and import under Locations, and an official map\'s own names from the world config on your server',
    icon: (
      <>
        <path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0Z" />
        <circle cx="12" cy="10" r="3" />
      </>
    ),
  },
  {
    key: 'grid',
    label: 'Coordinate grid',
    hint: 'Grid lines every kilometre (and every 100 m when zoomed in), labelled with the game X and Z the cursor readout shows',
    icon: (
      <>
        <rect x="3" y="3" width="18" height="18" rx="1" />
        <line x1="9" y1="3" x2="9" y2="21" />
        <line x1="15" y1="3" x2="15" y2="21" />
        <line x1="3" y1="9" x2="21" y2="9" />
        <line x1="3" y1="15" x2="21" y2="15" />
      </>
    ),
  },
  {
    key: 'spawns',
    missionData: true,
    label: 'Event Spawns',
    hint: 'Event spawn point markers from cfgeventspawns.xml',
    icon: (
      <>
        <circle cx="12" cy="12" r="10" />
        <path d="M12 2v4" />
        <path d="M12 18v4" />
        <path d="M2 12h4" />
        <path d="M18 12h4" />
      </>
    ),
  },
  {
    key: 'labels',
    missionData: true,
    sub: true,
    label: 'Spawn labels',
    hint: 'Show/hide event name labels on spawn markers',
    icon: (
      <>
        <polyline points="4 7 4 4 20 4 20 7" />
        <line x1="9" y1="20" x2="15" y2="20" />
        <line x1="12" y1="4" x2="12" y2="20" />
      </>
    ),
  },
  {
    key: 'zones',
    missionData: true,
    sub: true,
    label: 'Radius circles',
    hint: 'Radius circles around spawn points — inner = saferadius (exclusion zone), outer = distanceradius (spawn spread). Visible at zoom 3+',
    icon: (
      <>
        <circle cx="12" cy="12" r="10" />
        <circle cx="12" cy="12" r="6" />
        <circle cx="12" cy="12" r="2" />
      </>
    ),
  },
  {
    key: 'oob',
    missionData: true,
    label: 'Out of bounds',
    hint: 'Include spawn points outside the map boundaries (x/z < 0 or > map size)',
    icon: (
      <>
        <polyline points="15 3 21 3 21 9" />
        <polyline points="9 21 3 21 3 15" />
        <line x1="21" y1="3" x2="14" y2="10" />
        <line x1="3" y1="21" x2="10" y2="14" />
      </>
    ),
  },
  {
    key: 'playerspawns',
    missionData: true,
    label: 'Player Spawns',
    hint: 'Player spawn positions from cfgPlayerSpawnPoints.xml (blue diamonds = generators, green triangles = fresh spawns)',
    icon: (
      <>
        <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />
        <circle cx="9" cy="7" r="4" />
        <path d="M22 21v-2a4 4 0 0 0-3-3.87" />
        <path d="M16 3.13a4 4 0 0 1 0 7.75" />
      </>
    ),
  },
  {
    key: 'traders',
    missionData: true,
    label: 'Traders',
    hint: 'TraderPlus trader NPCs (gold) and trade objects (brown) from TraderPlusGeneralConfig.json',
    traderplusOnly: true,
    icon: (
      <>
        <path d="M3 9l9-6 9 6" />
        <rect x="5" y="9" width="14" height="13" />
        <rect x="9" y="14" width="6" height="8" />
      </>
    ),
  },
  // Expansion's own map data — four separate files, so four separate layers
  // (unlike Traders, where one checkbox covers three TraderPlus files that are
  // one feature to an admin).
  {
    key: 'exptraderzones',
    missionData: true,
    label: 'Expansion Trader Zones',
    hint: 'Expansion trading zones (violet) from <mission>/expansion/traderzones — drag to move, drag the edge to resize, click for prices',
    expansionOnly: true,
    icon: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M12 7v10" />
        <path d="M9 9.5h6" />
        <path d="M9 14.5h6" />
      </>
    ),
  },
  {
    key: 'expsafezones',
    missionData: true,
    label: 'Expansion Safe Zones',
    hint: 'Expansion safe zones (sky blue) from SafeZoneSettings.json — circles, cylinders and polygons. Drag a polygon corner to reshape it',
    expansionOnly: true,
    icon: (
      <>
        <path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6Z" />
      </>
    ),
  },
  {
    key: 'expspawns',
    missionData: true,
    label: 'Expansion Spawn Points',
    hint: "Player spawn positions (pink) from SpawnSettings.json, grouped by location — drag to move one. Players only spawn here when Expansion's spawn selection is on; the Players tab says which system is live",
    expansionOnly: true,
    icon: (
      <>
        <circle cx="12" cy="12" r="3" />
        <path d="M12 2v4" />
        <path d="M12 18v4" />
        <path d="M2 12h4" />
        <path d="M18 12h4" />
      </>
    ),
  },
  {
    key: 'underground',
    missionData: true,
    label: 'Underground Triggers',
    hint: 'Where it goes dark underground (teal outer, violet transitional, indigo inner) from cfgundergroundtriggers.json, with the amber breadcrumbs that fade the transition. Drag the square grip to move a box, a breadcrumb to move it, click the box for its size, height and light level. Chernarus ships none of these; Sakhal has thirty',
    icon: (
      <>
        <path d="M3 10l9-6 9 6" />
        <path d="M5 10v9h14v-9" />
        <circle cx="12" cy="15" r="1.6" />
      </>
    ),
  },
  // The Hazard zones family (plans/48 C, D2): one drawing path for every zone
  // that hurts players where they stand.
  {
    key: 'objects',
    missionData: true,
    label: 'Objects',
    hint: 'Objects placed for good from the Items tab, from Zedstead\'s own Object Spawner file (zsm-objects.json, listed in cfggameplay.json). DayZ spawns them at every start while serverDZ.cfg has enableCfgGameplayFile = 1. Remove one in the Items tab',
    icon: (
      <>
        <rect x="5" y="5" width="14" height="14" rx="2" />
        <path d="M5 12h14" />
        <path d="M12 5v14" />
      </>
    ),
  },
  {
    key: 'contaminated',
    missionData: true,
    label: 'Contaminated Areas',
    hint: "The mission's static contaminated areas (amber) from cfgEffectArea.json — drag the centre to move one, the edge handle to resize it. Dynamic contaminated zones are events and draw with the event spawns",
    icon: (
      <>
        <circle cx="12" cy="12" r="8" />
        <path d="M12 8v5" />
        <circle cx="12" cy="16" r="0.8" />
      </>
    ),
  },
  {
    key: 'radzone',
    missionData: true,
    label: 'RadZone Zones',
    hint: "RadZone's radiation zones (lime) from RadiationConfigV1.json — drag the centre to move a zone, the edge handle to resize a circle. A square zone moves as a whole; resize it in the RadZone Zones editor",
    radzoneOnly: true,
    icon: (
      <>
        <circle cx="12" cy="12" r="2" />
        <path d="M12 4a8 8 0 0 1 7 4l-5 3" />
        <path d="M5 8a8 8 0 0 1 7-4" />
        <path d="M7 19l3-5" />
        <path d="M17 19l-3-5" />
      </>
    ),
  },
  {
    key: 'radzonecrates',
    missionData: true,
    sub: true,
    label: 'Loot crates',
    hint: "Where RadZone's loot crates can spawn, from CrateSystemConfigV1.json. Shown, not dragged: edit their positions on that file's Raw tab",
    radzoneOnly: true,
    icon: (
      <>
        <rect x="5" y="8" width="14" height="10" rx="1" />
        <path d="M5 12h14" />
      </>
    ),
  },
  {
    key: 'toxiczone',
    missionData: true,
    label: 'ToxicZone Zones',
    hint: "ToxicZone's static zones (purple) from StaticToxicZoneConfigV5.json — drag the centre to move a zone, the edge handle to resize a circle. Its smoke and crates stay where they are",
    toxiczoneOnly: true,
    icon: (
      <>
        <circle cx="12" cy="12" r="8" />
        <path d="M8 14c1.5-2 2.5-2 4 0s2.5 2 4 0" />
        <path d="M8 10c1.5-2 2.5-2 4 0s2.5 2 4 0" />
      </>
    ),
  },
  {
    key: 'toxicmoving',
    missionData: true,
    sub: true,
    label: 'Moving zones',
    hint: "ToxicZone's moving zones from MovingToxicZoneConfigV5.json: a path from the filled start grip to the hollow end grip. Drag either end; Zedstead works out the path the mod needs when it saves. A red path is one the mod would move wrongly",
    toxiczoneOnly: true,
    icon: (
      <>
        <circle cx="5" cy="17" r="2" />
        <circle cx="19" cy="7" r="2" />
        <path d="M7 16l10-8" strokeDasharray="3 2" />
      </>
    ),
  },
  {
    key: 'toxiccrates',
    missionData: true,
    sub: true,
    label: 'Loot crates',
    hint: "Where ToxicZone's loot crates can spawn, from each static zone. Shown, not dragged: edit their positions on StaticToxicZoneConfigV5.json's Raw tab",
    toxiczoneOnly: true,
    icon: (
      <>
        <rect x="5" y="8" width="14" height="10" rx="1" />
        <path d="M5 12h14" />
      </>
    ),
  },
  {
    key: 'expaipatrols',
    missionData: true,
    label: 'Expansion AI Patrols',
    hint: 'AI patrol routes (red) from AIPatrolSettings.json — the line is the route, drag a waypoint to move it, click the line for the patrol. Patrols that spawn on objects have no route to draw',
    expansionOnly: true,
    icon: (
      <>
        <path d="M4 19l5-5 4 4 7-7" />
        <circle cx="4" cy="19" r="1.6" />
        <circle cx="9" cy="14" r="1.6" />
        <circle cx="13" cy="18" r="1.6" />
        <circle cx="20" cy="11" r="1.6" />
      </>
    ),
  },
  {
    key: 'expairoaming',
    missionData: true,
    label: 'Expansion AI Roaming',
    hint: 'Where roaming AI may spawn (amber) from AILocationSettings.json, plus the grey no-go areas it must stay out of. Drag to move, drag the edge to resize, click for name, type and on/off',
    expansionOnly: true,
    icon: (
      <>
        <circle cx="12" cy="12" r="9" strokeDasharray="4 3" />
        <circle cx="12" cy="12" r="2.5" />
      </>
    ),
  },
  {
    key: 'expmarket',
    missionData: true,
    label: 'Expansion Vehicle Spawns',
    hint: 'Where vehicles bought at a trader appear (teal) from MarketSettings.json: L, A, W and T for land, air, water and train, the arrow is the heading. Drag to move (the height stays), click for heading, height and Copy here',
    expansionOnly: true,
    icon: (
      <>
        <path d="M12 3l4 6h-8z" />
        <circle cx="12" cy="15" r="5" />
      </>
    ),
  },
  {
    key: 'expmarkers',
    missionData: true,
    label: 'Expansion Map Markers',
    hint: "The server markers every player sees, from MapSettings.json, in each marker's own colour. Drag to move, click for text, icon, colour, height and Copy here",
    expansionOnly: true,
    icon: (
      <>
        <path d="M12 21s-6-6.5-6-11a6 6 0 0 1 12 0c0 4.5-6 11-6 11z" />
        <circle cx="12" cy="10" r="2" />
      </>
    ),
  },
  {
    key: 'expbuildzones',
    missionData: true,
    label: 'Expansion Build Zones',
    hint: 'Base-building zones (rose) from BaseBuildingSettings.json, where only the listed items, or all but them, can be placed. Drag to move, drag the edge to resize, click for the item list and Copy here',
    expansionOnly: true,
    icon: (
      <>
        <circle cx="12" cy="12" r="9" strokeDasharray="4 3" />
        <path d="M8 16V11l4-3 4 3v5z" />
      </>
    ),
  },
  // Dynamic AI (plans/47 D, D3): the mod's own positions, in SpatialSettings.json.
  {
    key: 'dynaipoints',
    missionData: true,
    label: 'Dynamic AI Points',
    hint: "Dynamic AI's points (fuchsia; emerald = Safe, a no-spawn zone) — inside one, its own AI replace the group spawns. Drag the centre to move, the edge handle to resize, click for Copy here… A fresh install leaves them at 0, 0 in the bottom-left corner",
    dynamicAiOnly: true,
    icon: (
      <>
        <circle cx="12" cy="12" r="8" />
        <circle cx="12" cy="10" r="2" />
        <path d="M8.5 16a4 4 0 0 1 7 0" />
      </>
    ),
  },
  {
    key: 'dynailocations',
    missionData: true,
    label: 'Dynamic AI Locations',
    hint: "Dynamic AI's locations (cyan): the trigger circle a player walks into, and a diamond per spawn position, joined by a line. Drag the trigger or a diamond; click for Move here… (the whole location) and Copy here…. Heights are kept as the file has them",
    dynamicAiOnly: true,
    icon: (
      <>
        <circle cx="9" cy="12" r="6" />
        <path d="M18 5l3 3-3 3-3-3Z" />
        <path d="M13 10l3-2" strokeDasharray="2 2" />
      </>
    ),
  },
  {
    key: 'dynaiaudio',
    missionData: true,
    sub: true,
    label: 'Audio locations',
    hint: "Dynamic AI's audio locations (yellow): shaped like a location, triggered by a player's noise inside the circle rather than by walking in",
    dynamicAiOnly: true,
    icon: (
      <>
        <path d="M4 10v4h4l5 4V6L8 10Z" />
        <path d="M16 9a4 4 0 0 1 0 6" />
      </>
    ),
  },
  {
    key: 'expmissions',
    missionData: true,
    label: 'Expansion Missions',
    hint: 'Airdrops (orange) and contaminated areas (lime) from <mission>/expansion/missions. Read-only: their positions couple to heights and ring geometry, which the settings editors own',
    expansionOnly: true,
    icon: (
      <>
        <path d="M12 2l3 6 6 1-4.5 4.5L18 20l-6-3-6 3 1.5-6.5L3 9l6-1Z" />
      </>
    ),
  },
]

const LAYERS = ALL_LAYERS.filter((layer) => !HIDDEN_LAYERS.has(layer.key))

// What the map is drawn on (plans/69, plans/70): a ground, image or terrain,
// at most one of them, and the buildings and roads as toggles of their own.
// The image is the admin's own upload, offered and the default only when they
// made one. Each control writes nothing to the URL when it lands on the
// default, so a shared link follows the map's own.
// Why Terrain and Roads are off: `/map-info`'s terrain_problem
// (app/maps/terrain_map.py, world_file_problem). `not_available` is a map off
// registry.TERRAIN_LAYER_MAPS, whose world file is never read. An encrypted
// world file's terrain is drawn from the navmesh when the map has one
// (terrain_source, plans/74), and only its roads are missing.
// `navmesh_unreadable` is that navmesh after a failed read.
const TERRAIN_PROBLEMS = {
  not_available: 'The terrain layer is not available for this map',
  encrypted: "This map's world file is encrypted, so Zedstead cannot read its terrain or roads",
  unknown_version: "This map's world file is a version Zedstead cannot read yet",
  navmesh_unreadable: "This map's world file is encrypted, and its navmesh is a version Zedstead cannot read yet",
  missing: "This map's world file is not on your server. Download the map on the Maps page first",
}

function BaseMapChoice() {
  const {
    url,
    showImage,
    showTerrain,
    showBuildings,
    defaultBuildings,
    showRoads,
    showForest,
    hasImage,
    hasTerrain,
    hasRoads,
    hasForest,
    hasBuildings,
    mapInfo,
    buildings,
    buildingsLoading,
  } = useEditor()
  const imageHint = mapInfo?.image_own
    ? 'The image you uploaded for this map'
    : mapInfo?.image_cleared
      ? "The satellite image of this map, used with its maker's permission. Zedstead downloads it once from zedstead.com"
      : 'There is no image for this map'
  const fromNavmesh = mapInfo?.terrain_source === 'navmesh'
  const fromMaker = mapInfo?.terrain_source === 'maker'
  const terrainProblem = TERRAIN_PROBLEMS[mapInfo?.terrain_problem] || TERRAIN_PROBLEMS.missing
  const terrainHint = !hasTerrain
    ? terrainProblem
    : `${
        fromMaker
          ? "Coastline and contour lines, drawn from the height grid this map's maker gave Zedstead"
          : fromNavmesh
            ? "Coastline and contour lines, drawn from this map's navmesh on your server because its world file is encrypted"
            : "Coastline and contour lines, drawn from this map's world file on your server"
      }${showImage ? ', as lines over the image' : ''}`
  // Where the world file gives no roads (Sakhal's is encrypted), the gravel
  // and dirt roads come from the mission's cluster files (roads_source).
  const roadsFromMission = mapInfo?.roads_source === 'mission'
  const roadsHint =
    mapInfo?.terrain_problem === 'not_available'
      ? terrainProblem
      : roadsFromMission
        ? `The gravel and dirt roads and the paths in this map's mission on your server (mapgroupcluster*.xml), coloured by kind. The asphalt roads are only in the world file, which is encrypted${
            showImage ? '. Drawn over the image' : ''
          }`
        : fromNavmesh
        ? "This map's world file is encrypted, so Zedstead cannot read its roads, and the navmesh the terrain comes from has none"
        : !hasRoads && fromMaker
          ? TERRAIN_PROBLEMS.missing
          : !hasRoads
            ? terrainHint
            : `Every road in this map's world file on your server, coloured by kind${
                showImage ? ', drawn over the image' : ''
              }`
  const forestHint =
    mapInfo?.terrain_problem === 'not_available'
      ? terrainProblem
      : hasForest
        ? `Where the trees stand, from this map's mission on your server (mapgroupcluster*.xml). The file lists the trees the loot economy uses, so a darker square has more of them${
            showImage ? '. Drawn over the image' : ''
          }`
        : 'No installed mission for this map has mapgroupcluster*.xml files to draw the forest from'
  const count = buildings?.count
  const noMission = !hasBuildings || (showBuildings && !buildingsLoading && buildings && !buildings.mission)
  const buildingsHint = noMission
    ? 'No installed mission is for this map, so there are no buildings to draw'
    : `Every building in this map's mapgrouppos.xml, read from your server${
        count ? ` (${count.toLocaleString()} buildings)` : ''
      }${showImage ? ', drawn over the image' : ''}`

  // An older link's ?base= (one ground) is rewritten on the first click into
  // the switches it stood for, so nothing on screen changes but the clicked one.
  const legacy = Boolean(url.base)
  const setLayers = (change) =>
    url.setMapLayers({
      ...(legacy
        ? {
            base: '',
            image: showImage,
            terrain: showTerrain,
            buildings: url.buildingsLayer ?? (url.base === 'buildings' ? true : null),
          }
        : {}),
      ...change,
    })
  // On opens the Loot finder's Building view, which shows the buildings; off
  // closes that view too, so the switch and the view never disagree.
  const toggleBuildings = () => {
    const next = !showBuildings
    setLayers({
      buildings: next ? null : defaultBuildings ? false : null,
      buildingView: next,
    })
  }

  const choices = [
    {
      id: 'image',
      label: 'Image',
      hint: imageHint,
      disabled: !hasImage,
      checked: showImage,
      onChange: () => setLayers({ image: !showImage }),
    },
    {
      id: 'terrain',
      label: 'Terrain',
      hint: terrainHint,
      disabled: !hasTerrain,
      checked: showTerrain,
      onChange: () => setLayers({ terrain: !showTerrain }),
    },
    {
      id: 'buildings',
      label: 'Buildings',
      hint: buildingsHint,
      // Unticked while disabled, like a mission layer on a previewed map:
      // the URL keeps the choice for when there is something to draw.
      disabled: !hasBuildings,
      checked: showBuildings && hasBuildings,
      onChange: toggleBuildings,
    },
    {
      id: 'roads',
      label: 'Roads',
      hint: roadsHint,
      disabled: !hasRoads,
      checked: showRoads,
      onChange: () => setLayers({ roads: !showRoads }),
    },
    {
      id: 'forest',
      label: 'Forest',
      hint: forestHint,
      disabled: !hasForest,
      checked: showForest,
      onChange: () => setLayers({ forest: !showForest }),
    },
  ]
  const choice = (c) => (
    <span key={c.id} data-tooltip-id="app-tip" data-tooltip-content={c.hint}>
      <Switch checked={c.checked} disabled={c.disabled} onChange={c.onChange}>
        {c.label}
      </Switch>
    </span>
  )
  // The image row comes first, with the admin's own image beside its checkbox.
  const [image, ...rest] = choices
  return (
    <>
      <li className="me-base-map" role="group" aria-label="Map image">
        <span className="me-base-map-choices">
          {choice(image)}
          <OwnMapImage />
        </span>
      </li>
      <li className="me-base-map" role="group" aria-label="Base map">
        <span className="me-base-map-choices">{rest.map(choice)}</span>
      </li>
      {showRoads && (
        <li className="me-road-legend" aria-label="Road colours">
          {ROAD_LEGEND.map(([kind, label]) => (
            <span key={kind}>
              <i className={`me-road-swatch me-road-${kind}`} />
              {label}
            </span>
          ))}
        </li>
      )}
    </>
  )
}

// The ground (image, terrain, buildings, roads) is its own collapsible
// section above Layers, apart from the mission's layers.
export function BaseMapToggles() {
  const [open, setOpen] = useStoredOpen(BASE_OPEN_KEY)
  return (
    <ListSection
      className="me-layers me-base-map-section"
      title="Base map"
      foldable
      sticky
      open={open}
      onOpenChange={setOpen}
    >
      <ul className="me-layer-list">
        <BaseMapChoice />
      </ul>
    </ListSection>
  )
}

export default function LayerToggles() {
  const {
    url,
    traderplus,
    expansion,
    radzone,
    toxiczone,
    dynamicAi,
    isActiveTerrain,
    activeTerrain,
    dispatch,
  } = useEditor()
  const [open, setOpen] = useStoredOpen(OPEN_KEY)

  // Two dozen rows push the tabs and the tab content off a short screen, which
  // is the whole reason this collapses. Collapsed, the header carries the
  // count so the layers that are on are still accounted for.
  const activeCount = LAYERS.filter((layer) => url.layers[layer.key]).length

  return (
    <>
    <ListSection
      className="me-layers"
      title="Layers"
      count={!open && activeCount > 0 ? activeCount : undefined}
      foldable
      sticky
      open={open}
      onOpenChange={setOpen}
    >
      <ul className="me-layer-list">
        {/* The Loot finder leads the list (maintainer, 2026-11-06). */}
        <LootPanel />
        {LAYERS.map((layer) => {
          const offMap = layer.missionData && !isActiveTerrain
          const disabled =
            offMap ||
            (layer.traderplusOnly && !traderplus) ||
            (layer.expansionOnly && !expansion) ||
            (layer.radzoneOnly && !radzone) ||
            (layer.toxiczoneOnly && !toxiczone) ||
            (layer.dynamicAiOnly && !dynamicAi)
          const hint = offMap
            ? `Mission data belongs to ${activeTerrain} — switch back to that map to see it`
            : layer.traderplusOnly && !traderplus
              ? 'TraderPlus mod is not installed'
              : layer.expansionOnly && !expansion
                ? 'No Expansion map data yet — it is written when the server first starts with the mods loaded'
                : layer.radzoneOnly && !radzone
                  ? 'RadZone is not installed, or has not written its files yet — it does on its first boot'
                  : layer.toxiczoneOnly && !toxiczone
                    ? 'ToxicZone is not installed, or has not written its files yet — it does on its first boot'
                    : layer.dynamicAiOnly && !dynamicAi
                      ? 'Dynamic AI is not installed, or has not written SpatialSettings.json yet — it does on its first boot'
                      : layer.hint
          return (
            <li
              key={layer.key}
              className={[layer.sub ? 'sub' : '', disabled ? 'disabled' : '']
                .filter(Boolean)
                .join(' ')}
            >
              <div
                className={`me-layer-toggle${layer.sub ? ' sub-toggle' : ''}`}
                data-tooltip-id="app-tip"
                data-tooltip-content={hint}
              >
                <Switch
                  checked={!disabled && Boolean(url.layers[layer.key])}
                  disabled={disabled}
                  onChange={(on) => {
                    // Switching a layer on brings its editor to the front. Only
                    // on: unticking is a visibility change, not navigation. The
                    // tabs are dead on a previewed (non-mission) terrain. Layer
                    // + tab go in one URL update — two would race.
                    const tab =
                      on && isActiveTerrain ? LAYER_TABS[layer.key] : null
                    url.setLayerAndTab(layer.key, on, tab)
                    // "Event Spawns" is a master switch over the per-event
                    // checkboxes, as it was in the vanilla editor: without
                    // this the individual boxes stay checked while the layer
                    // is off, so they look live but toggle nothing.
                    if (layer.key === 'spawns') {
                      dispatch({ type: 'SET_ALL_EVENTS_VISIBLE', payload: on })
                    }
                  }}
                >
                  <svg
                  className="me-toggle-icon"
                  width="16"
                  height="16"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                    {layer.icon}
                  </svg>
                  {layer.label}
                </Switch>
              </div>
              {/* Adding a place name starts where the names are switched on. */}
              {layer.key === 'locations' && url.layers.locations && (
                <div className="me-layer-extra">
                  <AddPlaceNameLink />
                </div>
              )}
            </li>
          )
        })}
        {/* Last row of the same list, as in the vanilla editor — it is a layer
            like the others, it just carries its own filter panel. */}
        <CollisionPanel />
      </ul>
    </ListSection>
    {open && <SectionHeightHandle storageKey="zsm.mapeditor.layers.height" label="Layers height" />}
    </>
  )
}
