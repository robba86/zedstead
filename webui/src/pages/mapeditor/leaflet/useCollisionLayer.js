// Collision overlay: a pulsing ring on each side of every reported collision,
// plus a dashed connector when the two positions differ.
//
// Ports collisions.js's rendering half. Two things it does are load-bearing and
// survived verbatim:
//
//   1. **Batched insertion.** A whole-mission "all" scan can report thousands
//      of collisions (two circles + a polyline each), and building them in one
//      synchronous pass locks the tab for seconds. 200 per animation frame, as
//      before.
//   2. **Radius follows zoom.** Circle markers are sized in screen pixels, so
//      without the zoomend handler they stay pinprick-sized when zoomed in.

import { useEffect, useRef, useState } from 'react'
import L from 'leaflet'
import { isMapAlive } from './useLeafletMap'
import { setMeta } from './markerRegistry'
import { useEventCallback } from '../useEventCallback'

const SEVERITY_COLORS = {
  error: '#ff3333',
  warning: '#ffaa00',
  info: '#6699cc',
}

const RENDER_BATCH_SIZE = 200

function collisionRadius(map) {
  const zoom = isMapAlive(map) ? map.getZoom() : 0
  return Math.round(16 * Math.max(1, 1 + zoom * 0.15))
}

/**
 * @param map         Leaflet map (may be null or already removed)
 * @param collisions  GET /collisions/* payload, or null
 * @param options     { enabled, popupHost, onPopupOpen, onPopupClose }
 */
export function useCollisionLayer(map, collisions, { enabled, popupHost, onPopupOpen, onPopupClose }) {
  const [master, setMaster] = useState(null)
  const frameRef = useRef(null)

  const handlePopupOpen = useEventCallback(onPopupOpen)
  const handlePopupClose = useEventCallback(onPopupClose)

  // Master group, created once per map so the toggle has something stable to
  // add and remove.
  useEffect(() => {
    if (!isMapAlive(map)) return
    const group = L.layerGroup()
    setMaster(group)
    return () => {
      setMaster(null)
      group.remove()
    }
  }, [map])

  // Rebuild, in batches.
  useEffect(() => {
    if (!isMapAlive(map) || !master) return

    // Before the enabled check, so toggling the panel off and on again cannot
    // stack a second set of rings on the first.
    master.clearLayers()
    if (!enabled) return

    const items = collisions?.collisions || []
    if (!items.length) return

    const radius = collisionRadius(map)

    const ring = (pos, color, meta) => {
      const marker = L.circleMarker([pos.lat, pos.lng], {
        radius,
        color,
        weight: 3,
        fillColor: color,
        fillOpacity: 0.15,
        className: 'collision-pulse',
      })
      setMeta(marker, meta)
      if (popupHost) {
        marker.bindPopup(() => popupHost, { maxWidth: 280, className: 'dark-popup' })
        marker.on('popupopen', (e) => handlePopupOpen(meta, e.popup))
        marker.on('popupclose', () => handlePopupClose(meta))
      }
      master.addLayer(marker)
    }

    let index = 0
    const batch = () => {
      frameRef.current = null
      const end = Math.min(index + RENDER_BATCH_SIZE, items.length)
      for (; index < end; index++) {
        const c = items[index]
        const color = SEVERITY_COLORS[c.severity] || SEVERITY_COLORS.error
        ring(c.pos_a, color, { kind: 'collision', collision: c, side: 'A' })

        if (c.pos_a.lat !== c.pos_b.lat || c.pos_a.lng !== c.pos_b.lng) {
          ring(c.pos_b, color, { kind: 'collision', collision: c, side: 'B' })
          master.addLayer(
            L.polyline(
              [
                [c.pos_a.lat, c.pos_a.lng],
                [c.pos_b.lat, c.pos_b.lng],
              ],
              { color, weight: 2, dashArray: '6 4', opacity: 0.7, interactive: false }
            )
          )
        }
      }
      if (index < items.length) frameRef.current = requestAnimationFrame(batch)
    }
    batch()

    return () => {
      if (frameRef.current) cancelAnimationFrame(frameRef.current)
      frameRef.current = null
      master.clearLayers()
    }
  }, [map, master, collisions, enabled, popupHost, handlePopupOpen, handlePopupClose])

  // Circle markers are sized in pixels; rescale them with the icons.
  useEffect(() => {
    if (!isMapAlive(map) || !master) return
    const onZoom = () => {
      const radius = collisionRadius(map)
      master.eachLayer((layer) => {
        if (layer.setRadius) layer.setRadius(radius)
      })
    }
    map.on('zoomend', onZoom)
    return () => {
      map.off('zoomend', onZoom)
    }
  }, [map, master])

  // Sync what is on the map.
  useEffect(() => {
    if (!isMapAlive(map) || !master) return
    if (enabled && !map.hasLayer(master)) master.addTo(map)
    else if (!enabled && map.hasLayer(master)) master.remove()
  }, [map, master, enabled])

  return master
}
