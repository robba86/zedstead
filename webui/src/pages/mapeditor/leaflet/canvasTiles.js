// Canvas tile layers drawn in game metres (plans/69): the building map, the
// coordinate grid and the loot layer (plans/82).
//
// Each tile is a canvas Leaflet asks for as the view moves, so drawing ~15k
// buildings costs one pass over the few hundred that fall in that tile, never a
// DOM node per building (the collision layer needs batching for exactly that).
// `draw(ctx, view)` gets the tile's extent in game metres and a projector, so
// the layers never touch lat/lng themselves. The conversion is coords.js's, the
// same one the cursor readout uses, so a dot sits where the readout says it is.

import L from 'leaflet'
import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { latLngToGame } from '../coords'
import { toLatLngBounds } from './useLeafletMap'

const OVER_IMAGE_PANE = 'overImagePane'

/**
 * The pane for the terrain lines, roads and buildings drawn over the map
 * image. It shares the image's z-index (overlayPane, 400) and is created after
 * it, so it draws over the image and under the location labels (401). Inside
 * it, each layer's `zIndex` sets the order: terrain 1, forest 2, roads 3,
 * buildings 4.
 */
export function overImagePane(map) {
  if (!map.getPane(OVER_IMAGE_PANE)) {
    const pane = map.createPane(OVER_IMAGE_PANE)
    pane.style.zIndex = 400
    pane.style.pointerEvents = 'none'
  }
  return OVER_IMAGE_PANE
}

/**
 * Where a ground layer goes: over the image in `overImagePane`, else in the
 * tile pane. `zIndex` orders it among the other ground layers.
 */
export function groundPane(map, overImage, zIndex) {
  return { pane: overImage ? overImagePane(map) : 'tilePane', ...(zIndex ? { zIndex } : {}) }
}

/**
 * A GridLayer of canvas tiles, limited to the world's bounds.
 *
 * @param map     the Leaflet map, for its zoom range
 * @param info    /map-info's body: `bounds` and `scale` are read
 * @param draw    (ctx, view) => void, view = { ppm, xMin, xMax, zMin, zMax, toPx, width, height }
 * @param options passed to L.gridLayer (pane, zIndex …)
 */
export function canvasTileLayer(map, info, draw, options = {}) {
  const layer = L.gridLayer({
    ...options,
    bounds: toLatLngBounds(info.bounds),
    // The map's own range, not GridLayer's defaults: below minZoom (0 by
    // default, the map goes to -3) the layer draws nothing, and without a
    // maxZoom every tile level gets a NaN z-index, so the last level's tiles,
    // scaled up, stay drawn over the current ones.
    minZoom: map.getMinZoom(),
    maxZoom: map.getMaxZoom(),
  })
  layer.createTile = function createTile(coords) {
    const tile = L.DomUtil.create('canvas', 'leaflet-tile')
    const size = this.getTileSize()
    // Leaflet sizes the tile in CSS pixels; draw at the screen's density so
    // dots and lines stay sharp on a HiDPI display.
    const dpr = window.devicePixelRatio || 1
    tile.width = size.x * dpr
    tile.height = size.y * dpr
    const ctx = tile.getContext('2d')
    ctx.scale(dpr, dpr)

    const nwPx = coords.scaleBy(size)
    const nw = latLngToGame(this._map.unproject(nwPx, coords.z), info.bounds, info.scale)
    const se = latLngToGame(this._map.unproject(nwPx.add(size), coords.z), info.bounds, info.scale)
    const ppm = size.x / (se.x - nw.x)
    draw(ctx, {
      ppm,
      xMin: nw.x,
      xMax: se.x,
      zMin: se.z,
      zMax: nw.z,
      // Game z grows north, canvas y grows down.
      toPx: (x, z) => [(x - nw.x) * ppm, (nw.z - z) * ppm],
      width: size.x,
      height: size.y,
    })
    return tile
  }
  return layer
}

/**
 * First pair index whose x is >= value, in a flat [x0, z0, x1, z1, …] array
 * sorted by x. The building and loot layers find a tile's first point with it
 * instead of a pass over every point.
 */
export function firstAtOrAfter(points, value) {
  let lo = 0
  let hi = points.length / 2
  while (lo < hi) {
    const mid = (lo + hi) >> 1
    if (points[mid * 2] < value) lo = mid + 1
    else hi = mid
  }
  return lo * 2
}

/** A theme colour from the map container's CSS custom properties. */
export function mapColor(map, name, fallback) {
  const value = getComputedStyle(map.getContainer()).getPropertyValue(name).trim()
  return value || fallback
}

// The theme the CSS is showing, from <html data-theme>. A layer reads its
// colours with `mapColor` when it is built, so it lists this in its effect's
// dependencies to be built again after a theme switch. It watches the
// attribute rather than useTheme(): ThemeProvider writes it in an effect,
// which runs after the layers' own effects.
function subscribeTheme(onChange) {
  const observer = new MutationObserver(onChange)
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
  return () => observer.disconnect()
}

const currentTheme = () => document.documentElement.getAttribute('data-theme')

export function useMapTheme() {
  return useSyncExternalStore(subscribeTheme, currentTheme)
}

// Files a layer draws from (the terrain heights, the roads), fetched once per
// URL for the life of the page. A failed fetch is tried again next time.
const downloads = new Map()
const downloaded = new Map()

/** A failed response's FastAPI `detail`, else its status line. */
async function failureText(resp) {
  try {
    const detail = (await resp.json())?.detail
    if (typeof detail === 'string' && detail) return detail
  } catch {
    // Not JSON: the status line says enough.
  }
  return `${resp.status} ${resp.statusText}`
}

/** *url* parsed by *parse*, fetched once per URL; a failure is dropped so the next call retries. */
function fetchOnce(url, what, parse) {
  if (!downloads.has(url)) {
    const p = fetch(url, { credentials: 'same-origin' })
      .then(async (resp) => {
        if (!resp.ok) throw new Error(`${what}: ${await failureText(resp)}`)
        return parse(resp)
      })
      .then((data) => {
        downloaded.set(url, data)
        return data
      })
    p.catch(() => downloads.delete(url))
    downloads.set(url, p)
  }
  return downloads.get(url)
}

/**
 * The file at *url*, once *enabled*: `{data, loading}`. `data` is null until
 * that URL's file is in; `loading` is true while it is on its way, so the
 * page can say so (the first build of a big world file takes seconds).
 * `parse(resp)` must be a module-level function, and `onError` is called once
 * per failed fetch.
 */
export function useDownload(url, enabled, what, parse, onError) {
  const [state, setState] = useState(null)
  // A fresh closure every render; read through a ref so it never refetches.
  const onErrorRef = useRef(onError)
  onErrorRef.current = onError

  useEffect(() => {
    if (!enabled || !url) return
    let live = true
    fetchOnce(url, what, parse).then(
      (data) => live && setState({ url, data }),
      (error) => {
        if (!live) return
        setState({ url, error })
        onErrorRef.current?.(error)
      }
    )
    return () => {
      live = false
    }
  }, [url, enabled, what, parse])

  const mine = state?.url === url ? state : null
  const data = mine?.data ?? (url ? downloaded.get(url) : undefined) ?? null
  return { data, loading: Boolean(enabled && url && !data && !mine?.error) }
}
