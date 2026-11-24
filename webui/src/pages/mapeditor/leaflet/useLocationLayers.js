// Town/landmark labels, painted on one canvas the size of the map.
//
// They used to be a permanent Leaflet tooltip on an invisible marker per
// location: two DOM nodes each, every one with its own opacity and text-shadow
// and repositioned on every zoom, which made panning and zooming with
// Locations on visibly lag (217 labels on Chernarus). The labels take no
// clicks, so nothing is lost by painting them.
//
// The canvas, its redraw on a move and its fade around a zoom are
// `viewCanvas.js`, shared with the spawn labels.
//
// Zoomed out, every type at once was an unreadable heap, so labels are
// decluttered per zoom level: placed most important type first (`RANK`), and
// one whose box would overlap a label already placed is left out until the map
// is zoomed in far enough for it to fit. The placement is worked out once per
// zoom step, in world pixels, so a pan never changes which labels show.
//
// The canvas lives in a dedicated `locationLabels` pane above the map's canvas
// layers and below the markers. Visibility is React state; a change of data,
// types or theme builds the canvas again.

import { useEffect, useMemo } from 'react'
import L from 'leaflet'
import { isMapAlive } from './useLeafletMap'
import { useMapTheme } from './canvasTiles'
import { labelSize } from './useIconZoom'
import { ensurePane, mountViewCanvas } from './viewCanvas'

const LOCATION_PANE = 'locationLabels'
// What the tooltip CSS used to say: white at 60%, a 1px black shadow down and
// right, semibold, lines 1.3 apart, the last line's bottom 4px above the point.
const LINE_HEIGHT = 1.3
const OFFSET_Y = 4
const OPACITY = 0.6
// Space kept clear around a label, in px.
const GAP = 3
// Which labels win a crowded spot, first to last; a type not listed comes last.
const RANK = [
  'Capital', 'City', 'Airfield', 'Military', 'Village', 'RailroadStation',
  'Camp', 'Local', 'Marine', 'Hill', 'Ruin',
]
// Towns read larger than the places around them.
const SIZE_BY_TYPE = { Capital: 1.3, City: 1.15 }

const rankOf = (type) => {
  const i = RANK.indexOf(type)
  return i < 0 ? RANK.length : i
}

/** The pane the location canvas lives in, created once per map. */
export function useLocationPane(map) {
  useEffect(() => ensurePane(map, LOCATION_PANE, 401), [map])
}

/** Location types present in *locations*, sorted for a stable sidebar. */
export function locationTypes(locations) {
  return [...new Set((locations || []).map((l) => l.type || 'Other'))].sort()
}

const fontFor = (size, family) => `600 ${size}px ${family}`

/**
 * The labels that fit at *zoom*, each with its size and its half width.
 * *labels* comes sorted by rank, so a greedy pass keeps the most important
 * label of every crowded spot. Boxes are in world pixels at that zoom.
 */
function placeLabels(map, ctx, labels, zoom, family) {
  const base = labelSize(zoom)
  const placed = []
  const boxes = []
  for (const label of labels) {
    const size = Math.round(base * (SIZE_BY_TYPE[label.type] || 1))
    const lineHeight = size * LINE_HEIGHT
    ctx.font = fontFor(size, family)
    const half = Math.max(...label.lines.map((t) => ctx.measureText(t).width)) / 2
    const p = map.project(label.latlng, zoom)
    const box = {
      left: p.x - half - GAP,
      right: p.x + half + GAP,
      top: p.y - OFFSET_Y - lineHeight * label.lines.length - GAP,
      bottom: p.y - OFFSET_Y + GAP,
    }
    const hit = boxes.some(
      (b) => box.left < b.right && box.right > b.left && box.top < b.bottom && box.bottom > b.top
    )
    if (hit) continue
    boxes.push(box)
    placed.push({ ...label, size, lineHeight, half })
  }
  return placed
}

function drawLabels(map, ctx, placed, family, width, height) {
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.globalAlpha = OPACITY
  for (const { latlng, lines, size, lineHeight, half } of placed) {
    const { x, y: py } = map.latLngToContainerPoint(latlng)
    if (x + half < 0 || x - half > width) continue
    if (py < 0 || py - OFFSET_Y - lineHeight * lines.length > height) continue
    ctx.font = fontFor(size, family)
    // Bottom line first, then upwards.
    let y = py - OFFSET_Y - lineHeight / 2
    for (let i = lines.length - 1; i >= 0; i--) {
      ctx.fillStyle = '#000'
      ctx.fillText(lines[i], x + 1, y + 1)
      ctx.fillStyle = '#fff'
      ctx.fillText(lines[i], x, y)
      y -= lineHeight
    }
  }
}

export function useLocationLayers(map, locations, { enabled, visibleTypes }) {
  const theme = useMapTheme()
  useLocationPane(map)

  // Lines and rank order once per data set, not once per frame.
  const labels = useMemo(
    () =>
      (locations || [])
        .map((loc) => ({
          type: loc.type || 'Other',
          latlng: L.latLng(loc.lat, loc.lng),
          lines: (loc.label ? `${loc.name}\n${loc.label}` : loc.name).split('\n'),
        }))
        .sort((a, b) => rankOf(a.type) - rankOf(b.type)),
    [locations]
  )

  useEffect(() => {
    if (!isMapAlive(map) || !enabled) return
    const shown = visibleTypes ? labels.filter((l) => visibleTypes.has(l.type)) : labels
    if (!shown.length) return
    const family = getComputedStyle(map.getContainer()).fontFamily || 'sans-serif'
    // Placement per zoom step; a pinch passes through fractional zooms.
    const byZoom = new Map()
    const view = mountViewCanvas(map, LOCATION_PANE, 'me-location-labels', (ctx, width, height) => {
      const zoom = Math.round(map.getZoom() * 2) / 2
      if (!byZoom.has(zoom)) byZoom.set(zoom, placeLabels(map, ctx, shown, zoom, family))
      drawLabels(map, ctx, byZoom.get(zoom), family, width, height)
    })
    return view.remove
  }, [map, labels, enabled, visibleTypes, theme])
}
