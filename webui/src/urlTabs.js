// Page tab state lives in the URL, so every tab is a link:
//
//   /backups?tab=Bundles
//   /mods?tab=Install&subtab=Workshop
//
// `tab` is the app-wide key for a page's primary tab bar and `subtab` the
// nested one (the map editor's ?tab= and ModsPage's already-URL-backed tab set
// the precedent) — don't invent a synonym for either, the same way ?q= is the
// app-wide search-prefill key.
//
// Matching is case-insensitive and the *canonical* spelling is what gets
// written back, so a hand-typed `?tab=install` works and then tidies itself.
// An unknown value falls back to the first tab rather than an empty page.
//
// Two rules baked in here:
//   - The default tab is never written, so a page's own URL stays clean and
//     "is this the default?" has one answer.
//   - ONE write per handler. react-router resolves a functional setSearchParams
//     against the params of the last *render*, so switching tab and dropping the
//     now-stale subtab must happen inside a single update (see the long note in
//     pages/mapeditor/state/urlState.js).
//
// `tabs` (and a `subtabsFor` array/function) must be module-level constants —
// they are memo deps, and a fresh array per render would re-create the setters
// on every keystroke elsewhere on the page. A `useMemo`'d list is equally fine
// where the tab set is genuinely dynamic (ConfigEditorDrawer's: which of Interactive/
// Basic/Raw a given file has depends on its /meta).
//
// `legacyKey` is the one concession to history: `/configs/:id` addressed its tab
// as `?view=` before `?tab=` existed app-wide, so it reads that spelling as a
// fallback and drops it the moment it writes. Old bookmarks keep working and
// converge on the app-wide key. Don't reach for this for anything new — a
// second spelling of a param is exactly what it exists to retire.
//
// `renamed` maps a tab's old name (lower case) to its new one, so a link
// written before a rename still opens that tab (pageTabs.js
// ANALYSIS_TAB_RENAMES).
import { useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'

const TAB_KEY = 'tab'
const SUBTAB_KEY = 'subtab'

function canonical(tabs, value, renamed = null) {
  const want = String(value ?? '').trim().toLowerCase()
  if (!want || !tabs?.length) return null
  return tabs.find((t) => t.toLowerCase() === want) ?? renamed?.[want] ?? null
}

const listFor = (subtabsFor, tab) =>
  (typeof subtabsFor === 'function' ? subtabsFor(tab) : subtabsFor) || null

// Returns { tab, setTab, subtab, setSubtab }. `subtab` is null for pages (or
// tabs) with no nested bar — pass `subtabsFor` as a function of the active tab
// when only some tabs have one.
export function useUrlTabs(tabs, subtabsFor = null, { legacyKey = null, renamed = null } = {}) {
  const [params, setParams] = useSearchParams()

  const asked = params.get(TAB_KEY) ?? (legacyKey ? params.get(legacyKey) : null)
  const tab = canonical(tabs, asked, renamed) || tabs[0]
  const subtabs = listFor(subtabsFor, tab)
  const subtab = subtabs?.length ? canonical(subtabs, params.get(SUBTAB_KEY)) || subtabs[0] : null

  // replace:true — a tab click must not add a history entry, or Back walks the
  // user back through every tab they looked at.
  const update = useCallback(
    (mutate) => {
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev)
          mutate(next)
          return next
        },
        { replace: true }
      )
    },
    [setParams]
  )

  const setTab = useCallback(
    (value) => {
      const name = canonical(tabs, value, renamed) || tabs[0]
      update((next) => {
        const was = next.get(TAB_KEY) ?? (legacyKey ? next.get(legacyKey) : null)
        const from = canonical(tabs, was, renamed) || tabs[0]
        if (name === tabs[0]) next.delete(TAB_KEY)
        else next.set(TAB_KEY, name)
        if (legacyKey) next.delete(legacyKey) // superseded by what we just wrote
        // The subtab belongs to the tab being left. Re-clicking the active tab
        // is not "leaving" it, so it keeps its subtab.
        if (name !== from) next.delete(SUBTAB_KEY)
      })
    },
    [tabs, legacyKey, renamed, update]
  )

  const setSubtab = useCallback(
    (value) => {
      update((next) => {
        const active = canonical(tabs, next.get(TAB_KEY), renamed) || tabs[0]
        const list = listFor(subtabsFor, active)
        const name = canonical(list, value)
        if (!name || name === list[0]) next.delete(SUBTAB_KEY)
        else next.set(SUBTAB_KEY, name)
      })
    },
    [tabs, subtabsFor, renamed, update]
  )

  return { tab, setTab, subtab, setSubtab }
}

// Only the ?subtab= key, for a bar nested inside a view whose ?tab= another
// component owns: FormEditor's "Client only" bar inside the config drawer's
// Edit tab. The drawer's setTab drops the subtab when it leaves Edit, as
// useUrlTabs does for its own. `subtabs` follows the same memo rule.
export function useUrlSubtab(subtabs) {
  const [params, setParams] = useSearchParams()
  const subtab = subtabs?.length ? canonical(subtabs, params.get(SUBTAB_KEY)) || subtabs[0] : null
  const setSubtab = useCallback(
    (value) => {
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev)
          const name = canonical(subtabs, value)
          if (!name || name === subtabs[0]) next.delete(SUBTAB_KEY)
          else next.set(SUBTAB_KEY, name)
          return next
        },
        { replace: true }
      )
    },
    [subtabs, setParams]
  )
  return { subtab, setSubtab }
}
