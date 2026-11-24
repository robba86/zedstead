// Re-apply `?q=` when it changes under a page that is already open (plans/75).
//
// `?q=` is the app-wide search-prefill key, and each page seeds its filter box
// from it once, in a useState initializer. That misses a search made while the
// page is already on screen: the Ctrl+K palette navigates to the same path
// with a new `?q=` and the page does not remount. This hook calls `apply(q)`
// when `?q=` changes to something new, or when the palette's navigation
// carries a fresh `searchStamp` in the location state (picking the same
// result twice, or a result without `?q=` on a page whose box holds text).
// The box stays the user's in between: nothing is written back to the URL,
// and a tab switch (no stamp, same `?q=`) leaves it alone.

import { useEffect, useRef } from 'react'
import { useLocation, useSearchParams } from 'react-router-dom'

export function useSearchPrefill(apply) {
  const [params] = useSearchParams()
  const location = useLocation()
  const q = params.get('q') || ''
  const stamp = location.state?.searchStamp
  const last = useRef({ q, stamp })
  const applyRef = useRef(apply)
  applyRef.current = apply

  useEffect(() => {
    const prev = last.current
    last.current = { q, stamp }
    const freshStamp = stamp !== undefined && stamp !== prev.stamp
    const newQuery = q !== prev.q && q !== ''
    if (freshStamp || newQuery) applyRef.current(q)
  }, [q, stamp])
}
