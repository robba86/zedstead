// Unsaved edits ask before the page is left (test pass 1, F32; plans/55 C).
//
// Two halves, because react-router consults only ONE blocker at a time (the
// last one registered) and a page can mount several save bars:
//
// - useUnsavedChanges(dirty) is what a form calls — StickyActionBar does it for
//   every page that has one. It only records "something here is unsaved" in a
//   module-level set.
// - <UnsavedChangesGuard /> is mounted once, inside the router, and owns the
//   single useBlocker plus the beforeunload listener. Both read the set at the
//   moment they are asked, so neither re-registers when a form turns dirty.
//
// Leaving means another path, or another `?tab=`/`?subtab=` (urlTabs.js's
// keys): every page renders only its active tab, so a tab switch unmounts the
// form and drops its edits just as a path change does — on /configs/:id,
// Basic -> Raw did exactly that. Any other search param (a `?group=` pick, a
// filter) keeps the page mounted and does not ask.
//
// Edits a page keeps in its own state outlive its tabs, so their source is
// registered `pageWide` and only another path asks (Mods: an unsaved load
// order is still there after a look at the Updates tab).
//
// A page that swaps one form for another without a navigation (another file's
// Edit, another event on the map) names the form: `{ form: 'name' }`, and
// `useConfirmDiscard()(name)` asks before the swap. No router blocker sees it.

import { useCallback, useEffect, useId, useRef } from 'react'
import { useBlocker } from 'react-router-dom'
import { useConfirm } from './components/useConfirm.jsx'

// id -> { pageWide, form }
const dirtySources = new Map()

const TAB_KEYS = ['tab', 'subtab']

// The one question, for the guard and for anything else that discards edits
// (Log out, Layout.jsx).
export const LEAVE_DIALOG = {
  title: 'Leave without saving?',
  message: 'This page has unsaved changes. Leaving it discards them.',
  confirmLabel: 'Leave',
  cancelLabel: 'Stay',
  danger: true,
}

// Opening another record in the same form: the one question for that.
export const DISCARD_DIALOG = {
  title: 'Discard your changes?',
  message: 'This form has unsaved changes. Opening something else discards them.',
  confirmLabel: 'Discard',
  cancelLabel: 'Keep editing',
  danger: true,
}

export function hasUnsavedChanges() {
  return dirtySources.size > 0
}

// After the user has already said Leave: the navigation that follows must not
// be blocked and asked about a second time.
export function discardUnsavedChanges() {
  dirtySources.clear()
}

// Would going from -> to drop any of the unsaved edits?
function losesEdits(from, to) {
  if (dirtySources.size === 0) return false
  if (from.pathname !== to.pathname) return true
  const a = new URLSearchParams(from.search)
  const b = new URLSearchParams(to.search)
  if (!TAB_KEYS.some((key) => a.get(key) !== b.get(key))) return false
  return [...dirtySources.values()].some((source) => !source.pageWide)
}

export function useUnsavedChanges(dirty, { pageWide = false, form = null } = {}) {
  const id = useId()
  useEffect(() => {
    if (!dirty) return undefined
    dirtySources.set(id, { pageWide, form })
    return () => {
      dirtySources.delete(id)
    }
  }, [dirty, id, pageWide, form])
}

// `await confirmDiscard(form)` is true when the named form holds no edits, or
// the user chose to drop them.
export function useConfirmDiscard() {
  const confirm = useConfirm()
  return useCallback(
    async (form) => {
      const dirty = [...dirtySources.values()].some((source) => source.form === form)
      return !dirty || confirm(DISCARD_DIALOG)
    },
    [confirm]
  )
}

export function UnsavedChangesGuard() {
  const confirm = useConfirm()
  const shouldBlock = useCallback(
    ({ currentLocation, nextLocation }) => losesEdits(currentLocation, nextLocation),
    []
  )
  const blocker = useBlocker(shouldBlock)
  // One dialog per blocked navigation, also under StrictMode's double effect.
  const askingRef = useRef(false)

  useEffect(() => {
    if (blocker.state !== 'blocked' || askingRef.current) return
    askingRef.current = true
    confirm(LEAVE_DIALOG).then((leave) => {
      askingRef.current = false
      if (leave) blocker.proceed()
      else blocker.reset()
    })
  }, [blocker, confirm])

  // Reload and tab close: the browser shows its own prompt, whose text a page
  // can no longer set.
  useEffect(() => {
    const onBeforeUnload = (event) => {
      if (dirtySources.size === 0) return
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', onBeforeUnload)
    return () => window.removeEventListener('beforeunload', onBeforeUnload)
  }, [])

  return null
}
