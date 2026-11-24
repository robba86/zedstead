// Marker icons scale with zoom via CSS custom properties, not per-marker.
//
// Rewriting every divIcon on zoom would be prohibitive; instead the map
// container carries --icon-size and mapeditor.css sizes the
// SVGs from them. Formula is the vanilla editor's verbatim.

import { useEffect } from 'react'
import { isMapAlive } from './useLeafletMap'

/** Location label size in px at *zoom*: 11px at zoom <= 0, growing to 24px. */
export function labelSize(zoom) {
  return Math.min(24, Math.round(11 * Math.max(1, 1 + zoom * 0.1)))
}

/** Marker icon size in px at *zoom*: 14px at zoom <= 0, growing with zoom. */
export function iconSize(zoom) {
  return Math.round(14 * Math.max(1, 1 + zoom * 0.15))
}

/** Keep the map container's `--icon-size` in step with the zoom. */
export function useIconZoom(map, container) {
  useEffect(() => {
    if (!isMapAlive(map) || !container) return
    const el = container

    const update = () => {
      el.style.setProperty('--icon-size', `${iconSize(map.getZoom())}px`)
    }

    update()
    map.on('zoomend', update)
    return () => {
      map.off('zoomend', update)
    }
  }, [map, container])
}
