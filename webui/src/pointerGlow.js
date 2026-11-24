// The Tactical theme's pointer light (index.css, "Tactical theme"): writes
// where the pointer is, in each lit element's own box, into --mx/--my. The CSS
// paints a faint wash (and a card's rim) there, and only while :hover, so this
// never decides what is lit; it only says where.
//
// One passive listener on the document, one write per animation frame, and
// every enclosing card is updated too, so a card inside a card does not leave
// the outer one's light behind. theme.jsx starts it once; every theme is Tactical
// and calls the returned function to stop it. A touch has no hover, so it is
// ignored.
//
// While the page scrolls, the cards move under a still pointer, so :hover
// jumps from card to card and each jump starts a repaint over the glass blur.
// `data-scrolling` on <html> (index.css, "pointer wash") turns the light off
// until the scroll has been quiet for SCROLL_QUIET ms.

const LIT = '.card, .status-card, .login-form, .modal, .btn, .nav-link, .sidebar-action, .segmented-option, .mod-tile'

const SCROLL_QUIET = 150

export function startPointerGlow() {
  let frame = 0
  let last = null
  let quiet = 0
  const root = document.documentElement

  const paint = () => {
    frame = 0
    const target = last?.target
    if (!(target instanceof Element) || quiet) return
    for (let el = target.closest(LIT); el; el = el.parentElement?.closest(LIT)) {
      const box = el.getBoundingClientRect()
      el.style.setProperty('--mx', `${last.clientX - box.left}px`)
      el.style.setProperty('--my', `${last.clientY - box.top}px`)
    }
  }

  const onMove = (e) => {
    if (e.pointerType === 'touch') return
    last = e
    if (!frame) frame = requestAnimationFrame(paint)
  }

  const endScroll = () => {
    quiet = 0
    root.removeAttribute('data-scrolling')
  }

  const onScroll = () => {
    if (quiet) clearTimeout(quiet)
    else root.setAttribute('data-scrolling', '')
    quiet = setTimeout(endScroll, SCROLL_QUIET)
  }

  document.addEventListener('pointermove', onMove, { passive: true })
  // Capture, so a scrolling element inside the page counts too.
  document.addEventListener('scroll', onScroll, { passive: true, capture: true })
  return () => {
    document.removeEventListener('pointermove', onMove)
    document.removeEventListener('scroll', onScroll, { capture: true })
    if (frame) cancelAnimationFrame(frame)
    if (quiet) clearTimeout(quiet)
    root.removeAttribute('data-scrolling')
  }
}
