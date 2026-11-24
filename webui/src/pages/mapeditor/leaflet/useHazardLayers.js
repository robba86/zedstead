// The Hazard zones family (plans/48 C, D2): every zone that hurts players where
// they stand, on one drawing path.
//
//   - Contaminated areas (amber): the mission's static areas from
//     cfgEffectArea.json. Vanilla, and until this layer they had no map at all.
//   - RadZone zones (lime): circles, or with IsSquareZone a rectangle between
//     two corners. A circle moves and resizes like every other zone circle
//     (`drawCircleZone`); a rectangle only moves, as a whole — its corners are
//     edited in the RadZone Zones table.
//   - RadZone loot crates: fixed markers. Their positions live in a nested list
//     the map does not edit.
//   - ToxicZone (purple): static zones drawn exactly like RadZone's, their
//     crates as fixed markers, and **moving zones**: a dashed path from a start
//     grip to an end grip, with the zone's size drawn at both ends. Dragging an
//     end writes that point; the server recomputes the path the mod needs in
//     the same write (D3), so nothing else is edited here.
//
// Every write goes through `onSave(path, body)`; the server patches the one
// entry it names and broadcasts `hazards_changed`, which reloads this data.

import { useEffect, useState } from 'react'
import L from 'leaflet'
import { isMapAlive } from './useLeafletMap'
import { useEventCallback } from '../useEventCallback'
import { drawCircleZone, toggleGroup } from './circleZone'
import { bindTextTooltip } from './textTooltip'
import { html } from './html'

export const HAZARD_COLORS = {
  contaminated: '#f59e0b',
  radzone: '#a3e635',
  crate: '#65a30d',
  toxiczone: '#c084fc',
  toxicCrate: '#9333ea',
}

const squareCentreIcon = (color) =>
  L.divIcon({
    className: 'safezone-center-icon',
    html: html`<svg width="12" height="12" viewBox="0 0 12 12"><rect x="1.5" y="1.5" width="9" height="9" fill="${color}" stroke="#0f172a" stroke-width="1.5"/></svg>`,
    iconSize: [12, 12],
    iconAnchor: [6, 6],
  })

const crateIcon = (color) =>
  L.divIcon({
    className: 'hazard-crate-icon',
    html: html`<svg width="9" height="9" viewBox="0 0 9 9"><rect x="1" y="1" width="7" height="7" fill="${color}" stroke="#0f172a" stroke-width="1.2"/></svg>`,
    iconSize: [9, 9],
    iconAnchor: [4.5, 4.5],
  })

// A moving zone's ends: filled where it starts, hollow where it ends.
const pathEndIcon = (color, filled) =>
  L.divIcon({
    className: 'safezone-center-icon',
    html: html`<svg width="14" height="14" viewBox="0 0 14 14"><circle cx="7" cy="7" r="5.5" fill="${
      filled ? color : '#0f172a'
    }" stroke="${filled ? '#0f172a' : color}" stroke-width="2"/></svg>`,
    iconSize: [14, 14],
    iconAnchor: [7, 7],
  })

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

/** A square zone (RadZone, ToxicZone): the rectangle and a grip that moves it whole. */
function drawSquareZone(group, zone, { color, label, tooltip, onMove }) {
  const rect = L.rectangle(
    [
      [zone.sw.lat, zone.sw.lng],
      [zone.ne.lat, zone.ne.lng],
    ],
    {
      color,
      fillColor: color,
      fillOpacity: 0.1,
      weight: 2,
      dashArray: '6 4',
    }
  )
  bindTextTooltip(rect, tooltip, { direction: 'top', className: 'safezone-tooltip' })
  group.addLayer(rect)
  if (!zone.corners_set) return

  const start = L.latLng(zone.centre.lat, zone.centre.lng)
  const grip = L.marker(start, { draggable: true, icon: squareCentreIcon(color) })
  bindTextTooltip(grip, `${label} (drag to move)`, {
    direction: 'top',
    offset: [0, -8],
    className: 'safezone-tooltip',
  })
  const bounds = rect.getBounds()
  grip.on('drag', () => {
    const at = grip.getLatLng()
    const dLat = at.lat - start.lat
    const dLng = at.lng - start.lng
    rect.setBounds([
      [bounds.getSouth() + dLat, bounds.getWest() + dLng],
      [bounds.getNorth() + dLat, bounds.getEast() + dLng],
    ])
  })
  grip.on('dragend', () => onMove(grip.getLatLng()))
  group.addLayer(grip)
}

