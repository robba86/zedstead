// Expansion's nine map-data layers.
//
// Backed by the JSON Expansion writes on its first boot (app/spawn_editor/
// expansion_state.py): safe zones in three shapes, one file per trader zone,
// player spawn positions grouped by location, the mission markers, and — from
// the AI module — patrol routes and roaming locations. The last three
// (expansion_position_state.py) are lists inside settings files: market
// vehicle spawns (teal), server map markers (their own colour) and
// base-building zones (rose).
//
// Colours are data, not chrome, so they live here as hex like every other
// marker in this folder (mapeditor.css's header comment explains that split):
// safe zones sky, trader zones violet, spawn points pink, airdrops orange,
// contaminated areas lime, AI patrols red and roaming locations amber. They are
// picked to stay distinct from TraderPlus' green safe zones and gold traders,
// which can be on at the same time.
//
// Missions are **read-only** in this cut: an airdrop's position couples to its
// drop heights and a contaminated area's to its ring geometry, so moving one is
// a settings edit, not a drag. They still draw, because seeing them next to the
// trader zones is the whole reason to have the layer.

import { useEffect, useMemo, useState } from 'react'
import L from 'leaflet'
import { isMapAlive } from './useLeafletMap'
import { useEventCallback } from '../useEventCallback'
import { drawCircleZone, toggleGroup } from './circleZone'
import { bindTextTooltip } from './textTooltip'
import { html } from './html'

export const EXPANSION_COLORS = {
  safezone: '#38bdf8',
  traderzone: '#a855f7',
  spawn: '#f472b6',
  airdrop: '#fb923c',
  contaminated: '#84cc16',
  aipatrol: '#ef4444',
  airoaming: '#f59e0b',
  nogo: '#94a3b8',
  market: '#14b8a6',
  buildzone: '#e11d48',
}

const SPAWN_ICON = L.divIcon({
  className: 'expansion-spawn-icon',
  html: html`<svg width="12" height="12" viewBox="0 0 12 12"><circle cx="6" cy="6" r="4.5" fill="${EXPANSION_COLORS.spawn}" stroke="#0f172a" stroke-width="1.5"/></svg>`,
  iconSize: [12, 12],
  iconAnchor: [6, 6],
})

const WAYPOINT_ICON = L.divIcon({
  className: 'expansion-waypoint-icon',
  html: html`<svg width="10" height="10" viewBox="0 0 10 10"><rect x="1" y="1" width="8" height="8" transform="rotate(45 5 5)" fill="${EXPANSION_COLORS.aipatrol}" stroke="#0f172a" stroke-width="1.5"/></svg>`,
  iconSize: [10, 10],
  iconAnchor: [5, 5],
})

const VERTEX_ICON = L.divIcon({
  className: 'expansion-vertex-icon',
  html: html`<svg width="10" height="10" viewBox="0 0 10 10"><rect x="1" y="1" width="8" height="8" fill="#fff" stroke="${EXPANSION_COLORS.safezone}" stroke-width="2"/></svg>`,
  iconSize: [10, 10],
  iconAnchor: [5, 5],
})

/** A layer group that lives as long as the map does. */
function useLayerGroup(map) {
  const [group, setGroup] = useState(null)
  useEffect(() => {
    if (!isMapAlive(map)) return
    const layer = L.layerGroup()
    setGroup(layer)
    return () => {
      setGroup(null)
      layer.remove()
    }
  }, [map])
  return group
}

// ---------------------------------------------------------------------------
// Safe zones — circles, cylinders and polygons in one file
// ---------------------------------------------------------------------------

