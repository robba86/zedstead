// A rotated box footprint with a draggable centre — the rectangle counterpart
// to `circleZone.js`, for map data whose extent is a size in X/Y/Z rather than
// a radius. Underground triggers are the only such layer today.
//
// The box is drawn as a polygon rather than an `L.rectangle` because DayZ gives
// each trigger an `Orientation` (yaw, pitch, roll) and a rectangle cannot turn.
// Only yaw is drawn: pitch and roll tilt a box out of the ground plane, which a
// top-down map has nowhere to put. They are in the popup instead, and are never
// written by anything here.
//
// **Yaw turns clockwise seen from above** — a compass heading, 0 = north and
// 90 = east, the same convention as every other yaw in the editor. Bohemia
// states no sign, so it was measured against Sakhal's stock file (2026 Q3):
// the warhead-storage hall has yaw 17, and its own entrance and fill triggers
// line up with its long axis at headings 16.0-19.8° clockwise, i.e. within
// 0.5-2.8° of the box read that way and ~35° off it read counter-clockwise.
// This was drawn counter-clockwise until then.

import L from 'leaflet'
import { bindTextTooltip } from './textTooltip'
import { html } from './html'

/** Display-unit corners of a box centred at (lat, lng). */
export function boxCorners({ lat, lng, sizeX, sizeZ, yaw = 0 }, scale) {
  const hx = sizeX / 2 / scale
  const hz = sizeZ / 2 / scale
  const radians = ((yaw || 0) * Math.PI) / 180
  const cos = Math.cos(radians)
  const sin = Math.sin(radians)
  return [
    [-hx, -hz],
    [hx, -hz],
    [hx, hz],
    [-hx, hz],
  ].map(([x, z]) => [lat + (z * cos - x * sin), lng + (x * cos + z * sin)])
}

const centreIcon = (color) =>
  L.divIcon({
    className: 'underground-center-icon',
    html: html`<svg width="12" height="12" viewBox="0 0 12 12"><rect x="1.5" y="1.5" width="9" height="9" fill="${color}" stroke="#0f172a" stroke-width="1.5"/></svg>`,
    iconSize: [12, 12],
    iconAnchor: [6, 6],
  })

/**
 * Add one box to *group*.
 *
 * @param group   an L.layerGroup the caller owns
 * @param box     { lat, lng, sizeX, sizeZ, yaw, label, tooltip }
 * @param options { scale, color, editable, popupHost, onPopupOpen, onPopupClose,
 *                  popupMeta, onMove }
 *
 * `onMove(latlng)` fires on dragend only, like `drawCircleZone` — a drag fires
 * continuously and one request per frame would be a write storm.
 */
export function drawBoxZone(group, box, options) {
  const {
    scale,
    color,
    editable = true,
    popupHost,
    onPopupOpen,
    onPopupClose,
    popupMeta,
    onMove,
  } = options

  const polygon = L.polygon(boxCorners(box, scale), {
    color,
    fillColor: color,
    fillOpacity: 0.12,
    weight: 2,
    interactive: true,
  })
  if (popupHost && popupMeta && onPopupOpen) {
    polygon.bindPopup(() => popupHost, { maxWidth: 300, className: 'dark-popup' })
    polygon.on('popupopen', (e) => onPopupOpen(popupMeta, e.popup))
    polygon.on('popupclose', () => onPopupClose?.())
  }
  if (box.tooltip) {
    bindTextTooltip(polygon, box.tooltip, {
      direction: 'top',
      offset: [0, -10],
      className: 'safezone-tooltip',
    })
  }
  group.addLayer(polygon)

  if (!editable) return polygon

  const centre = L.marker([box.lat, box.lng], { draggable: true, icon: centreIcon(color) })
  bindTextTooltip(centre, `${box.label} (drag to move)`, {
    direction: 'top',
    offset: [0, -8],
    className: 'safezone-tooltip',
  })
  centre.on('move', () => {
    const position = centre.getLatLng()
    polygon.setLatLngs(boxCorners({ ...box, lat: position.lat, lng: position.lng }, scale))
  })
  centre.on('dragend', () => onMove?.(centre.getLatLng()))
  group.addLayer(centre)

  return polygon
}
