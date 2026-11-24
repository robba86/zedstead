// Underground triggers — `cfgundergroundtriggers.json`, vanilla map data.
//
// Each trigger is a box that decides how dark it is inside it; its breadcrumbs
// are the points along the way in that fade the darkness rather than switching
// it. The box is drawn with `drawBoxZone` because the extent is a size in
// X/Y/Z, not a radius, and the breadcrumbs as small markers on the path.
//
// The colour says the type, which is the one thing about a trigger you cannot
// read off its shape: teal for outer (the lit end), indigo for inner (full
// dark) and violet for the transitional boxes between them. The type itself is
// derived server-side from the spec's two rules — see
// `app/spawn_editor/underground_state.py` — so nothing here decides it.
//
// Chernarus ships an empty file, so a blank layer is the normal case on the map
// most people run; Sakhal is the one with thirty of these.

import { useEffect, useState } from 'react'
import L from 'leaflet'
import { isMapAlive } from './useLeafletMap'
import { useEventCallback } from '../useEventCallback'
import { drawBoxZone } from './boxZone'
import { toggleGroup } from './circleZone'
import { bindTextTooltip } from './textTooltip'
import { html } from './html'

export const UNDERGROUND_COLORS = {
  outer: '#2dd4bf',
  transitional: '#a78bfa',
  inner: '#6366f1',
  breadcrumb: '#fbbf24',
}

const breadcrumbIcon = L.divIcon({
  className: 'underground-breadcrumb-icon',
  html: html`<svg width="9" height="9" viewBox="0 0 9 9"><circle cx="4.5" cy="4.5" r="3.5" fill="${UNDERGROUND_COLORS.breadcrumb}" stroke="#0f172a" stroke-width="1.5"/></svg>`,
  iconSize: [9, 9],
  iconAnchor: [4.5, 4.5],
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

/** How dark it is in there, in the words the spec uses. */
function lightLabel(value) {
  if (value >= 1) return 'normal vision'
  if (value <= 0) return 'full darkness'
  return `${Math.round(value * 100)}% vision`
}

export function useUndergroundLayer(
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

    for (const trigger of data?.triggers || []) {
      const put = async (path, body) => {
        try {
          await save(path, body)
        } catch (err) {
          fail(err)
        }
      }
      const color = UNDERGROUND_COLORS[trigger.type] || UNDERGROUND_COLORS.inner
      const crumbs = trigger.breadcrumbs?.length
        ? `, ${trigger.breadcrumbs.length} breadcrumb${trigger.breadcrumbs.length === 1 ? '' : 's'}`
        : ''

      drawBoxZone(
        group,
        {
          lat: trigger.lat,
          lng: trigger.lng,
          sizeX: trigger.size_x,
          sizeZ: trigger.size_z,
          yaw: trigger.yaw,
          label: trigger.name,
          tooltip: `${trigger.name} — ${trigger.type}, ${lightLabel(
            trigger.eye_accommodation
          )}${crumbs}`,
        },
        {
          scale,
          color,
          popupHost,
          popupMeta: { kind: 'underground-trigger', index: trigger.index },
          onPopupOpen: handlePopupOpen,
          onPopupClose: handlePopupClose,
          onMove: ({ lat, lng }) => put(`/underground-triggers/${trigger.index}`, { lat, lng }),
        }
      )

      for (const crumb of trigger.breadcrumbs || []) {
        const marker = L.marker([crumb.lat, crumb.lng], {
          draggable: true,
          icon: breadcrumbIcon,
        })
        const reach = crumb.radius < 0 ? 'default reach' : `${Math.round(crumb.radius)} m`
        bindTextTooltip(
          marker,
          `Breadcrumb ${crumb.index} — ${lightLabel(crumb.eye_accommodation)}, ${reach}`,
          { direction: 'top', offset: [0, -8], className: 'safezone-tooltip' }
        )
        marker.on('dragend', () => {
          const { lat, lng } = marker.getLatLng()
          put(`/underground-triggers/${trigger.index}/breadcrumbs/${crumb.index}`, { lat, lng })
        })
        group.addLayer(marker)
      }
    }

    setReady((n) => n + 1)
    return () => group.clearLayers()
  }, [map, group, data, scale, popupHost, handlePopupOpen, handlePopupClose, save, fail])

  useEffect(() => {
    toggleGroup(map, group, enabled)
  }, [map, group, enabled, ready])
}
