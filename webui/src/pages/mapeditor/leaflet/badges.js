// Icon badges on a canvas map layer: a round disc with a Game-icons glyph,
// on a short stem that points down at the exact spot it belongs to. The loot
// layer (an item's icon over each building it can spawn in) and the building
// badges (each loot building's kind) draw them the same way.

import { firstAtOrAfter } from './canvasTiles'
import { gameToLatLng, latLngToGame } from '../coords'

// The badge's diameter, and the stem between it and its spot, in px.
export const BADGE_PX = 36
export const STEM_PX = 10
/** How far above its spot a badge reaches, for a tile's search pad. */
export const BADGE_REACH_PX = STEM_PX + BADGE_PX

// Badges show from this many screen px per game metre: a building about 10 m
// wide is 7 px on screen. The loot and the building badges share it, so both
// show at the same zoom (maintainer, 2026-11-05 and 2026-11-07).
export const BADGE_MIN_PPM = 0.7

// Below BADGE_MIN_PPM a thinned layer (the loot layer's picked item) keeps one
// badge per square cell this many screen px wide, so a zoomed-out map shows
// the icon spread over where it spawns, and each zoom step in shows more.
export const BADGE_CELL_PX = 72

const thinned = new WeakMap()

/**
 * The indexes (into flat `points`) that carry a badge at `ppm` screen px per
 * metre: null for every point, from BADGE_MIN_PPM up, else a Set with one
 * point per cell, the one nearest the cell's centre. The cells sit on a grid
 * in game metres, per whole zoom level, so every tile and the click test
 * (badgePointAt) pick the same points, and a pan never moves a badge.
 */
export function badgeIndexes(points, ppm) {
  if (ppm >= BADGE_MIN_PPM) return null
  const level = Math.round(Math.log2(ppm))
  let byLevel = thinned.get(points)
  if (!byLevel) thinned.set(points, (byLevel = new Map()))
  if (!byLevel.has(level)) {
    const cell = BADGE_CELL_PX / 2 ** level
    const best = new Map()
    for (let i = 0; i < points.length; i += 2) {
      const cx = Math.floor(points[i] / cell)
      const cz = Math.floor(points[i + 1] / cell)
      const d = Math.hypot(points[i] - (cx + 0.5) * cell, points[i + 1] - (cz + 0.5) * cell)
      const key = cx + ',' + cz
      const had = best.get(key)
      if (!had || d < had.d) best.set(key, { d, i })
    }
    byLevel.set(level, new Set([...best.values()].map((b) => b.i)))
  }
  return byLevel.get(level)
}

/**
 * The first zoom where badges show on a map of `scale` metres per map unit
 * (CRS.Simple: 2^zoom px per unit), on the editor's 0.5 zoom snap. The Loot
 * finder flies there, so a list row lands with the icons in view.
 */
export function badgeZoom(scale) {
  return Math.ceil(Math.log2(BADGE_MIN_PPM * scale) * 2) / 2
}

/**
 * The point under a map click, as {lat, lng}, or null, for a layer of flat
 * `points` ([x0, z0, …], sorted by x) drawn as squares with badges. It hits
 * the square (`halfAt(ppm)` px from its centre, at least `hitPx`) and, from
 * BADGE_MIN_PPM when `badges` is set, the badge drawn over it. With `thin`
 * set it also hits the badges a zoomed-out layer keeps (badgeIndexes). The
 * nearest hit wins. Display-only arithmetic: the caller sends the lat/lng on.
 */
