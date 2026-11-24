// Painted over the event-spawn markers on one canvas: the name labels, and a
// count badge on every pin that shares its exact spot with other pins.
//
// The labels used to be a permanent Leaflet tooltip on every spawn marker. A
// big mission has thousands of positions, so ticking Spawn labels added
// thousands of DOM nodes, each repositioned on every zoom, and the tick also
// rebuilt every marker. The browser hung. The labels take no clicks, so
// nothing is lost by painting them (the same move `useLocationLayers.js` made).
//
// Pins on the same spot draw as one pin. The top one carries a badge with how
// many pins stand there, and the spot's label is a column of boxes, one per
// pin, 1px apart (a name twice on the spot adds its position number, "#3").
// Only events shown on the map count.
//
// A click on a label box opens that pin's popup, so a pin under another one
// is reachable without hiding the top one, and a drag on a box moves its pin
// as a drag on the pin would (the same Leaflet drag events, so the selection
// and the save question work unchanged). The canvas takes no pointer events,
// so capture listeners on the map container test the boxes drawn last and
// stop the press before Leaflet starts a map pan. In add mode the labels take
// no clicks, so a click there still adds a point.
//
// The pin last clicked, opened or dragged is raised over the pins it shares
// a spot with (`zIndexOffset`), until another pin is. The Collisions panel reports the same spots as DUPLICATE or
// CROSS-EVENT OVERLAP, but only after a scan.
//
// A label sits under the square that marks the exact spot, clear of the pin.
// Where that place is taken (by another label or by a pin), it tries right of
// the square, then left, then above the disc. A label with no free place is
// left out until the map is zoomed in far enough, and at the maximum zoom
// every label shows. Placement is worked out once per zoom step in world
// pixels, through a grid of cells, so it stays linear in the number of
// markers. Positions are read from the markers on every frame, so a label and
// a badge follow a drag, and the drop groups the spots again.

import { useEffect, useRef } from 'react'
import L from 'leaflet'
import { isMapAlive } from './useLeafletMap'
import { getMeta } from './markerRegistry'
import { useMapTheme } from './canvasTiles'
import { iconSize } from './useIconZoom'
import { ensurePane, mountViewCanvas } from './viewCanvas'

// Above the markers (600), below popups (700), as the tooltips were.
const SPAWN_OVERLAY_PANE = 'spawnOverlay'
const PANE_Z = 650
// What the spawn tooltip CSS said: 10px text, 4px side padding, a 1px
// border, 3px corners.
const FONT_SIZE = 10
const PAD_X = 4
const PAD_Y = 2
const RADIUS = 3
const BOX_H = FONT_SIZE + 2 * PAD_Y + 2
// Space between the boxes of one spot, in px.
const BOX_GAP = 1
// A press that moves further than this, in px, is a drag, not a click.
const CLICK_SLOP = 4
// Lifts the raised pin over every other pin on its spot.
const RAISE_Z = 1000
// The pin, as `.spawn-icon-wrapper` in mapeditor.css draws it: a disc of
// --icon-size plus twice --icon-padding, an 8px stem, and an 8px square
// centred on the spot. Change both or neither.
const STEM = 8
const POINT = 8
// Space between a label and its pin, and kept clear around a label, in px.
const OFFSET = 3
const GAP = 2
const CELL = 64
// The places a label tries, in order.
const SIDES = ['below', 'right', 'left', 'above']
// The count badge: its diameter and its bold text, in px.
const BADGE = 15
const BADGE_FONT = 9

/** The pin's size at *zoom*: its disc width and its top above the spot. */
function pinAt(zoom, padding) {
  const disc = iconSize(zoom) + 2 * padding
  return { disc, top: disc + STEM + POINT / 2 }
}

/** The box of a label *w* by *h* on *side* of the spot at pixel *p*. */
function labelBox(p, w, h, side, pin) {
  if (side === 'right' || side === 'left') {
    const left = side === 'right' ? p.x + POINT / 2 + OFFSET : p.x - POINT / 2 - OFFSET - w
    return { left, right: left + w, top: p.y - h / 2, bottom: p.y + h / 2 }
  }
  const top = side === 'below' ? p.y + POINT / 2 + OFFSET : p.y - pin.top - OFFSET - h
  return { left: p.x - w / 2, right: p.x + w / 2, top, bottom: top + h }
}