export function useExpansionSafeZoneLayer(
  map,
  data,
  { enabled, scale, onSave, onError }
) {
  const group = useLayerGroup(map)
  const [ready, setReady] = useState(0)
  const save = useEventCallback(onSave)
  const fail = useEventCallback(onError)

  useEffect(() => {
    if (!isMapAlive(map) || !group || !scale) return
    group.clearLayers()

    const color = EXPANSION_COLORS.safezone
    const put = async (path, body) => {
      try {
        await save(path, body)
      } catch (err) {
        fail(err)
      }
    }

    for (const kind of ['circles', 'cylinders']) {
      for (const zone of data?.[kind] || []) {
        const isCylinder = kind === 'cylinders'
        const label = isCylinder
          ? `Expansion safe zone (cylinder, ${Math.round(zone.height || 0)} m tall)`
          : 'Expansion safe zone'
        drawCircleZone(
          group,
          { ...zone, label, tooltip: `${label} — ${Math.round(zone.radius)} m` },
          {
            scale,
            color,
            onMove: ({ lat, lng }) =>
              put(`/expansion/safezones/${zone.kind}/${zone.index}`, { lat, lng }),
            onResize: (radius) =>
              put(`/expansion/safezones/${zone.kind}/${zone.index}`, { radius }),
          }
        )
      }
    }

    for (const zone of data?.polygons || []) {
      const points = zone.points || []
      if (points.length < 3) continue
      const polygon = L.polygon(
        points.map((p) => [p.lat, p.lng]),
        { color, fillColor: color, fillOpacity: 0.1, weight: 2, dashArray: '6 4' }
      )
      bindTextTooltip(polygon, `Expansion safe zone (polygon, ${points.length} points)`, {
        direction: 'top',
        className: 'safezone-tooltip',
      })
      group.addLayer(polygon)

      points.forEach((point, pointIndex) => {
        const vertex = L.marker([point.lat, point.lng], { draggable: true, icon: VERTEX_ICON })
        bindTextTooltip(vertex, 'Drag to move this corner', {
          direction: 'right',
          offset: [6, 0],
          className: 'safezone-tooltip',
        })
        // Redraw the outline from the live marker positions while dragging —
        // setLatLngs on the whole ring keeps the shape consistent with what
        // will be saved.
        vertex.on('drag', () => {
          const ring = polygon.getLatLngs()[0].slice()
          ring[pointIndex] = vertex.getLatLng()
          polygon.setLatLngs([ring])
        })
        vertex.on('dragend', () => {
          const { lat, lng } = vertex.getLatLng()
          put(`/expansion/safezones/polygon/${zone.index}`, { point: pointIndex, lat, lng })
        })
        group.addLayer(vertex)
      })
    }

    setReady((n) => n + 1)
    return () => group.clearLayers()
  }, [map, group, data, scale, save, fail])

  useEffect(() => {
    toggleGroup(map, group, enabled)
  }, [map, group, enabled, ready])
}

// ---------------------------------------------------------------------------
// Trader zones — one circle per zone file
// ---------------------------------------------------------------------------

export function useExpansionTraderZoneLayer(
  map,
  data,
  { enabled, scale, popupHost, onPopupOpen, onPopupClose, onSave, onError }
) {
  const group = useLayerGroup(map)
  const [ready, setReady] = useState(0)
  const handlePopupOpen = useEventCallback(onPopupOpen)
  const handlePopupClose = useEventCallback(onPopupClose)
  const save = useEventCallback(onSave)
  const fail = useEventCallback(onError)

  useEffect(() => {
    if (!isMapAlive(map) || !group || !scale) return
    group.clearLayers()

    for (const zone of data?.trader_zones || []) {
      const put = async (body) => {
        try {
          await save(`/expansion/trader-zones/${encodeURIComponent(zone.id)}`, body)
        } catch (err) {
          fail(err)
        }
      }
      drawCircleZone(
        group,
        {
          ...zone,
          label: zone.name,
          tooltip: `${zone.name} — ${Math.round(zone.radius)} m, ${zone.stock_items} stock entries`,
        },
        {
          scale,
          color: EXPANSION_COLORS.traderzone,
          popupHost,
          popupMeta: { kind: 'expansion-trader-zone', id: zone.id },
          onPopupOpen: handlePopupOpen,
          onPopupClose: handlePopupClose,
          onMove: ({ lat, lng }) => put({ lat, lng }),
          onResize: (radius) => put({ radius }),
        }
      )
    }

    setReady((n) => n + 1)
    return () => group.clearLayers()
  }, [map, group, data, scale, popupHost, handlePopupOpen, handlePopupClose, save, fail])

  useEffect(() => {
    toggleGroup(map, group, enabled)
  }, [map, group, enabled, ready])
}

// ---------------------------------------------------------------------------
// Spawn points — one marker per position, grouped by location
// ---------------------------------------------------------------------------

export function useExpansionSpawnLayer(map, data, { enabled, onDragEnd }) {
  const group = useLayerGroup(map)
  const [ready, setReady] = useState(0)
  const handleDragEnd = useEventCallback(onDragEnd)

  useEffect(() => {
    if (!isMapAlive(map) || !group) return
    group.clearLayers()

    for (const location of data?.spawn_locations || []) {
      for (const point of location.positions || []) {
        const marker = L.marker([point.lat, point.lng], { draggable: true, icon: SPAWN_ICON })
        bindTextTooltip(marker, `${location.name} — spawn ${point.index + 1}`, {
          direction: 'top',
          offset: [0, -8],
          className: 'safezone-tooltip',
        })
        marker.on('dragend', () =>
          handleDragEnd(
            { location: location.index, index: point.index, name: location.name },
            marker,
            point
          )
        )
        group.addLayer(marker)
      }
    }

    setReady((n) => n + 1)
    return () => group.clearLayers()
  }, [map, group, data, handleDragEnd])

  useEffect(() => {
    toggleGroup(map, group, enabled)
  }, [map, group, enabled, ready])
}

