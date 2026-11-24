// Keyboard focus for the surfaces that open over the page.
//
// useFocusTrap(ref, active): a modal (ConfirmModal, the command palette, the
// Guide shown as a modal, the map editor's .dialog-box dialogs) keeps Tab and
// Shift+Tab inside itself while open, and gives focus back to the control that
// opened it when it closes. The `focus-trap` package does the work, and its
// trap stack pauses the modal underneath when a second one opens over it.
//
// useFocusReturn(ref): a non-modal drawer (SideDrawer) takes focus when it
// opens and gives it back when it closes, but Tab can still leave it, because
// the page beside it stays usable.
//
// Each surface keeps its own Escape and backdrop-click handling, so the trap
// never closes anything itself. A field that focuses itself on open (autoFocus
// or an effect) keeps that focus: the trap focuses the first control only when
// nothing inside the surface has focus yet.

import { useEffect, useRef } from 'react'
import { createFocusTrap } from 'focus-trap'

// A surface with no tabbable control left (a dialog that shows only a
// message while it loads) takes focus itself, so it needs a tabindex.
function makeFocusable(el) {
  if (!el.hasAttribute('tabindex')) el.setAttribute('tabindex', '-1')
}

// The control that had focus before the surface rendered. Read during render,
// because an autoFocus field takes focus at commit, before any effect runs.
function useOpener(active) {
  const opener = useRef(null)
  if (!active) opener.current = null
  else if (opener.current === null) opener.current = document.activeElement
  return opener
}

export function useFocusTrap(ref, active = true) {
  const opener = useOpener(active)
  useEffect(() => {
    const el = ref.current
    if (!active || !el) return undefined
    const back = opener.current
    makeFocusable(el)
    const trap = createFocusTrap(el, {
      // Runs after the dialog's own effects (delayInitialFocus is on), so a
      // field the dialog focused itself is still the one in focus.
      initialFocus: () => (el.contains(document.activeElement) ? document.activeElement : undefined),
      fallbackFocus: el,
      escapeDeactivates: false,
      clickOutsideDeactivates: false,
      // The backdrop is outside the trap and its click closes the dialog.
      allowOutsideClick: true,
      setReturnFocus: (previous) => (back?.isConnected ? back : previous),
    })
    trap.activate()
    // A surface still in the page was not closed: StrictMode runs every effect
    // twice in development, and focus must stay where it is for the second run.
    return () => trap.deactivate({ returnFocus: !el.isConnected })
  }, [ref, active, opener])
}

export function useFocusReturn(ref) {
  const openerRef = useOpener(true)
  useEffect(() => {
    const el = ref.current
    if (!el) return undefined
    const opener = openerRef.current
    makeFocusable(el)
    if (!el.contains(document.activeElement)) el.focus({ preventScroll: true })
    return () => {
      // Give focus back only when the drawer closed (not a StrictMode rerun)
      // and focus is still in it or lost to the body: a click on the page
      // beside the drawer chose another target.
      const now = document.activeElement
      if (!el.isConnected && (!now || now === document.body || el.contains(now)) && opener?.isConnected) {
        opener.focus({ preventScroll: true })
      }
    }
  }, [ref, openerRef])
}
