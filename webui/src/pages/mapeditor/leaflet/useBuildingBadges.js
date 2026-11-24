// Building badges: zoomed in on the Loot finder's Building view, every
// building that holds loot gets a badge with its kind's icon
// (icons/BuildingIcon.jsx) on a stem down to the building itself.
//
// A building whose name ends in a colour (Land_Camp_House_red) has its whole
// badge, the point included, drawn in that colour. Zoomed out, below the
// badges, it is a dot in that colour.
//
// The positions are GET /loot/catalog's `building_points` (flat, sorted by x)
// with `building_point_kinds` (an index into `buildings`). The badges use the
// building dots' colours, so they read as part of the Buildings layer, and
// they draw in their own pane over the map image and under the loot layer.

import { useEffect } from 'react'
import { isMapAlive } from './useLeafletMap'
import { canvasTileLayer, mapColor, useMapTheme } from './canvasTiles'
import { BADGE_MIN_PPM, BADGE_REACH_PX, badgePointAt, drawBadge, glyphOf, tilePointIndexes } from './badges'
import { buildingIcon } from '../icons/BuildingIcon.jsx'
import { swatchColor } from '../../../components/ItemIcon.jsx'

const PANE = 'buildingBadgePane'

// The building's own point under the stem, drawn with the badge.
const POINT_PX = 8

// A dark name colour (Black, Navy, Wine) on the dark disc is unreadable, so
// its disc is the light building colour instead.
function isDark(hex) {
  const n = parseInt(hex.slice(1), 16)
  const lum = 0.2126 * ((n >> 16) & 255) + 0.7152 * ((n >> 8) & 255) + 0.0722 * (n & 255)
  return lum < 80
}

/** Ring, stem, icon and point in the colour the name ends in, else the dots'. */
function badgeColors(paint, colors) {
  if (!paint) return colors
  return { fill: paint, edge: isDark(paint) ? colors.fill : colors.edge }
}

// Zoomed out, a coloured building is a dot the size of the Buildings layer's
// (useBuildingLayer.js), drawn over it in the colour of its name.
const DOT_METRES = 8
const DOT_MIN_PX = 1.5
const DOT_MAX_PX = 8

function drawPaintedDots(ctx, view, points, kindOf, paints, edge) {
  const side = Math.min(DOT_MAX_PX, Math.max(DOT_MIN_PX, DOT_METRES * view.ppm))
  const half = side / 2
  ctx.lineWidth = 1
  ctx.strokeStyle = edge
  for (const i of tilePointIndexes(view, points, half)) {
    const paint = paints[kindOf[i / 2]]
    if (!paint) continue
    const [px, py] = view.toPx(points[i], points[i + 1])
    ctx.fillStyle = paint
    if (side >= 4) ctx.strokeRect(px - half - 0.5, py - half - 0.5, side + 1, side + 1)
    ctx.fillRect(px - half, py - half, side, side)
  }
}

function drawBuildingBadges(ctx, view, points, kindOf, glyphs, paints, colors) {
  if (view.ppm < BADGE_MIN_PPM) {
    drawPaintedDots(ctx, view, points, kindOf, paints, colors.edge)
    return
  }
  const half = POINT_PX / 2
  for (const i of tilePointIndexes(view, points, BADGE_REACH_PX)) {
    const kind = kindOf[i / 2]
    if (!glyphs[kind]) continue
    const [px, py] = view.toPx(points[i], points[i + 1])
    const own = badgeColors(paints[kind], colors)
    ctx.fillStyle = own.fill
    ctx.fillRect(px - half, py - half, POINT_PX, POINT_PX)
    ctx.lineWidth = 1
    ctx.strokeStyle = own.edge
    ctx.strokeRect(px - half + 0.5, py - half + 0.5, POINT_PX - 1, POINT_PX - 1)
    drawBadge(ctx, px, py - half, glyphs[kind], own)
  }
}

/**
 * The building whose badge (or point) is under a map click, as {lat, lng},
 * or null. Only once the badges show; zoomed out, the caller's own pick runs.
 */
export function buildingBadgeAt(map, mapInfo, catalog, latlng) {
  return badgePointAt(map, mapInfo, catalog?.building_points, latlng, {
    halfAt: () => POINT_PX / 2,
    hitPx: 0,
    badges: true,
  })
}

export function useBuildingBadges(map, mapInfo, catalog, enabled) {
  const theme = useMapTheme()
  useEffect(() => {
    if (!isMapAlive(map) || !enabled || !mapInfo?.bounds) return
    const points = catalog?.building_points || []
    if (!points.length) return
    const kindOf = catalog.building_point_kinds || []
    const kinds = catalog.buildings || []
    const glyphs = kinds.map((k) => glyphOf(buildingIcon(k.name, k.usages)))
    // The colour a building's name ends in (Land_Camp_House_red, Land_House_1W05_Yellow).
    const paints = kinds.map((k) => swatchColor(k.name))
    if (!map.getPane(PANE)) {
      const pane = map.createPane(PANE)
      pane.style.zIndex = 400
      pane.style.pointerEvents = 'none'
    }
    const colors = {
      fill: mapColor(map, '--map-building', '#c8c3ad'),
      edge: mapColor(map, '--map-land', '#2a322c'),
    }
    const layer = canvasTileLayer(
      map,
      mapInfo,
      (ctx, view) => drawBuildingBadges(ctx, view, points, kindOf, glyphs, paints, colors),
      { pane: PANE }
    ).addTo(map)
    return () => {
      layer.remove()
    }
  }, [map, mapInfo, catalog, enabled, theme])
}
