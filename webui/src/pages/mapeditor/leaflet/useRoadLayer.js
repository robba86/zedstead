// The roads (plans/70): every road in the server's own world file, drawn on
// canvas tiles over the ground and under the buildings, coloured by kind.
//
// GET /roads is {roads: {kind: [[x0, z0, x1, z1, …], …]}} in game metres
// (app/maps/road_map.py). The kind comes from the road piece's model name, and
// pieces that meet are already joined, so a dirt road's dashes run along it.
// A tile strokes only the lines whose box reaches it, one path per kind.

import { useEffect } from 'react'
import { roadsUrl } from '../api'
import { isMapAlive } from './useLeafletMap'
import { canvasTileLayer, groundPane, mapColor, useDownload, useMapTheme } from './canvasTiles'

// [kind, label, CSS colour, width in metres, narrowest and widest in px, dash
// in px, first drawn from this many px per metre]. Drawn in this order, so
// the main roads end up on top. "other" is a road no kind fits (a bridge no
// road meets) and is drawn like a minor road; the legend leaves it out.
const STYLES = [
  ['path', 'Path', '--map-road-path', 2, 0.8, 2, [0.1, 3], 0.25],
  ['dirt', 'Dirt', '--map-road-dirt', 4, 0.8, 3, [6, 4], 0],
  ['gravel', 'Gravel', '--map-road-gravel', 5, 0.9, 4, null, 0],
  ['airfield', 'Airfield', '--map-road-airfield', 20, 1.5, 14, null, 0],
  ['other', null, '--map-road-minor', 6, 1, 5, null, 0],
  ['minor', 'Minor', '--map-road-minor', 6, 1, 5, null, 0],
  ['secondary', 'Secondary', '--map-road-secondary', 7, 1.3, 6, null, 0],
  ['main', 'Main', '--map-road-main', 9, 1.6, 7, null, 0],
]

/** The legend: [kind, label], main road first. */
export const ROAD_LEGEND = STYLES.filter(([, label]) => label)
  .map(([kind, label]) => [kind, label])
  .reverse()

function prepare(body) {
  const roads = {}
  for (const [kind, lines] of Object.entries(body?.roads || {})) {
    roads[kind] = lines.map((pts) => {
      let xMin = Infinity
      let xMax = -Infinity
      let zMin = Infinity
      let zMax = -Infinity
      for (let i = 0; i < pts.length; i += 2) {
        xMin = Math.min(xMin, pts[i])
        xMax = Math.max(xMax, pts[i])
        zMin = Math.min(zMin, pts[i + 1])
        zMax = Math.max(zMax, pts[i + 1])
      }
      return { pts, xMin, xMax, zMin, zMax }
    })
  }
  return roads
}

const readRoads = (resp) => resp.json().then(prepare)

function draw(ctx, view, roads, colors) {
  ctx.lineJoin = 'round'
  for (const [kind, , , metres, min, max, dash, fromPpm] of STYLES) {
    const lines = roads[kind]
    if (!lines || view.ppm < fromPpm) continue
    const width = Math.min(max, Math.max(min, metres * view.ppm))
    const pad = width / view.ppm
    const path = new Path2D()
    let any = false
    for (const line of lines) {
      if (
        line.xMax < view.xMin - pad ||
        line.xMin > view.xMax + pad ||
        line.zMax < view.zMin - pad ||
        line.zMin > view.zMax + pad
      )
        continue
      const { pts } = line
      const [x0, y0] = view.toPx(pts[0], pts[1])
      path.moveTo(x0, y0)
      for (let i = 2; i < pts.length; i += 2) {
        const [x, y] = view.toPx(pts[i], pts[i + 1])
        path.lineTo(x, y)
      }
      any = true
    }
    if (!any) continue
    ctx.strokeStyle = colors[kind]
    ctx.lineWidth = width
    // A path is a row of dots: zero-length dashes with round ends.
    ctx.lineCap = kind === 'path' ? 'round' : 'butt'
    ctx.setLineDash(dash || [])
    ctx.stroke(path)
  }
  ctx.setLineDash([])
}

/** Draws the roads; returns true while they are still on their way. */
export function useRoadLayer(map, mapInfo, terrain, enabled, overTerrain, onError, overImage = false) {
  const url = terrain ? roadsUrl(terrain) : null
  const { data: roads, loading } = useDownload(url, enabled, 'Roads', readRoads, onError)
  const theme = useMapTheme()

  useEffect(() => {
    if (!isMapAlive(map) || !enabled || !mapInfo?.bounds || !roads) return
    const colors = Object.fromEntries(
      STYLES.map(([kind, , name]) => [kind, mapColor(map, name, '#c9c3ab')])
    )
    // Over the terrain and under the buildings' dots. With no ground the
    // building layer paints the land itself, so the roads go above it.
    const layer = canvasTileLayer(
      map,
      mapInfo,
      (ctx, view) => draw(ctx, view, roads, colors),
      groundPane(map, overImage, overImage || overTerrain ? 3 : 4)
    ).addTo(map)
    return () => {
      layer.remove()
    }
  }, [map, mapInfo, roads, enabled, overTerrain, overImage, theme])

  return loading
}
