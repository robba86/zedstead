// The editor's state, replacing the vanilla editor's `appState` god object.
//
// appState was 30 mutable keys read lazily through circular imports between
// thirteen modules. It splits three ways here:
//
//   1. map metadata  -> plain useState (loaded once per terrain)
//   2. UI mode       -> useReducer, because the add-mode transitions are
//                       mutually exclusive and used to be three functions in
//                       three modules clearing each other's globals
//   3. server data   -> useResource, invalidated by WebSocket pings
//
// One `useWs` handler maps each mapeditor_* ping to the matching reload. The
// backend never sends payloads on this channel — a ping means "refetch" — so
// there is exactly one path into the cache.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
} from 'react'
import { useWs } from '../../../ws'
import { useToast } from '../../../components/Toast.jsx'
import { useConfirm } from '../../../components/useConfirm.jsx'
import { useResource } from './useResource'
import { useEditorUrlState } from './urlState'
import { useSelection } from '../leaflet/useSelection'

const MapEditorContext = createContext(null)

export function useEditor() {
  const ctx = useContext(MapEditorContext)
  if (!ctx) throw new Error('useEditor must be used inside <MapEditorProvider>')
  return ctx
}

// ---------------------------------------------------------------------------
// UI reducer
// ---------------------------------------------------------------------------

const initialUi = {
  // Exactly one add mode can be active. The vanilla editor kept three
  // independent globals (mapAddMode, playerSpawnAddMode, the spawns module's
  // own flag) and had each entry point clear the other two by hand.
  addMode: null, // null | {kind:'event',event,classname} | {kind:'player-spawn',section,groupIndex} | {kind:'map',mode,data}
  // Event whose detail editor is open in the sidebar; null = the event list.
  selectedEvent: null,
  // Same, for the Player Spawns tab's section parameter editor.
  selectedPsSection: null,
  // The Items tab's placed object picked on the map: `{disabled, index, seq}`.
  selectedObject: null,
  // The placed object whose Edit form is open: `{key, lat, lng, drag}`. Its
  // marker is drawn at lat/lng and is draggable; `drag` is the last drop,
  // `{lat, lng, seq}`, which the form turns into X, Z and Y.
  objectEdit: null,
  // Map-move request from the sidebar. The seq makes a repeat of the same
  // coordinates a new request, so "fly here" works twice in a row. The
  // markers it lands on glow for a moment (MapEditor.jsx, leaflet/markAt.js).
  flyTo: null,
  // A building clicked with the Loot finder closed, `{lat, lng, radius}`: the
  // click opens its Building view, which looks it up and clears this.
  lootPick: null,
  visibleEvents: new Set(),
  // Every event name the last SYNC_EVENTS saw, so a *newly created* event can
  // be defaulted visible while user-hidden ones stay hidden.
  knownEvents: null,
  visibleLocationTypes: new Set(),
  hoverCoords: null,
  // Full-window mode: the app's nav sidebar, info bar and breadcrumbs are
  // hidden so the map gets the whole viewport. EditorShell mirrors this onto
  // <body> as `me-fullscreen`.
  expanded: false,
}

