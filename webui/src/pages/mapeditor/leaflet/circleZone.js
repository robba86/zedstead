// A draggable, resizable circle on the map: the circle, a centre grip and an
// edge handle, wired together.
//
// The three Expansion layers that draw a radius (safe-zone circles, safe-zone
// cylinders, trader zones) share this; the shapes and the maths are exactly the
// pattern `useSafeZoneLayer` established for TraderPlus. That hook is
// deliberately **not** refactored onto this helper: it is working, browser-
// verified code with no test around it, and the only thing a rewrite would buy
// is tidiness. If it is ever touched for another reason, fold it in then.
//
// The radius is stored in metres and drawn in CRS.Simple units, so it is
// divided by the map's calibration scale on the way in and multiplied on the
// way out. The handle sits due east at (lat, lng + displayRadius), which is why
// dragging it north-south resizes too: what is saved is the straight-line
// distance from the centre.

import L from 'leaflet'
import { isMapAlive } from './useLeafletMap'
import { bindTextTooltip } from './textTooltip'
import { html } from './html'

const distance = (a, b) => Math.sqrt((a.lat - b.lat) ** 2 + (a.lng - b.lng) ** 2)

const centreIcon = (color) =>
  L.divIcon({
    className: 'safezone-center-icon',
    html: html`<svg width="12" height="12" viewBox="0 0 12 12"><circle cx="6" cy="6" r="5" fill="${color}" stroke="#0f172a" stroke-width="1.5"/></svg>`,
    iconSize: [12, 12],
    iconAnchor: [6, 6],
  })

const handleIcon = (color) =>
  L.divIcon({
    className: 'safezone-resize-handle',
    html: html`<svg width="10" height="10" viewBox="0 0 10 10"><circle cx="5" cy="5" r="4" fill="#fff" stroke="${color}" stroke-width="2"/></svg>`,
    iconSize: [10, 10],
    iconAnchor: [5, 5],
  })

/**
 * Add one zone to *group*.
 *
 * @param group   an L.layerGroup the caller owns
 * @param zone    { lat, lng, radius, label, tooltip }
 * @param options { scale, color, editable, popupHost, onPopupOpen, onPopupClose,
 *                  popupMeta, onMove, onResize }
 *
 * `onMove(latlng)` / `onResize(metres)` are called on dragend only — a drag
 * fires continuously, and one request per frame would be a write storm.
 */
export function drawCircleZone(group, zone, options) {
  const {
    scale,
    color,
    editable = true,
    popupHost,
    onPopupOpen,
    onPopupClose,
    popupMeta,
    onMove,
    onResize,
  } = options
  const displayRadius = (zone.radius || 0) / scale

  const circle = L.circle([zone.lat, zone.lng], {
    radius: displayRadius,
    color,
    fillColor: color,
    fillOpacity: 0.1,
    weight: 2,
    dashArray: '6 4',
    interactive: true,
  })
  if (popupHost && popupMeta && onPopupOpen) {
    circle.bindPopup(() => popupHost, { maxWidth: 280, className: 'dark-popup' })
    circle.on('popupopen', (e) => onPopupOpen(popupMeta, e.popup))
    circle.on('popupclose', () => onPopupClose?.())
  }
  if (zone.tooltip) {
    bindTextTooltip(circle, zone.tooltip, {
      direction: 'top',
      offset: [0, -20],
      className: 'safezone-tooltip',
    })
  }
  group.addLayer(circle)

  if (!editable) return circle

  const centre = L.marker([zone.lat, zone.lng], { draggable: true, icon: centreIcon(color) })
  bindTextTooltip(centre, `${zone.label} (drag to move)`, {
    direction: 'top',
    offset: [0, -8],
    className: 'safezone-tooltip',
  })

  const handle = L.marker([zone.lat, zone.lng + displayRadius], {
    draggable: true,
    icon: handleIcon(color),
  })
  bindTextTooltip(handle, 'Drag to resize', {
    direction: 'right',
    offset: [6, 0],
    className: 'safezone-tooltip',
  })

  handle.on('drag', () => {
    circle.setRadius(Math.max(0.1, distance(handle.getLatLng(), centre.getLatLng())))
  })
  handle.on('dragend', () => {
    const metres = Math.max(1, Math.round(distance(handle.getLatLng(), centre.getLatLng()) * scale))
    onResize?.(metres)
  })

  // Dragging the centre carries the circle and the handle with it.
  centre.on('move', () => {
    const position = centre.getLatLng()
    circle.setLatLng(position)
    handle.setLatLng([position.lat, position.lng + circle.getRadius()])
  })
  centre.on('dragend', () => onMove?.(centre.getLatLng()))

  group.addLayer(centre)
  group.addLayer(handle)
  return circle
}

/** Mount/unmount a layer group as `enabled` changes. */
export function toggleGroup(map, group, enabled) {
  if (!isMapAlive(map) || !group) return
  if (enabled && !map.hasLayer(group)) group.addTo(map)
  else if (!enabled && map.hasLayer(group)) group.remove()
}
