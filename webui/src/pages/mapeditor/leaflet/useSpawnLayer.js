// Event-spawn markers: one Leaflet layer group per event, inside a master
// group that the "Event Spawns" layer toggle switches on and off.
//
// The rebuild is destructive (clear everything, recreate) exactly as the
// vanilla renderSpawnMarkers() was — that is what parity is being validated
// against, and keyed diffing is a later optimisation for the two layers that
// actually need it (collisions, and spawns on very large missions).
//
// What did NOT survive the port is the snapshot/restore dance around it. The
// old code recorded which groups were hidden and which were empty before
// clearing, then reconstructed that afterwards, because visibility lived in a
// DOM checkbox plus `spawnLayerGroup.hasLayer()`. Visibility is React state
// now (`ui.visibleEvents`), so the rebuild just rebuilds and a separate effect
// syncs what is on the map.

import { useEffect, useState } from 'react'
import L from 'leaflet'
import { isMapAlive } from './useLeafletMap'
import { setMeta } from './markerRegistry'
import { getEventColor, spawnMarkerSvg } from '../icons/iconRules'
import { useEventCallback } from '../useEventCallback'
import { meApi } from '../api'
import { useSpawnOverlay } from './useSpawnOverlay'
import { html, raw } from './html'
import { isModOwner, spawnPath } from '../spawnOwner'

const DEFAULT_MAP_SIZE = 15360

/** A spawn position outside the playable square (mods and hand-edits produce these). */
export function isOutOfBounds(pos, mapSize) {
  const size = mapSize || DEFAULT_MAP_SIZE
  return pos.game_x < 0 || pos.game_x > size || pos.game_z < 0 || pos.game_z > size
}

function createSpawnIcon(color, eventName) {
  return L.divIcon({
    className: 'spawn-icon-wrapper',
    html: html`<span style="--icon-color:${color}">${raw(spawnMarkerSvg(color, eventName))}</span>`,
    iconSize: [14, 14],
    iconAnchor: [7, 7],
  })
}

/**
 * @param map      Leaflet map (may be null or already removed)
 * @param spawns   GET /spawns payload
 * @param options  { enabled, visibleEvents, showOutOfBounds, showLabels,
 *                   mapSize, popupHost, onPopupOpen, onPopupClose, onDragEnd,
 *                   selection, labelClicks }
 */
export function useSpawnLayer(
  map,
  spawns,
  {
    enabled,
    visibleEvents,
    showOutOfBounds,
    showLabels,
    mapSize,
    popupHost,
    onPopupOpen,
    onPopupClose,
    onDragEnd,
    selection,
    labelClicks = true,
  }
) {
  const [master, setMaster] = useState(null)
  const [groups, setGroups] = useState(null)

  // Marker handlers are bound once per rebuild but must see the latest props,
  // or a drag would save against the data as it was when the layer was built.
  const handlePopupOpen = useEventCallback(onPopupOpen)
  const handlePopupClose = useEventCallback(onPopupClose)
  const handleDragEnd = useEventCallback(onDragEnd)

  // Master group: created once per map, so the "Event Spawns" toggle has
  // something stable to add and remove.
  useEffect(() => {
    if (!isMapAlive(map)) return
    const group = L.layerGroup()
    setMaster(group)
    return () => {
      setMaster(null)
      group.remove()
    }
  }, [map])

  // Rebuild the per-event groups.
  useEffect(() => {
    if (!isMapAlive(map) || !master) return

    // Every marker we are about to destroy may be in the selection.
    selection?.reset()

    const built = new Map()
    for (const event of spawns?.events || []) {
      const icon = createSpawnIcon(getEventColor(event.name), event.name)
      // The same name can come more than once (the mission's own entry, a
      // manual file's, a mod's); visibility is per name, so they share a group.
      const group = built.get(event.name) || L.layerGroup()

      for (const pos of event.positions) {
        if (!showOutOfBounds && isOutOfBounds(pos, mapSize)) continue

        // A mod's position can be dragged too: on drop the page asks to copy
        // the mod's file into a manual file first (CopyIntoManualFile.jsx).
        const marker = L.marker([pos.lat, pos.lng], { draggable: true, icon })
        const meta = {
          kind: 'spawn',
          eventName: event.name,
          eventOwner: event.owner || null,
          eventEntry: event.entry || 0,
          owner: pos.owner || null,
          posIndex: pos.index,
          angle: pos.angle,
          // Box selection deletes highest-index-first *within a list*; this is
          // the list a spawn position belongs to.
          groupKey: `spawn:${event.name}|${event.owner || ''}|${event.entry || 0}`,
          // `copy` (only for a mod's position) is spawnPath's: copy the mod's
          // file into a manual file first.
          save: (lat, lng, copy) => meApi.put(spawnPath(event, pos.index, '', copy), { lat, lng, angle: pos.angle }),
          del: () => meApi.del(spawnPath(event, pos.index)),
          // Box selection leaves a mod's positions out (it toasts how many);
          // each one is copied and moved on its own, after its own question.
          batchSkip: isModOwner(pos.owner),
        }
        setMeta(marker, meta)

        // One shared popup element for the whole layer, handed over on open —
        // Leaflet keeps at most one popup open, and a div per marker would
        // mean thousands of detached nodes on a big mission. The vanilla
        // editor built its content lazily in the same way, via a function.
        if (popupHost) {
          marker.bindPopup(() => popupHost, { maxWidth: 250, className: 'dark-popup' })
          marker.on('popupopen', (e) => handlePopupOpen(meta, e.popup))
          marker.on('popupclose', () => handlePopupClose(meta))
        }

        // A drag on a selected marker moves the whole selection; otherwise it
        // is an ordinary single-marker move.
        marker.on('dragstart', () => {
          if (selection?.isSelected(marker)) selection.dragStart(marker)
        })
        marker.on('drag', () => {
          if (selection?.isSelected(marker)) selection.dragMove(marker)
        })
        marker.on('dragend', () => {
          if (selection?.isSelected(marker)) selection.dragEnd(marker)
          else handleDragEnd(meta, marker)
        })
        group.addLayer(marker)
      }

      built.set(event.name, group)
    }

    setGroups(built)

    return () => {
      for (const group of built.values()) {
        master.removeLayer(group)
        group.clearLayers()
      }
      setGroups(null)
    }
  }, [
    map,
    master,
    spawns,
    showOutOfBounds,
    mapSize,
    popupHost,
    selection,
    handlePopupOpen,
    handlePopupClose,
    handleDragEnd,
  ])

  // Make the whole layer box-selectable.
  useEffect(() => {
    if (!master || !selection) return
    return selection.addSource(master)
  }, [master, selection])

  // Sync what is actually on the map.
  useEffect(() => {
    if (!isMapAlive(map) || !master || !groups) return
    if (enabled && !map.hasLayer(master)) master.addTo(map)
    else if (!enabled && map.hasLayer(master)) master.remove()

    for (const [name, group] of groups) {
      const show = visibleEvents ? visibleEvents.has(name) : true
      if (show && !master.hasLayer(group)) master.addLayer(group)
      else if (!show && master.hasLayer(group)) master.removeLayer(group)
    }
  }, [map, master, groups, enabled, visibleEvents])

  // Labels and shared-spot badges: painted, not a tooltip per marker, so a
  // tick never rebuilds the markers.
  useSpawnOverlay(map, master, groups, { enabled, showLabels, labelClicks, visibleEvents })

  return { master, groups }
}
