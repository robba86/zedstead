// A small map of the running mission for the mod page's spawn test: click the
// map or drag the marker to choose where the test's items appear (and where the
// first player who joins is moved). Without `onPick` it only shows the point.
//
// It draws the terrain from the world file (else the admin's own
// image, else the buildings), the roads, the grid
// and the named places, through the editor's own layer hooks. No editor state:
// the picker reads /map-info, /locations and /buildings itself.
//
// The spawn test's endpoint takes X and Z in metres, so the click is converted
// here with coords.js, the same conversion the editor's cursor readout shows.
// The server checks the map's edges again.

import { useEffect, useRef, useState } from 'react'
import L from 'leaflet'
import Segmented from '../../components/Segmented.jsx'
import { meApi } from './api'
import MapCredit from './MapCredit.jsx'
import { gameToLatLng, latLngToMetres } from './coords'
import { isMapAlive, toLatLngBounds, useLeafletMap, useMapEvent } from './leaflet/useLeafletMap'
import { useImageOverlay } from './leaflet/useImageOverlay'
import { useTerrainLayer } from './leaflet/useTerrainLayer'
import { useRoadLayer } from './leaflet/useRoadLayer'
import { useBuildingLayer } from './leaflet/useBuildingLayer'
import { useGridLayer } from './leaflet/useGridLayer'
import { useLocationLayers } from './leaflet/useLocationLayers'
import './mapeditor.css'

// The ground picked with the switcher, per browser (not per map: a ground a
// map lacks falls back to its default).
const BASE_KEY = 'zsm.spawnpick.base'

function storedBase() {
  try {
    return localStorage.getItem(BASE_KEY)
  } catch {
    return null
  }
}

/** What the spawn test's X and Z boxes hold for *latlng*. */
function toPoint(latlng, info) {
  return latLngToMetres(latlng, info.bounds, info.scale, info.map_size)
}

