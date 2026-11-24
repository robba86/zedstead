// A stable function identity that always calls the latest callback.
//
// Leaflet handlers are bound when a layer is built and live until it is torn
// down. If a handler closed over props directly, the layer would have to be
// rebuilt whenever any handler changed — which for the spawn layer means
// destroying and recreating every marker on the map on each render. Wrapping
// them here keeps the handler identity stable (so it can sit in an effect's
// dependency list without triggering rebuilds) while the body still sees the
// current render's values.

import { useCallback, useRef } from 'react'

export function useEventCallback(fn) {
  const ref = useRef(fn)
  ref.current = fn
  return useCallback((...args) => ref.current?.(...args), [])
}
