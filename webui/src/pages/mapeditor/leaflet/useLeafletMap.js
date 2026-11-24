// The Leaflet map instance, owned by React but driven imperatively.
//
// The map is created into a div and handed to feature hooks through
// MapContext. Layer components render null and only run effects — Leaflet owns
// every DOM node under .me-map, and React must not try to reconcile it.

import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import L from 'leaflet'

const MapContext = createContext(null)

export const MapProvider = MapContext.Provider

export function useMap() {
  return useContext(MapContext)
}

// Maps that have been .remove()d.
//
// React runs every cleanup in a commit before any effect body, so on a terrain
// change this happens in one pass:
//
//   1. useLeafletMap cleanup   -> instance.remove()
//   2. useImageOverlay cleanup -> overlay.remove()
//   3. useLeafletMap body      -> new map, setMap(new)   [state, so async]
//   4. useImageOverlay body    -> `map` is STILL the old, removed instance
//
// Step 4 would add layers to a dead map — a silent no-op at best, a throw
// (blank page) at worst. Every layer hook guards on isMapAlive first and waits
// for the setMap commit to re-run it with the new instance.
const removedMaps = new WeakSet()

export function isMapAlive(map) {
  return Boolean(map) && !removedMaps.has(map)
}

/**
 * Create the Leaflet map for *bounds* (null until map-info has loaded).
 *
 * Returns `{ containerRef, container, map }`. `containerRef` is a CALLBACK ref,
 * not a useRef object: the map must be rebuilt if its div is ever unmounted and
 * remounted, and a plain ref mutation does not re-run effects — the map would
 * be left bound to a detached node, showing nothing until a page reload.
 *
 * Options are the vanilla editor's verbatim: CRS.Simple (flat, non-geographic
 * — the world is a square image, not a globe), half-step zoom, and a padded
 * maxBounds with viscosity so panning off the map springs back.
 */
export function useLeafletMap(bounds) {
  const [container, setContainer] = useState(null)
  const [map, setMap] = useState(null)
  const boundsKey = bounds ? JSON.stringify(bounds) : null

  const containerRef = useCallback((node) => setContainer(node), [])

  useEffect(() => {
    if (!container || !bounds) return

    const latLngBounds = L.latLngBounds(
      L.latLng(bounds.south_west[0], bounds.south_west[1]),
      L.latLng(bounds.north_east[0], bounds.north_east[1])
    )

    const instance = L.map(container, {
      crs: L.CRS.Simple,
      minZoom: -3,
      maxZoom: 12,
      zoomSnap: 0.5,
      zoomDelta: 0.5,
      maxBounds: latLngBounds.pad(0.2),
      maxBoundsViscosity: 0.8,
      attributionControl: false,
    })
    instance.fitBounds(latLngBounds)
    setMap(instance)

    return () => {
      setMap(null)
      removedMaps.add(instance)
      // Required, not tidiness: <StrictMode> double-invokes effects in dev, and
      // Leaflet refuses to initialize a container twice ("Map container is
      // already initialized").
      instance.remove()
    }
    // `container` (the element), not `containerRef` (a stable ref object) — if
    // the div is ever unmounted and remounted, the map must be rebuilt into the
    // new element rather than left pointing at a detached one.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [container, boundsKey])

  // The map container resizes underneath us: .content pads by --bottom-height,
  // which ActivityDrawer republishes live while the user drags the drawer. The
  // old editor never needed this — it owned a fixed viewport.
  //
  // Until the map is moved (a drag, a zoom, a panel's fly-to), every resize
  // fits the world again, so a map opened while the expanded drawer left it a
  // strip grows back to full size when the drawer collapses (night test
  // 2026-11-22). A zoom test could not tell: in a 97px strip the editor
  // opened at a zoom above the strip's fit. Our own fit and resize are not
  // moves (`ours`), and they run without animation so their events fire now.
  useEffect(() => {
    if (!isMapAlive(map) || !container || !bounds) return
    const world = toLatLngBounds(bounds)
    let ours = false
    let moved = false
    const onMove = () => {
      if (!ours) moved = true
    }
    map.on('movestart zoomstart', onMove)
    const observer = new ResizeObserver(() => {
      if (!isMapAlive(map)) return
      ours = true
      try {
        map.invalidateSize({ animate: false })
        if (!moved) map.fitBounds(world, { animate: false })
      } finally {
        ours = false
      }
    })
    observer.observe(container)
    return () => {
      observer.disconnect()
      if (isMapAlive(map)) map.off('movestart zoomstart', onMove)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, container, boundsKey])

  return { containerRef, container, map }
}

/** Current map bounds as an L.LatLngBounds, for layers that need to clamp. */
export function toLatLngBounds(bounds) {
  if (!bounds) return null
  return L.latLngBounds(
    L.latLng(bounds.south_west[0], bounds.south_west[1]),
    L.latLng(bounds.north_east[0], bounds.north_east[1])
  )
}

/** Subscribe to a map event for the lifetime of the caller. */
export function useMapEvent(map, event, handler) {
  const handlerRef = useRef(handler)
  handlerRef.current = handler

  useEffect(() => {
    if (!isMapAlive(map)) return
    const fn = (...args) => handlerRef.current?.(...args)
    map.on(event, fn)
    return () => {
      map.off(event, fn)
    }
  }, [map, event])
}