// ---------------------------------------------------------------------------
// Missions — airdrops and contaminated areas, read-only
// ---------------------------------------------------------------------------

export function useExpansionMissionLayer(map, data, { enabled, scale }) {
  const group = useLayerGroup(map)
  const [ready, setReady] = useState(0)

  useEffect(() => {
    if (!isMapAlive(map) || !group || !scale) return
    group.clearLayers()

    for (const mission of data?.missions || []) {
      const color = EXPANSION_COLORS[mission.kind] || EXPANSION_COLORS.airdrop
      const kindLabel = mission.kind === 'airdrop' ? 'Airdrop' : 'Contaminated area'
      const state = mission.enabled ? '' : ' (disabled)'
      drawCircleZone(
        group,
        {
          ...mission,
          label: mission.name,
          tooltip: `${kindLabel}: ${mission.name}${state} — ${Math.round(mission.radius)} m`,
        },
        { scale, color, editable: false }
      )
    }

    setReady((n) => n + 1)
    return () => group.clearLayers()
  }, [map, group, data, scale])

  useEffect(() => {
    toggleGroup(map, group, enabled)
  }, [map, group, enabled, ready])
}

// ---------------------------------------------------------------------------
// AI patrols — one polyline per patrol, one draggable marker per waypoint
// ---------------------------------------------------------------------------
//
// The line IS the route: Expansion walks `Waypoints` in order, so the drawing
// is a path and not a cloud of points. The line carries the popup (it is the
// only thing that represents the patrol as a whole); the markers carry the
// dragging, which confirms like every other point drag on this map.

export function useExpansionAIPatrolLayer(
  map,
  data,
  { enabled, popupHost, onPopupOpen, onPopupClose, onDragEnd }
) {
  const group = useLayerGroup(map)
  const [ready, setReady] = useState(0)
  const handlePopupOpen = useEventCallback(onPopupOpen)
  const handlePopupClose = useEventCallback(onPopupClose)
  const handleDragEnd = useEventCallback(onDragEnd)

  useEffect(() => {
    if (!isMapAlive(map) || !group) return
    group.clearLayers()

    const color = EXPANSION_COLORS.aipatrol
    for (const patrol of data?.patrols || []) {
      const points = patrol.waypoints || []
      if (!points.length) continue

      const line = L.polyline(
        points.map((p) => [p.lat, p.lng]),
        { color, weight: 2, opacity: 0.85, dashArray: '4 4' }
      )
      bindTextTooltip(
        line,
        `${patrol.name} — ${patrol.number_of_ai} AI, ${points.length} waypoints`,
        { direction: 'top', className: 'safezone-tooltip', sticky: true }
      )
      if (popupHost && onPopupOpen) {
        line.bindPopup(() => popupHost, { maxWidth: 280, className: 'dark-popup' })
        line.on('popupopen', (e) =>
          handlePopupOpen({ kind: 'expansion-ai-patrol', index: patrol.index }, e.popup)
        )
        line.on('popupclose', () => handlePopupClose?.())
      }
      group.addLayer(line)

      points.forEach((point) => {
        const marker = L.marker([point.lat, point.lng], {
          draggable: true,
          icon: WAYPOINT_ICON,
        })
        bindTextTooltip(marker, `${patrol.name} — waypoint ${point.index + 1}`, {
          direction: 'top',
          offset: [0, -8],
          className: 'safezone-tooltip',
        })
        // Redraw the leg being dragged so the route stays readable mid-drag.
        marker.on('drag', () => {
          const ring = line.getLatLngs().slice()
          ring[point.index] = marker.getLatLng()
          line.setLatLngs(ring)
        })
        marker.on('dragend', () =>
          handleDragEnd(
            { patrol: patrol.index, index: point.index, name: patrol.name },
            marker,
            point
          )
        )
        group.addLayer(marker)
      })
    }

    setReady((n) => n + 1)
    return () => group.clearLayers()
  }, [map, group, data, popupHost, onPopupOpen, handlePopupOpen, handlePopupClose, handleDragEnd])

  useEffect(() => {
    toggleGroup(map, group, enabled)
  }, [map, group, enabled, ready])
}

