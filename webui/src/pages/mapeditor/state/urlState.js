// Editor state that lives in the URL, so a view is linkable.
//
// The `layers` and collision params keep the vanilla editor's exact contract:
// app/ops/collision_ops.py::build_collision_query generates the collision ones
// server-side and the Analysis page links straight into the editor with them.
// Renaming a key breaks that link.

import { useCallback, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'

// The Loot finder's views (?lootview=).
const LOOT_VIEWS = ['item', 'mod', 'building']

// Layer keys, in sidebar order. Values are the URL tokens.
export const LAYER_KEYS = [
  'locations',
  // The coordinate grid (plans/69). Not mission data: it draws on any map.
  'grid',
  'spawns',
  'labels',
  'zones',
  'oob',
  'playerspawns',
  'traders',
  // Expansion's map data, one key per file it writes.
  'exptraderzones',
  'expsafezones',
  'expspawns',
  'expmissions',
  'expaipatrols',
  'expairoaming',
  // Position lists inside three Expansion settings files (TODO H4).
  'expmarket',
  'expmarkers',
  'expbuildzones',
  // Vanilla mission data, not a mod's.
  'underground',
  // Zedstead's own Object Spawner file, from the Items tab.
  'objects',
  // The Hazard zones family (plans/48 C): vanilla contaminated areas, then
  // RadZone's zones and its loot crates, then ToxicZone's static zones, moving
  // zones and loot crates.
  'contaminated',
  'radzone',
  'radzonecrates',
  'toxiczone',
  'toxicmoving',
  'toxiccrates',
  // Dynamic AI (plans/47 D): its points, locations and audio locations.
  'dynaipoints',
  'dynailocations',
  'dynaiaudio',
]

// The sidebar's Edit tabs (?tab=). `mod-items` is panels/ModItemsTab.jsx.
export const TABS = ['spawns', 'player-spawns', 'traders', 'mod-items']

// The older one-ground value (plans/69, plans/70): the map image, the
// terrain map, or nothing. It is still read, so old links open as they did,
// and the first switch click replaces it with ?img= and ?ter=. "buildings" is
// plans/69's value: no ground, buildings on.
export const BASE_MAPS = ['image', 'terrain', 'none', 'buildings']

// Scan modes accepted in ?collisions=. Same four the vanilla editor took, and
// the ones app/ops/collision_ops.py::build_collision_query emits.
// Layers kept in LAYER_KEYS but not offered and never drawn. Add a key here
// to hide its layer. Locations came back with the world-config place names
// (app/maps/world_names.py, official maps only, plans/86).
export const HIDDEN_LAYERS = new Set()

export const COLLISION_MODES = ['eventspawns', 'playerspawns', 'territories', 'all']

export function useEditorUrlState() {
  const [params, setParams] = useSearchParams()

  // replace:true — a checkbox toggle must not add a history entry, or Back
  // walks the user through every click they made.
  const update = useCallback(
    (mutate) => {
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev)
          mutate(next)
          return next
        },
        { replace: true }
      )
    },
    [setParams]
  )

  const layers = useMemo(() => {
    const raw = params.get('layers')
    const active = new Set(raw ? raw.split(',').filter(Boolean) : [])
    return Object.fromEntries(LAYER_KEYS.map((k) => [k, active.has(k) && !HIDDEN_LAYERS.has(k)]))
  }, [params])

  // Pure mutators over a URLSearchParams. Kept separate from the setters so a
  // caller can change a layer AND the tab in ONE update: react-router resolves
  // a functional setSearchParams against the params of the last *render*, so
  // two setters fired from one handler would have the second overwrite the
  // first (ticking a layer that also switches tab left the box unchecked).
  const applyLayer = (next, key, on) => {
    const raw = next.get('layers')
    const active = new Set(raw ? raw.split(',').filter(Boolean) : [])
    if (on) active.add(key)
    else active.delete(key)
    const ordered = LAYER_KEYS.filter((k) => active.has(k))
    if (ordered.length) next.set('layers', ordered.join(','))
    else next.delete('layers')
  }

  const applyTab = (next, value) => {
    if (value && value !== 'spawns') next.set('tab', value)
    else next.delete('tab')
  }

  const setLayer = useCallback(
    (key, on) => {
      update((next) => applyLayer(next, key, on))
    },
    [update]
  )

  // Toggle a layer and (optionally) jump to the tab that edits it, atomically.
  const setLayerAndTab = useCallback(
    (key, on, tabValue) => {
      update((next) => {
        applyLayer(next, key, on)
        if (tabValue) applyTab(next, tabValue)
      })
    },
    [update]
  )

  const tabParam = params.get('tab')
  const tab = TABS.includes(tabParam) ? tabParam : 'spawns'

  const setTab = useCallback(
    (value) => {
      update((next) => applyTab(next, value))
    },
    [update]
  )

  const terrain = params.get('terrain') || ''
  const setTerrain = useCallback(
    (value) => {
      update((next) => {
        if (value) next.set('terrain', value)
        else next.delete('terrain')
      })
    },
    [update]
  )

  const baseParam = params.get('base')
  const base = BASE_MAPS.includes(baseParam) ? baseParam : ''

  // ?img= ?ter= ?bld= ?roads= — each base-map layer on (1) or off (0).
  // Absent means the map's default (MapEditorProvider), so they stack: the
  // terrain's lines, the roads and the buildings can all lie over the image.
  const flag = (key) => {
    const v = params.get(key)
    return v === '1' ? true : v === '0' ? false : null
  }
  const imageLayer = flag('img')
  const terrainLayer = flag('ter')
  const buildingsLayer = flag('bld')
  const roadsLayer = flag('roads')
  // ?forest= — the trees of the mission's cluster files (ROADMAP 50).
  const forestLayer = flag('forest')
  // Image, terrain, buildings, roads and forest in one write: two writes in one click
  // would each start from the same URL, and the second would undo the first.
  // A layer given as true or false is written, null drops it to the default,
  // and undefined leaves it. `buildingView` true opens the Loot finder on its
  // Building view, which shows the buildings; false closes that view if it is
  // the open one.
  const setMapLayers = useCallback(
    ({ base: nextBase, image, terrain: ter, buildings, roads, forest, buildingView }) => {
      update((next) => {
        if (buildingView === true) {
          next.delete('lootmod')
          next.set('loot', next.get('loot') || '')
          next.set('lootview', 'building')
        } else if (buildingView === false && next.get('lootview') === 'building') {
          for (const key of ['loot', 'lootmod', 'lootview']) next.delete(key)
        }
        if (nextBase !== undefined) {
          if (BASE_MAPS.includes(nextBase)) next.set('base', nextBase)
          else next.delete('base')
        }
        for (const [key, value] of [
          ['img', image],
          ['ter', ter],
          ['bld', buildings],
          ['roads', roads],
          ['forest', forest],
        ]) {
          if (value === undefined) continue
          if (value === true || value === false) next.set(key, value ? '1' : '0')
          else next.delete(key)
        }
      })
    },
    [update]
  )

  // ?event=<name> — an inbound deep link from the config editors ("View on
  // map"), asking the editor to open one event. Consumed once and then dropped
  // from the URL: a pasted link works, but a refresh after the user has moved on
  // must not yank them back to an event they left.
  const focusEvent = params.get('event') || ''

  // Turn the spawns layer on, force the spawns tab and drop ?event= — THREE
  // mutations, ONE update(). Two url.set* calls here would silently lose the
  // first (see the note above applyLayer).
  const consumeFocusEvent = useCallback(() => {
    update((next) => {
      applyLayer(next, 'spawns', true)
      applyTab(next, 'spawns')
      next.delete('event')
    })
  }, [update])

  // ?spawntest=<mod>&class=<class>&count=<n> — the mod page's spawn test,
  // opened here to pick its place on the full map (crossLinks.spawnTestMapLink).
  // Consumed once, like ?event=: the editor enters its spawn-test mode and the
  // keys leave the URL, so a refresh does not arm the mode again.
  const spawnTestMod = params.get('spawntest') || ''
  const spawnTestClass = params.get('class') || ''
  const spawnTestCount = params.get('count') || ''
  const spawnTest = useMemo(
    () =>
      spawnTestMod
        ? { mod: spawnTestMod, classname: spawnTestClass, count: spawnTestCount }
        : null,
    [spawnTestMod, spawnTestClass, spawnTestCount]
  )
  const consumeSpawnTest = useCallback(() => {
    update((next) => {
      next.delete('spawntest')
      next.delete('class')
      next.delete('count')
    })
  }, [update])

  // Collision params. The exact contract of collision_ops.build_collision_query:
  // `collisions=<mode>`, `types=a,b`, `cross-event-overlaps=true`,
  // `radius-conflicts=true`. The booleans are the literal string "true" — the
  // Analysis page's deep link is generated server-side and never writes "1".
  const collisions = useMemo(
    () => ({
      mode: COLLISION_MODES.includes(params.get('collisions'))
        ? params.get('collisions')
        : '',
      types: (params.get('types') || '').split(',').filter(Boolean),
      crossEventOverlaps: params.get('cross-event-overlaps') === 'true',
      radiusConflicts: params.get('radius-conflicts') === 'true',
    }),
    [params]
  )

  // Patch of {mode, types, crossEventOverlaps, radiusConflicts}. A falsy mode
  // clears the whole group — the panel being closed means no collision params
  // at all, so a copied URL doesn't reopen it.
  const setCollisions = useCallback(
    (patch) => {
      update((next) => {
        const merged = {
          mode: COLLISION_MODES.includes(next.get('collisions')) ? next.get('collisions') : '',
          types: (next.get('types') || '').split(',').filter(Boolean),
          crossEventOverlaps: next.get('cross-event-overlaps') === 'true',
          radiusConflicts: next.get('radius-conflicts') === 'true',
          ...patch,
        }
        for (const key of ['collisions', 'types', 'cross-event-overlaps', 'radius-conflicts']) {
          next.delete(key)
        }
        if (!merged.mode) return
        next.set('collisions', merged.mode)
        if (merged.types.length) next.set('types', merged.types.join(','))
        if (merged.crossEventOverlaps) next.set('cross-event-overlaps', 'true')
        if (merged.radiusConflicts) next.set('radius-conflicts', 'true')
      })
    },
    [update]
  )

  // ?loot=<classname> or ?lootmod=<owner key> — the Loot layer (plans/82).
  // Open with nothing picked is `?loot=` with no value. Not LAYER_KEYS
  // entries: like the collision params, the panel is open exactly when a key
  // is there.
  const lootItem = params.get('loot') || ''
  const lootMod = params.get('lootmod') || ''
  const lootOpen = params.has('loot') || params.has('lootmod')
  // ?lootview=item|mod|building — the Loot finder's view, written only when it
  // is not the one the pick implies (a mod picked: Mod, else Item). The
  // Building view also shows the buildings (MapEditorProvider `showBuildings`).
  const lootDefaultView = lootMod ? 'mod' : 'item'
  const lootViewParam = params.get('lootview')
  const lootView = LOOT_VIEWS.includes(lootViewParam) ? lootViewParam : lootDefaultView
  const loot = useMemo(
    () => ({ open: lootOpen, item: lootItem, mod: lootMod, view: lootView }),
    [lootOpen, lootItem, lootMod, lootView]
  )

  // {open, item, mod, view} in ONE update. Closing deletes every key; picking
  // an item drops the mod and the reverse, so at most one is ever set. No
  // `view` means the one the pick implies.
  const setLoot = useCallback(
    ({ open = true, item = '', mod = '', view = '' }) => {
      update((next) => {
        for (const key of ['loot', 'lootmod', 'lootview']) next.delete(key)
        if (!open) return
        if (mod) next.set('lootmod', mod)
        else next.set('loot', item)
        if (view && view !== (mod ? 'mod' : 'item')) next.set('lootview', view)
      })
    },
    [update]
  )

  return {
    layers,
    setLayer,
    setLayerAndTab,
    tab,
    setTab,
    terrain,
    setTerrain,
    base,
    buildingsLayer,
    roadsLayer,
    forestLayer,
    imageLayer,
    terrainLayer,
    setMapLayers,
    focusEvent,
    consumeFocusEvent,
    spawnTest,
    consumeSpawnTest,
    collisions,
    setCollisions,
    loot,
    setLoot,
  }
}