/** The areas the pin covers (the disc, then the stem and square), so no label hides one. */
function pinBoxes(p, pin) {
  const neck = p.y - POINT / 2 - STEM
  return [
    { left: p.x - pin.disc / 2, right: p.x + pin.disc / 2, top: p.y - pin.top, bottom: neck },
    { left: p.x - POINT / 2, right: p.x + POINT / 2, top: neck, bottom: p.y + POINT / 2 },
  ]
}

/** Boxes in a grid of CELL-sized cells, so a test looks only nearby. */
function boxGrid() {
  const cells = new Map()
  const span = (box, fn) => {
    const x1 = Math.floor((box.right + GAP) / CELL)
    const y1 = Math.floor((box.bottom + GAP) / CELL)
    for (let cx = Math.floor((box.left - GAP) / CELL); cx <= x1; cx++) {
      for (let cy = Math.floor((box.top - GAP) / CELL); cy <= y1; cy++) {
        if (fn(`${cx},${cy}`)) return true
      }
    }
    return false
  }
  return {
    add(box) {
      span(box, (key) => {
        const list = cells.get(key)
        if (list) list.push(box)
        else cells.set(key, [box])
      })
    },
    hits(box) {
      return span(box, (key) =>
        (cells.get(key) || []).some(
          (b) =>
            box.left - GAP < b.right &&
            box.right + GAP > b.left &&
            box.top - GAP < b.bottom &&
            box.bottom + GAP > b.top
        )
      )
    },
  }
}

/**
 * The shown pins grouped by exact spot: one entry per spot, with the marker
 * on top (Leaflet draws the last one added over the others), the pin count
 * and one label row per pin, each with the marker it opens.
 */
function groupSpots(items) {
  const bySpot = new Map()
  for (const item of items) {
    const { lat, lng } = item.marker.getLatLng()
    const key = `${lat},${lng}`
    const spot = bySpot.get(key)
    if (spot) spot.push(item)
    else bySpot.set(key, [item])
  }
  return [...bySpot.values()].map((stack) => {
    const counts = new Map()
    for (const { name } of stack) counts.set(name, (counts.get(name) || 0) + 1)
    return {
      marker: stack[stack.length - 1].marker,
      count: stack.length,
      rows: stack.map(({ marker, name }) => ({
        marker,
        text: counts.get(name) > 1 ? `${name} #${getMeta(marker)?.posIndex}` : name,
      })),
    }
  })
}

/**
 * The spots whose label shows at *zoom*, each with the side it took: the
 * first one in each crowded place wins. At *all* (the maximum zoom) every
 * label shows.
 */
function placeLabels(map, spots, zoom, sizeOf, padding, all) {
  const pin = pinAt(zoom, padding)
  const points = spots.map((spot) => map.project(spot.marker.getLatLng(), zoom))
  const grid = boxGrid()
  for (const p of points) for (const box of pinBoxes(p, pin)) grid.add(box)
  const placed = []
  spots.forEach((spot, i) => {
    const { w, h } = sizeOf(spot.rows)
    for (const side of SIDES) {
      const box = labelBox(points[i], w, h, side, pin)
      if (grid.hits(box)) continue
      grid.add(box)
      placed.push({ ...spot, side })
      return
    }
    if (all) placed.push({ ...spot, side: SIDES[0] })
  })
  return placed
}

/**
 * Draw the placed labels, one box per row, and return the boxes drawn (in
 * container pixels, each with its marker) for the click test.
 */
function drawLabels(map, ctx, placed, sizeOf, look, width, height) {
  const pin = pinAt(map.getZoom(), look.padding)
  const hits = []
  ctx.font = look.font
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.lineWidth = 1
  for (const { marker, rows, side } of placed) {
    const { w, h } = sizeOf(rows)
    const box = labelBox(map.latLngToContainerPoint(marker.getLatLng()), w, h, side, pin)
    if (box.right < 0 || box.left > width || box.bottom < 0 || box.top > height) continue
    const x = Math.round(box.left) + 0.5
    rows.forEach((row, i) => {
      const y = Math.round(box.top + i * (BOX_H + BOX_GAP)) + 0.5
      ctx.beginPath()
      if (ctx.roundRect) ctx.roundRect(x, y, w, BOX_H, RADIUS)
      else ctx.rect(x, y, w, BOX_H)
      ctx.fillStyle = look.bg
      ctx.fill()
      ctx.strokeStyle = look.border
      ctx.stroke()
      ctx.fillStyle = look.text
      ctx.fillText(row.text, x + w / 2, y + BOX_H / 2)
      hits.push({ left: x, right: x + w, top: y, bottom: y + BOX_H, marker: row.marker })
    })
  }
  return hits
}