// ---------------------------------------------------------------------------
// AI roaming locations — circles, plus the no-go areas in the same layer
// ---------------------------------------------------------------------------
//
// Two lists from one file, and one toggle: they are the same subject seen from
// both sides ("roam here" / "never here"). A disabled roaming location still
// draws, dimmed — it is in the file and that is worth seeing. No-go areas are
// read-only: nothing writes them, since a stock file ships none and their
// shape has only been read, never round-tripped.

export function useExpansionAILocationLayer(
  map,
  data,
  { enabled, scale, popupHost, onPopupOpen, onPopupClose, onSave, onError }
) {
  const group = useLayerGroup(map)
  const [ready, setReady] = useState(0)
  const handlePopupOpen = useEventCallback(onPopupOpen)
  const handlePopupClose = useEventCallback(onPopupClose)
  const save = useEventCallback(onSave)
  const fail = useEventCallback(onError)

  useEffect(() => {
    if (!isMapAlive(map) || !group || !scale) return
    group.clearLayers()

    for (const location of data?.roaming_locations || []) {
      const put = async (body) => {
        try {
          await save(`/expansion/ai/locations/${location.index}`, body)
        } catch (err) {
          fail(err)
        }
      }
      const state = location.enabled ? '' : ' (disabled)'
      drawCircleZone(
        group,
        {
          ...location,
          label: location.name,
          tooltip: `${location.name}${state} — ${location.type || 'no type'}, ${Math.round(
            location.radius
          )} m`,
        },
        {
          scale,
          color: EXPANSION_COLORS.airoaming,
          popupHost,
          popupMeta: { kind: 'expansion-ai-roaming', index: location.index },
          onPopupOpen: handlePopupOpen,
          onPopupClose: handlePopupClose,
          onMove: ({ lat, lng }) => put({ lat, lng }),
          onResize: (radius) => put({ radius }),
        }
      )
    }

    for (const area of data?.no_go_areas || []) {
      const height = area.height ? `, ${Math.round(area.height)} m tall` : ''
      drawCircleZone(
        group,
        {
          ...area,
          label: area.name,
          tooltip: `No-go area: ${area.name} — ${Math.round(area.radius)} m${height}`,
        },
        { scale, color: EXPANSION_COLORS.nogo, editable: false }
      )
    }

    setReady((n) => n + 1)
    return () => group.clearLayers()
  }, [map, group, data, scale, popupHost, handlePopupOpen, handlePopupClose, save, fail])

  useEffect(() => {
    toggleGroup(map, group, enabled)
  }, [map, group, enabled, ready])
}

// ---------------------------------------------------------------------------
// Position lists inside three settings files (TODO H4, 2026 Q3):
// market vehicle spawns, server map markers, base-building zones.
// Move, resize (zones) and copy; deleting stays in the Configs editor. Heights
// are never touched by a drag: zsm has no terrain heights, and what Expansion
// does with each one is in app/spawn_editor/expansion_position_state.py.
// ---------------------------------------------------------------------------

export const MARKET_KINDS = { land: 'Land', air: 'Air', water: 'Water', train: 'Train' }

/** An arrow turned to the heading (clockwise, 0 = north) around a lettered dot. */
function marketIcon(kind, heading) {
  const c = EXPANSION_COLORS.market
  return L.divIcon({
    className: 'expansion-market-icon',
    html: html`<svg width="24" height="24" viewBox="0 0 24 24"><g transform="rotate(${Number(heading) || 0} 12 12)"><path d="M12 0.5 L16 6.5 L8 6.5 Z" fill="${c}" stroke="#0f172a" stroke-width="1"/></g><circle cx="12" cy="12" r="6.5" fill="${c}" stroke="#0f172a" stroke-width="1.5"/><text x="12" y="15.2" text-anchor="middle" font-size="9" font-weight="700" font-family="sans-serif" fill="#0f172a">${MARKET_KINDS[kind][0]}</text></svg>`,
    iconSize: [24, 24],
    iconAnchor: [12, 12],
  })
}

/** A marker's own colour, ringed so a dark one still shows on the map. */
const serverMarkerIcon = (color) =>
  L.divIcon({
    className: 'expansion-server-marker-icon',
    html: html`<svg width="16" height="16" viewBox="0 0 16 16"><circle cx="8" cy="8" r="6" fill="${color}" stroke="#fff" stroke-width="2"/><circle cx="8" cy="8" r="7.5" fill="none" stroke="#0f172a" stroke-width="1"/></svg>`,
    iconSize: [16, 16],
    iconAnchor: [8, 8],
  })

