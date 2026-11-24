// TraderPlus safe zones: a dashed circle, a draggable centre, and a draggable
// handle on the east edge that resizes it.
//
// The radius is stored in metres and drawn in CRS.Simple units, so it is
// divided by the map's calibration scale on the way in and multiplied on the
// way out. The handle sits at (lat, lng + displayRadius) — due east — which is
// why dragging it north-south also changes the radius: what is saved is the
// straight-line distance from the centre, not the longitude delta.
//
// These markers are deliberately NOT registered for box selection. A safe zone
// is three linked layers; group-moving the centre without its handle would
// leave the circle and the resize grip in different places.

import { useEffect, useState } from 'react'
import L from 'leaflet'
import { isMapAlive } from './useLeafletMap'
import { useEventCallback } from '../useEventCallback'
import { meApi } from '../api'
import { bindTextTooltip } from './textTooltip'

const CENTRE_ICON = L.divIcon({
  className: 'safezone-center-icon',
  html: '<svg width="12" height="12" viewBox="0 0 12 12"><circle cx="6" cy="6" r="5" fill="#22c55e" stroke="#166534" stroke-width="1.5"/></svg>',
  iconSize: [12, 12],
  iconAnchor: [6, 6],
})

const HANDLE_ICON = L.divIcon({
  className: 'safezone-resize-handle',
  html: '<svg width="10" height="10" viewBox="0 0 10 10"><circle cx="5" cy="5" r="4" fill="#fff" stroke="#22c55e" stroke-width="2"/></svg>',
  iconSize: [10, 10],
  iconAnchor: [5, 5],
})

const distance = (a, b) => Math.sqrt((a.lat - b.lat) ** 2 + (a.lng - b.lng) ** 2)

export function useSafeZoneLayer(
  map,
  safezones,
  { enabled, scale, popupHost, onPopupOpen, onPopupClose, onCentreDragEnd, onResizeError }
) {
  const [master, setMaster] = useState(null)
  const [ready, setReady] = useState(0)

  const handlePopupOpen = useEventCallback(onPopupOpen)
  const handlePopupClose = useEventCallback(onPopupClose)
  const handleCentreDragEnd = useEventCallback(onCentreDragEnd)
  const handleResizeError = useEventCallback(onResizeError)

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
    if (!isMapAlive(map) || !master || !scale) return
    master.clearLayers()

    for (const zone of safezones?.safezones || []) {
      const displayRadius = zone.radius / scale

      const circle = L.circle([zone.lat, zone.lng], {
        radius: displayRadius,
        color: '#22c55e',
        fillColor: '#22c55e',
        fillOpacity: 0.12,
        weight: 2,
        dashArray: '6 4',
        interactive: true,
      })
      if (popupHost) {
        circle.bindPopup(() => popupHost, { maxWidth: 280, className: 'dark-popup' })
        circle.on('popupopen', (e) => handlePopupOpen({ kind: 'safezone', index: zone.index }, e.popup))
        circle.on('popupclose', () => handlePopupClose())
      }
      bindTextTooltip(circle, zone.name, {
        direction: 'top',
        offset: [0, -20],
        className: 'safezone-tooltip',
      })

      const centre = L.marker([zone.lat, zone.lng], { draggable: true, icon: CENTRE_ICON })
      bindTextTooltip(centre, `${zone.name} (drag to move)`, {
        direction: 'top',
        offset: [0, -8],
        className: 'safezone-tooltip',
      })

      const handle = L.marker([zone.lat, zone.lng + displayRadius], {
        draggable: true,
        icon: HANDLE_ICON,
      })
      bindTextTooltip(handle, 'Drag to resize', {
        direction: 'right',
        offset: [6, 0],
        className: 'safezone-tooltip',
      })

      handle.on('drag', () => {
        circle.setRadius(Math.max(0.1, distance(handle.getLatLng(), centre.getLatLng())))
      })

      handle.on('dragend', async () => {
        const radius = Math.max(1, Math.round(distance(handle.getLatLng(), centre.getLatLng()) * scale))
        try {
          await meApi.put(`/safezones/${zone.index}`, { radius })
        } catch (err) {
          handleResizeError(err)
        }
      })

      // Dragging the centre carries the circle and the handle with it.
      centre.on('move', () => {
        const position = centre.getLatLng()
        circle.setLatLng(position)
        handle.setLatLng([position.lat, position.lng + circle.getRadius()])
      })
      centre.on('dragend', () => handleCentreDragEnd(zone, centre))

      master.addLayer(circle)
      master.addLayer(centre)
      master.addLayer(handle)
    }

    setReady((n) => n + 1)
    return () => master.clearLayers()
  }, [
    map,
    master,
    safezones,
    scale,
    popupHost,
    handlePopupOpen,
    handlePopupClose,
    handleCentreDragEnd,
    handleResizeError,
  ])

  useEffect(() => {
    if (!isMapAlive(map) || !master) return
    if (enabled && !map.hasLayer(master)) master.addTo(map)
    else if (!enabled && map.hasLayer(master)) master.remove()
  }, [map, master, enabled, ready])
}