export function badgePointAt(map, mapInfo, points, latlng, { halfAt, hitPx, badges, thin }) {
  if (!points?.length || !mapInfo?.bounds) return null
  const { bounds, scale } = mapInfo
  const here = map.latLngToContainerPoint(latlng)
  const a = latLngToGame(latlng, bounds, scale)
  const b = latLngToGame(map.containerPointToLatLng(here.add([1, 0])), bounds, scale)
  if (!a || !b) return null
  const metresPerPx = Math.hypot(b.x - a.x, b.z - a.z) || 1
  const half = halfAt(1 / metresPerPx)
  // The tiles draw at the whole zoom (a half step scales them), so the thinned
  // set is the one for the tile's px per metre.
  const zoom = map.getZoom()
  const tilePpm = 2 ** (Math.round(zoom) - zoom) / metresPerPx
  const kept = badges && thin ? badgeIndexes(points, tilePpm) : null
  // A thinned layer always draws badges: all of them where `kept` is null.
  const withBadge = badges && (thin || 1 / metresPerPx >= BADGE_MIN_PPM)
  const reach = Math.max(half, hitPx)
  const pad = (withBadge ? half + BADGE_REACH_PX : reach) * metresPerPx
  let best = null
  for (let i = firstAtOrAfter(points, a.x - pad); i < points.length; i += 2) {
    const x = points[i]
    if (x > a.x + pad) break
    const z = points[i + 1]
    if (Math.abs(z - a.z) > pad) continue
    const pos = gameToLatLng(x, z, bounds, scale)
    const p = map.latLngToContainerPoint(pos)
    let d = Math.hypot(p.x - here.x, p.y - here.y)
    if (d > reach) {
      // The badge: a disc BADGE_PX wide on a stem above the square (drawBadge).
      const cy = p.y - half - STEM_PX - BADGE_PX / 2
      if (!withBadge || (kept && !kept.has(i)) || Math.hypot(p.x - here.x, cy - here.y) > BADGE_PX / 2) continue
      d = reach
    }
    if (!best || d < best.d) best = { d, pos }
  }
  return best ? best.pos : null
}

// A react-icons component called as a plain function returns its element tree
// without rendering (icons/markerIcons.js does the same): the path data is
// what a canvas needs.
function pathData(nodes) {
  return [].concat(nodes ?? []).flatMap((node) =>
    node.props.d ? [node.props.d] : pathData(node.props.children))
}

const glyphs = new Map()
const glyphPaths = new Map()

/** A react-icons component as `{size, paths}` with SVG path strings, for a divIcon's markup, cached. */
export function glyphSvgPaths(Icon) {
  if (!Icon) return null
  if (!glyphPaths.has(Icon)) {
    const el = Icon({})
    const size = Number(String(el.props.attr?.viewBox || '0 0 512 512').split(' ')[2]) || 512
    glyphPaths.set(Icon, { size, paths: pathData(el.props.children) })
  }
  return glyphPaths.get(Icon)
}

/** A react-icons component as `{size, paths}` (Path2D) for `drawBadge`, cached. */
export function glyphOf(Icon) {
  if (!Icon) return null
  if (!glyphs.has(Icon)) {
    const el = Icon({})
    const size = Number(String(el.props.attr?.viewBox || '0 0 512 512').split(' ')[2]) || 512
    glyphs.set(Icon, { size, paths: pathData(el.props.children).map((d) => new Path2D(d)) })
  }
  return glyphs.get(Icon)
}

/** The index (into the flat, x-sorted `points`) of every point near this tile. */
export function tilePointIndexes(view, points, reachPx) {
  const pad = reachPx / view.ppm
  const out = []
  for (let i = firstAtOrAfter(points, view.xMin - pad); i < points.length; i += 2) {
    if (points[i] > view.xMax + pad) break
    const z = points[i + 1]
    if (z < view.zMin - pad || z > view.zMax + pad) continue
    out.push(i)
  }
  return out
}

/**
 * One badge whose stem ends at (`px`, `top`): `colors.fill` draws the ring,
 * the stem and the glyph, `colors.edge` the disc.
 */
export function drawBadge(ctx, px, top, glyph, colors) {
  const r = BADGE_PX / 2
  const cy = top - STEM_PX - r
  ctx.strokeStyle = colors.fill
  ctx.lineWidth = 2
  ctx.beginPath()
  ctx.moveTo(px, top)
  ctx.lineTo(px, cy + r)
  ctx.stroke()
  ctx.beginPath()
  ctx.arc(px, cy, r, 0, Math.PI * 2)
  ctx.fillStyle = colors.edge
  ctx.fill()
  ctx.stroke()
  const side = BADGE_PX * 0.66
  ctx.save()
  ctx.translate(px - side / 2, cy - side / 2)
  ctx.scale(side / glyph.size, side / glyph.size)
  ctx.fillStyle = colors.fill
  for (const p of glyph.paths) ctx.fill(p)
  ctx.restore()
}