/** Draggable point markers with a popup: the shape both point layers share. */
function usePointLayer(map, points, { enabled, popupHost, onPopupOpen, onPopupClose, onDragEnd }) {
  const group = useLayerGroup(map)
  const [ready, setReady] = useState(0)
  const handlePopupOpen = useEventCallback(onPopupOpen)
  const handlePopupClose = useEventCallback(onPopupClose)
  const handleDragEnd = useEventCallback(onDragEnd)

  useEffect(() => {
    if (!isMapAlive(map) || !group) return
    group.clearLayers()
    for (const point of points) {
      const marker = L.marker([point.lat, point.lng], { draggable: true, icon: point.icon })
      bindTextTooltip(marker, point.tooltip, {
        direction: 'top',
        offset: [0, -10],
        className: 'safezone-tooltip',
      })
      if (popupHost) {
        marker.bindPopup(() => popupHost, { maxWidth: 300, className: 'dark-popup' })
        marker.on('popupopen', (e) => handlePopupOpen(point.meta, e.popup))
        marker.on('popupclose', () => handlePopupClose())
      }
      marker.on('dragend', () => handleDragEnd(point.meta, marker, point))
      group.addLayer(marker)
    }
    setReady((n) => n + 1)
    return () => group.clearLayers()
  }, [map, group, points, popupHost, handlePopupOpen, handlePopupClose, handleDragEnd])

  useEffect(() => {
    toggleGroup(map, group, enabled)
  }, [map, group, enabled, ready])
}

export function useExpansionMarketSpawnLayer(map, data, options) {
  const points = useMemo(
    () =>
      Object.keys(MARKET_KINDS).flatMap((kind) =>
        (data?.[kind] || []).map((spawn) => ({
          ...spawn,
          icon: marketIcon(kind, spawn.heading),
          tooltip: `${MARKET_KINDS[kind]} vehicle spawn ${spawn.index + 1} — heading ${Math.round(
            spawn.heading
          )} deg`,
          meta: { kind: 'expansion-market-spawn', list: kind, index: spawn.index },
        }))
      ),
    [data]
  )
  usePointLayer(map, points, options)
}

export function useExpansionMapMarkerLayer(map, data, options) {
  const points = useMemo(
    () =>
      (data?.markers || []).map((marker) => ({
        ...marker,
        icon: serverMarkerIcon(marker.color),
        tooltip: `${marker.text || marker.uid} (${marker.icon || 'no icon'})`,
        meta: { kind: 'expansion-map-marker', index: marker.index },
      })),
    [data]
  )
  usePointLayer(map, points, options)
}

export function useExpansionBuildZoneLayer(
  map,
  data,
  { enabled, scale, popupHost, onPopupOpen, onPopupClose, onSave, onError }
) {
  const group = useLayerGroup(map)
  const [ready, setReady] = useState(0)
  const handlePopupOpen = useEventCallback(onPopupOpen)
  const handlePopupClose = useEventCallback(onPopupClose)
  const save = useEventCallback(onSave)
  const fail = useEventCallback(onError)

  useEffect(() => {
    if (!isMapAlive(map) || !group || !scale) return
    group.clearLayers()
    for (const zone of data?.zones || []) {
      const put = async (body) => {
        try {
          await save(`/expansion/build-zones/${zone.index}`, body)
        } catch (err) {
          fail(err)
        }
      }
      const rule = zone.whitelist ? 'only' : 'not'
      drawCircleZone(
        group,
        {
          ...zone,
          label: zone.name,
          tooltip: `${zone.name} — ${Math.round(zone.radius)} m, ${zone.items.length} items (${rule})`,
        },
        {
          scale,
          color: EXPANSION_COLORS.buildzone,
          popupHost,
          popupMeta: { kind: 'expansion-build-zone', index: zone.index },
          onPopupOpen: handlePopupOpen,
          onPopupClose: handlePopupClose,
          onMove: ({ lat, lng }) => put({ lat, lng }),
          onResize: (radius) => put({ radius }),
        }
      )
    }
    setReady((n) => n + 1)
    return () => group.clearLayers()
  }, [map, group, data, scale, popupHost, handlePopupOpen, handlePopupClose, save, fail])

  useEffect(() => {
    toggleGroup(map, group, enabled)
  }, [map, group, enabled, ready])
}
