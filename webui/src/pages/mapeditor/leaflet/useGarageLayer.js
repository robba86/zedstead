// TraderPlus garages: an NPC marker, a parking marker, and a dashed line
// joining them.
//
// The two positions are independent fields on the same record, and the line
// exists because they are meaningless apart — a parking spot 200 m from its
// NPC is a configuration mistake you can only see when they are drawn
// together.
//
// Both markers are box-selectable and carry `save`, so a group move updates
// whichever half was selected. Only the NPC marker carries `del`: the parking
// spot is not independently deletable, and giving both a `del` would make a
// bulk delete of a selected pair try to remove the same garage twice.

import { useEffect, useState } from 'react'
import L from 'leaflet'
import { isMapAlive } from './useLeafletMap'
import { setMeta } from './markerRegistry'
import { garageNpcMarkerSvg } from '../icons/iconRules'
import { useEventCallback } from '../useEventCallback'
import { meApi } from '../api'
import { bindTextTooltip } from './textTooltip'

const NPC_ICON = L.divIcon({
  className: 'garage-icon-wrapper',
  html: garageNpcMarkerSvg(),
  iconSize: [16, 16],
  iconAnchor: [8, 8],
})

const PARKING_ICON = L.divIcon({
  className: 'parking-icon-wrapper',
  html: '<svg width="14" height="14" viewBox="0 0 14 14"><rect x="1" y="1" width="12" height="12" rx="2" fill="#3b82f6" stroke="#1e3a5f" stroke-width="1"/><text x="7" y="10.5" text-anchor="middle" fill="white" font-size="9" font-weight="bold">P</text></svg>',
  iconSize: [14, 14],
  iconAnchor: [7, 7],
})

export function useGarageLayer(
  map,
  garage,
  { enabled, popupHost, onPopupOpen, onPopupClose, onDragEnd, selection }
) {
  const [master, setMaster] = useState(null)
  const [ready, setReady] = useState(0)

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
    master.clearLayers()

    for (const npc of garage?.garage_npcs || []) {
      const npcMarker = L.marker([npc.lat, npc.lng], { draggable: true, icon: NPC_ICON })
      bindTextTooltip(npcMarker, `Garage: ${npc.class_name}`, {
        direction: 'top',
        offset: [0, -8],
        className: 'garage-tooltip',
      })

      const parkMarker = L.marker([npc.park_lat, npc.park_lng], {
        draggable: true,
        icon: PARKING_ICON,
      })
      bindTextTooltip(parkMarker, 'Parking spot — keep 5–15 m clear of obstacles', {
        direction: 'top',
        offset: [0, -6],
        className: 'garage-tooltip',
      })

      const line = L.polyline(
        [
          [npc.lat, npc.lng],
          [npc.park_lat, npc.park_lng],
        ],
        { color: '#f59e0b', weight: 1.5, dashArray: '4 4', opacity: 0.6 }
      )
      const syncLine = () => line.setLatLngs([npcMarker.getLatLng(), parkMarker.getLatLng()])
      npcMarker.on('move', syncLine)
      parkMarker.on('move', syncLine)

      const npcMeta = {
        kind: 'garage-npc',
        index: npc.index,
        posIndex: npc.index,
        groupKey: 'garage-npc',
        label: npc.class_name,
        save: (lat, lng) => meApi.put(`/garage/${npc.index}`, { lat, lng, game_y: npc.game_y }),
        del: () => meApi.del(`/garage/${npc.index}`),
      }
      const parkMeta = {
        kind: 'garage-parking',
        index: npc.index,
        posIndex: npc.index,
        groupKey: 'garage-parking',
        label: `${npc.class_name} parking`,
        save: (lat, lng) =>
          meApi.put(`/garage/${npc.index}`, {
            park_lat: lat,
            park_lng: lng,
            park_game_y: npc.park_game_y,
          }),
      }
      setMeta(npcMarker, npcMeta)
      setMeta(parkMarker, parkMeta)

      if (popupHost) {
        npcMarker.bindPopup(() => popupHost, { maxWidth: 300, className: 'dark-popup' })
        npcMarker.on('popupopen', (e) => handlePopupOpen(npcMeta, e.popup))
        npcMarker.on('popupclose', () => handlePopupClose(npcMeta))
      }

      for (const [marker, meta] of [
        [npcMarker, npcMeta],
        [parkMarker, parkMeta],
      ]) {
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
      }

      master.addLayer(line)
      master.addLayer(npcMarker)
      master.addLayer(parkMarker)
    }

    setReady((n) => n + 1)
    return () => master.clearLayers()
  }, [
    map,
    master,
    garage,
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
    if (!isMapAlive(map) || !master) return
    if (enabled && !map.hasLayer(master)) master.addTo(map)
    else if (!enabled && map.hasLayer(master)) master.remove()
  }, [map, master, enabled, ready])
}