/** A RadZone-shaped zone (RadZone's, ToxicZone's static ones): circle or square. */
function drawZone(group, zone, { color, tooltip, scale, path, put }) {
  const label = zone.name || `Zone ${zone.index + 1}`
  const text = tooltip(label)
  const onMove = ({ lat, lng }) => put(`${path}/${zone.index}`, { lat, lng })
  if (zone.shape === 'square') {
    drawSquareZone(group, zone, {
      color,
      label,
      tooltip: zone.corners_set ? text : `${text} — corners not set`,
      onMove,
    })
    return
  }
  drawCircleZone(
    group,
    { lat: zone.lat, lng: zone.lng, radius: zone.radius, label, tooltip: text },
    {
      scale,
      color,
      onMove,
      onResize: (radius) => put(`${path}/${zone.index}`, { radius }),
    }
  )
}

function drawCrates(group, crates, { color, off, what }) {
  for (const crate of crates || []) {
    const marker = L.marker([crate.lat, crate.lng], { icon: crateIcon(color), interactive: true })
    bindTextTooltip(
      marker,
      `${crate.area} crate ${crate.index + 1}${crate.loot ? ` — ${crate.loot}` : ''}${off ? ` (${what} off)` : ''}`,
      { direction: 'top', offset: [0, -6], className: 'safezone-tooltip' }
    )
    group.addLayer(marker)
  }
}

/** What the checks say about a moving zone, in its tooltip. */
function movingProblem(zone) {
  if (zone.north_south) return 'runs straight north-south, which ToxicZone cannot move along'
  if (zone.backwards) return 'moves away from its end — drag an end or save it in the Moving Zones editor'
  if (zone.stale) return 'path out of date — drag an end or save it in the Moving Zones editor'
  return ''
}

/**
 * A ToxicZone moving zone: the path, the zone's size at both ends, and a grip
 * on each end. Dragging a grip redraws its end live and saves on dragend.
 */
function drawMovingZone(group, zone, { scale, active, onMoveEnd }) {
  const color = HAZARD_COLORS.toxiczone
  const label = zone.name || `Moving zone ${zone.index + 1}`
  const problem = movingProblem(zone)
  const tooltip = `${label} — ToxicZone moving zone, ${Math.round(zone.radius)} m, ${zone.speed} m/min${
    active ? '' : ' (moving zones off)'
  }${problem ? ` — ${problem}` : ''}`
  const start = L.latLng(zone.start.lat, zone.start.lng)
  const end = L.latLng(zone.end.lat, zone.end.lng)
  const radius = (zone.radius || 0) / scale

  const line = L.polyline([start, end], {
    color: problem ? '#ef4444' : color,
    weight: 3,
    dashArray: '8 6',
  })
  bindTextTooltip(line, tooltip, { direction: 'top', className: 'safezone-tooltip', sticky: true })
  const ring = (at, opacity) =>
    L.circle(at, {
      radius,
      color,
      fillColor: color,
      fillOpacity: opacity,
      weight: 1,
      interactive: false,
    })
  const startRing = ring(start, 0.12)
  const endRing = ring(end, 0.04)
  group.addLayer(line)
  group.addLayer(startRing)
  group.addLayer(endRing)

  for (const [point, at, ringLayer] of [
    ['start', start, startRing],
    ['end', end, endRing],
  ]) {
    const grip = L.marker(at, { draggable: true, icon: pathEndIcon(color, point === 'start') })
    bindTextTooltip(grip, `${label}: ${point} (drag to move)`, {
      direction: 'top',
      offset: [0, -8],
      className: 'safezone-tooltip',
    })
    grip.on('drag', () => {
      const moved = grip.getLatLng()
      ringLayer.setLatLng(moved)
      line.setLatLngs(point === 'start' ? [moved, line.getLatLngs()[1]] : [line.getLatLngs()[0], moved])
    })
    grip.on('dragend', () => onMoveEnd(point, grip.getLatLng()))
    group.addLayer(grip)
  }
}

