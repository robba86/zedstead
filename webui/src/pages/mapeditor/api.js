// Map editor API surface.
//
// Every editor endpoint lives under /api/map-editor and goes through the
// app-wide fetch wrapper, so the editor inherits session handling (401 ->
// /login) instead of the old frontend's forever-reconnecting socket.
//
// The vanilla editor derived its prefix from window.location.pathname
// (utils.js's BASE_PATH) because one bundle was served at two mount roots.
// There is one root now — never reintroduce that.

import { api } from '../../api'
import { withBase } from '../../instance'

export const ME_BASE = '/api/map-editor'

const url = (path) => ME_BASE + (path.startsWith('/') ? path : '/' + path)

export const meApi = {
  get: (path) => api.get(url(path)),
  post: (path, data) => api.post(url(path), data),
  put: (path, data) => api.put(url(path), data),
  del: (path, data) => api.del(url(path), data),
  // The body is the file itself (app/web/uploads.py), not a form.
  postFile: (path, file) => api.postFile(url(path), file),
}

// Image URLs are used as <img>/L.imageOverlay sources, not fetched, so they
// take this page's instance prefix themselves. `version` changes when the
// admin uploads their own image, so the overlay is rebuilt with the new one.
export const mapImageUrl = (terrain, version) => {
  const query = new URLSearchParams()
  if (terrain) query.set('terrain', terrain)
  if (version) query.set('v', version)
  const qs = query.toString()
  return withBase(`${ME_BASE}/map-image${qs ? `?${qs}` : ''}`)
}

// The terrain map's height grid (plans/70). Fetched as bytes, not through
// meApi, which parses JSON.
export const terrainHeightsUrl = (terrain) =>
  withBase(`${ME_BASE}/terrain-heights?terrain=${encodeURIComponent(terrain)}`)

// The roads from the same world file, as JSON (plans/70).
export const roadsUrl = (terrain) => withBase(`${ME_BASE}/roads?terrain=${encodeURIComponent(terrain)}`)

// The forest from the mission's cluster files, as JSON (ROADMAP 50).
export const forestUrl = (terrain) => withBase(`${ME_BASE}/forest?terrain=${encodeURIComponent(terrain)}`)