/** A count badge on the upper right of the disc of every shared spot. */
function drawBadges(map, ctx, stacks, look, width, height) {
  const pin = pinAt(map.getZoom(), look.padding)
  ctx.font = `bold ${BADGE_FONT}px ${look.family}`
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.lineWidth = 1.5
  for (const { marker, count } of stacks) {
    const p = map.latLngToContainerPoint(marker.getLatLng())
    const x = p.x + pin.disc * 0.38
    const y = p.y - pin.top + pin.disc * 0.12
    if (x < -BADGE || x > width + BADGE || y < -BADGE || y > height + BADGE) continue
    ctx.beginPath()
    ctx.arc(x, y, BADGE / 2, 0, Math.PI * 2)
    ctx.fillStyle = look.accent
    ctx.fill()
    ctx.strokeStyle = look.onAccent
    ctx.stroke()
    ctx.fillStyle = look.onAccent
    ctx.fillText(String(count), x, y + 0.5)
  }
}

/** The tooltip and badge look and the pin padding, from the tokens on the map container. */
function overlayLook(map) {
  const style = getComputedStyle(map.getContainer())
  const token = (name, fallback) => style.getPropertyValue(name).trim() || fallback
  const family = style.fontFamily || 'sans-serif'
  return {
    bg: token('--bg-hover', '#333'),
    border: token('--border', '#555'),
    text: token('--text', '#eee'),
    accent: token('--accent', '#5b8cff'),
    onAccent: token('--on-accent', '#fff'),
    family,
    font: `${FONT_SIZE}px ${family}`,
    // `.me-map` is the map container and sets the pin's padding.
    padding: parseFloat(style.getPropertyValue('--icon-padding')) || 3,
  }
}

/**
 * @param map      Leaflet map (may be null or already removed)
 * @param master   the "Event Spawns" layer group
 * @param groups   Map of event name to its layer group, from useSpawnLayer
 * @param options  { enabled, showLabels, labelClicks, visibleEvents }
 */
