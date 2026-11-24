import { useLayoutEffect, useRef } from 'react'

const DURATION_MS = 180

// Animates an element's height whenever `key` changes, from the height it had
// on the render before to the height it has now. For a box whose content is
// mounted or unmounted by the change (the Activity drawer, the info bar), where
// a CSS transition has no fixed start and end to run between.
//
// The height is recorded after every render, so content that grows on its own
// (a log line, a job) is the start of the next animation, not a stale one.
// Overflow is hidden only while it runs, so the old content does not spill out
// of a shrinking box. Off under prefers-reduced-motion, like the sidebar.
export function useHeightAnimation(ref, key) {
  const prevHeight = useRef(null)
  const prevKey = useRef(key)
  const running = useRef(null)

  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    if (prevKey.current !== key) {
      prevKey.current = key
      const from = prevHeight.current
      // An animation still running when the key flips again is replaced, so
      // its height is where the new one starts.
      running.current?.cancel()
      const to = el.offsetHeight
      const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
      if (from != null && from !== to && !reduce && el.animate) {
        const anim = el.animate(
          [
            { height: `${from}px`, overflow: 'hidden' },
            { height: `${to}px`, overflow: 'hidden' },
          ],
          { duration: DURATION_MS, easing: 'ease-out' }
        )
        running.current = anim
        anim.onfinish = anim.oncancel = () => {
          if (running.current === anim) running.current = null
        }
      }
      prevHeight.current = to
      return
    }
    if (!running.current) prevHeight.current = el.offsetHeight
  })
}
