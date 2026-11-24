// Ctrl+drag box selection, group move and bulk delete.
//
// Ported from the vanilla editor's selection.js, near-verbatim on purpose: the
// mouse handling (capture-phase mousedown on the map container, document-level
// mousemove/mouseup so a drag that leaves the map still ends) is fiddly and
// already correct.
//
// What is React-shaped here:
//
//   - The selected set is a ref, not state. A box drag fires mousemove at
//     screen rate; re-rendering the tree on each one would be miserable. Only
//     the COUNT is state, and that is what drives SelectionBar.
//   - Selection highlight stays `marker._icon.classList.add('spawn-selected')`.
//     It is a Leaflet-owned DOM node inside a divIcon; React has no business
//     reconciling it, and toggling a class is the cheapest thing that works.
//   - Layers register themselves as selection sources instead of the selector
//     reaching into a global appState for four named layer-group variables.
//     Phase 4's trader and garage layers register the same way.
//
// Every selectable marker carries `save(lat,lng)` / `del()` on its registry
// meta, so this module never needs to know which endpoint a marker belongs to.

import { useMemo, useRef, useState } from 'react'
import L from 'leaflet'
import { collectWithMeta, getMeta } from './markerRegistry'
import { isMapAlive } from './useLeafletMap'

const SELECTED_CLASS = 'spawn-selected'

// Below this the "drag" was a click; selecting on it would be infuriating.
const MIN_BOX_PX = 5

