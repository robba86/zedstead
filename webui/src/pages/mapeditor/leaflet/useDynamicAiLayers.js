// Dynamic AI's positions (plans/47 D, decision D3), from the mod's
// SpatialSettings.json:
//
//   - Points (fuchsia; emerald for a Safe point, where nothing spawns): a circle
//     that moves and resizes like every other zone circle.
//   - Locations (cyan) and Audio (yellow): the trigger circle, plus a marker per
//     spawn position joined to it by a thin line. The trigger moves and resizes;
//     each spawn position is dragged on its own. The popup's "Move here…" moves
//     the trigger and every spawn position together.
//
// New entries are copies only (the popup's "Copy here…"), never a map click out
// of nothing: faction, loadouts and counts have to come from an entry that
// already works. Heights are the file's and stay as they are; a top-down drag
// has no third axis.
//
// A fresh install puts every entry at [0, 1, 0], the mod's placeholder, which
// is the map's bottom-left corner. They are drawn there, and the tooltip says
// why, rather than hidden: dragging one out of the corner is how it gets placed.
//
// Every write goes through `onSave(path, body)`; the server patches the one
// entry and broadcasts `dynamic_ai_changed`, which reloads this data.

import { useEffect, useState } from 'react'
import L from 'leaflet'
import { isMapAlive } from './useLeafletMap'
import { useEventCallback } from '../useEventCallback'
import { drawCircleZone, toggleGroup } from './circleZone'
import { bindTextTooltip } from './textTooltip'
import { html } from './html'

export const DYNAMIC_AI_COLORS = {
  point: '#e879f9',
  safe: '#10b981',
  location: '#22d3ee',
  audio: '#facc15',
}

const LIST_NAMES = { points: 'points', locations: 'locations', audio: 'audio locations' }

