// Radius circles around event spawn points.
//
// Two per position: the inner solid circle is `saferadius` (nothing else
// spawns inside it) and the dashed outer one is `distanceradius` (how far the
// event's items scatter). Radii are metres, so they are divided by the map's
// calibration scale to land in CRS.Simple units.
//
// Only drawn at zoom >= 3, as in the vanilla editor. Zoomed out, a mission's
// worth of circles is both illegible and thousands of SVG paths.

import { useEffect, useState } from 'react'
import L from 'leaflet'
import { isMapAlive } from './useLeafletMap'
import { getEventColor } from '../icons/iconRules'

const MIN_ZOOM = 3

export function useZoneCircles(map, spawns, { enabled, visibleEvents, scale }) {
  const [group, setGroup] = useState(null)

  useEffect(() => {
    if (!isMapAlive(map)) return
    const layer = L.layerGroup()
    setGroup(layer)
    return () => {
      setGroup(null)
      layer.remove()
    }
  }, [map])

  // Rebuild whenever the data, the scale or which events are shown changes.
  // Circles follow the event checkboxes: a hidden event's circles would be
  // orphaned rings with no markers under them.
  useEffect(() => {
    if (!isMapAlive(map) || !group) return
    // Unconditionally, before the enabled check: switching the layer off and
    // on again must not stack a second set of circles on top of the first.
    group.clearLayers()
    if (!enabled || !scale) return

    for (const event of spawns?.events || []) {
      if (!event.saferadius && !event.distanceradius) continue
      if (visibleEvents && !visibleEvents.has(event.name)) continue

      const color = getEventColor(event.name)
      for (const pos of event.positions) {
        if (event.saferadius > 0) {
          L.circle([pos.lat, pos.lng], {
            radius: event.saferadius / scale,
            color,
            weight: 1,
            opacity: 0.4,
            fillColor: color,
            fillOpacity: 0.08,
            interactive: false,
          }).addTo(group)
        }
        if (event.distanceradius > 0) {
          L.circle([pos.lat, pos.lng], {
            radius: event.distanceradius / scale,
            color,
            weight: 1,
            opacity: 0.25,
            fillColor: color,
            fillOpacity: 0.03,
            dashArray: '4 4',
            interactive: false,
          }).addTo(group)
        }
      }
    }

    return () => group.clearLayers()
  }, [map, group, spawns, enabled, visibleEvents, scale])

  // Zoom gate. Re-evaluated on zoomend and whenever the toggle changes.
  useEffect(() => {
    if (!isMapAlive(map) || !group) return

    const update = () => {
      const show = enabled && map.getZoom() >= MIN_ZOOM
      if (show && !map.hasLayer(group)) group.addTo(map)
      else if (!show && map.hasLayer(group)) group.remove()
    }

    update()
    map.on('zoomend', update)
    return () => {
      map.off('zoomend', update)
    }
  }, [map, group, enabled])
}
