// The coordinate grid (plans/69): lines every kilometre, plus every 100 m once
// those are far enough apart to read, labelled in game metres along the top and
// left edges of the view. The numbers are the cursor readout's X and Z.
//
// Lines are canvas tiles (see canvasTiles.js) in a pane of their own, above the
// map image and below every marker, so they work over either base map. Labels
// are one absolutely placed div over the map container, redrawn as the view
// moves: tile-drawn labels would repeat on every tile.

import { useEffect } from 'react'
import L from 'leaflet'
import { gameToLatLng, latLngToGame } from '../coords'
import { isMapAlive } from './useLeafletMap'
import { canvasTileLayer, mapColor, useMapTheme } from './canvasTiles'
import { html } from './html'

const MAJOR = 1000
const MINOR = 100
const WIDE = 5000
// Label spacings to try, finest first. Zoomed all the way out a map is a few
// dozen pixels wide, so the last ones label little more than the corner.
const LABEL_STEPS = [MINOR, MAJOR, WIDE, 10000, 20000]
// Closest two lines may be on screen before the finer step is dropped.
const MIN_LINE_GAP_PX = 24
const MIN_LABEL_GAP_PX = 64

const PANE = 'me-grid'

function lineSteps(ppm) {
  return {
    major: MAJOR * ppm >= MIN_LINE_GAP_PX / 2 ? MAJOR : WIDE,
    minor: MINOR * ppm >= MIN_LINE_GAP_PX ? MINOR : null,
  }
}

function labelStep(ppm) {
  return LABEL_STEPS.find((step) => step * ppm >= MIN_LABEL_GAP_PX) || LABEL_STEPS.at(-1)
}

/** Multiples of `step` from lo to hi, kept inside the world. */
function multiples(step, lo, hi, worldSize) {
  const out = []
  const from = Math.max(0, Math.ceil(lo / step) * step)
  const to = Math.min(worldSize, hi)
  for (let v = from; v <= to; v += step) out.push(v)
  return out
}

function drawLines(ctx, view, step, worldSize, color, width) {
  ctx.strokeStyle = color
  ctx.lineWidth = width
  ctx.beginPath()
  const [, top] = view.toPx(0, worldSize)
  const [, bottom] = view.toPx(0, 0)
  const [left] = view.toPx(0, 0)
  const [right] = view.toPx(worldSize, 0)
  for (const x of multiples(step, view.xMin, view.xMax, worldSize)) {
    const px = Math.round(view.toPx(x, 0)[0]) + 0.5
    ctx.moveTo(px, Math.max(0, top))
    ctx.lineTo(px, Math.min(view.height, bottom))
  }
  for (const z of multiples(step, view.zMin, view.zMax, worldSize)) {
    const py = Math.round(view.toPx(0, z)[1]) + 0.5
    ctx.moveTo(Math.max(0, left), py)
    ctx.lineTo(Math.min(view.width, right), py)
  }
  ctx.stroke()
}

export function useGridLayer(map, mapInfo, enabled) {
  const theme = useMapTheme()
  useEffect(() => {
    if (!isMapAlive(map) || !enabled || !mapInfo?.bounds) return
    const { bounds, scale, map_size: worldSize } = mapInfo
    if (!map.getPane(PANE)) {
      const pane = map.createPane(PANE)
      // Above the image overlay (overlayPane, 400), below markers (600).
      pane.style.zIndex = 450
      pane.style.pointerEvents = 'none'
    }
    const colors = {
      major: mapColor(map, '--map-grid-major', 'rgba(255, 255, 255, 0.35)'),
      minor: mapColor(map, '--map-grid-minor', 'rgba(255, 255, 255, 0.12)'),
    }
    const lines = canvasTileLayer(
      map,
      mapInfo,
      (ctx, view) => {
        const steps = lineSteps(view.ppm)
        if (steps.minor) drawLines(ctx, view, steps.minor, worldSize, colors.minor, 1)
        drawLines(ctx, view, steps.major, worldSize, colors.major, 1)
      },
      { pane: PANE }
    ).addTo(map)

    const labels = L.DomUtil.create('div', 'me-grid-labels', map.getContainer())
    const render = () => {
      if (!isMapAlive(map)) return
      const view = map.getBounds()
      const nw = latLngToGame(view.getNorthWest(), bounds, scale)
      const se = latLngToGame(view.getSouthEast(), bounds, scale)
      const step = labelStep(map.getSize().x / (se.x - nw.x))
      const parts = []
      for (const x of multiples(step, nw.x, se.x, worldSize)) {
        const p = map.latLngToContainerPoint(gameToLatLng(x, 0, bounds, scale))
        parts.push(html`<span class="me-grid-label x" style="left:${p.x}px">${x}</span>`)
      }
      for (const z of multiples(step, se.z, nw.z, worldSize)) {
        const p = map.latLngToContainerPoint(gameToLatLng(0, z, bounds, scale))
        parts.push(html`<span class="me-grid-label z" style="top:${p.y}px">${z}</span>`)
      }
      labels.innerHTML = parts.join('')
    }
    render()
    map.on('move zoom resize viewreset', render)

    return () => {
      map.off('move zoom resize viewreset', render)
      labels.remove()
      lines.remove()
    }
  }, [map, mapInfo, enabled, theme])
}
