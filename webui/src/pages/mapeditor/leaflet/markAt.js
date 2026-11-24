// The one "this is the marker you picked" cue on the map: an accent glow on the
// marker's icon (`.me-marked` in mapeditor.css). Every sidebar fly-to marks the
// markers it lands on, whatever layer they belong to, so a busy spot shows which
// icon the row meant. Box selection (`.spawn-selected`) uses the same glow.

import L from 'leaflet'
import { isMapAlive } from './useLeafletMap'

export const MARKED_CLASS = 'me-marked'
const MARK_MS = 2500
// A marker whose anchor sits within this many screen pixels of the target counts.
const HIT_PX = 3

// Glow every marker drawn at lat/lng for MARK_MS. Returns a cleanup that drops
// the glow at once (a new fly-to or an unmount).
export function markAt(map, lat, lng) {
  if (!isMapAlive(map)) return () => {}
  const target = map.latLngToContainerPoint([lat, lng])
  const icons = []
  map.eachLayer((layer) => {
    if (!(layer instanceof L.Marker) || !layer._icon) return
    if (map.latLngToContainerPoint(layer.getLatLng()).distanceTo(target) > HIT_PX) return
    layer._icon.classList.add(MARKED_CLASS)
    icons.push(layer._icon)
  })
  const clear = () => icons.forEach((icon) => icon.classList.remove(MARKED_CLASS))
  const timer = setTimeout(clear, MARK_MS)
  return () => {
    clearTimeout(timer)
    clear()
  }
}
