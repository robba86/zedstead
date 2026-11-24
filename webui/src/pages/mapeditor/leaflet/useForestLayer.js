// The forest (ROADMAP 50): where the trees stand, from the mission's
// mapgroupcluster*.xml on the server (app/maps/cluster_map.py), drawn on
// canvas tiles over the terrain and under the roads.
//
// GET /forest is {cell, points: [x0, z0, …], counts: [n0, …], conifers:
// [c0, …]} in game metres: each square's centre, sorted by x, how many trees
// stand in it and how many of those are conifers. The files list the trees
// the economy uses, not every tree. The layer draws only tree icons, with no
// fill under them:
//
// - Below `ICONS_FROM_PX` per square, only some squares carry a tree, about
//   one per `ICONS_FROM_PX` square of screen, so a zoomed-out map draws a
//   few thousand icons, not tens of thousands.
// - Zoomed in, it carries one icon per listed tree, as many as fit, on a
//   3 x 3 pattern of spots in the square's middle, fixed by its position so
//   they never move while the map pans. Extra trees were tried and dropped:
//   they put trees in the sea and in towns.
// - Firs and round crowns follow the square's share of conifers.

import { useEffect } from 'react'
import { forestUrl } from '../api'
import { isMapAlive } from './useLeafletMap'
import { canvasTileLayer, firstAtOrAfter, groundPane, mapColor, useDownload, useMapTheme } from './canvasTiles'

// Below this many pixels per square, a square gets at most one tree, and
// only a share of the squares get one.
const ICONS_FROM_PX = 12
// An icon's height: a share of the square, between these pixel sizes.
const ICON_SHARE = 0.42
const ICON_MIN_PX = 7
const ICON_MAX_PX = 22
// At most this many trees per square: the 3 x 3 spots.
const SPOTS = 9
// The spots cover this share of the square, around its middle, so a tree at
// a square's edge does not land in the sea or a town next to it.
const SPREAD = 0.7

const readForest = (resp) => resp.json()

// A number in [0, 1) fixed by a square and an icon index, so an icon keeps its
// spot on every tile and at every zoom.
function jitter(x, z, k) {
  let h = (Math.imul(x | 0, 73856093) ^ Math.imul(z | 0, 19349663) ^ Math.imul(k + 1, 83492791)) >>> 0
  h = Math.imul(h ^ (h >>> 15), 2246822507) >>> 0
  h = Math.imul(h ^ (h >>> 13), 3266489909) >>> 0
  return ((h ^ (h >>> 16)) >>> 0) / 4294967296
}

// The squares on this tile: index i into points (x at i, z at i + 1).
function* visible(view, points, pad) {
  for (let i = firstAtOrAfter(points, view.xMin - pad); i < points.length; i += 2) {
    const x = points[i]
    if (x > view.xMax + pad) return
    const z = points[i + 1]
    if (z < view.zMin - pad || z > view.zMax + pad) continue
    yield i
  }
}

// A fir: a two-tier triangle on a short trunk, `h` px tall, its foot at (x, y).
function fir(path, x, y, h) {
  const w = h * 0.62
  path.moveTo(x, y - h)
  path.lineTo(x + w * 0.38, y - h * 0.5)
  path.lineTo(x + w * 0.2, y - h * 0.5)
  path.lineTo(x + w * 0.5, y - h * 0.18)
  path.lineTo(x + w * 0.08, y - h * 0.18)
  path.lineTo(x + w * 0.08, y)
  path.lineTo(x - w * 0.08, y)
  path.lineTo(x - w * 0.08, y - h * 0.18)
  path.lineTo(x - w * 0.5, y - h * 0.18)
  path.lineTo(x - w * 0.2, y - h * 0.5)
  path.lineTo(x - w * 0.38, y - h * 0.5)
  path.closePath()
}

// A broadleaf tree: a round crown on a short trunk, `h` px tall, its foot at (x, y).
function crown(path, x, y, h) {
  const r = h * 0.36
  path.moveTo(x + r, y - h + r)
  path.arc(x, y - h + r, r, 0, Math.PI * 2)
  path.rect(x - h * 0.06, y - h + r * 1.8, h * 0.12, h - r * 1.8)
}


// Draws the tree icons of the squares on this tile, in one fill.
function draw(ctx, view, forest, color) {
  const { cell, points, counts, conifers } = forest
  const squarePx = cell * view.ppm
  const size = Math.min(ICON_MAX_PX, Math.max(ICON_MIN_PX, squarePx * ICON_SHARE))
  // Zoomed out, one tree per square; zoomed in, as many as fit without
  // piling up, up to the square's own count.
  const fit = squarePx < ICONS_FROM_PX ? 1 : Math.max(1, Math.min(SPOTS, Math.floor((squarePx / size) ** 2 * 0.6)))
  // Zoomed out, the share of squares that carry a tree: fewer the smaller a
  // square is on screen, so the icons per screen pixel stay the same.
  const keep = squarePx < ICONS_FROM_PX ? (squarePx / ICONS_FROM_PX) ** 2 : 1
  const spot = (cell * SPREAD) / 3
  const path = new Path2D()
  for (const i of visible(view, points, cell)) {
    const x = points[i]
    const z = points[i + 1]
    // The same squares at every pan, picked by their position.
    if (keep < 1 && jitter(x, z, 999) >= keep) continue
    const n = counts[i / 2]
    const many = Math.min(fit, n, SPOTS)
    // Firs and crowns in the square's own proportion.
    const firs = Math.round((many * (conifers?.[i / 2] ?? 0)) / n)
    // A fixed shuffle of the 3 x 3 spots, so the trees spread over the square.
    const order = [0, 1, 2, 3, 4, 5, 6, 7, 8]
    for (let k = SPOTS - 1; k > 0; k--) {
      const j = Math.floor(jitter(x, z, 100 + k) * (k + 1))
      ;[order[k], order[j]] = [order[j], order[k]]
    }
    for (let k = 0; k < many; k++) {
      const col = order[k] % 3
      const row = (order[k] / 3) | 0
      const gx = x - spot * 1.5 + spot * (col + 0.2 + 0.6 * jitter(x, z, k * 2))
      const gz = z - spot * 1.5 + spot * (row + 0.2 + 0.6 * jitter(x, z, k * 2 + 1))
      const [px, py] = view.toPx(gx, gz)
      // The icon's foot sits on the spot, so it stands above it.
      if (k < firs) fir(path, px, py + size / 2, size)
      else crown(path, px, py + size / 2, size)
    }
  }
  ctx.fillStyle = color
  ctx.fill(path)
}

/** Draws the forest; returns true while it is still on its way. */
export function useForestLayer(map, mapInfo, terrain, enabled, onError, overImage = false) {
  const url = terrain ? forestUrl(terrain) : null
  const { data: forest, loading } = useDownload(url, enabled, 'Forest', readForest, onError)
  const theme = useMapTheme()

  useEffect(() => {
    if (!isMapAlive(map) || !enabled || !mapInfo?.bounds || !forest?.points) return
    const color = mapColor(map, '--map-forest-tree', '#56744d')
    // Over the terrain and the building map's land, under the roads.
    const layer = canvasTileLayer(
      map,
      mapInfo,
      (ctx, view) => draw(ctx, view, forest, color),
      groundPane(map, overImage, 2)
    ).addTo(map)
    return () => {
      layer.remove()
    }
  }, [map, mapInfo, forest, enabled, overImage, theme])

  return loading
}
