// The loot layer (plans/82): every building where the picked item, or any item
// of the picked mod, can spawn, as an accent square with a contrasting edge.
//
// `loot.points` comes from GET /loot, flat ([x0, z0, x1, z1, …], game metres)
// and sorted by x, so a tile binary-searches to its x range like the building
// layer does. Its own pane (`lootPane`, z-index 400, created after
// overlayPane) puts it over the map image, under the location labels (401)
// and the grid (450). It draws on every ground, and whether or not Buildings
// is ticked.
//
// For one picked item, each square also carries a round badge with the item's
// icon (components/ItemIcon.jsx) on a short stem. The square stays on the
// exact spot and the stem points at it, so the badge never hides where the
// item spawns. Zoomed out, only a spread of the squares keeps one
// (badges.js badgeIndexes), more with each zoom step in, every one from
// BADGE_MIN_PPM.

import { useEffect } from 'react'
import { isMapAlive } from './useLeafletMap'
import { canvasTileLayer, mapColor, useMapTheme } from './canvasTiles'
import { BADGE_REACH_PX, badgeIndexes, badgePointAt, drawBadge, glyphOf, tilePointIndexes } from './badges'
import { itemIconComponent, useItemCategory } from '../../../components/ItemIcon.jsx'

const PANE = 'lootPane'
// A marked building is about this many metres wide, within a px range: big
// enough to stand out over the building dots, never bigger than a marker.
const LOOT_METRES = 10
const MIN_PX = 5
const MAX_PX = 14
// A click this many screen pixels from a small square still hits it.
const HIT_PX = 10

// A square's side in screen pixels at `ppm` pixels per metre.
const sideAt = (ppm) => Math.min(MAX_PX, Math.max(MIN_PX, LOOT_METRES * ppm))

/** The marked point (or its item badge) under a map click, or null. */
export function lootPointAt(map, mapInfo, points, latlng, badges) {
  return badgePointAt(map, mapInfo, points, latlng, { halfAt: (ppm) => sideAt(ppm) / 2, hitPx: HIT_PX, badges, thin: true })
}

function drawLoot(ctx, view, points, colors, icon) {
  const side = sideAt(view.ppm)
  const half = side / 2
  // Every square from the zoom the building badges use, a spread below it.
  const kept = icon ? badgeIndexes(points, view.ppm) : null
  const indexes = tilePointIndexes(view, points, icon ? half + BADGE_REACH_PX : half)
  ctx.fillStyle = colors.fill
  ctx.strokeStyle = colors.edge
  ctx.lineWidth = 1
  for (const i of indexes) {
    const [px, py] = view.toPx(points[i], points[i + 1])
    ctx.fillRect(px - half, py - half, side, side)
    ctx.strokeRect(px - half + 0.5, py - half + 0.5, side - 1, side - 1)
  }
  if (!icon) return
  for (const i of indexes) {
    if (kept && !kept.has(i)) continue
    const [px, py] = view.toPx(points[i], points[i + 1])
    drawBadge(ctx, px, py - half, icon, colors)
  }
}

/** The loot layer; `item` is the picked item's name (none in a mod's view). */
export function useLootLayer(map, mapInfo, loot, enabled, item) {
  const theme = useMapTheme()
  const category = useItemCategory(item || '', null)
  useEffect(() => {
    if (!isMapAlive(map) || !enabled || !mapInfo?.bounds) return
    const points = loot?.points || []
    if (!points.length) return
    if (!map.getPane(PANE)) {
      const pane = map.createPane(PANE)
      pane.style.zIndex = 400
      pane.style.pointerEvents = 'none'
    }
    const colors = {
      fill: mapColor(map, '--map-loot', '#ff4fa3'),
      edge: mapColor(map, '--map-loot-edge', '#1a0d14'),
    }
    const icon = item && category ? glyphOf(itemIconComponent(category, item)) : null
    const layer = canvasTileLayer(map, mapInfo, (ctx, view) => drawLoot(ctx, view, points, colors, icon), {
      pane: PANE,
    }).addTo(map)
    return () => {
      layer.remove()
    }
  }, [map, mapInfo, loot, enabled, theme, item, category])
}
