// Visual Map Editor — root component.
//
// Layout is the editor's own sidebar plus a full-bleed Leaflet map. The map
// lives in a ref'd div that React never re-renders into; feature layers are
// components that render null and only run effects against the map instance
// from MapContext.

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Link } from 'react-router-dom'
import ErrorBoundary from '../../components/ErrorBoundary.jsx'
import { modLink } from '../../crossLinks.js'
import { api } from '../../api'
import { useJobs } from '../../jobs'
import { useToast } from '../../components/Toast.jsx'
import { useConfirm } from '../../components/useConfirm.jsx'
import { useConfirmDiscard } from '../../useUnsavedChanges.js'
import { MapEditorProvider, useEditor } from './state/MapEditorProvider'
import MapCredit from './MapCredit.jsx'
import { MapProvider, useLeafletMap, useMapEvent, isMapAlive } from './leaflet/useLeafletMap'
import { useImageOverlay } from './leaflet/useImageOverlay'
import { buildingNear, pickRadius, useBuildingLayer } from './leaflet/useBuildingLayer'
import { buildingBadgeAt, useBuildingBadges } from './leaflet/useBuildingBadges'
import { markAt } from './leaflet/markAt'
import { useLootLayer } from './leaflet/useLootLayer'
import { useRoadLayer } from './leaflet/useRoadLayer'
import { useForestLayer } from './leaflet/useForestLayer'
import { useGridLayer } from './leaflet/useGridLayer'
import { useTerrainLayer } from './leaflet/useTerrainLayer'
import { useIconZoom } from './leaflet/useIconZoom'
import { useLocationLayers } from './leaflet/useLocationLayers'
import { useSpawnLayer } from './leaflet/useSpawnLayer'
import { usePlayerSpawnLayer } from './leaflet/usePlayerSpawnLayer'
import { useZoneCircles } from './leaflet/useZoneCircles'
import { useTraderLayer } from './leaflet/useTraderLayer'
import { useSafeZoneLayer } from './leaflet/useSafeZoneLayer'
import { useGarageLayer } from './leaflet/useGarageLayer'
import { useUndergroundLayer } from './leaflet/useUndergroundLayer'
import { objectKey, useObjectSpawnerLayer } from './leaflet/useObjectSpawnerLayer'
import { useHazardLayers } from './leaflet/useHazardLayers'
import { useDynamicAiLayers } from './leaflet/useDynamicAiLayers'
import {
  MARKET_KINDS,
  useExpansionAILocationLayer,
  useExpansionAIPatrolLayer,
  useExpansionBuildZoneLayer,
  useExpansionMapMarkerLayer,
  useExpansionMarketSpawnLayer,
  useExpansionMissionLayer,
  useExpansionSafeZoneLayer,
  useExpansionSpawnLayer,
  useExpansionTraderZoneLayer,
} from './leaflet/useExpansionLayers'
import { useCollisionLayer } from './leaflet/useCollisionLayer'
import { latLngToGame, latLngToMetres } from './coords'
import { meApi } from './api'
import { MAP_ADD_MODES, addEndpoint, buildAddBody } from './addModes'
import LeftPanel from './panels/LeftPanel.jsx'
import SpawnsTab from './panels/SpawnsTab.jsx'
import { EVENT_FORM } from './panels/EventDetail.jsx'
import PlayerSpawnsTab from './panels/PlayerSpawnsTab.jsx'
import TradersTab from './panels/TradersTab.jsx'
import ModItemsTab from './panels/ModItemsTab.jsx'
import SpawnPopup, { HEIGHT_KEPT } from './popups/SpawnPopup.jsx'
import { findEventEntry, isModOwner, ownerNote } from './spawnOwner'
import { useCopyIntoManualFile } from './CopyIntoManualFile.jsx'
import PlayerSpawnPopup from './popups/PlayerSpawnPopup.jsx'
import TraderNpcPopup from './popups/TraderNpcPopup.jsx'
import TraderObjectPopup from './popups/TraderObjectPopup.jsx'
import SafeZonePopup from './popups/SafeZonePopup.jsx'
import GarageNpcPopup from './popups/GarageNpcPopup.jsx'
import ExpansionTraderZonePopup from './popups/ExpansionTraderZonePopup.jsx'
import ExpansionPatrolPopup from './popups/ExpansionPatrolPopup.jsx'
import ExpansionRoamingPopup from './popups/ExpansionRoamingPopup.jsx'
import {
  ExpansionBuildZonePopup,
  ExpansionMapMarkerPopup,
  ExpansionMarketSpawnPopup,
} from './popups/ExpansionPositionPopups.jsx'
import UndergroundTriggerPopup from './popups/UndergroundTriggerPopup.jsx'
import DynamicAiPopup from './popups/DynamicAiPopup.jsx'
import DynamicAiMoveDialog from './panels/DynamicAiMoveDialog.jsx'
import PlaceNameInput from './panels/PlaceNameInput.jsx'
import CollisionPopup from './popups/CollisionPopup.jsx'
import LootDrawer from './panels/LootDrawer.jsx'
import SaveOverlay from './SaveOverlay.jsx'
import './mapeditor.css'
import PanelNote from '../../components/PanelNote.jsx'
import LoadingState from '../../components/LoadingState.jsx'

// The first read of a world file takes a few seconds; after
// that the server answers from its cache and this is gone at once.
// `fromMaker`: the heights come from the grid the map's maker gave Zedstead,
// downloaded once from zedstead.com (app/maps/maker_heights.py).
// What the map is waiting for, and where it comes from.
function WorldFileNote({ what, fromMaker = false, fromMission = false }) {
  return (
    <div className="me-map-note" role="status">
      {fromMaker
        ? "Downloading this map's terrain from zedstead.com…"
        : fromMission
          ? `Reading the ${what} from this map's mission on your server…`
          : `Reading the ${what} from this map's world file on your server…`}
    </div>
  )
}

