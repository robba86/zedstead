// The map image: the admin's own upload, one picture stretched over the
// world's bounds. Not tiles: GET /map-image serves the single file.
//
// `enabled` is false while another ground is the base map (plans/69), so the
// image is requested only when it is shown.
// `version` is map-info's `image_own.version`: a new upload, a new request.

import { useEffect } from 'react'
import L from 'leaflet'
import { mapImageUrl } from '../api'
import { isMapAlive, toLatLngBounds } from './useLeafletMap'

export function useImageOverlay(map, terrain, bounds, enabled = true, version = null) {
  useEffect(() => {
    // Not `!map`: on a terrain change this body can run in the same commit that
    // already removed the previous map (see isMapAlive). Adding to a dead map
    // silently shows nothing; the effect re-runs with the new instance.
    if (!isMapAlive(map) || !bounds) return
    map.setMaxBounds(toLatLngBounds(bounds).pad(0.2))
    if (!enabled) return
    const overlay = L.imageOverlay(mapImageUrl(terrain, version), toLatLngBounds(bounds)).addTo(map)
    return () => {
      overlay.remove()
    }
  }, [map, terrain, bounds, enabled, version])
}
