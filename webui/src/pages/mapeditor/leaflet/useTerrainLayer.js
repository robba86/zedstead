// The terrain map (plans/70): the ground's shape, drawn on canvas tiles from
// the height grid in the server's own world file. Land and sea are flat; the
// coastline is where the ground crosses 0 m, and contour lines mark the rest,
// closer together as you zoom in. A lake above sea level is the dip in the
// same heights that `/map-info` names with a point (terrainLakes.js), drawn
// like the sea.
//
// GET /terrain-heights is the grid (app/maps/terrain_map.py, encode_heights).
// A tile samples the coarsest copy of it whose cells are still MIN_CELL_PX
// wide, so a tile costs about the same at every zoom. The land and sea fill is
// per pixel from the same interpolation the lines are traced on, so the two
// meet where the coastline is drawn.
//
// Over a map image (`overImage`) only the lines are drawn, with no fill, so
// the image stays visible under them.

import { useEffect } from 'react'
import { terrainHeightsUrl } from '../api'
import { isMapAlive } from './useLeafletMap'
import { lakeDepths, NO_LAKE } from './terrainLakes'
import { canvasTileLayer, groundPane, mapColor, useDownload, useMapTheme } from './canvasTiles'

const MIN_CELL_PX = 2
const SMALLEST_SIDE = 64
// [from this many CSS px per metre, contour step in metres]; every fifth line
// is drawn stronger.
const CONTOUR_STEPS = [
  [0.6, 10],
  [0.15, 20],
  [0.04, 50],
  [0, 100],
]
const MAJOR_EVERY = 5

function decode(buffer) {
  const head = new Uint8Array(buffer, 0, 4)
  if (String.fromCharCode(...head) !== 'DZTH') throw new Error('Not a terrain height file')
  const view = new DataView(buffer)
  const side = view.getUint32(4, true)
  const cell = view.getFloat32(8, true)
  const unit = view.getFloat32(12, true)
  if (buffer.byteLength < 16 + side * side * 2) throw new Error('Terrain height file is cut short')
  const steps = new Int16Array(buffer, 16, side * side)
  const values = new Int16Array(side * side)
  for (let r = 0; r < side; r++) {
    let h = 0
    for (let x = r * side, end = x + side; x < end; x++) {
      h += steps[x]
      values[x] = h
    }
  }
  return { cell, unit, levels: [{ side, values }], lakes: [] }
}

// Fetched as bytes, not through meApi, which parses JSON.
const readHeights = (resp) => resp.arrayBuffer().then(decode)

/** Level *n* of the grid: every other point of the level above, smoothed 1-2-1. */
function level(grid, n) {
  while (grid.levels.length <= n) {
    const { side: ps, values: prev } = grid.levels.at(-1)
    const side = Math.ceil(ps / 2)
    const values = new Int16Array(side * side)
    for (let j = 0; j < side; j++) {
      for (let i = 0; i < side; i++) {
        let sum = 0
        let weight = 0
        for (let dj = -1; dj <= 1; dj++) {
          const y = 2 * j + dj
          if (y < 0 || y >= ps) continue
          for (let di = -1; di <= 1; di++) {
            const x = 2 * i + di
            if (x < 0 || x >= ps) continue
            const w = (dj ? 1 : 2) * (di ? 1 : 2)
            sum += prev[y * ps + x] * w
            weight += w
          }
        }
        values[j * side + i] = Math.round(sum / weight)
      }
    }
    grid.levels.push({ side, values })
  }
  return grid.levels[n]
}

/** Level *n*'s lake field (terrainLakes.js), worked out once per level. */
function lakes(grid, n, points) {
  if (grid.lakes[n] === undefined) {
    const { side, values } = level(grid, n)
    grid.lakes[n] = lakeDepths(side, values, grid.cell * 2 ** n, points)
  }
  return grid.lakes[n]
}

function pickLevel(grid, ppm) {
  let n = 0
  while (grid.cell * 2 ** n * ppm < MIN_CELL_PX && grid.levels[0].side >> (n + 1) >= SMALLEST_SIDE) n++
  return n
}

// A lake's field is whole units of depth, 0 or less on its shore.
const LAKE_EDGE = 0.5

