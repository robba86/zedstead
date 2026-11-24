// Player spawn markers from cfgPlayerSpawnPoints.xml.
//
// Shape and colour encode the section: green triangle = fresh spawns, orange
// diamond = server hoppers, purple diamond = travellers. One layer group per
// section inside a master group, matching the spawn layer's structure, so box
// selection sees the same tree.
//
// There is no per-section visibility toggle — the vanilla editor had none
// either, and the sections are few and visually distinct.

import { useEffect, useState } from 'react'
import L from 'leaflet'
import { isMapAlive } from './useLeafletMap'
import { setMeta } from './markerRegistry'
import {
  freshSpawnMarkerSvg,
  hopSpawnMarkerSvg,
  travelSpawnMarkerSvg,
} from '../icons/iconRules'
import { useEventCallback } from '../useEventCallback'
import { meApi } from '../api'
import { bindTextTooltip } from './textTooltip'

const SECTION_ICONS = {
  fresh: freshSpawnMarkerSvg,
  hop: hopSpawnMarkerSvg,
  travel: travelSpawnMarkerSvg,
}

export const SECTION_COLORS = {
  fresh: '#4caf50',
  hop: '#ff9800',
  travel: '#7e57c2',
}

export const SECTION_LABELS = {
  fresh: 'Fresh Spawns',
  hop: 'Server Hoppers',
  travel: 'Travellers',
}

function createSectionIcon(section) {
  const svgFn = SECTION_ICONS[section] || freshSpawnMarkerSvg
  return L.divIcon({
    className: 'player-spawn-icon-wrapper',
    html: svgFn(),
    iconSize: [14, 14],
    iconAnchor: [7, 7],
  })
}

export function usePlayerSpawnLayer(
  map,
  playerSpawns,
  { enabled, popupHost, onPopupOpen, onPopupClose, onDragEnd, selection }
) {
  const [master, setMaster] = useState(null)
  const [groups, setGroups] = useState(null)

  const handlePopupOpen = useEventCallback(onPopupOpen)
  const handlePopupClose = useEventCallback(onPopupClose)
  const handleDragEnd = useEventCallback(onDragEnd)

  useEffect(() => {
    if (!isMapAlive(map)) return
    const group = L.layerGroup()
    setMaster(group)
    return () => {
      setMaster(null)
      group.remove()
    }
  }, [map])

  useEffect(() => {
    if (!isMapAlive(map) || !master) return

    selection?.reset()

    const built = new Map()
    for (const section of playerSpawns || []) {
      const icon = createSectionIcon(section.section)
      const sectionLayer = L.layerGroup()

      for (const group of section.groups) {
        for (const pos of group.positions) {
          const marker = L.marker([pos.lat, pos.lng], {
            draggable: true,
            icon,
            interactive: true,
          })

          const path = `/player-spawns/${encodeURIComponent(section.section)}/groups/${
            group.index
          }/positions/${pos.index}`
          const meta = {
            kind: 'playerSpawn',
            section: section.section,
            groupIndex: group.index,
            posIndex: pos.index,
            groupKey: `ps:${section.section}|${group.index}`,
            // The lat/lng endpoint, never the /game one — the client does no
            // projection maths (that is what mirrored every clicked player
            // spawn about the map centre in the old editor).
            save: (lat, lng) => meApi.put(path, { lat, lng }),
            del: () => meApi.del(path),
          }
          setMeta(marker, meta)

          bindTextTooltip(
            marker,
            `${section.section}: ${group.name}\n(${pos.game_x.toFixed(0)}, ${pos.game_z.toFixed(
              0
            )})`,
            { direction: 'top', offset: [0, -6], className: 'player-spawn-tooltip' }
          )

          if (popupHost) {
            marker.bindPopup(() => popupHost, { maxWidth: 220, className: 'dark-popup' })
            marker.on('popupopen', (e) => handlePopupOpen(meta, e.popup, group.name))
            marker.on('popupclose', () => handlePopupClose(meta))
          }

          marker.on('dragstart', () => {
            if (selection?.isSelected(marker)) selection.dragStart(marker)
          })
          marker.on('drag', () => {
            if (selection?.isSelected(marker)) selection.dragMove(marker)
          })
          marker.on('dragend', () => {
            if (selection?.isSelected(marker)) selection.dragEnd(marker)
            else handleDragEnd(meta, marker, pos)
          })

          sectionLayer.addLayer(marker)
        }
      }

      built.set(section.section, sectionLayer)
      master.addLayer(sectionLayer)
    }

    setGroups(built)

    return () => {
      for (const layer of built.values()) {
        master.removeLayer(layer)
        layer.clearLayers()
      }
      setGroups(null)
    }
  }, [
    map,
    master,
    playerSpawns,
    popupHost,
    selection,
    handlePopupOpen,
    handlePopupClose,
    handleDragEnd,
  ])

  useEffect(() => {
    if (!master || !selection) return
    return selection.addSource(master)
  }, [master, selection])

  useEffect(() => {
    if (!isMapAlive(map) || !master || !groups) return
    if (enabled && !map.hasLayer(master)) master.addTo(map)
    else if (!enabled && map.hasLayer(master)) master.remove()
  }, [map, master, groups, enabled])

  return { master, groups }
}
