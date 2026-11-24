// One canvas the size of the map view, for text the map paints instead of
// putting a DOM node per label on the page (town labels, spawn labels).
//
// A screen-sized canvas, not canvas tiles: Leaflet scales a tile level up
// during the zoom animation, so tiled text swelled to twice its size and then
// snapped back. The canvas is redrawn on every move (a pan, a pinch) and fades
// out for the length of an animated zoom, then is drawn at the new zoom and
// fades back in. Text never scales with the map.

import L from 'leaflet'
import { isMapAlive } from './useLeafletMap'

// Fade around an animated zoom, in ms.
const FADE_MS = 150

/** Create the pane *name* once per map: no clicks, stacked at *zIndex*. */
export function ensurePane(map, name, zIndex) {
  if (!isMapAlive(map) || map.getPane(name)) return
  const pane = map.createPane(name)
  pane.style.zIndex = zIndex
  pane.style.pointerEvents = 'none'
}

/**
 * Put a view-sized canvas in *pane* and call `paint(ctx, width, height)` on
 * every frame the map moves. Returns `{ schedule, remove }`: `schedule` asks
 * for one more frame (a marker moved), `remove` takes the canvas away.
 */
export function mountViewCanvas(map, pane, className, paint) {
  const canvas = L.DomUtil.create('canvas', className, map.getPane(pane))
  canvas.style.position = 'absolute'
  canvas.style.left = '0'
  canvas.style.top = '0'
  if (!window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    canvas.style.transition = `opacity ${FADE_MS}ms ease-out`
  }
  const ctx = canvas.getContext('2d')
  let frame = 0

  const draw = () => {
    frame = 0
    if (!isMapAlive(map)) return
    const { x: width, y: height } = map.getSize()
    const dpr = window.devicePixelRatio || 1
    if (canvas.width !== width * dpr || canvas.height !== height * dpr) {
      canvas.width = width * dpr
      canvas.height = height * dpr
      canvas.style.width = `${width}px`
      canvas.style.height = `${height}px`
    }
    // The pane moves with the map; keep the canvas over the view.
    L.DomUtil.setPosition(canvas, map.containerPointToLayerPoint([0, 0]))
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.clearRect(0, 0, width, height)
    paint(ctx, width, height)
  }
  const schedule = () => {
    if (!frame) frame = requestAnimationFrame(draw)
  }
  const hide = () => {
    canvas.style.opacity = '0'
  }
  const show = () => {
    draw()
    canvas.style.opacity = '1'
  }

  draw()
  map.on('move resize', schedule)
  map.on('zoomanim', hide)
  map.on('zoomend moveend viewreset', show)
  return {
    schedule,
    remove() {
      if (frame) cancelAnimationFrame(frame)
      map.off('move resize', schedule)
      map.off('zoomanim', hide)
      map.off('zoomend moveend viewreset', show)
      canvas.remove()
    },
  }
}