/** Sea and land, one device pixel at a time; outside the world stays clear. */
function fill(ctx, view, lvl, lake, cell, worldSize, threshold, colors) {
  const { side: s, values: v } = lvl
  const W = ctx.canvas.width
  const H = ctx.canvas.height
  const pxPerM = W / (view.xMax - view.xMin)
  const img = ctx.createImageData(W, H)
  const d = img.data
  for (let py = 0; py < H; py++) {
    const z = view.zMax - (py + 0.5) / pxPerM
    if (z < 0 || z > worldSize) continue
    const gz = z / cell
    const j = Math.max(0, Math.min(s - 2, Math.floor(gz)))
    const fz = Math.max(0, Math.min(1, gz - j))
    const south = j * s
    const north = south + s
    for (let px = 0; px < W; px++) {
      const x = view.xMin + (px + 0.5) / pxPerM
      if (x < 0 || x > worldSize) continue
      const gx = x / cell
      const i = Math.max(0, Math.min(s - 2, Math.floor(gx)))
      const fx = Math.max(0, Math.min(1, gx - i))
      const a = v[south + i]
      const b = v[south + i + 1]
      const c = v[north + i]
      const e = v[north + i + 1]
      const lo = a + (b - a) * fx
      const hi = c + (e - c) * fx
      let wet = lo + (hi - lo) * fz < threshold
      if (!wet && lake && lake[south + i] !== NO_LAKE) {
        // Water where the lake field, read like the heights, is over half a unit.
        const la = lake[south + i] + (lake[south + i + 1] - lake[south + i]) * fx
        const lc = lake[north + i] + (lake[north + i + 1] - lake[north + i]) * fx
        wet = la + (lc - la) * fz > LAKE_EDGE
      }
      const col = wet ? colors.sea : colors.land
      const o = (py * W + px) * 4
      d[o] = col[0]
      d[o + 1] = col[1]
      d[o + 2] = col[2]
      d[o + 3] = 255
    }
  }
  ctx.putImageData(img, 0, 0)
}

/**
 * Marching squares over the cells under the tile: the coastline at
 * `threshold`, and a line every `step` units above it.
 */
function trace(view, lvl, lake, cell, threshold, step) {
  const { side: s, values: v } = lvl
  const paths = { coast: new Path2D(), minor: new Path2D(), major: new Path2D() }
  const { ppm, xMin, zMax } = view
  const X = (gx) => (gx * cell - xMin) * ppm
  const Y = (gz) => (zMax - gz * cell) * ppm
  const iMin = Math.max(0, Math.floor(view.xMin / cell) - 1)
  const iMax = Math.min(s - 2, Math.ceil(view.xMax / cell))
  const jMin = Math.max(0, Math.floor(view.zMin / cell) - 1)
  const jMax = Math.min(s - 2, Math.ceil(view.zMax / cell))
  const pts = []

  // Corners: a south-west, b south-east, c north-east, d north-west.
  function segments(path, i, j, a, b, c, d, t) {
    pts.length = 0
    if (a < t !== b < t) pts.push(X(i + (t - a) / (b - a)), Y(j))
    if (b < t !== c < t) pts.push(X(i + 1), Y(j + (t - b) / (c - b)))
    if (d < t !== c < t) pts.push(X(i + (t - d) / (c - d)), Y(j + 1))
    if (a < t !== d < t) pts.push(X(i), Y(j + (t - a) / (d - a)))
    if (pts.length === 4) {
      path.moveTo(pts[0], pts[1])
      path.lineTo(pts[2], pts[3])
    } else if (pts.length === 8) {
      // A saddle: the centre says which two corners are joined.
      const joined = (a + b + c + d) / 4 < t === a < t
      const [p, q] = joined ? [[0, 1], [2, 3]] : [[0, 3], [1, 2]]
      path.moveTo(pts[p[0] * 2], pts[p[0] * 2 + 1])
      path.lineTo(pts[p[1] * 2], pts[p[1] * 2 + 1])
      path.moveTo(pts[q[0] * 2], pts[q[0] * 2 + 1])
      path.lineTo(pts[q[1] * 2], pts[q[1] * 2 + 1])
    }
  }

  for (let j = jMin; j <= jMax; j++) {
    const south = j * s
    const north = south + s
    for (let i = iMin; i <= iMax; i++) {
      const a = v[south + i]
      const b = v[south + i + 1]
      const c = v[north + i + 1]
      const d = v[north + i]
      const lo = Math.min(a, b, c, d)
      const hi = Math.max(a, b, c, d)
      if (lo < threshold && hi >= threshold) segments(paths.coast, i, j, a, b, c, d, threshold)
      const la = lake ? lake[south + i] : NO_LAKE
      const lb = lake ? lake[south + i + 1] : NO_LAKE
      const lc = lake ? lake[north + i + 1] : NO_LAKE
      const ld = lake ? lake[north + i] : NO_LAKE
      if (la !== NO_LAKE && lb !== NO_LAKE && lc !== NO_LAKE && ld !== NO_LAKE) {
        const wetLo = Math.min(la, lb, lc, ld)
        const wetHi = Math.max(la, lb, lc, ld)
        if (wetLo <= LAKE_EDGE && wetHi > LAKE_EDGE) segments(paths.coast, i, j, la, lb, lc, ld, LAKE_EDGE)
        // A lake bed under open water gets no contour lines, like the sea floor.
        if (wetLo > LAKE_EDGE) continue
      }
      for (let k = Math.max(1, Math.floor(lo / step) + 1); k * step <= hi; k++) {
        segments(k % MAJOR_EVERY ? paths.minor : paths.major, i, j, a, b, c, d, k * step)
      }
    }
  }
  return paths
}