function uiReducer(state, action) {
  switch (action.type) {
    case 'ENTER_EVENT_ADD': {
      // Placing points into a hidden layer would look like nothing happened.
      const visibleEvents = new Set(state.visibleEvents)
      visibleEvents.add(action.payload.event)
      return { ...state, addMode: { kind: 'event', ...action.payload }, visibleEvents }
    }
    case 'ENTER_PS_ADD':
      return { ...state, addMode: { kind: 'player-spawn', ...action.payload } }
    case 'ENTER_MAP_ADD':
      return { ...state, addMode: { kind: 'map', ...action.payload } }
    // Expansion AI: appending waypoints to a patrol (repeatable — a route is
    // built point after point) or dropping a copy of a roaming location.
    case 'ENTER_AI_ADD':
      return { ...state, addMode: { kind: 'ai', ...action.payload } }
    // The mod page's spawn test, placed with a click (see MapEditor's click
    // handler). One-shot: a click starts the test, and only one can run.
    case 'ENTER_SPAWN_TEST':
      return { ...state, addMode: { kind: 'spawn-test', verb: 'Spawn test', ...action.payload } }
    case 'SET_ADD_COUNT':
      if (!state.addMode) return state
      return { ...state, addMode: { ...state.addMode, count: action.payload } }
    // The Object mode's optional height (MAP_ADD_MODES.object).
    case 'SET_ADD_Y':
      if (!state.addMode) return state
      return { ...state, addMode: { ...state.addMode, y: action.payload } }
    case 'SET_ADD_CLASSNAME':
      if (!state.addMode) return state
      return { ...state, addMode: { ...state.addMode, className: action.payload } }
    case 'EXIT_ADD':
      return { ...state, addMode: null }
    case 'SELECT_EVENT':
      return { ...state, selectedEvent: action.payload }
    case 'SELECT_PS_SECTION':
      return { ...state, selectedPsSection: action.payload }
    case 'OBJECT_EDIT_START':
      return { ...state, objectEdit: { ...action.payload, drag: null } }
    case 'OBJECT_EDIT_MOVE':
      return state.objectEdit ? { ...state, objectEdit: { ...state.objectEdit, ...action.payload } } : state
    case 'OBJECT_EDIT_DRAG':
      return state.objectEdit
        ? {
            ...state,
            objectEdit: {
              ...state.objectEdit,
              ...action.payload,
              drag: { ...action.payload, seq: (state.objectEdit.drag?.seq || 0) + 1 },
            },
          }
        : state
    case 'OBJECT_EDIT_END':
      return state.objectEdit?.key === action.payload ? { ...state, objectEdit: null } : state
    case 'SELECT_OBJECT':
      return {
        ...state,
        selectedObject: action.payload && { ...action.payload, seq: (state.selectedObject?.seq || 0) + 1 },
      }
    case 'LOOT_PICK':
      return { ...state, lootPick: action.payload }
    case 'FLY_TO':
      return { ...state, flyTo: { ...action.payload, seq: (state.flyTo?.seq || 0) + 1 } }
    case 'SET_VISIBLE_EVENTS':
      return { ...state, visibleEvents: action.payload }
    case 'SYNC_EVENTS': {
      // Called whenever the set of event names changes (first load, WS reload,
      // event created). First time everything is visible; afterwards existing
      // choices are preserved, names that vanished are dropped, and names that
      // are genuinely new default to visible.
      //
      // `defaultVisible` is the Event Spawns master layer: with it off, the
      // per-event boxes must start unchecked, otherwise they read as live while
      // the layer they belong to renders nothing.
      const { names, defaultVisible } = action.payload
      if (state.knownEvents === null) {
        return {
          ...state,
          knownEvents: new Set(names),
          visibleEvents: defaultVisible ? new Set(names) : new Set(),
        }
      }
      const visibleEvents = new Set()
      for (const name of names) {
        const isNew = !state.knownEvents.has(name)
        if (state.visibleEvents.has(name) || (isNew && defaultVisible)) {
          visibleEvents.add(name)
        }
      }
      return { ...state, knownEvents: new Set(names), visibleEvents }
    }
    case 'SET_ALL_EVENTS_VISIBLE':
      return {
        ...state,
        visibleEvents: action.payload ? new Set(state.knownEvents || []) : new Set(),
      }
    case 'SHOW_EVENT': {
      // Make one event visible without knowing its current state — TOGGLE_EVENT
      // would hide it if it already was.
      const next = new Set(state.visibleEvents)
      next.add(action.payload)
      return { ...state, visibleEvents: next }
    }
    case 'TOGGLE_EVENT': {
      const next = new Set(state.visibleEvents)
      if (next.has(action.payload)) next.delete(action.payload)
      else next.add(action.payload)
      return { ...state, visibleEvents: next }
    }
    case 'SET_VISIBLE_LOCATION_TYPES':
      return { ...state, visibleLocationTypes: action.payload }
    case 'TOGGLE_LOCATION_TYPE': {
      const next = new Set(state.visibleLocationTypes)
      if (next.has(action.payload)) next.delete(action.payload)
      else next.add(action.payload)
      return { ...state, visibleLocationTypes: next }
    }
    case 'SET_HOVER_COORDS':
      return { ...state, hoverCoords: action.payload }
    case 'SET_EXPANDED':
      return { ...state, expanded: Boolean(action.payload) }
    default:
      return state
  }
}

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------