const spawnIcon = (color) =>
  L.divIcon({
    className: 'dynai-spawn-icon',
    html: html`<svg width="11" height="11" viewBox="0 0 11 11"><path d="M5.5 1 10 5.5 5.5 10 1 5.5Z" fill="${color}" stroke="#0f172a" stroke-width="1.4"/></svg>`,
    iconSize: [11, 11],
    iconAnchor: [5.5, 5.5],
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

/** The tooltip's tail: whether the list is on, and whether the entry was ever placed. */
function notes(section, entry, modes) {
  const off = modes && !modes[section] ? ` (${LIST_NAMES[section]} off)` : ''
  const unplaced = entry.unplaced
    ? " — at 0, 0, the mod's placeholder: drag it to where it belongs"
    : ''
  return `${off}${unplaced}`
}

function aiCount(entry) {
  return `${entry.faction || 'no faction'}, ${entry.min_count}–${entry.max_count} AI`
}

/** A location or audio entry: trigger circle, spawn markers, and a line to each. */
function drawLocation(group, entry, { section, color, scale, modes, popup, put }) {
  const label = entry.name
  const kind = section === 'audio' ? 'audio location' : 'location'
  const circle = drawCircleZone(
    group,
    {
      lat: entry.trigger.lat,
      lng: entry.trigger.lng,
      radius: entry.radius,
      label: `${label} trigger`,
      tooltip: `${label} — Dynamic AI ${kind}, ${aiCount(entry)}, trigger ${Math.round(
        entry.radius
      )} m, ${entry.spawns.length} spawn position${entry.spawns.length === 1 ? '' : 's'}${notes(
        section,
        entry,
        modes
      )}`,
    },
    {
      scale,
      color,
      ...popup({ kind: 'dynamic-ai', section, index: entry.index }),
      onMove: ({ lat, lng }) => put(`/dynamic-ai/${section}/${entry.index}`, { lat, lng }),
      onResize: (radius) => put(`/dynamic-ai/${section}/${entry.index}`, { radius }),
    }
  )

  for (const spawn of entry.spawns) {
    const line = L.polyline([circle.getLatLng(), [spawn.lat, spawn.lng]], {
      color,
      weight: 1,
      opacity: 0.7,
      dashArray: '3 4',
      interactive: false,
    })
    // The trigger's centre grip moves the circle; the lines follow it.
    circle.on('move', () => line.setLatLngs([circle.getLatLng(), line.getLatLngs()[1]]))
    group.addLayer(line)

    const marker = L.marker([spawn.lat, spawn.lng], { draggable: true, icon: spawnIcon(color) })
    bindTextTooltip(
      marker,
      `${label}: spawn position ${spawn.index + 1} (height ${spawn.game_y} m, kept when moved) — drag to move`,
      { direction: 'top', offset: [0, -6], className: 'safezone-tooltip' }
    )
    marker.on('drag', () => line.setLatLngs([line.getLatLngs()[0], marker.getLatLng()]))
    marker.on('dragend', () => {
      const { lat, lng } = marker.getLatLng()
      put(`/dynamic-ai/${section}/${entry.index}`, { spawn: spawn.index, lat, lng })
    })
    group.addLayer(marker)
  }
}

export function useDynamicAiLayers(
  map,
  data,
  { points, locations, audio, scale, popupHost, onPopupOpen, onPopupClose, onSave, onError }
) {
  const pointGroup = useLayerGroup(map)
  const locationGroup = useLayerGroup(map)
  const audioGroup = useLayerGroup(map)
  const [ready, setReady] = useState(0)
  const save = useEventCallback(onSave)
  const fail = useEventCallback(onError)
  const handlePopupOpen = useEventCallback(onPopupOpen)
  const handlePopupClose = useEventCallback(onPopupClose)

  useEffect(() => {
    const groups = [pointGroup, locationGroup, audioGroup]
    if (!isMapAlive(map) || groups.some((g) => !g) || !scale) return
    for (const g of groups) g.clearLayers()

    const put = async (path, body) => {
      try {
        await save(path, body)
      } catch (err) {
        fail(err)
      }
    }
    const popup = (popupMeta) =>
      popupHost
        ? {
            popupHost,
            popupMeta,
            onPopupOpen: handlePopupOpen,
            onPopupClose: handlePopupClose,
          }
        : {}
    const modes = data?.modes

    for (const point of data?.points || []) {
      const what = point.safe ? 'no-spawn zone' : aiCount(point)
      drawCircleZone(
        pointGroup,
        {
          lat: point.lat,
          lng: point.lng,
          radius: point.radius,
          label: point.name,
          tooltip: `${point.name} — Dynamic AI point, ${what}, ${Math.round(point.radius)} m${notes(
            'points',
            point,
            modes
          )}`,
        },
        {
          scale,
          color: point.safe ? DYNAMIC_AI_COLORS.safe : DYNAMIC_AI_COLORS.point,
          ...popup({ kind: 'dynamic-ai', section: 'points', index: point.index }),
          onMove: ({ lat, lng }) => put(`/dynamic-ai/points/${point.index}`, { lat, lng }),
          onResize: (radius) => put(`/dynamic-ai/points/${point.index}`, { radius }),
        }
      )
    }

    for (const [section, group, color] of [
      ['locations', locationGroup, DYNAMIC_AI_COLORS.location],
      ['audio', audioGroup, DYNAMIC_AI_COLORS.audio],
    ]) {
      for (const entry of data?.[section] || []) {
        drawLocation(group, entry, { section, color, scale, modes, popup, put })
      }
    }

    setReady((n) => n + 1)
    return () => {
      for (const g of groups) g.clearLayers()
    }
  }, [
    map,
    pointGroup,
    locationGroup,
    audioGroup,
    data,
    scale,
    popupHost,
    handlePopupOpen,
    handlePopupClose,
    save,
    fail,
  ])

  useEffect(() => {
    toggleGroup(map, pointGroup, points)
    toggleGroup(map, locationGroup, locations)
    toggleGroup(map, audioGroup, audio)
  }, [map, pointGroup, locationGroup, audioGroup, points, locations, audio, ready])
}
