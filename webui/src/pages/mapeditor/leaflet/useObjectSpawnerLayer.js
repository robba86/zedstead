// Objects layer: Zedstead's own Object Spawner files (zsm-objects.json, and the
// disabled objects' zsm-objects-disabled.json), the objects the Items tab
// placed for good (app/spawn_editor/object_spawner.py).
//
// Each marker is the item's own glyph (components/ItemIcon.jsx, the same one
// the lists show) on a square in OBJECT_COLOR. A disabled object is drawn
// faded, the selected one with a ring. A click selects the object, and the
// Items tab shows it in its Placed objects list (`onSelect`). The object whose
// Edit form is open (`editing`, ui.objectEdit) is drawn where the form puts
// it and is draggable, and a drop reports its lat/lng (`onDrag`). The colour lives
// only here, like Expansion's layer colours: nothing in mapeditor.css paints it.

import { useEffect, useRef, useState } from 'react'
import L from 'leaflet'
import { isMapAlive } from './useLeafletMap'
import { toggleGroup } from './circleZone'
import { bindTextTooltip } from './textTooltip'
import { html, raw } from './html'
import { glyphSvgPaths } from './badges'
import { itemIconComponent, useItemCategories } from '../../../components/ItemIcon.jsx'

export const OBJECT_COLOR = '#38bdf8'

const SIZE = 22

/** The divIcon for one object: its item glyph, faded when disabled, ringed when selected. */
function objectIcon(name, category, { disabled, selected }) {
  const glyph = glyphSvgPaths(itemIconComponent(category, name))
  const paths = glyph.paths.map((d) => html`<path d="${d}"/>`).join('')
  const ring = selected ? html`<rect x="0.5" y="0.5" width="21" height="21" rx="5" fill="none" stroke="#f8fafc" stroke-width="1.5"/>` : ''
  return L.divIcon({
    className: `object-spawner-icon${disabled ? ' is-disabled' : ''}`,
    html: html`<svg width="${SIZE}" height="${SIZE}" viewBox="0 0 22 22" opacity="${disabled ? 0.45 : 1}"><rect x="2.5" y="2.5" width="17" height="17" rx="4" fill="${OBJECT_COLOR}" stroke="#0f172a" stroke-width="1.5"/>${raw(ring)}<svg x="5" y="5" width="12" height="12" viewBox="0 0 ${glyph.size} ${glyph.size}" fill="#0f172a">${raw(paths)}</svg></svg>`,
    iconSize: [SIZE, SIZE],
    iconAnchor: [SIZE / 2, SIZE / 2],
  })
}

/** The key one object goes by in `selectedObject`: its file and its index there. */
export function objectKey(obj) {
  return `${obj.disabled ? 'off' : 'on'}:${obj.index}`
}

/**
 * Draws *data*.objects (GET /object-spawner) while *enabled*. *selected* is
 * the selected object's key (`objectKey`), and *onSelect(obj)* runs on a click.
 */
export function useObjectSpawnerLayer(map, data, { enabled, selected, onSelect, editing, onDrag }) {
  const [group, setGroup] = useState(null)
  const [ready, setReady] = useState(0)
  const categories = useItemCategories()
  // The latest handler, so a parent render never redraws every marker.
  const onSelectRef = useRef(onSelect)
  onSelectRef.current = onSelect
  const onDragRef = useRef(onDrag)
  onDragRef.current = onDrag

  useEffect(() => {
    if (!isMapAlive(map)) return
    const layer = L.layerGroup()
    setGroup(layer)
    return () => {
      setGroup(null)
      layer.remove()
    }
  }, [map])

  useEffect(() => {
    if (!isMapAlive(map) || !group) return
    group.clearLayers()
    for (const obj of data?.objects || []) {
      const category = categories?.[obj.name.toLowerCase()] ?? 'other'
      const edited = editing?.key === objectKey(obj)
      const at = edited && editing.lat != null ? [editing.lat, editing.lng] : [obj.lat, obj.lng]
      const marker = L.marker(at, {
        icon: objectIcon(obj.name, category, {
          disabled: obj.disabled,
          selected: edited || objectKey(obj) === selected,
        }),
        keyboard: false,
        draggable: edited,
        zIndexOffset: edited || objectKey(obj) === selected ? 1000 : 0,
      })
      if (edited) {
        marker.on('dragend', () => {
          const { lat, lng } = marker.getLatLng()
          onDragRef.current?.({ lat, lng })
        })
      }
      bindTextTooltip(
        marker,
        `${obj.name}${obj.disabled ? ' (disabled)' : ''} — Object Spawner, X ${obj.x.toFixed(1)} Y ${obj.y.toFixed(1)} Z ${obj.z.toFixed(1)}`,
        { direction: 'top', offset: [0, -12] }
      )
      marker.on('click', (e) => {
        L.DomEvent.stopPropagation(e)
        onSelectRef.current?.(obj)
      })
      group.addLayer(marker)
    }
    setReady((n) => n + 1)
    return () => group.clearLayers()
  }, [map, group, data, categories, selected, editing])

  useEffect(() => {
    toggleGroup(map, group, enabled)
  }, [map, group, enabled, ready])
}