function MapCanvas() {
  const {
    mapInfo,
    mapInfoError,
    terrain,
    showImage,
    showTerrain,
    showBuildings,
    showRoads,
    showForest,
    buildings,
    lootData,
    lootCatalog,
    hasBuildings,
    locations,
    spawns,
    playerSpawns,
    traders,
    traderIds,
    safezones,
    garage,
    expSafezones,
    expTraderZones,
    expSpawnPoints,
    expMissions,
    expAiPatrols,
    expAiLocations,
    expMarketSpawns,
    expMapMarkers,
    expBuildZones,
    underground,
    objects,
    hazards,
    dynamicAiData,
    collisions,
    url,
    ui,
    dispatch,
    reload,
    selection,
    saveProgress,
  } = useEditor()
  const bounds = mapInfo?.bounds || null
  const { containerRef, container, map } = useLeafletMap(bounds)
  const pushToast = useToast()
  const confirm = useConfirm()
  const confirmDiscard = useConfirmDiscard()
  const { askCopy, copyDialog } = useCopyIntoManualFile()
  const { trackJob } = useJobs()
  // An Object placement on its way (the map click handler's Object branch).
  const placingRef = useRef(false)

  // The mod page's spawn test at the clicked point (?spawntest=, plans/37's
  // "at a location" mode). It goes to the mod's own route, which takes X and Z
  // in metres and checks the class, the edges and that the server is stopped.
  // The mode stays on until a test is armed, so a refused click can be retried.
  async function startSpawnTest(mode, latlng) {
    const classname = (mode.className || '').trim()
    const count = String(mode.count ?? '').trim()
    if (!classname || !/^\d+$/.test(count)) {
      pushToast('Type the class and a count in the bar first.', 'error')
      return
    }
    const point = latLngToMetres(latlng, bounds, mapInfo?.scale, mapInfo?.map_size)
    if (!point) return
    const ok = await confirm({
      title: `Start a spawn test for ${classname}?`,
      message:
        `Adds a spawn-test block to init.c and copies the storage folder aside. Start the server and join: ` +
        `${count} x ${classname} appear at ${point.x}, ${point.z}, and the first player who joins is moved there. ` +
        `"End test & restore" on ${mode.mod}'s page puts init.c and the storage folder back.`,
      confirmLabel: 'Start test',
    })
    if (!ok) return
    const data = await api.post(`/api/mods/${encodeURIComponent(mode.mod)}/spawn-test`, {
      mode: 'at_location',
      classname,
      count: Number(count),
      x: point.x,
      z: point.z,
      location: null,
    })
    dispatch({ type: 'EXIT_ADD' })
    if (data?.job_id) {
      trackJob(data.job_id, { name: `Start spawn test for ${mode.mod}`, kind: 'spawn_test_start' })
    } else {
      pushToast(
        `Spawn test armed: ${data.count} x ${data.classname} at ${point.x}, ${point.z}. Start the server and join.`,
        'success'
      )
      if (data.warning) pushToast(data.warning, 'info')
    }
  }

  useImageOverlay(
    map,
    terrain,
    bounds,
    showImage,
    mapInfo?.image_own?.version
  )
  // A failed build is remembered on the server, so /map-info now turns the
  // Terrain switch off and says why (terrain_map.heights_source).
  const terrainLoading = useTerrainLayer(
    map,
    mapInfo,
    terrain,
    showTerrain,
    (err) => {
      pushToast(`Could not draw the terrain map: ${err.message}`, 'error')
      reload.mapInfo()
    },
    showImage
  )
  const roadsLoading = useRoadLayer(
    map,
    mapInfo,
    terrain,
    showRoads,
    showTerrain,
    (err) => pushToast(`Could not draw the roads: ${err.message}`, 'error'),
    showImage
  )
  const forestLoading = useForestLayer(
    map,
    mapInfo,
    terrain,
    showForest,
    (err) => pushToast(`Could not draw the forest: ${err.message}`, 'error'),
    showImage
  )
  useBuildingLayer(map, mapInfo, buildings, showBuildings, {
    overlay: showImage,
    overTerrain: showTerrain,
  })
  useLootLayer(map, mapInfo, lootData, url.loot.open, url.loot.mod ? null : url.loot.item)
  useGridLayer(map, mapInfo, Boolean(url.layers.grid))
  useIconZoom(map, container)
  useLocationLayers(map, locations, {
    enabled: Boolean(url.layers.locations),
    visibleTypes: ui.visibleLocationTypes,
  })

  // Box selection binds handlers to the map container and to document; it is
  // created in the provider (the SelectionBar lives in the sidebar) and only
  // learns about the map here.
  useEffect(() => {
    if (!isMapAlive(map)) return
    return selection.attach(map)
  }, [selection, map])

  // ---- Popups -----------------------------------------------------------

  // One popup body for the whole editor. Leaflet keeps at most one popup open,
  // so a node per marker would just be thousands of detached divs.
  const [popupHost] = useState(() => document.createElement('div'))
  const [popupTarget, setPopupTarget] = useState(null)

  const onSpawnPopupOpen = useCallback((meta, popup) => {
    setPopupTarget({ kind: 'spawn', meta, popup })
  }, [])

  const onPlayerSpawnPopupOpen = useCallback((meta, popup, groupName) => {
    setPopupTarget({ kind: 'playerSpawn', meta, popup, groupName })
  }, [])

  // The TraderPlus layers all carry `kind` on their meta already.
  const onEntityPopupOpen = useCallback((meta, popup) => {
    setPopupTarget({ kind: meta.kind, meta, popup })
  }, [])

  const onPopupClose = useCallback(() => setPopupTarget(null), [])

  // Leaflet sizes and auto-pans the popup when it opens, which is before React
  // has portalled anything into the host — so re-measure once the content is
  // in the DOM. Children commit before this parent effect runs.
  useLayoutEffect(() => {
    popupTarget?.popup?.update()
  }, [popupTarget])

  const closePopup = useCallback(() => map?.closePopup(), [map])

  // ---- Spawn markers ----------------------------------------------------

  const onSpawnDragEnd = useCallback(
    async (meta, marker) => {
      const { lat, lng } = marker.getLatLng()
      const event = findEventEntry(spawns?.events, meta.eventName, meta.eventOwner, meta.eventEntry)
      const original = event?.positions.find((p) => p.index === meta.posIndex)

      // A mod's position: one question, and one click copies the mod's file
      // into a manual file and saves the move into the copy.
      let copy = null
      if (isModOwner(meta.owner)) {
        copy = await askCopy({ mod: meta.owner, eventName: meta.eventName, action: 'move' })
      } else {
        const note = ownerNote(meta.owner)
        const ok = await confirm({
          title: 'Move spawn point?',
          message: `Save the new position of "${meta.eventName}" spawn point #${meta.posIndex}?${note ? ` ${note}` : ''}`,
        })
        if (ok) copy = undefined
      }
      if (copy === null) {
        if (original) marker.setLatLng([original.lat, original.lng])
        else reload.spawns()
        return
      }

      try {
        const moved = await meta.save(lat, lng, copy)
        if (moved?.height_kept) pushToast(HEIGHT_KEPT, 'warning')
        if (copy != null) pushToast(`Copied ${meta.owner}'s file into a manual XML file and saved the move there.`, 'success')
      } catch (err) {
        pushToast(`Failed to move spawn: ${err.message}`, 'error')
        reload.spawns()
      }
    },
    [spawns, confirm, askCopy, pushToast, reload]
  )

  useSpawnLayer(map, spawns, {
    enabled: Boolean(url.layers.spawns),
    visibleEvents: ui.visibleEvents,
    showOutOfBounds: Boolean(url.layers.oob),
    showLabels: Boolean(url.layers.labels),
    mapSize: mapInfo?.map_size,
    popupHost,
    onPopupOpen: onSpawnPopupOpen,
    onPopupClose,
    onDragEnd: onSpawnDragEnd,
    selection,
    // In add mode a click on a label adds a point there instead.
    labelClicks: !ui.addMode,
  })

  useZoneCircles(map, spawns, {
    enabled: Boolean(url.layers.zones),
    visibleEvents: ui.visibleEvents,
    scale: mapInfo?.scale,
  })

  // ---- Player spawn markers ---------------------------------------------

  const onPlayerSpawnDragEnd = useCallback(
    async (meta, marker, original) => {
      const { lat, lng } = marker.getLatLng()
      const ok = await confirm({
        title: 'Move player spawn?',
        message: `Save the new position of this "${meta.section}" spawn point?`,
      })
      if (!ok) {
        marker.setLatLng([original.lat, original.lng])
        return
      }
      try {
        await meta.save(lat, lng)
      } catch (err) {
        pushToast(`Failed to move player spawn: ${err.message}`, 'error')
        reload.playerSpawns()
      }
    },
    [confirm, pushToast, reload]
  )

  usePlayerSpawnLayer(map, playerSpawns, {
    enabled: Boolean(url.layers.playerspawns),
    popupHost,
    onPopupOpen: onPlayerSpawnPopupOpen,
    onPopupClose,
    onDragEnd: onPlayerSpawnDragEnd,
    selection,
  })

  // ---- TraderPlus layers ------------------------------------------------

  // One "Traders" checkbox switches all three TraderPlus layers, as in the
  // vanilla editor — they are one feature as far as an admin is concerned.
  const traderPlusEnabled = Boolean(url.layers.traders)

  // Every TraderPlus entity drag is the same shape: confirm, then save through
  // the meta (which preserves the entity's own game_y — a 2D map drag must not
  // flatten something deliberately placed on a rooftop or a ship deck).
  // Cancelling reloads rather than snapping back, because a garage drag also
  // moved its connector line and a trader drag may have moved a clone target.
  const onEntityDragEnd = useCallback(
    async (meta, marker) => {
      const reloadFn = meta.kind.startsWith('garage') ? reload.garage : reload.traders
      const { lat, lng } = marker.getLatLng()
      const ok = await confirm({
        title: 'Move to the new position?',
        message: `Save the new position of ${meta.label}?`,
      })
      if (!ok) {
        reloadFn()
        return
      }
      try {
        await meta.save(lat, lng)
      } catch (err) {
        pushToast(`Move failed: ${err.message}`, 'error')
        reloadFn()
      }
    },
    [confirm, pushToast, reload]
  )

  const onSafeZoneDragEnd = useCallback(
    async (zone, centre) => {
      const { lat, lng } = centre.getLatLng()
      const ok = await confirm({
        title: 'Move safe zone?',
        message: `Save the new centre of "${zone.name}"?`,
      })
      if (!ok) {
        reload.safezones()
        return
      }
      try {
        await meApi.put(`/safezones/${zone.index}`, { lat, lng })
      } catch (err) {
        pushToast(`Move failed: ${err.message}`, 'error')
        reload.safezones()
      }
    },
    [confirm, pushToast, reload]
  )

  const onSafeZoneResizeError = useCallback(
    (err) => {
      pushToast(`Resize failed: ${err.message}`, 'error')
      reload.safezones()
    },
    [pushToast, reload]
  )

  useTraderLayer(map, traders, {
    enabled: traderPlusEnabled,
    popupHost,
    onPopupOpen: onEntityPopupOpen,
    onPopupClose,
    onDragEnd: onEntityDragEnd,
    selection,
  })

  useSafeZoneLayer(map, safezones, {
    enabled: traderPlusEnabled,
    scale: mapInfo?.scale,
    popupHost,
    onPopupOpen: onEntityPopupOpen,
    onPopupClose,
    onCentreDragEnd: onSafeZoneDragEnd,
    onResizeError: onSafeZoneResizeError,
  })

  useGarageLayer(map, garage, {
    enabled: traderPlusEnabled,
    popupHost,
    onPopupOpen: onEntityPopupOpen,
    onPopupClose,
    onDragEnd: onEntityDragEnd,
    selection,
  })

  // ---- Expansion layers -------------------------------------------------
  //
  // Four independent files, so four independent toggles — unlike the single
  // Traders checkbox above, which covers three TraderPlus files that are one
  // feature to an admin.
  //
  // Every save is the same shape: PUT and let the WS ping refetch, reload on
  // failure so the map never keeps a position the file does not have.
  //
  // Zone drags (safe zones, trader zones) are **not** confirmed: they are areas
  // being shaped, and a dialog per nudge while dragging a polygon corner would
  // be unusable — the undo is the editor itself. A spawn *point* is a placed
  // object like every other marker on this map, so it confirms, same as the
  // vanilla player spawns above.
  const onExpansionSave = useCallback((path, body) => meApi.put(path, body), [])

  const onExpansionError = useCallback(
    (err) => {
      pushToast(`Save failed: ${err.message}`, 'error')
      reload.expSafezones()
      reload.expTraderZones()
    },
    [pushToast, reload]
  )

  useExpansionSafeZoneLayer(map, expSafezones, {
    enabled: Boolean(url.layers.expsafezones),
    scale: mapInfo?.scale,
    onSave: onExpansionSave,
    onError: onExpansionError,
  })

  useExpansionTraderZoneLayer(map, expTraderZones, {
    enabled: Boolean(url.layers.exptraderzones),
    scale: mapInfo?.scale,
    popupHost,
    onPopupOpen: onEntityPopupOpen,
    onPopupClose,
    onSave: onExpansionSave,
    onError: onExpansionError,
  })

  const onExpansionSpawnDragEnd = useCallback(
    async (meta, marker, original) => {
      const { lat, lng } = marker.getLatLng()
      const ok = await confirm({
        title: 'Move Expansion spawn point?',
        message: `Save the new position of "${meta.name}" spawn point #${meta.index + 1}?`,
      })
      if (!ok) {
        marker.setLatLng([original.lat, original.lng])
        return
      }
      try {
        await meApi.put(`/expansion/spawn-points/${meta.location}/${meta.index}`, { lat, lng })
      } catch (err) {
        pushToast(`Failed to move spawn point: ${err.message}`, 'error')
        marker.setLatLng([original.lat, original.lng])
        reload.expSpawnPoints()
      }
    },
    [confirm, pushToast, reload]
  )

  useExpansionSpawnLayer(map, expSpawnPoints, {
    enabled: Boolean(url.layers.expspawns),
    onDragEnd: onExpansionSpawnDragEnd,
  })

  useExpansionMissionLayer(map, expMissions, {
    enabled: Boolean(url.layers.expmissions),
    scale: mapInfo?.scale,
  })

  // ---- Expansion AI -----------------------------------------------------

  const onAIWaypointDragEnd = useCallback(
    async (meta, marker, original) => {
      const { lat, lng } = marker.getLatLng()
      const ok = await confirm({
        title: 'Move waypoint?',
        message: `Save the new position of waypoint #${meta.index + 1} on "${meta.name}"?`,
      })
      if (!ok) {
        marker.setLatLng([original.lat, original.lng])
        reload.expAiPatrols()
        return
      }
      try {
        await meApi.put(
          `/expansion/ai/patrols/${meta.patrol}/waypoints/${meta.index}`,
          { lat, lng }
        )
      } catch (err) {
        pushToast(`Failed to move waypoint: ${err.message}`, 'error')
        reload.expAiPatrols()
      }
    },
    [confirm, pushToast, reload]
  )

  const onAILocationError = useCallback(
    (err) => {
      pushToast(`Save failed: ${err.message}`, 'error')
      reload.expAiLocations()
    },
    [pushToast, reload]
  )

  useExpansionAIPatrolLayer(map, expAiPatrols, {
    enabled: Boolean(url.layers.expaipatrols),
    popupHost,
    onPopupOpen: onEntityPopupOpen,
    onPopupClose,
    onDragEnd: onAIWaypointDragEnd,
  })

  useExpansionAILocationLayer(map, expAiLocations, {
    enabled: Boolean(url.layers.expairoaming),
    scale: mapInfo?.scale,
    popupHost,
    onPopupOpen: onEntityPopupOpen,
    onPopupClose,
    onSave: onExpansionSave,
    onError: onAILocationError,
  })

  // ---- Expansion settings position lists (TODO H4) ----------------------

  // A vehicle spawn or a server marker is a placed object, so a drag asks
  // first, like an Expansion spawn point; a build zone is an area and does not.
  const confirmPointDrag = useCallback(
    async ({ title, message, path, reload: reloadLayer }, marker, original) => {
      const { lat, lng } = marker.getLatLng()
      if (!(await confirm({ title, message }))) {
        marker.setLatLng([original.lat, original.lng])
        return
      }
      try {
        await meApi.put(path, { lat, lng })
      } catch (err) {
        pushToast(`Move failed: ${err.message}`, 'error')
        marker.setLatLng([original.lat, original.lng])
        reloadLayer()
      }
    },
    [confirm, pushToast]
  )

  useExpansionMarketSpawnLayer(map, expMarketSpawns, {
    enabled: Boolean(url.layers.expmarket),
    popupHost,
    onPopupOpen: onEntityPopupOpen,
    onPopupClose,
    onDragEnd: (meta, marker, original) =>
      confirmPointDrag(
        {
          title: 'Move vehicle spawn?',
          message: `Save the new position of ${MARKET_KINDS[meta.list].toLowerCase()} vehicle spawn #${
            meta.index + 1
          }? Its height stays ${original.game_y} m.`,
          path: `/expansion/market-spawns/${meta.list}/${meta.index}`,
          reload: reload.expMarketSpawns,
        },
        marker,
        original
      ),
  })

  useExpansionMapMarkerLayer(map, expMapMarkers, {
    enabled: Boolean(url.layers.expmarkers),
    popupHost,
    onPopupOpen: onEntityPopupOpen,
    onPopupClose,
    onDragEnd: (meta, marker, original) =>
      confirmPointDrag(
        {
          title: 'Move map marker?',
          message: `Save the new position of "${original.text || original.uid}"?`,
          path: `/expansion/map-markers/${meta.index}`,
          reload: reload.expMapMarkers,
        },
        marker,
        original
      ),
  })

  const onBuildZoneError = useCallback(
    (err) => {
      pushToast(`Save failed: ${err.message}`, 'error')
      reload.expBuildZones()
    },
    [pushToast, reload]
  )

  useExpansionBuildZoneLayer(map, expBuildZones, {
    enabled: Boolean(url.layers.expbuildzones),
    scale: mapInfo?.scale,
    popupHost,
    onPopupOpen: onEntityPopupOpen,
    onPopupClose,
    onSave: onExpansionSave,
    onError: onBuildZoneError,
  })

  const onUndergroundError = useCallback(
    (err) => {
      pushToast(`Save failed: ${err.message}`, 'error')
      reload.underground()
    },
    [pushToast, reload]
  )

  useUndergroundLayer(map, underground, {
    enabled: Boolean(url.layers.underground),
    scale: mapInfo?.scale,
    popupHost,
    onPopupOpen: onEntityPopupOpen,
    onPopupClose,
    onSave: onExpansionSave,
    onError: onUndergroundError,
  })

  // A click on a placed object selects it in the Items tab's Placed objects list.
  const onObjectSelect = useCallback(
    (obj) => {
      dispatch({ type: 'SELECT_OBJECT', payload: { disabled: obj.disabled, index: obj.index } })
      if (url.tab !== 'mod-items') url.setTab('mod-items')
    },
    [dispatch, url]
  )
  const onObjectDrag = useCallback(
    (latlng) => dispatch({ type: 'OBJECT_EDIT_DRAG', payload: latlng }),
    [dispatch]
  )
  useObjectSpawnerLayer(map, objects, {
    enabled: Boolean(url.layers.objects),
    selected: ui.selectedObject && objectKey(ui.selectedObject),
    onSelect: onObjectSelect,
    editing: ui.objectEdit,
    onDrag: onObjectDrag,
  })

  // ---- Hazard zones (plans/48 C) ----------------------------------------

  const onHazardError = useCallback(
    (err) => {
      pushToast(`Save failed: ${err.message}`, 'error')
      reload.hazards()
    },
    [pushToast, reload]
  )

  useHazardLayers(map, hazards, {
    contaminated: Boolean(url.layers.contaminated),
    radzone: Boolean(url.layers.radzone),
    crates: Boolean(url.layers.radzonecrates),
    toxiczone: Boolean(url.layers.toxiczone),
    toxicmoving: Boolean(url.layers.toxicmoving),
    toxiccrates: Boolean(url.layers.toxiccrates),
    scale: mapInfo?.scale,
    onSave: onExpansionSave,
    onError: onHazardError,
  })

  // ---- Dynamic AI (plans/47 D) ------------------------------------------

  const onDynamicAiError = useCallback(
    (err) => {
      pushToast(`Save failed: ${err.message}`, 'error')
      reload.dynamicAi()
    },
    [pushToast, reload]
  )

  // "Move here…" on a location: the map click lands here, and the dialog asks
  // whether the spawn positions move along before anything is saved.
  const [pendingMove, setPendingMove] = useState(null)
  // A place name clicked onto the map with no name in the add bar: its name
  // is typed in a box at the spot (PlaceNameInput).
  const [pendingPlace, setPendingPlace] = useState(null)

  const moveDynamicAi = useCallback(
    async ({ section, index, lat, lng }, carry) => {
      try {
        await meApi.put(`/dynamic-ai/${section}/${index}`, { lat, lng, carry_spawns: carry })
      } catch (err) {
        onDynamicAiError(err)
      }
    },
    [onDynamicAiError]
  )

  useDynamicAiLayers(map, dynamicAiData, {
    points: Boolean(url.layers.dynaipoints),
    locations: Boolean(url.layers.dynailocations),
    audio: Boolean(url.layers.dynaiaudio),
    scale: mapInfo?.scale,
    popupHost,
    onPopupOpen: onEntityPopupOpen,
    onPopupClose,
    onSave: onExpansionSave,
    onError: onDynamicAiError,
  })

  // ---- Collisions -------------------------------------------------------

  // Not a ?layers= entry: the collision overlay is on exactly when the panel
  // has a mode, which is what the Analysis deep link sets.
  useCollisionLayer(map, collisions, {
    enabled: Boolean(url.collisions.mode),
    popupHost,
    onPopupOpen: onEntityPopupOpen,
    onPopupClose,
  })

  // ---- Popup contents ---------------------------------------------------

  const selectedEvent = ui.selectedEvent
  const popupContent = useMemo(() => {
    if (!popupTarget) return null
    const { kind, meta } = popupTarget

    if (kind === 'spawn') {
      const event = findEventEntry(spawns?.events, meta.eventName, meta.eventOwner, meta.eventEntry)
      const pos = event?.positions.find((p) => p.index === meta.posIndex)
      if (!event || !pos) return null
      return (
        <SpawnPopup
          key={`spawn:${event.name}:${event.owner || ''}:${event.entry || 0}:${pos.index}`}
          event={event}
          pos={pos}
          askCopy={askCopy}
          onClose={closePopup}
          onOpenDetail={async (name) => {
            closePopup()
            // Another event's form replaces the open one and its edits.
            if (name === selectedEvent) return
            if (await confirmDiscard(EVENT_FORM)) dispatch({ type: 'SELECT_EVENT', payload: name })
          }}
        />
      )
    }

    if (kind === 'playerSpawn') {
      const section = playerSpawns.find((s) => s.section === meta.section)
      const group = section?.groups.find((g) => g.index === meta.groupIndex)
      const pos = group?.positions.find((p) => p.index === meta.posIndex)
      if (!group || !pos) return null
      return (
        <PlayerSpawnPopup
          key={`ps:${meta.section}:${meta.groupIndex}:${pos.index}`}
          section={meta.section}
          groupIndex={meta.groupIndex}
          groupName={group.name}
          pos={pos}
          onClose={closePopup}
        />
      )
    }

    // TraderPlus entities are addressed by array index; after a reload the
    // record at that index may be gone (or, worse, be a different one — see
    // the note in useTraderLayer). Rendering nothing is the safe outcome.
    if (kind === 'trader-npc') {
      const trader = traders?.traders.find((t) => t.index === meta.index)
      if (!trader) return null
      return (
        <TraderNpcPopup
          key={`trader:${trader.index}`}
          trader={trader}
          allTraders={traders.traders}
          traderIds={traderIds}
          onClose={closePopup}
        />
      )
    }

    if (kind === 'trader-object') {
      const obj = traders?.trader_objects.find((o) => o.index === meta.index)
      if (!obj) return null
      return <TraderObjectPopup key={`object:${obj.index}`} obj={obj} onClose={closePopup} />
    }

    if (kind === 'safezone') {
      const zone = safezones?.safezones.find((z) => z.index === meta.index)
      if (!zone) return null
      return <SafeZonePopup key={`safezone:${zone.index}`} zone={zone} onClose={closePopup} />
    }

    // The collision record travels on the meta: it is a scan result, not a row
    // in any of the resources, so there is nothing to look it back up in.
    if (kind === 'collision') {
      return <CollisionPopup collision={meta.collision} side={meta.side} />
    }

    if (kind === 'expansion-trader-zone') {
      const zone = expTraderZones?.trader_zones.find((z) => z.id === meta.id)
      if (!zone) return null
      return (
        <ExpansionTraderZonePopup
          key={`exp-zone:${zone.id}`}
          zone={zone}
          onClose={closePopup}
        />
      )
    }

    if (kind === 'expansion-ai-patrol') {
      const patrol = expAiPatrols?.patrols.find((p) => p.index === meta.index)
      if (!patrol) return null
      return (
        <ExpansionPatrolPopup
          key={`ai-patrol:${patrol.index}`}
          patrol={patrol}
          onClose={closePopup}
          onAddWaypoint={() => {
            closePopup()
            dispatch({
              type: 'ENTER_AI_ADD',
              payload: {
                mode: 'waypoint',
                patrol: patrol.index,
                label: `Waypoint on ${patrol.name}`,
              },
            })
          }}
        />
      )
    }

    if (kind === 'expansion-ai-roaming') {
      const location = expAiLocations?.roaming_locations.find((l) => l.index === meta.index)
      if (!location) return null
      return (
        <ExpansionRoamingPopup
          key={`ai-roaming:${location.index}`}
          location={location}
          onClose={closePopup}
          onCopy={() => {
            closePopup()
            dispatch({
              type: 'ENTER_AI_ADD',
              payload: {
                mode: 'roaming-copy',
                index: location.index,
                label: `Copy of ${location.name}`,
              },
            })
          }}
        />
      )
    }

    // Copy here…: a one-shot add mode that POSTs the duplicate endpoint.
    const copyHere = (endpoint, label) => {
      closePopup()
      dispatch({ type: 'ENTER_AI_ADD', payload: { mode: 'exp-copy', endpoint, label, verb: 'Placing' } })
    }

    if (kind === 'expansion-market-spawn') {
      const spawn = expMarketSpawns?.[meta.list]?.find((s) => s.index === meta.index)
      if (!spawn) return null
      return (
        <ExpansionMarketSpawnPopup
          key={`market:${meta.list}:${spawn.index}`}
          list={meta.list}
          spawn={spawn}
          data={expMarketSpawns}
          onClose={closePopup}
          onCopy={() =>
            copyHere(
              `/expansion/market-spawns/${meta.list}/${spawn.index}/duplicate`,
              `Copy of ${MARKET_KINDS[meta.list].toLowerCase()} vehicle spawn ${spawn.index + 1}`
            )
          }
        />
      )
    }

    if (kind === 'expansion-map-marker') {
      const marker = expMapMarkers?.markers.find((m) => m.index === meta.index)
      if (!marker) return null
      return (
        <ExpansionMapMarkerPopup
          key={`marker:${marker.index}`}
          marker={marker}
          enabled={expMapMarkers.enabled}
          onClose={closePopup}
          onCopy={() =>
            copyHere(`/expansion/map-markers/${marker.index}/duplicate`, `Copy of ${marker.text || marker.uid}`)
          }
        />
      )
    }

    if (kind === 'expansion-build-zone') {
      const zone = expBuildZones?.zones.find((z) => z.index === meta.index)
      if (!zone) return null
      return (
        <ExpansionBuildZonePopup
          key={`buildzone:${zone.index}`}
          zone={zone}
          noBuildZones={expBuildZones.no_build_zones}
          onClose={closePopup}
          onCopy={() => copyHere(`/expansion/build-zones/${zone.index}/duplicate`, `Copy of ${zone.name}`)}
        />
      )
    }

    if (kind === 'underground-trigger') {
      const trigger = underground?.triggers.find((tr) => tr.index === meta.index)
      if (!trigger) return null
      return (
        <UndergroundTriggerPopup
          key={`underground:${trigger.index}`}
          trigger={trigger}
          onClose={closePopup}
        />
      )
    }

    if (kind === 'dynamic-ai') {
      const entry = dynamicAiData?.[meta.section]?.find((e) => e.index === meta.index)
      if (!entry) return null
      const place = (mode, label) => {
        closePopup()
        dispatch({
          type: 'ENTER_AI_ADD',
          payload: { mode, section: meta.section, index: entry.index, label, verb: 'Placing' },
        })
      }
      return (
        <DynamicAiPopup
          key={`dynai:${meta.section}:${entry.index}`}
          section={meta.section}
          entry={entry}
          modes={dynamicAiData.modes}
          onClose={closePopup}
          onCopy={() => place('dynai-copy', `Copy of ${entry.name}`)}
          onMove={() => place('dynai-move', entry.name)}
        />
      )
    }

    if (kind === 'garage-npc') {
      const npc = garage?.garage_npcs.find((g) => g.index === meta.index)
      if (!npc) return null
      return <GarageNpcPopup key={`garage:${npc.index}`} npc={npc} onClose={closePopup} />
    }

    return null
  }, [
    popupTarget,
    spawns,
    playerSpawns,
    traders,
    traderIds,
    safezones,
    garage,
    expTraderZones,
    expAiPatrols,
    expAiLocations,
    expMarketSpawns,
    expMapMarkers,
    expBuildZones,
    dynamicAiData,
    closePopup,
    askCopy,
    confirmDiscard,
    selectedEvent,
    dispatch,
  ])

  // ---- A building click with the Loot finder closed -----------------------

  // Zoomed in, each loot building carries its kind's icon: on the Buildings
  // layer, and in the Loot finder's Building view. The finder's Item and Mod
  // views show their own points instead.
  useBuildingBadges(map, mapInfo, lootCatalog, url.loot.open ? url.loot.view === 'building' : showBuildings)

  // With the Buildings layer on, a click on a building opens the Loot finder's
  // Building view on it, as a click does while that view is open. A click on
  // empty ground does nothing. With the finder open, LootDrawer owns the click.
  useMapEvent(map, 'click', (e) => {
    if (url.loot.open || ui.addMode || ui.objectEdit || !showBuildings || !hasBuildings) return
    // A badge picks its own building, as in the open finder.
    const badge = buildingBadgeAt(map, mapInfo, lootCatalog, e.latlng)
    const radius = badge ? 3 : pickRadius(map, mapInfo, e.latlng)
    const at = badge || e.latlng
    if (!badge && !buildingNear(buildings?.points, mapInfo, e.latlng, radius)) return
    dispatch({ type: 'LOOT_PICK', payload: { lat: at.lat, lng: at.lng, radius } })
    url.setMapLayers({ buildingView: true })
  })

  // ---- Add mode ---------------------------------------------------------

  const addMode = ui.addMode

  useMapEvent(map, 'click', async (e) => {
    // An open object Edit form: a click moves that object there (a drag does too).
    if (!addMode && ui.objectEdit) {
      dispatch({ type: 'OBJECT_EDIT_DRAG', payload: { lat: e.latlng.lat, lng: e.latlng.lng } })
      return
    }
    if (!addMode) return
    const { lat, lng } = e.latlng
    try {
      if (addMode.kind === 'event') {
        await meApi.post(`/spawns/${encodeURIComponent(addMode.event)}`, { lat, lng, angle: -1 })
      } else if (addMode.kind === 'player-spawn') {
        // lat/lng, never client-projected game coords — see coords.js.
        await meApi.post(
          `/player-spawns/${encodeURIComponent(addMode.section)}/groups/${
            addMode.groupIndex
          }/positions`,
          { lat, lng }
        )
      } else if (addMode.kind === 'ai') {
        if (addMode.mode === 'waypoint') {
          // Repeatable: a route is built point after point, so the mode stays
          // on until the bar's Cancel — the same call the event-spawn mode makes.
          await meApi.post(`/expansion/ai/patrols/${addMode.patrol}/waypoints`, { lat, lng })
        } else if (addMode.mode === 'exp-copy') {
          // The three settings position lists (TODO H4): one-shot, like every copy.
          dispatch({ type: 'EXIT_ADD' })
          await meApi.post(addMode.endpoint, { lat, lng })
        } else if (addMode.mode === 'dynai-copy') {
          // Dynamic AI (plans/47 D): one-shot, like the roaming copy below.
          dispatch({ type: 'EXIT_ADD' })
          await meApi.post(`/dynamic-ai/${addMode.section}/${addMode.index}/duplicate`, {
            lat,
            lng,
          })
        } else if (addMode.mode === 'dynai-move') {
          // The trigger lands here; the dialog asks about the spawn positions.
          // A location with none has nothing to ask.
          dispatch({ type: 'EXIT_ADD' })
          const { section, index } = addMode
          const entry = dynamicAiData?.[section]?.find((e) => e.index === index)
          const spawnCount = entry?.spawns?.length || 0
          const move = { section, index, lat, lng }
          if (spawnCount) setPendingMove({ ...move, name: entry.name, spawnCount })
          else await moveDynamicAi(move, false)
        } else {
          // One-shot, like the TraderPlus creates: a copy is a whole new record.
          dispatch({ type: 'EXIT_ADD' })
          await meApi.post(`/expansion/ai/locations/${addMode.index}/duplicate`, { lat, lng })
        }
      } else if (addMode.kind === 'spawn-test') {
        try {
          await startSpawnTest(addMode, e.latlng)
        } catch (err) {
          // The route's own sentence: server running, unknown class, off the map.
          pushToast(`Spawn test not started: ${err.message}`, 'error')
        }
      } else if (addMode.kind === 'map' && MAP_ADD_MODES[addMode.mode].yField) {
        // The Object mode stays on until a placement lands, so a refused click
        // (no terrain heights: type Y in the bar) can be tried again. The ref
        // stops a double click from placing two.
        if (placingRef.current) return
        placingRef.current = true
        try {
          const config = MAP_ADD_MODES[addMode.mode]
          const saved = await meApi.post(addEndpoint(addMode), buildAddBody(addMode, lat, lng))
          dispatch({ type: 'EXIT_ADD' })
          reload[config.reload]()
          pushToast(`Placed ${saved.name} at ${saved.x.toFixed(1)}, ${saved.z.toFixed(1)} (Y ${saved.y.toFixed(1)}).`, 'success')
          if (saved.gameplay_on === false) {
            pushToast(
              'serverDZ.cfg has enableCfgGameplayFile = 0, so DayZ ignores cfggameplay.json and spawns no Object Spawner object. Turn it on in the Items tab or the Server config.',
              'warning',
              10000
            )
          }
        } finally {
          placingRef.current = false
        }
      } else if (addMode.kind === 'map') {
        // One-shot: the TraderPlus modes create a whole new record, and you
        // rarely want ten in a row. Exit before awaiting so a double click
        // cannot place two.
        dispatch({ type: 'EXIT_ADD' })
        if (addMode.mode === 'location' && !(addMode.className || '').trim()) {
          setPendingPlace({ lat, lng })
          return
        }
        const config = MAP_ADD_MODES[addMode.mode]
        await meApi[config.method || 'post'](addEndpoint(addMode), buildAddBody(addMode, lat, lng))
        if (config.reload) reload[config.reload]()
      }
    } catch (err) {
      pushToast(`Failed to add position: ${err.message}`, 'error')
    }
  })

  // ---- Sidebar-driven map moves -----------------------------------------

  // A fly-to marks the markers it lands on (markAt.js), once the move ends:
  // only then are they drawn at their new screen spot. The timer marks again,
  // for a setView that does not move (no moveend) and for a layer the same
  // click just switched on, whose markers arrive after the move.
  useEffect(() => {
    if (!isMapAlive(map) || !ui.flyTo) return
    const { lat, lng } = ui.flyTo
    let unmark = null
    const mark = () => {
      unmark?.()
      unmark = markAt(map, lat, lng)
    }
    map.once('moveend', mark)
    const timer = setTimeout(mark, 400)
    map.setView([lat, lng], ui.flyTo.zoom ?? map.getZoom())
    return () => {
      clearTimeout(timer)
      if (isMapAlive(map)) map.off('moveend', mark)
      unmark?.()
    }
    // seq, not the coordinates: asking to fly to the same place twice must work.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, ui.flyTo?.seq])

  // Live cursor readout, display-only (see coords.js — writes always send
  // lat/lng and let the server project).
  useMapEvent(map, 'mousemove', (e) => {
    dispatch({
      type: 'SET_HOVER_COORDS',
      payload: latLngToGame(e.latlng, bounds, mapInfo?.scale),
    })
  })
  useMapEvent(map, 'mouseout', () => dispatch({ type: 'SET_HOVER_COORDS', payload: null }))

  // The error renders OVER the map, never instead of it. Unmounting the
  // container would strand the Leaflet instance on a detached node, and
  // remounting it later would not rebuild the map — the map area would stay
  // blank until a full page reload.
  //
  // The add-mode cursor class goes on the WRAPPER, never on .me-map: React
  // writes className wholesale, and Leaflet has put its own classes
  // (leaflet-container, leaflet-touch, …) on that same element.
  return (
    <div className={`me-map-wrap${addMode ? ' add-mode-cursor' : ''}`}>
      <div ref={containerRef} className="me-map" />
      {mapInfo?.credit && ((showImage && !mapInfo.image_own) || showTerrain || showRoads) && (
        <MapCredit credit={mapInfo.credit} />
      )}
      {(terrainLoading || roadsLoading) && (
        <WorldFileNote
          what={terrainLoading && roadsLoading ? 'terrain and roads' : terrainLoading ? 'terrain' : 'roads'}
          fromMaker={terrainLoading && !roadsLoading && mapInfo?.terrain_source === 'maker'}
          fromMission={!terrainLoading && mapInfo?.roads_source === 'mission'}
        />
      )}
      {forestLoading && !terrainLoading && !roadsLoading && (
        <WorldFileNote what="forest" fromMission />
      )}
      {mapInfoError && (
        <div className="me-error me-error-overlay">
          <strong>Could not load the map.</strong>
          <span>{mapInfoError.message}</span>
          <button className="btn btn-primary btn-small" onClick={reload.mapInfo}>
            Retry
          </button>
        </div>
      )}
      {url.loot.open && hasBuildings && <LootDrawer map={map} />}
      {popupContent && createPortal(popupContent, popupHost)}
      {pendingMove && (
        <DynamicAiMoveDialog
          move={pendingMove}
          onConfirm={(carry) => {
            setPendingMove(null)
            moveDynamicAi(pendingMove, carry)
          }}
          onCancel={() => setPendingMove(null)}
        />
      )}
      {pendingPlace && (
        <PlaceNameInput
          map={map}
          at={pendingPlace}
          onCancel={() => setPendingPlace(null)}
          onSave={async (name) => {
            setPendingPlace(null)
            try {
              await meApi.post(MAP_ADD_MODES.location.endpoint, { ...pendingPlace, name })
            } catch (err) {
              pushToast(`Place name not added: ${err.message}`, 'error')
            } finally {
              reload.locations()
            }
          }}
        />
      )}
      {copyDialog}
      <SaveOverlay progress={saveProgress} />
      {map && <MapProvider value={map}>{null}</MapProvider>}
    </div>
  )
}

function EditorShell() {
  const { mapInfo, mapInfoLoading, ui, dispatch, url, selectionCount, traderplus } = useEditor()

  // Escape leaves add mode. The vanilla editor had no such binding — the only
  // way out was the Cancel button — and a stuck crosshair that adds a spawn on
  // every stray map click is worth one keydown listener.
  //
  // Selection has its own Escape handler, so this one stands down while
  // anything is selected: first Escape clears the selection, second leaves add
  // mode. Both firing at once would silently do two things.
  useEffect(() => {
    if (!ui.addMode || selectionCount) return
    const onKey = (e) => {
      if (e.key === 'Escape') dispatch({ type: 'EXIT_ADD' })
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [ui.addMode, selectionCount, dispatch])

  // Full-window mode. The class goes on <body> rather than raising the
  // editor's z-index: the app's modals (100) and toasts (200) live in the root
  // stacking context, and an editor stacked above them would hide its own
  // confirm dialogs. Removing it on unmount matters — navigating away while
  // expanded must not leave the app without its nav.
  //
  // A change after the first render goes through a view transition, so the
  // app's chrome fades while the editor grows into its space (or shrinks back)
  // instead of jumping. Skipped without the API or with reduced motion.
  const expandedOnce = useRef(false)
  useEffect(() => {
    const apply = () => document.body.classList.toggle('me-fullscreen', ui.expanded)
    const animate =
      expandedOnce.current &&
      document.startViewTransition &&
      !window.matchMedia('(prefers-reduced-motion: reduce)').matches
    expandedOnce.current = true
    if (animate) document.startViewTransition(apply)
    else apply()
  }, [ui.expanded])
  // Only on unmount: a cleanup per change would drop the class before the
  // transition takes its "before" picture, and shrinking would not animate.
  useEffect(() => () => document.body.classList.remove('me-fullscreen'), [])

  // Escape leaves full-window mode, but only when it is the outermost thing
  // open: add mode owns Escape while it is active (the effect above), the
  // selection clears itself first, and any dialog closes itself — none of
  // which should also collapse the view.
  useEffect(() => {
    if (!ui.expanded || ui.addMode || selectionCount) return
    const onKey = (e) => {
      if (e.key !== 'Escape') return
      if (document.querySelector('.dialog-overlay, .modal-backdrop')) return
      dispatch({ type: 'SET_EXPANDED', payload: false })
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [ui.expanded, ui.addMode, selectionCount, dispatch])

  if (!mapInfo && mapInfoLoading) {
    return <LoadingState label="Loading mission data…" />
  }

  return (
    <>
      <LeftPanel>
        {url.tab === 'spawns' && <SpawnsTab />}
        {url.tab === 'player-spawns' && <PlayerSpawnsTab />}
        {url.tab === 'mod-items' && <ModItemsTab />}
        {/* The tab button is disabled without TraderPlus, but ?tab=traders in
            a bookmarked URL can still land here. */}
        {url.tab === 'traders' &&
          (traderplus ? (
            <TradersTab />
          ) : (
            <PanelNote>
              TraderPlus mod is not installed.{' '}
              <Link to={modLink('@TraderPlus')}>Install it →</Link>
            </PanelNote>
          ))}
      </LeftPanel>
      <MapCanvas />
    </>
  )
}

export default function MapEditor() {
  return (
    <ErrorBoundary name="MapEditor" title="The map editor crashed">
      <div className="me-editor">
        <MapEditorProvider>
          <EditorShell />
        </MapEditorProvider>
      </div>
    </ErrorBoundary>
  )
}