/** [r, g, b] of any CSS colour. */
function rgb(color) {
  const ctx = document.createElement('canvas').getContext('2d')
  ctx.fillStyle = color
  ctx.fillRect(0, 0, 1, 1)
  return Array.from(ctx.getImageData(0, 0, 1, 1).data.slice(0, 3))
}

function draw(ctx, view, grid, lakePoints, worldSize, colors, overImage) {
  const n = pickLevel(grid, view.ppm)
  const lvl = level(grid, n)
  const cell = grid.cell * 2 ** n
  // Heights are whole units, and anything below 0 m is negative: half a unit
  // below 0 is where the sea starts.
  const threshold = -0.5
  const lake = lakes(grid, n, lakePoints)
  if (!overImage) fill(ctx, view, lvl, lake, cell, worldSize, threshold, colors)
  const metres = CONTOUR_STEPS.find(([from]) => view.ppm >= from)[1]
  const paths = trace(view, lvl, lake, cell, threshold, metres / grid.unit)
  ctx.lineJoin = 'round'
  ctx.lineCap = 'round'
  ctx.strokeStyle = colors.contour
  ctx.lineWidth = 0.8
  ctx.stroke(paths.minor)
  ctx.strokeStyle = colors.contourMajor
  ctx.lineWidth = 1.2
  ctx.stroke(paths.major)
  ctx.strokeStyle = colors.coast
  ctx.lineWidth = 1.4
  ctx.stroke(paths.coast)
}

/** Draws the terrain; returns true while its heights are still on their way. */
export function useTerrainLayer(map, mapInfo, terrain, enabled, onError, overImage = false) {
  const url = terrain ? terrainHeightsUrl(terrain) : null
  const { data: grid, loading } = useDownload(url, enabled, 'Terrain heights', readHeights, onError)
  const theme = useMapTheme()

  useEffect(() => {
    if (!isMapAlive(map) || !enabled || !mapInfo?.bounds || !grid) return
    const colors = {
      land: rgb(mapColor(map, '--map-terrain-land', '#2a322c')),
      sea: rgb(mapColor(map, '--map-terrain-sea', '#16212b')),
      coast: mapColor(map, '--map-terrain-coast', '#6f9cc0'),
      contour: overImage
        ? mapColor(map, '--map-terrain-contour-on-image', 'rgba(255, 244, 214, 0.38)')
        : mapColor(map, '--map-terrain-contour', 'rgba(201, 195, 171, 0.16)'),
      contourMajor: overImage
        ? mapColor(map, '--map-terrain-contour-major-on-image', 'rgba(255, 244, 214, 0.72)')
        : mapColor(map, '--map-terrain-contour-major', 'rgba(201, 195, 171, 0.34)'),
    }
    const worldSize = mapInfo.map_size
    // The base map below every marker; over the image, its lines only.
    const layer = canvasTileLayer(
      map,
      mapInfo,
      (ctx, view) => draw(ctx, view, grid, mapInfo.lakes, worldSize, colors, overImage),
      groundPane(map, overImage, overImage ? 1 : 0)
    ).addTo(map)
    return () => {
      layer.remove()
    }
  }, [map, mapInfo, grid, enabled, overImage, theme])

  return loading
}