export function useHazardLayers(
  map,
  hazards,
  { contaminated, radzone, crates, toxiczone, toxicmoving, toxiccrates, scale, onSave, onError }
) {
  const areaGroup = useLayerGroup(map)
  const zoneGroup = useLayerGroup(map)
  const crateGroup = useLayerGroup(map)
  const toxicGroup = useLayerGroup(map)
  const movingGroup = useLayerGroup(map)
  const toxicCrateGroup = useLayerGroup(map)
  const [ready, setReady] = useState(0)
  const save = useEventCallback(onSave)
  const fail = useEventCallback(onError)

  useEffect(() => {
    const groups = [areaGroup, zoneGroup, crateGroup, toxicGroup, movingGroup, toxicCrateGroup]
    if (!isMapAlive(map) || groups.some((g) => !g) || !scale) return
    for (const g of groups) g.clearLayers()

    const put = async (path, body) => {
      try {
        await save(path, body)
      } catch (err) {
        fail(err)
      }
    }

    for (const area of hazards?.contaminated || []) {
      const label = area.name || `Area ${area.index + 1}`
      drawCircleZone(
        areaGroup,
        {
          lat: area.lat,
          lng: area.lng,
          radius: area.radius,
          label,
          tooltip: `${label} — contaminated area, ${Math.round(area.radius)} m`,
        },
        {
          scale,
          color: HAZARD_COLORS.contaminated,
          onMove: ({ lat, lng }) => put(`/hazards/contaminated/${area.index}`, { lat, lng }),
          onResize: (radius) => put(`/hazards/contaminated/${area.index}`, { radius }),
        }
      )
    }

    const shape = (zone) => (zone.shape === 'square' ? ', square' : `, ${Math.round(zone.radius)} m`)

    for (const zone of hazards?.radzone?.zones || []) {
      drawZone(zoneGroup, zone, {
        color: HAZARD_COLORS.radzone,
        tooltip: (label) => `${label} — RadZone, radiation ${zone.radiation}${shape(zone)}`,
        scale,
        path: '/hazards/radzone',
        put,
      })
    }
    drawCrates(crateGroup, hazards?.radzone?.crates, {
      color: HAZARD_COLORS.crate,
      off: hazards?.radzone && !hazards.radzone.crates_active,
      what: 'crate system',
    })

    const toxic = hazards?.toxiczone
    for (const zone of toxic?.zones || []) {
      drawZone(toxicGroup, zone, {
        color: HAZARD_COLORS.toxiczone,
        tooltip: (label) =>
          `${label} — ToxicZone${shape(zone)}${toxic.static_active ? '' : ' (static zones off)'}`,
        scale,
        path: '/hazards/toxiczone',
        put,
      })
    }
    for (const zone of toxic?.moving || []) {
      drawMovingZone(movingGroup, zone, {
        scale,
        active: toxic.moving_active,
        onMoveEnd: (point, { lat, lng }) =>
          put(`/hazards/toxiczone-moving/${zone.index}`, { point, lat, lng }),
      })
    }
    drawCrates(toxicCrateGroup, toxic?.crates, { color: HAZARD_COLORS.toxicCrate })

    setReady((n) => n + 1)
    return () => {
      for (const g of groups) g.clearLayers()
    }
  }, [
    map,
    areaGroup,
    zoneGroup,
    crateGroup,
    toxicGroup,
    movingGroup,
    toxicCrateGroup,
    hazards,
    scale,
    save,
    fail,
  ])

  useEffect(() => {
    toggleGroup(map, areaGroup, contaminated)
    toggleGroup(map, zoneGroup, radzone)
    toggleGroup(map, crateGroup, crates)
    toggleGroup(map, toxicGroup, toxiczone)
    toggleGroup(map, movingGroup, toxicmoving)
    toggleGroup(map, toxicCrateGroup, toxiccrates)
  }, [
    map,
    areaGroup,
    zoneGroup,
    crateGroup,
    toxicGroup,
    movingGroup,
    toxicCrateGroup,
    contaminated,
    radzone,
    crates,
    toxiczone,
    toxicmoving,
    toxiccrates,
    ready,
  ])
}