export function useSpawnOverlay(map, master, groups, { enabled, showLabels, labelClicks, visibleEvents }) {
  const theme = useMapTheme()
  // Read at click time, so entering add mode does not repaint the canvas.
  const clicksRef = useRef(labelClicks)
  clicksRef.current = labelClicks

  useEffect(() => {
    if (!isMapAlive(map) || !enabled || !master || !groups) return
    const items = []
    for (const [name, group] of groups) {
      if (visibleEvents && !visibleEvents.has(name)) continue
      group.eachLayer((marker) => items.push({ marker, name }))
    }
    if (!items.length) return

    ensurePane(map, SPAWN_OVERLAY_PANE, PANE_Z)
    const look = overlayLook(map)
    // A few event names, thousands of labels: measure each name once.
    const widths = new Map()
    const measure = document.createElement('canvas').getContext('2d')
    measure.font = look.font
    const widthOf = (line) => {
      let w = widths.get(line)
      if (w === undefined) {
        w = Math.ceil(measure.measureText(line).width)
        widths.set(line, w)
      }
      return w
    }
    const sizeOf = (rows) => ({
      w: Math.max(...rows.map((row) => widthOf(row.text))) + 2 * PAD_X + 2,
      h: rows.length * BOX_H + (rows.length - 1) * BOX_GAP,
    })

    let spots = groupSpots(items)
    let stacks = spots.filter((spot) => spot.count > 1)
    // Placement per zoom step; a pinch passes through fractional zooms.
    const byZoom = new Map()
    // The label boxes on screen now, for the click test.
    let hits = []
    const view = mountViewCanvas(map, SPAWN_OVERLAY_PANE, 'me-spawn-overlay', (ctx, width, height) => {
      if (showLabels) {
        const zoom = Math.round(map.getZoom() * 2) / 2
        if (!byZoom.has(zoom)) {
          const all = zoom >= map.getMaxZoom()
          byZoom.set(zoom, placeLabels(map, spots, zoom, sizeOf, look.padding, all))
        }
        hits = drawLabels(map, ctx, byZoom.get(zoom), sizeOf, look, width, height)
      }
      drawBadges(map, ctx, stacks, look, width, height)
    })

    // A dragged marker (or a dragged selection) moves its label and badge
    // with it, and the drop groups the spots and places the labels again.
    const moved = () => view.schedule()
    const dropped = () => {
      spots = groupSpots(items)
      stacks = spots.filter((spot) => spot.count > 1)
      byZoom.clear()
      view.schedule()
    }
    // The pin last used goes on top of its spot.
    let raised = null
    const raise = (e) => {
      const marker = e.target
      if (raised === marker) return
      raised?.setZIndexOffset(0)
      raised = marker
      marker.setZIndexOffset(RAISE_Z)
    }
    for (const { marker } of items) {
      marker.on('move', moved).on('dragend', dropped).on('click popupopen dragstart', raise)
    }

    // A press on a label box: a click opens that pin's popup, a drag moves it.
    const container = map.getContainer()
    const pointAt = (e) => {
      const rect = container.getBoundingClientRect()
      return L.point(e.clientX - rect.left, e.clientY - rect.top)
    }
    const hitAt = (e) => {
      if (!showLabels || !clicksRef.current || !isMapAlive(map)) return null
      const { x, y } = pointAt(e)
      // Last drawn is on top.
      for (let i = hits.length - 1; i >= 0; i--) {
        const b = hits[i]
        if (x >= b.left && x <= b.right && y >= b.top && y <= b.bottom) return b.marker
      }
      return null
    }
    // The press in progress: its marker, where it began, the offset from the
    // pointer to the pin's spot, and whether it became a drag.
    let press = null
    // Set when a press ended as a drag, so the click that follows is eaten.
    let eatClick = false
    // Where the last press began, so a map pan that ends on a label is no click.
    let lastDown = null
    const onMove = (e) => {
      if (!press || !isMapAlive(map)) return
      if (!press.dragging) {
        if (Math.hypot(e.clientX - press.x, e.clientY - press.y) <= CLICK_SLOP) return
        press.dragging = true
        press.marker.fire('dragstart')
      }
      press.marker.setLatLng(map.containerPointToLatLng(pointAt(e).add(press.offset)))
      press.marker.fire('drag')
    }
    const unlisten = () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      window.removeEventListener('pointercancel', onUp)
    }
    const onUp = () => {
      unlisten()
      const done = press
      press = null
      if (!done?.dragging) return
      eatClick = true
      done.marker.fire('dragend')
    }
    const onDown = (e) => {
      eatClick = false
      lastDown = { x: e.clientX, y: e.clientY }
      if (e.button !== 0) return
      const marker = hitAt(e)
      if (!marker) return
      // Leaflet starts a map pan on this press; the label takes it instead.
      e.stopPropagation()
      const canDrag = marker.dragging?.enabled()
      press = {
        marker,
        x: e.clientX,
        y: e.clientY,
        offset: map.latLngToContainerPoint(marker.getLatLng()).subtract(pointAt(e)),
        dragging: false,
      }
      if (!canDrag) return
      window.addEventListener('pointermove', onMove)
      window.addEventListener('pointerup', onUp)
      window.addEventListener('pointercancel', onUp)
    }
    // Older Leaflet paths listen for mousedown and touchstart too.
    const onPressOther = (e) => {
      if (hitAt(e.touches?.[0] || e)) e.stopPropagation()
    }
    const onClick = (e) => {
      if (eatClick) {
        eatClick = false
        e.stopPropagation()
        e.preventDefault()
        return
      }
      const start = lastDown
      lastDown = null
      if (start && Math.hypot(e.clientX - start.x, e.clientY - start.y) > CLICK_SLOP) return
      const marker = hitAt(e)
      if (!marker) return
      e.stopPropagation()
      e.preventDefault()
      marker.openPopup()
    }
    let pointing = false
    const onHover = (e) => {
      const over = Boolean(hitAt(e))
      if (over === pointing) return
      pointing = over
      container.style.cursor = over ? 'pointer' : ''
    }
    container.addEventListener('pointerdown', onDown, true)
    container.addEventListener('mousedown', onPressOther, true)
    container.addEventListener('touchstart', onPressOther, true)
    container.addEventListener('click', onClick, true)
    container.addEventListener('mousemove', onHover, true)
    return () => {
      // A rebuild mid-drag drops the drag; the new markers come from the server.
      unlisten()
      for (const { marker } of items) {
        marker.off('move', moved).off('dragend', dropped).off('click popupopen dragstart', raise)
      }
      container.removeEventListener('pointerdown', onDown, true)
      container.removeEventListener('mousedown', onPressOther, true)
      container.removeEventListener('touchstart', onPressOther, true)
      container.removeEventListener('click', onClick, true)
      container.removeEventListener('mousemove', onHover, true)
      if (pointing) container.style.cursor = ''
      view.remove()
    }
  }, [map, master, groups, enabled, showLabels, visibleEvents, theme])
}
