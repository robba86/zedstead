// Cross-page deep links to a section of a page (e.g. the Doctor page's
// "reset XML files" link → /cleanup#reset-traderplus). React Router does not
// scroll to `#id` itself, and the target usually only exists after the page's
// first data load — hence the `ready` gate.
import { useEffect } from 'react'
import { useLocation } from 'react-router-dom'

// How long to keep watching for a target that is not in the DOM yet. The page
// being ready is not the same as the *card* being ready: several of them fetch
// their own data (ManualXmlFiles renders null until `/api/modxml/files`
// answers), so `#manual-files` routinely appears after ModXmlPage's own report
// has landed and flipped `ready`. Looking once and giving up is why that link
// scrolled nowhere.
const WAIT_MS = 10000

// How much of the window's bottom edge stays covered while the page scrolls:
// the Activity dock. Nothing covers the top: the phone's top bar and the
// status strip scroll away with the page.
function coveredEdges() {
  const css = getComputedStyle(document.documentElement)
  return {
    top: 0,
    bottom: parseFloat(css.getPropertyValue('--bottom-height')) || 0,
  }
}

/**
 * Scroll the element named by the URL hash into view and flash it once.
 * `ready` should be false while the page still renders a placeholder, so the
 * lookup does not start before the page itself has anything to show.
 */
export function useHashTarget(ready = true) {
  const { hash } = useLocation()

  useEffect(() => {
    if (!ready || !hash) return undefined
    const id = hash.slice(1)
    let flashTimer = null
    let giveUpTimer = null
    let observer = null

    const reveal = (el) => {
      observer?.disconnect()
      clearTimeout(giveUpTimer)
      // Centred when it fits; a card taller than the window starts at its top,
      // or centring scrolled its heading out of view.
      // Both measure the part of the window the Activity dock leaves free.
      const { top, bottom } = coveredEdges()
      el.style.scrollMarginTop = `${top + 8}px`
      el.style.scrollMarginBottom = `${bottom}px`
      const room = window.innerHeight - top - bottom
      const tall = el.getBoundingClientRect().height > room * 0.8
      el.scrollIntoView({ behavior: 'smooth', block: tall ? 'start' : 'center' })
      el.classList.add('hash-flash')
      flashTimer = setTimeout(() => el.classList.remove('hash-flash'), 1600)
    }

    const present = document.getElementById(id)
    if (present) {
      reveal(present)
    } else {
      // Watch for it instead. One observer, disconnected the moment it hits or
      // when the window closes — not a poll, and never left running.
      observer = new MutationObserver(() => {
        const el = document.getElementById(id)
        if (el) reveal(el)
      })
      observer.observe(document.body, { childList: true, subtree: true })
      giveUpTimer = setTimeout(() => observer.disconnect(), WAIT_MS)
    }

    return () => {
      observer?.disconnect()
      clearTimeout(flashTimer)
      clearTimeout(giveUpTimer)
    }
  }, [hash, ready])
}
