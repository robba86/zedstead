import { useLayoutEffect, useRef, useState } from 'react'
import { useCan } from '../role.jsx'
import { useUnsavedChanges } from '../useUnsavedChanges.js'

// Sticky footer that pins Save/Reset to the bottom of the content area on long
// pages. Presentational only — the parent owns all form state and handlers.
//
// Renders nothing below admin. Every save it fronts is an admin write — the
// config editors' PUTs, the Raw tab's, Settings — so for anyone else its only
// outcome is a 403 (plans/43 §4). One check here covers all eight callers.
//
// It is also where unsaved edits ask before the page is left (F32): every
// dirty-tracking page passes `dirty` here, so a new form page gets the guard
// by passing it too. Below admin there is nothing to save, so nothing asks.
// `position: sticky` only sticks inside the bar's parent, so on a page shorter
// than the window the bar sat straight under the content, mid-screen (the
// Messages table with no rows). This pushes it down by the free space below
// the page, through --sticky-push, so it rests on the Activity drawer the way
// it does on a long page; when the page grows the push shrinks back to 0.
// Measured, not a flex chain: the bar's parent is a different element on every
// page, and turning each ancestor into a flex column stops their paragraph
// margins collapsing. Pages that size themselves (.content-bleed) and the
// side drawer are left alone.
function usePushToBottom(ref, rendered) {
  useLayoutEffect(() => {
    const bar = ref.current
    const content = bar?.closest('.content')
    if (!content || content.classList.contains('content-bleed') || bar.closest('.side-drawer')) {
      return
    }
    const page = [...content.children].find((c) => c.contains(bar))
    // Measured at push 0, where the bar is at its natural place: on a page
    // taller than the window it is stuck on the drawer already, so the free
    // space comes out 0 and so does the push. `bottom` is the bar's own sticky
    // offset, the drawer's live height.
    const fit = () => {
      bar.style.setProperty('--sticky-push', '0px')
      // clientHeight, not innerHeight: a horizontal scrollbar is not room.
      const target =
        document.documentElement.clientHeight - parseFloat(getComputedStyle(bar).bottom)
      const push = Math.max(0, Math.floor(target - bar.getBoundingClientRect().bottom))
      bar.style.setProperty('--sticky-push', `${push}px`)
    }
    fit()
    // .content reports a padding change (the drawer opening) as a resize of
    // its content box; the page reports everything that changes its height.
    const ro = new ResizeObserver(fit)
    ro.observe(content)
    ro.observe(page)
    window.addEventListener('resize', fit)
    return () => {
      ro.disconnect()
      window.removeEventListener('resize', fit)
      bar.style.removeProperty('--sticky-push')
    }
  }, [ref, rendered])
}

export default function StickyActionBar({
  dirty, // bool | undefined. Controls the disabled state and the
  // "Unsaved changes" hint. Pass undefined to always enable.
  saving = false, // disables buttons + swaps the label to "Saving…"
  onSave,
  onReset, // optional — renders a "Reset" button when provided
  saveLabel = 'Save',
  children, // optional extra controls, left-aligned (e.g. "Add row")
  center = null, // optional, centred on the bar (TableEditor's pager)
  // The file changed on disk while these edits were unsaved (configSync.js).
  // It replaces the "Unsaved changes" hint rather than joining it — stale only
  // ever happens when dirty, and it is the more urgent half of the same fact.
  stale = false,
  onReload, // required for `stale` to render anything to act on
}) {
  const isAdmin = useCan('admin')
  const ref = useRef(null)
  usePushToBottom(ref, isAdmin)
  useUnsavedChanges(isAdmin && dirty === true)
  // Save locks itself while the promise `onSave` returns is pending, so a
  // second click before the caller's `saving` arrives sends nothing (user
  // test in 2026 Q4: a slow save clicked twice). The ref answers that click
  // before React re-renders the button disabled.
  const busyRef = useRef(false)
  const [busy, setBusy] = useState(false)
  const isSaving = saving || busy
  const handleSave = () => {
    if (busyRef.current || saving) return
    const result = onSave?.()
    if (result && typeof result.then === 'function') {
      busyRef.current = true
      setBusy(true)
      Promise.resolve(result)
        .catch(() => {})
        .finally(() => {
          busyRef.current = false
          setBusy(false)
        })
    }
  }
  const canSave = dirty === undefined ? !isSaving : dirty && !isSaving
  if (!isAdmin) return null
  // Two clusters (user test, 2026 Q4: on a phone Save dropped onto a row of
  // its own under "Unsaved changes"). The caller's controls on the left wrap
  // among themselves first; the status, Reset and Save stay together on the
  // right. Below 840px the status shrinks to a dot, its words kept for screen
  // readers (index.css).
  return (
    <div className="sticky-action-bar" ref={ref}>
      {children && <div className="sticky-action-bar-start">{children}</div>}
      {center && <div className="sticky-action-bar-center">{center}</div>}
      <div className="sticky-action-bar-end">
        {stale ? (
          <>
            <BarStatus stale text="Changed on disk — saving overwrites it" />
            {onReload && (
              <button className="btn btn-ghost" disabled={isSaving} onClick={onReload}>
                Reload
              </button>
            )}
          </>
        ) : (
          dirty && <BarStatus text="Unsaved changes" />
        )}
        {onReset && (
          <button className="btn btn-ghost" disabled={!dirty || isSaving} onClick={onReset}>
            Reset
          </button>
        )}
        <button className="btn btn-primary" disabled={!canSave} onClick={handleSave}>
          {isSaving ? 'Saving…' : saveLabel}
        </button>
      </div>
    </div>
  )
}

// The bar's status: a coloured dot and its words. role="status" so a screen
// reader hears it when it appears, also where the words are clipped to the dot.
function BarStatus({ text, stale = false }) {
  return (
    <span role="status" className={`sticky-action-bar-hint${stale ? ' sticky-action-bar-stale' : ''}`}>
      <span className="sticky-action-bar-dot" aria-hidden="true" />
      <span className="sticky-action-bar-hint-text">{text}</span>
    </span>
  )
}