export default function SpawnPointPicker({ point, onPick }) {
  const [info, setInfo] = useState(null)
  const [locations, setLocations] = useState([])
  const [buildings, setBuildings] = useState(null)
  const [error, setError] = useState('')
  const [cursor, setCursor] = useState(null)
  const [chosen, setChosen] = useState(storedBase)

  useEffect(() => {
    let live = true
    meApi
      .get('/map-info')
      .then((data) => live && setInfo(data))
      .catch((err) => live && setError(err.message || 'Could not load the map'))
    return () => {
      live = false
    }
  }, [])

  const terrain = info?.terrain || ''
  // The terrain drawn from the server's world file first, so roads and towns
  // read the same on every map (maintainer, 2026 Q4); the admin's own image
  // only when there is no world file to draw, else the buildings on no ground.
  // A cleared map's image (image_cleared) counts like the admin's own.
  const imageUsable = Boolean(info?.has_image)
  const defaultBase = !info ? null : info.has_terrain ? 'terrain' : imageUsable ? 'image' : 'none'
  // The switcher offers every ground this map has.
  const grounds = !info
    ? []
    : [
        info.has_terrain && { value: 'terrain', label: 'Terrain' },
        info.has_image && { value: 'image', label: 'Image' },
        info.has_buildings !== false && { value: 'none', label: 'Buildings' },
      ].filter(Boolean)
  const base = grounds.some((g) => g.value === chosen) ? chosen : defaultBase
  function chooseBase(value) {
    setChosen(value)
    try {
      localStorage.setItem(BASE_KEY, value)
    } catch {
      // Private mode: the choice lasts for this page only.
    }
  }
  const showRoads = base === 'terrain' && (info?.has_roads ?? true)
  const showBuildings = base === 'none' && info?.has_buildings !== false

  useEffect(() => {
    if (!terrain) return
    let live = true
    meApi
      .get(`/locations?terrain=${encodeURIComponent(terrain)}`)
      .then((data) => live && setLocations(data || []))
      .catch(() => {})
    return () => {
      live = false
    }
  }, [terrain])

  useEffect(() => {
    if (!terrain || !showBuildings) return
    let live = true
    meApi
      .get(`/buildings?terrain=${encodeURIComponent(terrain)}`)
      .then((data) => live && setBuildings(data))
      .catch(() => {})
    return () => {
      live = false
    }
  }, [terrain, showBuildings])

  const bounds = info?.bounds || null
  const { containerRef, map } = useLeafletMap(bounds)
  const failed = (what) => (err) => setError(`Could not draw the ${what}: ${err.message}`)

  useImageOverlay(map, terrain, bounds, base === 'image', info?.image_own?.version)
  useTerrainLayer(map, info, terrain, base === 'terrain', failed('terrain map'))
  useRoadLayer(map, info, terrain, showRoads, base === 'terrain', failed('roads'))
  useBuildingLayer(map, info, buildings, showBuildings)
  useGridLayer(map, info, true)
  useLocationLayers(map, locations, { enabled: true, visibleTypes: null })

  // The marker: draggable when picking, and kept where `point` says.
  const markerRef = useRef(null)
  const onPickRef = useRef(onPick)
  onPickRef.current = onPick
  const x = point?.x
  const z = point?.z
  const hasPoint = Number.isFinite(x) && Number.isFinite(z)
  const picking = Boolean(onPick)

  useEffect(() => {
    if (!isMapAlive(map) || !info || !hasPoint) return
    const latlng = gameToLatLng(x, z, info.bounds, info.scale)
    const marker = L.marker(latlng, {
      draggable: picking,
      keyboard: false,
      icon: L.divIcon({ className: 'spawn-pick-marker', iconSize: [22, 22] }),
    }).addTo(map)
    if (picking) {
      marker.on('dragend', () => {
        const picked = toPoint(marker.getLatLng(), info)
        if (picked) onPickRef.current?.(picked)
      })
    }
    markerRef.current = marker
    return () => {
      marker.remove()
      markerRef.current = null
    }
  }, [map, info, hasPoint, x, z, picking])

  // A place chosen from the list may be off screen: bring it into view.
  useEffect(() => {
    if (!isMapAlive(map) || !info || !hasPoint) return
    const latlng = gameToLatLng(x, z, info.bounds, info.scale)
    if (!map.getBounds().contains(latlng)) map.panTo(latlng)
  }, [map, info, hasPoint, x, z])

  useMapEvent(map, 'click', (e) => {
    if (!picking || !info) return
    if (!toLatLngBounds(info.bounds).contains(e.latlng)) return
    const picked = toPoint(e.latlng, info)
    if (picked) onPickRef.current?.(picked)
  })
  useMapEvent(map, 'mousemove', (e) => {
    if (info) setCursor(toLatLngBounds(info.bounds).contains(e.latlng) ? toPoint(e.latlng, info) : null)
  })
  useMapEvent(map, 'mouseout', () => setCursor(null))

  return (
    <div className="spawn-pick">
      <div className={`me-map-wrap spawn-pick-map${picking ? ' add-mode-cursor' : ''}`}>
        <div ref={containerRef} className="me-map" />
        {!info && !error && <div className="spawn-pick-note">Loading the map…</div>}
        {info?.credit && ((base === 'image' && !info.image_own) || base === 'terrain' || showRoads) && (
          <MapCredit credit={info.credit} />
        )}
        {grounds.length > 1 && (
          <div className="spawn-pick-base">
            <Segmented label="Map" value={base} options={grounds} onChange={chooseBase} />
          </div>
        )}
      </div>
      <div className="spawn-pick-status">
        {error ? (
          <span className="spawn-pick-error">{error}</span>
        ) : cursor ? (
          <span>
            X {cursor.x} · Z {cursor.z}
          </span>
        ) : (
          <span>{picking ? 'Click the map to place the spawn point.' : terrain}</span>
        )}
      </div>
    </div>
  )
}