export function useSelection({ confirm, pushToast }) {
  const [count, setCount] = useState(0)
  const [progress, setProgress] = useState(null) // null | {action, done, total}

  const selectedRef = useRef(new Set())
  const originsRef = useRef(null)
  const sourcesRef = useRef(new Set())
  const mapRef = useRef(null)
  const boxRef = useRef({ active: false, start: null, rect: null })

  // Latest confirm/toast without rebuilding the controller.
  const depsRef = useRef({ confirm, pushToast })
  depsRef.current = { confirm, pushToast }

  const selection = useMemo(() => {
    const sync = () => setCount(selectedRef.current.size)

    const clearVisuals = () => {
      for (const marker of selectedRef.current) {
        marker._icon?.classList.remove(SELECTED_CLASS)
      }
      selectedRef.current.clear()
      originsRef.current = null
    }

    const selectInBounds = (bounds, addToExisting) => {
      if (!addToExisting) clearVisuals()
      for (const group of sourcesRef.current) {
        const map = mapRef.current
        // A layer group that is switched off must not be selectable — the
        // markers still exist, they are just not on the map.
        if (!map || !map.hasLayer(group)) continue
        for (const { layer } of collectWithMeta(group)) {
          if (typeof layer.getLatLng !== 'function') continue
          if (!bounds.contains(layer.getLatLng())) continue
          selectedRef.current.add(layer)
          layer._icon?.classList.add(SELECTED_CLASS)
        }
      }
      sync()
    }

    const restoreOrigins = (markers = selectedRef.current) => {
      if (!originsRef.current) return
      for (const marker of markers) {
        const origin = originsRef.current.get(marker)
        if (origin) marker.setLatLng([origin.lat, origin.lng])
      }
    }

    // A marker whose meta sets `batchSkip` (a mod's spawn position, which is
    // changed only by copying the mod's file first, one question per
    // position) is left out of a group move or delete, and the toast says so.
    const splitSkipped = (markers) => {
      const kept = []
      const skipped = []
      for (const marker of markers) (getMeta(marker)?.batchSkip ? skipped : kept).push(marker)
      return { kept, skipped }
    }
    const skippedNote = (n, verb) =>
      `${n} selected position${n === 1 ? '' : 's'} from a mod ${n === 1 ? 'was' : 'were'} not ${verb}: ` +
      `a mod's position is ${verb} one at a time, after copying the mod's file into a manual XML file.`

    return {
      /** Bind the box-select handlers. Returns the detach function. */
      attach(map) {
        if (!isMapAlive(map)) return () => {}
        mapRef.current = map
        const container = map.getContainer()

        const onDown = (e) => {
          if (!e.ctrlKey || e.button !== 0) return
          if (e.target.closest('.leaflet-popup, .leaflet-control, .me-sidebar')) return
          e.stopPropagation()
          e.preventDefault()
          map.dragging.disable()

          const start = map.mouseEventToLatLng(e)
          boxRef.current = {
            active: true,
            start,
            rect: L.rectangle([start, start], {
              color: '#4fc3f7',
              weight: 1,
              fillOpacity: 0.15,
              dashArray: '4 4',
              interactive: false,
            }).addTo(map),
          }
        }

        const onMove = (e) => {
          const box = boxRef.current
          if (!box.active || !box.rect) return
          box.rect.setBounds(L.latLngBounds(box.start, map.mouseEventToLatLng(e)))
        }

        const onUp = (e) => {
          const box = boxRef.current
          if (!box.active) return
          boxRef.current = { active: false, start: null, rect: null }
          map.dragging.enable()
          if (!box.rect) return

          const bounds = box.rect.getBounds()
          box.rect.remove()

          const ne = map.latLngToContainerPoint(bounds.getNorthEast())
          const sw = map.latLngToContainerPoint(bounds.getSouthWest())
          if (Math.abs(ne.x - sw.x) < MIN_BOX_PX && Math.abs(ne.y - sw.y) < MIN_BOX_PX) return

          selectInBounds(bounds, e.shiftKey)
        }

        const onKey = (e) => {
          if (e.key === 'Escape' && selectedRef.current.size > 0) {
            clearVisuals()
            sync()
          }
        }

        container.addEventListener('mousedown', onDown, true)
        document.addEventListener('mousemove', onMove)
        document.addEventListener('mouseup', onUp)
        document.addEventListener('keydown', onKey)

        return () => {
          container.removeEventListener('mousedown', onDown, true)
          document.removeEventListener('mousemove', onMove)
          document.removeEventListener('mouseup', onUp)
          document.removeEventListener('keydown', onKey)
          boxRef.current.rect?.remove()
          boxRef.current = { active: false, start: null, rect: null }
          mapRef.current = null
        }
      },

      /** Register a layer group whose markers can be box-selected. */
      addSource(group) {
        sourcesRef.current.add(group)
        return () => sourcesRef.current.delete(group)
      },

      isSelected(marker) {
        return selectedRef.current.has(marker)
      },

      clear() {
        clearVisuals()
        sync()
      },

      /** A layer rebuild invalidated every marker reference we were holding. */
      reset() {
        selectedRef.current.clear()
        originsRef.current = null
        sync()
      },

      dragStart() {
        const origins = new Map()
        for (const marker of selectedRef.current) origins.set(marker, { ...marker.getLatLng() })
        originsRef.current = origins
      },

      dragMove(dragged) {
        const origins = originsRef.current
        const origin = origins?.get(dragged)
        if (!origin) return
        const current = dragged.getLatLng()
        const dLat = current.lat - origin.lat
        const dLng = current.lng - origin.lng
        for (const marker of selectedRef.current) {
          if (marker === dragged) continue
          const o = origins.get(marker)
          if (o) marker.setLatLng([o.lat + dLat, o.lng + dLng])
        }
      },

      async dragEnd(dragged) {
        const origins = originsRef.current
        const origin = origins?.get(dragged)
        if (!origin) return
        const current = dragged.getLatLng()
        const dLat = current.lat - origin.lat
        const dLng = current.lng - origin.lng
        const { kept: markers, skipped } = splitSkipped([...selectedRef.current])
        if (skipped.length) {
          restoreOrigins(skipped)
          depsRef.current.pushToast(skippedNote(skipped.length, 'moved'), 'info')
        }
        if (markers.length === 0) {
          originsRef.current = null
          return
        }

        const ok = await depsRef.current.confirm({
          title: 'Move selected points?',
          message: `Save the new position of ${markers.length} selected point${
            markers.length === 1 ? '' : 's'
          }?`,
        })
        if (!ok) {
          restoreOrigins()
          originsRef.current = null
          return
        }

        let done = 0
        const total = markers.length
        setProgress({ action: 'Saving', done, total })
        try {
          await Promise.all(
            markers.map((marker) => {
              const o = origins.get(marker)
              const meta = getMeta(marker)
              if (!o || !meta?.save) return Promise.resolve()
              return meta.save(o.lat + dLat, o.lng + dLng).then((r) => {
                setProgress({ action: 'Saving', done: ++done, total })
                return r
              })
            })
          )
        } catch (err) {
          depsRef.current.pushToast(`Group move failed: ${err.message}`, 'error')
          restoreOrigins()
        } finally {
          setProgress(null)
          originsRef.current = null
        }
      },

      async deleteSelected() {
        const { kept: markers, skipped } = splitSkipped([...selectedRef.current])
        if (skipped.length) depsRef.current.pushToast(skippedNote(skipped.length, 'deleted'), 'info')
        if (markers.length === 0) return

        const ok = await depsRef.current.confirm({
          title: 'Delete selected points?',
          message: `Delete ${markers.length} selected point${
            markers.length === 1 ? '' : 's'
          }? This cannot be undone.`,
          confirmLabel: 'Delete',
          danger: true,
        })
        if (!ok) return

        // Deleting by index shifts every later index in the same list, so each
        // list is deleted highest-index-first and one at a time. This ordering
        // is load-bearing — parallelising it corrupts the file.
        const byList = new Map()
        for (const marker of markers) {
          const meta = getMeta(marker)
          if (!meta?.del) continue
          const key = meta.groupKey || 'default'
          if (!byList.has(key)) byList.set(key, [])
          byList.get(key).push(meta)
        }
        for (const metas of byList.values()) metas.sort((a, b) => b.posIndex - a.posIndex)

        let done = 0
        const total = [...byList.values()].reduce((sum, m) => sum + m.length, 0)
        setProgress({ action: 'Deleting', done, total })
        try {
          for (const metas of byList.values()) {
            for (const meta of metas) {
              await meta.del()
              setProgress({ action: 'Deleting', done: ++done, total })
            }
          }
        } catch (err) {
          depsRef.current.pushToast(`Delete failed: ${err.message}`, 'error')
        } finally {
          setProgress(null)
          clearVisuals()
          sync()
        }
      },
    }
  }, [])

  return { selection, count, progress }
}
