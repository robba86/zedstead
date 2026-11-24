// TraderPlus trader NPCs (gold) and trade objects (brown).
//
// Both live in one master group, which the "Traders" layer toggle switches
// along with the safe-zone and garage layers — one checkbox for all of
// TraderPlus, as in the vanilla editor.
//
// Note, not fixed here: these entities are addressed by array index in
// TraderPlusGeneralConfig.json, with no stable id. Two people editing at once,
// or a watcher reload landing between render and save, can retarget a write.
// That is pre-existing; the ping-then-refetch after every mutation resyncs the
// indices, which is as good as it gets without a schema change.

import { useEffect, useState } from 'react'
import L from 'leaflet'
import { isMapAlive } from './useLeafletMap'
import { setMeta } from './markerRegistry'
import { traderNpcMarkerSvg, traderObjectMarkerSvg } from '../icons/iconRules'
import { useEventCallback } from '../useEventCallback'
import { meApi } from '../api'
import { bindTextTooltip } from './textTooltip'


function npcIcon(name) {
  return L.divIcon({
    className: 'trader-icon-wrapper',
    html: traderNpcMarkerSvg(name),
    iconSize: [16, 16],
    iconAnchor: [8, 8],
  })
}

function objectIcon(name) {
  return L.divIcon({
    className: 'trader-object-icon-wrapper',
    html: traderObjectMarkerSvg(name),
    iconSize: [14, 14],
    iconAnchor: [7, 7],
  })
}

export function useTraderLayer(
  map,
  traders,
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

    const wire = (marker, meta) => {
      setMeta(marker, meta)
      if (popupHost) {
        marker.bindPopup(() => popupHost, { maxWidth: 280, className: 'dark-popup' })
        marker.on('popupopen', (e) => handlePopupOpen(meta, e.popup))
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
        else handleDragEnd(meta, marker)
      })
      master.addLayer(marker)
    }

    for (const trader of traders?.traders || []) {
      const marker = L.marker([trader.lat, trader.lng], {
        draggable: true,
        icon: npcIcon(`${trader.given_name} ${trader.role}`),
      })
      bindTextTooltip(marker, `${trader.given_name} (${trader.role})`, {
        direction: 'top',
        offset: [0, -8],
        className: 'trader-tooltip',
      })
      wire(marker, {
        kind: 'trader-npc',
        index: trader.index,
        posIndex: trader.index,
        groupKey: 'trader-npc',
        label: `${trader.given_name} (${trader.role})`,
        // game_y is preserved: dragging on a 2D map must not flatten a trader
        // that was deliberately placed on a rooftop or a ship deck.
        save: (lat, lng) =>
          meApi.put(`/traders/${trader.index}`, { lat, lng, game_y: trader.game_y }),
        del: () => meApi.del(`/traders/${trader.index}`),
      })
    }

    for (const obj of traders?.trader_objects || []) {
      const marker = L.marker([obj.lat, obj.lng], {
        draggable: true,
        icon: objectIcon(obj.object_name),
      })
      bindTextTooltip(marker, obj.object_name, {
        direction: 'top',
        offset: [0, -6],
        className: 'trader-tooltip',
      })
      wire(marker, {
        kind: 'trader-object',
        index: obj.index,
        posIndex: obj.index,
        groupKey: 'trader-object',
        label: obj.object_name,
        save: (lat, lng) =>
          meApi.put(`/trader-objects/${obj.index}`, { lat, lng, game_y: obj.game_y }),
        del: () => meApi.del(`/trader-objects/${obj.index}`),
      })
    }

    setReady((n) => n + 1)

    return () => {
      master.clearLayers()
    }
  }, [
    map,
    master,
    traders,
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
