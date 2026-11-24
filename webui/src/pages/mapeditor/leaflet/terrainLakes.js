// Inland lakes for the terrain map. A world file's heights hold a lake's bed
// but not its water, so a lake above sea level would draw as land. Most dips
// in the ground are dry (Banov has over 200), so only the dips the map's
// registry entry names with a point (`/map-info` `lakes`, from
// app/maps/registry.py `ClearedMap.lakes`) are filled, up to where each one
// would spill over (priority flood). The level is an estimate from the
// heights: the maker's own water line is in map data Zedstead must not read
// (app/maps/CLAUDE.md, "World files are untrusted input").

/** A grid point that is neither in a lake nor on its shore. */
export const NO_LAKE = -32768
/** How far from its point, in grid cells, a lake's dip is looked for. */
const SEARCH_CELLS = 3

const NEIGHBOURS = [
  [-1, -1], [0, -1], [1, -1],
  [-1, 0], [1, 0],
  [-1, 1], [0, 1], [1, 1],
]

/** Calls *visit* with the index of each in-grid neighbour of point *k*. */
function eachNeighbour(side, k, visit) {
  const x = k % side
  const y = (k - x) / side
  for (const [dx, dy] of NEIGHBOURS) {
    const nx = x + dx
    const ny = y + dy
    if (nx >= 0 && ny >= 0 && nx < side && ny < side) visit(ny * side + nx)
  }
}

/**
 * Each point's water level: its own height, or the level a dip around it
 * fills to. Water leaves over the world's edge and into the sea (below 0).
 * Heights are whole units, so a bucket per unit keeps the flood linear.
 */
export function fillDips(side, values) {
  const n = side * side
  let lo = Infinity
  let hi = -Infinity
  for (let k = 0; k < n; k++) {
    if (values[k] < lo) lo = values[k]
    if (values[k] > hi) hi = values[k]
  }
  const head = new Int32Array(hi - lo + 1).fill(-1)
  const next = new Int32Array(n)
  const filled = new Int16Array(n)
  const closed = new Uint8Array(n)
  const push = (k, level) => {
    closed[k] = 1
    filled[k] = level
    next[k] = head[level - lo]
    head[level - lo] = k
  }
  for (let k = 0; k < n; k++) {
    const x = k % side
    const y = (k - x) / side
    if (values[k] < 0 || x === 0 || y === 0 || x === side - 1 || y === side - 1) push(k, values[k])
  }
  for (let b = 0; b < head.length; b++) {
    // A push never goes below the bucket being emptied, so b only rises.
    while (head[b] !== -1) {
      const k = head[b]
      head[b] = next[k]
      const level = b + lo
      eachNeighbour(side, k, (m) => {
        if (!closed[m]) push(m, Math.max(values[m], level))
      })
    }
  }
  return filled
}

/** The dip point nearest to world point [x, z], or -1 if none is close. */
function dipNear(side, cell, filled, values, [x, z]) {
  const cx = Math.round(x / cell)
  const cy = Math.round(z / cell)
  let best = -1
  let bestDist = Infinity
  for (let dy = -SEARCH_CELLS; dy <= SEARCH_CELLS; dy++) {
    for (let dx = -SEARCH_CELLS; dx <= SEARCH_CELLS; dx++) {
      const nx = cx + dx
      const ny = cy + dy
      if (nx < 0 || ny < 0 || nx >= side || ny >= side) continue
      const k = ny * side + nx
      if (filled[k] > values[k] && dx * dx + dy * dy < bestDist) {
        best = k
        bestDist = dx * dx + dy * dy
      }
    }
  }
  return best
}

/**
 * The lakes of one grid level as a field the terrain map draws like the sea:
 * water depth in units at a lake point, the step down to the water (0 or
 * less) at a point next to one, and NO_LAKE everywhere else. `points` are the
 * lakes' [x, z] in metres; with none, the answer is null and nothing is done.
 */
export function lakeDepths(side, values, cell, points) {
  if (!points?.length) return null
  const filled = fillDips(side, values)
  const depth = new Int16Array(side * side).fill(NO_LAKE)
  const lake = []
  for (const point of points) {
    const start = dipNear(side, cell, filled, values, point)
    if (start < 0 || depth[start] > 0) continue
    // The lake: the 8-connected points under water around its point.
    depth[start] = filled[start] - values[start]
    lake.length = 0
    lake.push(start)
    for (let i = 0; i < lake.length; i++) {
      eachNeighbour(side, lake[i], (m) => {
        if (depth[m] <= 0 && filled[m] > values[m]) {
          depth[m] = filled[m] - values[m]
          lake.push(m)
        }
      })
    }
    // The shore: its height below the water level, so the line between a
    // lake point and a shore point crosses 0 where the ground meets the water.
    for (const k of lake) {
      eachNeighbour(side, k, (m) => {
        if (depth[m] <= 0) depth[m] = Math.max(depth[m], Math.min(0, filled[k] - values[m]))
      })
    }
  }
  return depth
}