const TRADER_SYSTEM_EVENTS = new Set([
  'mapeditor_traders_changed',
  'mapeditor_safezones_changed',
  'mapeditor_expansion_traderzones_changed',
])

export function MapEditorProvider({ children }) {
  const pushToast = useToast()
  const confirm = useConfirm()
  const url = useEditorUrlState()
  const [ui, dispatch] = useReducer(uiReducer, initialUi)

  // Box selection lives here rather than in MapCanvas: the SelectionBar is in
  // the sidebar, the layers that feed it are on the map, and the controller
  // itself is map-agnostic (MapCanvas attaches the map to it).
  const { selection, count: selectionCount, progress: saveProgress } = useSelection({
    confirm,
    pushToast,
  })

  const onError = useCallback(
    (err) => pushToast(err.message || 'Map editor request failed', 'error'),
    [pushToast]
  )

  // Which terrain we're looking at. Defaults to the mission's own; the map
  // selector can preview another (locations only — editing stays locked to the
  // running mission's terrain).
  const mapInfoPath = url.terrain
    ? `/map-info?terrain=${encodeURIComponent(url.terrain)}`
    : '/map-info'

  const mapInfo = useResource(mapInfoPath, { onError })
  const maps = useResource('/maps', { onError })

  const terrain = mapInfo.data?.terrain || ''
  const locations = useResource(
    terrain ? `/locations?terrain=${encodeURIComponent(terrain)}` : null,
    { onError, initial: [] }
  )

  // What the map is drawn from: four layers that stack, each its own switch
  // (?img= ?ter= ?bld= ?roads=). The image is the admin's upload or a cleared
  // map's (`image_cleared`); the terrain and the roads come from the server's
  // world file (`has_terrain`, `has_roads`); the buildings from the mission.
  // Defaults: the image when there is one, else the terrain; the roads over
  // the terrain but not over the image, which shows its own; the buildings
  // never (the maintainer, 2026-11-11: on, they load the loot catalog for
  // their icons), so a map with neither image nor terrain starts empty until
  // the admin turns one on. Over the image the terrain
  // draws its lines only (useTerrainLayer's `overImage`).
  // An older link's ?base= still picks its one ground until the first click.
  const hasImage = Boolean(mapInfo.data?.has_image)
  const hasTerrain = Boolean(mapInfo.data?.has_terrain)
  // An older backend does not say; the toggle then stays usable.
  const hasBuildings = mapInfo.data?.has_buildings !== false
  const hasRoads = Boolean(mapInfo.data?.has_roads)
  const legacyBase = url.base
  const legacyBuildings = legacyBase === 'buildings'
  const defaultImage = legacyBase ? legacyBase === 'image' : hasImage
  const defaultTerrain = legacyBase ? legacyBase === 'terrain' : !hasImage
  const showImage = hasImage && (url.imageLayer ?? defaultImage)
  const showTerrain = hasTerrain && (url.terrainLayer ?? defaultTerrain)
  const defaultBuildings = legacyBuildings
  // The Loot finder's Building view shows them too: the Buildings switch
  // opens that view (LayerToggles.jsx), so the two are one state.
  const buildingView = url.loot.open && url.loot.view === 'building' && hasBuildings
  const showBuildings = (url.buildingsLayer ?? defaultBuildings) || buildingView
  // A terrain drawn from the navmesh (plans/74) has no roads, and neither has
  // a cleared map whose maker allowed only its heights.
  const defaultRoads = !showImage
  const showRoads = hasRoads && (url.roadsLayer ?? defaultRoads)
  // The forest from the mission's cluster files (ROADMAP 50), official maps
  // only, on by default where the roads are: an image shows its own trees.
  const hasForest = Boolean(mapInfo.data?.has_forest)
  const showForest = hasForest && (url.forestLayer ?? defaultRoads)
  // Not gated on isActiveTerrain: a previewed terrain uses its own installed
  // mission, so its buildings are as valid as the running one's.
  const buildings = useResource(
    terrain && showBuildings
      ? `/buildings?terrain=${encodeURIComponent(terrain)}`
      : null,
    { onError }
  )

  // The Loot layer (plans/82). Like the buildings, not gated on
  // isActiveTerrain: a previewed terrain reads its own installed mission. The
  // catalog is fetched while the panel is open, the match once something is
  // picked. The Buildings layer reads the catalog too, for its badges
  // (MapEditor.jsx useBuildingBadges), with the panel closed.
  const lootTerrain = encodeURIComponent(terrain)
  const lootCatalog = useResource(
    (url.loot.open || (showBuildings && hasBuildings)) && terrain ? `/loot/catalog?terrain=${lootTerrain}` : null,
    { onError }
  )
  const lootPick = url.loot.mod
    ? `&mod=${encodeURIComponent(url.loot.mod)}`
    : url.loot.item
      ? `&item=${encodeURIComponent(url.loot.item)}`
      : ''
  const lootData = useResource(
    terrain && url.loot.open && lootPick ? `/loot?terrain=${lootTerrain}${lootPick}` : null,
    { onError }
  )

  // The mission's own terrain — data editing is only valid there.
  const activeTerrain = useMemo(
    () => maps.data?.find((m) => m.current)?.terrain || '',
    [maps.data]
  )
  const isActiveTerrain = !terrain || !activeTerrain || terrain === activeTerrain

  // An add mode is a write to the running mission, which the server places on
  // its own terrain: armed while another map is previewed, a click there
  // added a position at that spot of the wrong map.
  const addArmed = Boolean(ui.addMode)
  useEffect(() => {
    if (addArmed && !isActiveTerrain) dispatch({ type: 'EXIT_ADD' })
  }, [addArmed, isActiveTerrain])

  const spawns = useResource(isActiveTerrain ? '/spawns' : null, { onError })
  const events = useResource(isActiveTerrain ? '/events' : null, { onError })
  const playerSpawns = useResource(isActiveTerrain ? '/player-spawns' : null, {
    onError,
    initial: [],
  })

  // Keep visibleEvents in step with the data. Keyed on the joined names rather
  // than the spawns object: every WS ping refetches and hands back a fresh
  // object, and re-syncing on identity would fight the user's checkboxes on
  // every drag-save.
  const eventNames = useMemo(
    () => (spawns.data?.events || []).map((e) => e.name),
    [spawns.data]
  )
  const eventNamesKey = eventNames.join('\u0000')
  // Read through a ref: the sync must not re-run when the layer is toggled
  // (that path is SET_ALL_EVENTS_VISIBLE) — it only consults its current value.
  const spawnsLayerRef = useRef(false)
  spawnsLayerRef.current = url.layers.spawns
  useEffect(() => {
    if (!spawns.data) return
    dispatch({
      type: 'SYNC_EVENTS',
      payload: { names: eventNames, defaultVisible: spawnsLayerRef.current },
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [eventNamesKey])

  // Inbound deep link (?event=<name>, from a config editor's "View on map"):
  // open that event's detail panel and fly to its first position. Runs once,
  // after the spawns data lands. The URL key is consumed either way — a name
  // that isn't in the file must not retry on every later render.
  //
  // The link asks for ONE event, so it isolates it (SET_VISIBLE_EVENTS) rather
  // than adding to what is on: it arrives with `layers=spawns`, so the
  // SYNC_EVENTS above has just ticked every event, and the target's markers
  // land in a map covered by every other event's.
  //
  // A miss is not "the event does not exist" — /spawns is cfgeventspawns.xml
  // only, and an events.xml event with no <pos> entries is a normal thing to
  // link to from the events editor. Say that, or the toast reads as a bug.
  const focusedEventRef = useRef(false)
  useEffect(() => {
    if (focusedEventRef.current || !url.focusEvent || !spawns.data) return
    focusedEventRef.current = true
    const name = url.focusEvent
    const event = (spawns.data.events || []).find((e) => e.name === name)
    url.consumeFocusEvent()
    if (!event) {
      pushToast(
        `"${name}" has no positions in cfgeventspawns.xml — only events with <pos> ` +
          'entries show on the map. Add it with + New Event in the Spawns tab.',
        'info'
      )
      return
    }
    dispatch({ type: 'SELECT_EVENT', payload: name })
    dispatch({ type: 'SET_VISIBLE_EVENTS', payload: new Set([name]) })
    const pos = event.positions?.[0]
    if (pos) dispatch({ type: 'FLY_TO', payload: { lat: pos.lat, lng: pos.lng, zoom: 6 } })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url.focusEvent, spawns.data])

  // Inbound spawn test (?spawntest=, from the mod page): enter the spawn-test
  // mode once the map knows which terrain it shows. The test runs on the
  // running mission, so a previewed terrain only says so.
  const spawnTestRef = useRef(false)
  useEffect(() => {
    if (spawnTestRef.current || !url.spawnTest || !mapInfo.data || !maps.data) return
    spawnTestRef.current = true
    const { mod, classname, count } = url.spawnTest
    url.consumeSpawnTest()
    if (!isActiveTerrain) {
      pushToast(`A spawn test runs on the running mission's map (${activeTerrain}). Switch to it to place one.`, 'info')
      return
    }
    dispatch({
      type: 'ENTER_SPAWN_TEST',
      payload: { mod, label: mod, className: classname, count: /^\d+$/.test(count) ? count : '10' },
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url.spawnTest, mapInfo.data, maps.data])

  const traderplus = Boolean(mapInfo.data?.traderplus)
  const tpEnabled = traderplus && isActiveTerrain
  const traders = useResource(tpEnabled ? '/traders' : null, { onError })
  const safezones = useResource(tpEnabled ? '/safezones' : null, { onError })
  const garage = useResource(tpEnabled ? '/garage' : null, { onError })
  // Trader stock Ids. Read-only reference data from TraderPlusIDsConfig.json;
  // nothing in the editor writes it, so no WS ping reloads it.
  const traderIds = useResource(tpEnabled ? '/trader-ids' : null, { onError })

  // -- Expansion's map data ----------------------------------------------
  //
  // Gated the same way TraderPlus is, on a /map-info flag — except that the
  // flag is False both when the mod is absent and when it has never booted,
  // since Expansion writes these files on its first start.
  const expansion = Boolean(mapInfo.data?.expansion)
  const expEnabled = expansion && isActiveTerrain
  const expSafezones = useResource(expEnabled ? '/expansion/safezones' : null, { onError })
  const expTraderZones = useResource(expEnabled ? '/expansion/trader-zones' : null, { onError })
  const expSpawnPoints = useResource(expEnabled ? '/expansion/spawn-points' : null, { onError })
  const expMissions = useResource(expEnabled ? '/expansion/missions' : null, { onError })
  // The AI module's two files. Same gate — they are Expansion map data like the
  // rest, and absent unless that module is installed and has booted.
  const expAiPatrols = useResource(expEnabled ? '/expansion/ai/patrols' : null, { onError })
  const expAiLocations = useResource(expEnabled ? '/expansion/ai/locations' : null, { onError })
  // Position lists inside three settings files (TODO H4).
  const expMarketSpawns = useResource(expEnabled ? '/expansion/market-spawns' : null, { onError })
  const expMapMarkers = useResource(expEnabled ? '/expansion/map-markers' : null, { onError })
  const expBuildZones = useResource(expEnabled ? '/expansion/build-zones' : null, { onError })

  // Vanilla, not Expansion: cfgundergroundtriggers.json ships with the mission.
  // Gated on the terrain only — Chernarus' file is an empty stub and Sakhal's
  // has thirty triggers, and the route answers an empty list either way.
  const underground = useResource(isActiveTerrain ? '/underground-triggers' : null, { onError })
  // Zedstead's own Object Spawner file (the Items tab's lasting objects). Not
  // watched: a placement reloads it (MAP_ADD_MODES.object), as a remove does.
  const objects = useResource(isActiveTerrain ? '/object-spawner' : null, { onError })

  // The Hazard zones family (plans/48 C): the mission's contaminated areas and,
  // while RadZone or ToxicZone is installed, its zones. One route answers all
  // three, empty rather than an error when there are none.
  const radzone = Boolean(mapInfo.data?.radzone)
  const toxiczone = Boolean(mapInfo.data?.toxiczone)
  const hazards = useResource(isActiveTerrain ? '/hazards' : null, { onError })

  // Dynamic AI (plans/47 D): gated on its /map-info flag, which is True once the
  // mod is installed and has written SpatialSettings.json.
  const dynamicAi = Boolean(mapInfo.data?.dynamic_ai)
  const dynamicAiData = useResource(dynamicAi && isActiveTerrain ? '/dynamic-ai' : null, {
    onError,
  })

  // Both trader mods at once. Fetched only when both have data — the answer is
  // "only one trader system is configured" on almost every server, and that
  // costs a request nobody reads. Reloaded by either side's WS ping, since the
  // overlap is a fact about both files.
  const traderSystems = useResource(
    traderplus && expansion && isActiveTerrain ? '/trader-systems' : null,
    { onError }
  )
  // Read through a ref in the WS handler: it is not one event's reloader, it
  // is a second reload several events trigger, and putting it in `reloaders`
  // would replace the layer reload that event is for.
  const traderSystemsReloadRef = useRef(null)
  traderSystemsReloadRef.current = traderSystems.reload

  // -- Collision scans ----------------------------------------------------
  //
  // Unlike the other resources this one is NOT fetched when its inputs change:
  // a scan is a full mission walk (the "all" mode reads every territory and
  // TraderPlus file), so it runs when the user presses Scan, or once when the
  // URL arrives with collision params — the Analysis page's deep link.
  //
  // The path IS the query, so re-scanning after changing a filter is a new
  // path, and re-scanning the same filters is a reload of the same one.
  const [collisionPath, setCollisionPath] = useState(null)
  const collisionRes = useResource(collisionPath, { onError })
  const collisionPathRef = useRef(null)
  collisionPathRef.current = collisionPath
  const collisionReloadRef = useRef(null)
  collisionReloadRef.current = collisionRes.reload

  const runCollisionScan = useCallback((path) => {
    if (path && path === collisionPathRef.current) collisionReloadRef.current?.()
    else setCollisionPath(path)
  }, [])

  // Refresh an existing scan in place; a no-op when nothing has been scanned.
  const rescanCollisions = useCallback(() => {
    if (collisionPathRef.current) collisionReloadRef.current?.()
  }, [])

  // -- WebSocket: one handler, ping -> reload -----------------------------

  const reloaders = useMemo(
    () => ({
      mapeditor_spawns_changed: spawns.reload,
      mapeditor_events_changed: events.reload,
      mapeditor_player_spawns_changed: playerSpawns.reload,
      mapeditor_traders_changed: traders.reload,
      mapeditor_safezones_changed: safezones.reload,
      mapeditor_garage_changed: garage.reload,
      mapeditor_expansion_safezones_changed: expSafezones.reload,
      mapeditor_expansion_traderzones_changed: expTraderZones.reload,
      mapeditor_expansion_spawnpoints_changed: expSpawnPoints.reload,
      mapeditor_expansion_missions_changed: expMissions.reload,
      mapeditor_expansion_ai_patrols_changed: expAiPatrols.reload,
      mapeditor_expansion_ai_locations_changed: expAiLocations.reload,
      mapeditor_expansion_market_spawns_changed: expMarketSpawns.reload,
      mapeditor_expansion_map_markers_changed: expMapMarkers.reload,
      mapeditor_expansion_build_zones_changed: expBuildZones.reload,
      mapeditor_underground_triggers_changed: underground.reload,
      mapeditor_hazards_changed: hazards.reload,
      mapeditor_dynamic_ai_changed: dynamicAiData.reload,
    }),
    [
      spawns.reload,
      events.reload,
      playerSpawns.reload,
      traders.reload,
      safezones.reload,
      garage.reload,
      expSafezones.reload,
      expTraderZones.reload,
      expSpawnPoints.reload,
      expMissions.reload,
      expAiPatrols.reload,
      expAiLocations.reload,
      expMarketSpawns.reload,
      expMapMarkers.reload,
      expBuildZones.reload,
      underground.reload,
      hazards.reload,
      dynamicAiData.reload,
    ]
  )

  const { connected, subscribe, unsubscribe } = useWs(
    useCallback(
      (msg) => {
        if (msg.type === 'mapeditor_reloaded') {
          mapInfo.reload()
          locations.reload()
          objects.reload()
          for (const reload of Object.values(reloaders)) reload()
          rescanCollisions()
          return
        }
        reloaders[msg.type]?.()
        // A scan already on screen goes stale the moment a spawn moves, and
        // stale collision rings point at coordinates that no longer exist.
        if (msg.type === 'mapeditor_spawns_changed') rescanCollisions()
        // The two-trader-systems note is a fact about both mods' files, so
        // either side moving changes it.
        if (TRADER_SYSTEM_EVENTS.has(msg.type)) traderSystemsReloadRef.current?.()
      },
      // mapInfo/locations reload identities are stable per path
      // eslint-disable-next-line react-hooks/exhaustive-deps
      [reloaders, mapInfo.reload, locations.reload, objects.reload, rescanCollisions]
    )
  )

  // Subscribing is what starts the server-side mission-file watcher, and
  // unsubscribing stops it — the channel is the editor's "someone is looking"
  // signal, not just a message filter.
  useEffect(() => {
    subscribe('mapeditor')
    return () => unsubscribe('mapeditor')
  }, [subscribe, unsubscribe])

  const value = useMemo(
    () => ({
      ui,
      dispatch,
      url,
      connected,
      selection,
      selectionCount,
      saveProgress,
      mapInfo: mapInfo.data,
      mapInfoLoading: mapInfo.loading,
      mapInfoError: mapInfo.error,
      maps: maps.data || [],
      showImage,
      showTerrain,
      defaultImage,
      defaultTerrain,
      showBuildings,
      defaultBuildings,
      showRoads,
      defaultRoads,
      showForest,
      hasImage,
      hasTerrain,
      hasRoads,
      hasForest,
      hasBuildings,
      buildings: buildings.data,
      buildingsLoading: buildings.loading,
      lootCatalog: lootCatalog.data,
      lootCatalogLoading: lootCatalog.loading,
      lootData: lootData.data,
      lootLoading: lootData.loading,
      terrain,
      activeTerrain,
      isActiveTerrain,
      traderplus,
      expansion,
      expSafezones: expSafezones.data,
      expTraderZones: expTraderZones.data,
      expSpawnPoints: expSpawnPoints.data,
      expMissions: expMissions.data,
      expAiPatrols: expAiPatrols.data,
      expAiLocations: expAiLocations.data,
      expMarketSpawns: expMarketSpawns.data,
      expMapMarkers: expMapMarkers.data,
      expBuildZones: expBuildZones.data,
      underground: underground.data,
      objects: objects.data,
      radzone,
      toxiczone,
      hazards: hazards.data,
      dynamicAi,
      dynamicAiData: dynamicAiData.data,
      traderSystems: traderSystems.data,
      locations: locations.data || [],
      spawns: spawns.data,
      events: events.data,
      playerSpawns: playerSpawns.data || [],
      traders: traders.data,
      safezones: safezones.data,
      garage: garage.data,
      traderIds: traderIds.data?.ids || [],
      // Null until a scan has run; also null again after the panel's mode
      // changes, so stale rings never outlive the filter that produced them,
      // and while another terrain is previewed — the results are this
      // mission's coordinates and would draw on the wrong map.
      collisions: collisionPath && isActiveTerrain ? collisionRes.data : null,
      collisionsLoading: collisionRes.loading,
      runCollisionScan,
      reload: {
        mapInfo: mapInfo.reload,
        locations: locations.reload,
        buildings: buildings.reload,
        spawns: spawns.reload,
        events: events.reload,
        playerSpawns: playerSpawns.reload,
        traders: traders.reload,
        safezones: safezones.reload,
        garage: garage.reload,
        expSafezones: expSafezones.reload,
        expTraderZones: expTraderZones.reload,
        expSpawnPoints: expSpawnPoints.reload,
        expMissions: expMissions.reload,
        expAiPatrols: expAiPatrols.reload,
        expAiLocations: expAiLocations.reload,
        expMarketSpawns: expMarketSpawns.reload,
        expMapMarkers: expMapMarkers.reload,
        expBuildZones: expBuildZones.reload,
        underground: underground.reload,
        objects: objects.reload,
        hazards: hazards.reload,
        dynamicAi: dynamicAiData.reload,
      },
    }),
    [
      ui,
      url,
      connected,
      selection,
      selectionCount,
      saveProgress,
      mapInfo.data,
      mapInfo.loading,
      mapInfo.error,
      mapInfo.reload,
      maps.data,
      showImage,
      showTerrain,
      defaultImage,
      defaultTerrain,
      showBuildings,
      defaultBuildings,
      showRoads,
      defaultRoads,
      showForest,
      hasImage,
      hasTerrain,
      hasRoads,
      hasForest,
      hasBuildings,
      buildings.data,
      buildings.loading,
      buildings.reload,
      lootCatalog.data,
      lootCatalog.loading,
      lootData.data,
      lootData.loading,
      terrain,
      activeTerrain,
      isActiveTerrain,
      traderplus,
      expansion,
      expSafezones.data,
      expSafezones.reload,
      expTraderZones.data,
      expTraderZones.reload,
      expSpawnPoints.data,
      expSpawnPoints.reload,
      expMissions.data,
      expMissions.reload,
      expAiPatrols.data,
      expAiPatrols.reload,
      expAiLocations.data,
      expAiLocations.reload,
      expMarketSpawns.data,
      expMarketSpawns.reload,
      expMapMarkers.data,
      expMapMarkers.reload,
      expBuildZones.data,
      expBuildZones.reload,
      underground.data,
      underground.reload,
      objects.data,
      objects.reload,
      radzone,
      toxiczone,
      hazards.data,
      hazards.reload,
      dynamicAi,
      dynamicAiData.data,
      dynamicAiData.reload,
      traderSystems.data,
      locations.data,
      locations.reload,
      spawns.data,
      spawns.reload,
      events.data,
      events.reload,
      playerSpawns.data,
      playerSpawns.reload,
      traders.data,
      traders.reload,
      safezones.data,
      safezones.reload,
      garage.data,
      garage.reload,
      traderIds.data,
      collisionPath,
      collisionRes.data,
      collisionRes.loading,
      runCollisionScan,
    ]
  )

  return <MapEditorContext.Provider value={value}>{children}</MapEditorContext.Provider>
}
