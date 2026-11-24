import { useEffect, useState } from 'react'

// The mobile breakpoint, in one place. `--bp-mobile` in index.css documents the
// same number for CSS, but a media query cannot read a custom property, so the
// literal is repeated there in every `@media` — keep the two in step.
//
// Below this the sidebar stops being a column and becomes an off-canvas drawer
// behind the top bar's hamburger (Layout.jsx + "Mobile shell" in index.css).
export const MOBILE_QUERY = '(max-width: 840px)'

/**
 * Subscribe to a media query. Returns whether it currently matches.
 *
 * Same shape as theme.jsx's prefers-color-scheme listener, generalized: the
 * initial value is read synchronously so the first render is already correct
 * (a `useState(false)` + effect would render the desktop shell for one frame
 * and visibly snap on every phone load), and the listener is torn down on
 * unmount.
 *
 * Guarded for a missing `matchMedia` because this runs before anything else in
 * the shell; a throw here would blank the whole SPA rather than degrade.
 */
export function useMediaQuery(query) {
  const [matches, setMatches] = useState(() => {
    try {
      return window.matchMedia(query).matches
    } catch {
      return false
    }
  })

  useEffect(() => {
    let mq
    try {
      mq = window.matchMedia(query)
    } catch {
      return
    }
    // Re-read on subscribe: the query can have flipped between the initial
    // useState and this effect (an orientation change during hydration).
    setMatches(mq.matches)
    const onChange = (e) => setMatches(e.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [query])

  return matches
}
