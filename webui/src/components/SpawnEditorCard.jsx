import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { withBase } from '../instance'
import PanelNote from './PanelNote.jsx'

// Dashboard card linking to the Visual Map Editor — a page of this app
// (/map-editor), not a separate server, since the React port landed.
//
// The preview is the active map's bundled low-res image (GET
// /api/maps/thumbnail); `active` is only used to bust the browser cache after
// a map switch, since the URL itself is terrain-agnostic.
export default function SpawnEditorCard() {
  const [active, setActive] = useState(null)
  const [hasImage, setHasImage] = useState(true)
  // The preview is a 2048px jpg — without this the card holds an empty 170px
  // box while it decodes. Keyed by the src that finished, not a boolean: the
  // src is rewritten once `active` lands (cache-bust), and a boolean reset in
  // an effect would race a cached image's load event and could strand the
  // placeholder over a decoded picture.
  const [loadedSrc, setLoadedSrc] = useState(null)

  useEffect(() => {
    let cancelled = false
    api
      .get('/api/maps')
      .then((body) => {
        if (!cancelled) setActive(body.active || null)
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [])

  const src = withBase(`/api/maps/thumbnail${active ? `?v=${encodeURIComponent(active)}` : ''}`)

  const loaded = loadedSrc === src

  return (
    <div className="card">
      <div className="card-header">
        <h2>Map Editor</h2>
      </div>
      {hasImage && (
        <Link
          className={`map-preview${loaded ? '' : ' map-preview-loading'}`}
          to="/map-editor"
          aria-label="Open the Map Editor"
        >
          <img
            src={src}
            alt={active ? `${active} map` : 'Current map'}
            onLoad={() => setLoadedSrc(src)}
            onError={() => setHasImage(false)}
          />
          {active && loaded && <span className="map-preview-label">{active}</span>}
        </Link>
      )}
      <PanelNote>
        Edit event spawns, player spawns, traders, safe zones, and garages on an interactive map.
      </PanelNote>
      <div className="inline-form">
        <Link className="btn btn-primary btn-small" to="/map-editor">
          Open editor
        </Link>
      </div>
    </div>
  )
}
