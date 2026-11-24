// The building map (plans/69): every building on the terrain as a dot, over a
// plain land colour that covers the world's square.
//
// What a map without terrain or an image of its own is drawn on. The positions
// come from GET /buildings, which reads the admin's own
// <mission>/mapgrouppos.xml; towns, villages and the roads they line show up on
// their own.
//
// `points` is flat ([x0, z0, x1, z1, …], game metres) and sorted by x, so a
// tile finds its first building with a binary search instead of a full pass.
//
// `overlay` draws the same dots with a dark edge and no ground, over the map
// image (`canvasTiles.overImagePane`): the Buildings switch with the image on,
// and the Loot finder's Building view with Buildings off.
//
// `overTerrain` draws only the dots, with no ground, in a tile layer stacked
// above the terrain's and the roads' (plans/70).

import { useEffect } from 'react'
import { isMapAlive } from './useLeafletMap'
import { canvasTileLayer, firstAtOrAfter, groundPane, mapColor, useMapTheme } from './canvasTiles'
import { latLngToGame } from '../coords'

// A click picks the nearest building within this many screen pixels.
export const PICK_PX = 14

// The click's reach in game metres: PICK_PX at the current zoom, 3 to 500 m.
// Display-only arithmetic; a pick sends the point itself to the server as lat/lng.
export function pickRadius(map, mapInfo, latlng) {
  const here = map.latLngToContainerPoint(latlng)
  const edge = map.containerPointToLatLng(here.add([PICK_PX, 0]))
  const a = latLngToGame(latlng, mapInfo?.bounds, mapInfo?.scale)
  const b = latLngToGame(edge, mapInfo?.bounds, mapInfo?.scale)
  return a && b ? Math.min(500, Math.max(3, Math.hypot(b.x - a.x, b.z - a.z))) : 30
}

// Whether a building of `points` (flat, sorted by x) stands within `radius`
// game metres of `latlng`: the Buildings layer's click test while the Loot
// finder is closed.
export function buildingNear(points, mapInfo, latlng, radius) {
  const g = latLngToGame(latlng, mapInfo?.bounds, mapInfo?.scale)
  if (!g || !points?.length) return false
  for (let i = firstAtOrAfter(points, g.x - radius); i < points.length; i += 2) {
    if (points[i] > g.x + radius) break
    if (Math.hypot(points[i] - g.x, points[i + 1] - g.z) <= radius) return true
  }
  return false
}

// A building is drawn about this many metres wide, but never smaller than a
// visible speck when zoomed out or bigger than a marker when zoomed in.
const BUILDING_METRES = 8
const MIN_PX = 1.5
const MAX_PX = 8


function drawBuildings(ctx, view, points, worldSize, colors, overlay) {
  if (!overlay && colors.land) {
    const [left, top] = view.toPx(0, worldSize)
    const [right, bottom] = view.toPx(worldSize, 0)
    ctx.fillStyle = colors.land
    ctx.fillRect(left, top, right - left, bottom - top)
  }

  const side = Math.min(MAX_PX, Math.max(MIN_PX, BUILDING_METRES * view.ppm))
  const half = side / 2
  const pad = half / view.ppm
  ctx.fillStyle = colors.building
  for (let i = firstAtOrAfter(points, view.xMin - pad); i < points.length; i += 2) {
    const x = points[i]
    if (x > view.xMax + pad) break
    const z = points[i + 1]
    if (z < view.zMin - pad || z > view.zMax + pad) continue
    const [px, py] = view.toPx(x, z)
    if (overlay) {
      ctx.strokeStyle = colors.land
      ctx.lineWidth = 1
      ctx.strokeRect(px - half - 0.5, py - half - 0.5, side + 1, side + 1)
    }
    ctx.fillRect(px - half, py - half, side, side)
  }
}

export function useBuildingLayer(
  map,
  mapInfo,
  buildings,
  enabled,
  { overlay = false, overTerrain = false } = {}
) {
  const theme = useMapTheme()
  useEffect(() => {
    if (!isMapAlive(map) || !enabled || !mapInfo?.bounds) return
    const points = buildings?.points || []
    const worldSize = mapInfo.map_size
    const colors = {
      land: overTerrain && !overlay ? null : mapColor(map, '--map-land', '#2a322c'),
      building: mapColor(map, '--map-building', '#c8c3ad'),
    }
    // Below every marker: without the image this IS the map. Over the image
    // it sits above the terrain lines and the roads.
    const layer = canvasTileLayer(
      map,
      mapInfo,
      (ctx, view) => drawBuildings(ctx, view, points, worldSize, colors, overlay),
      groundPane(map, overlay, overlay ? 4 : overTerrain ? 4 : 0)
    ).addTo(map)
    return () => {
      layer.remove()
    }
  }, [map, mapInfo, buildings, enabled, overlay, overTerrain, theme])
}
