import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { api } from './api'
import { useWs } from './ws'

// Keeping a config editor in step with the file on disk (2026 Q3).
//
// Every save in the app broadcasts `configs_changed` (a dozen routers do it) —
// this view's own save, a save of a completely different file, a mods install
// that rewrote twenty of them. So the event is a hint to LOOK, never a reason
// to replace what is on screen:
//
//   1. re-GET the payload the view was built from;
//   2. identical to what it is showing -> do nothing at all. This is the step
//      that matters: without it every unrelated broadcast (and the echo of the
//      view's own save) would blank and re-render the editor;
//   3. changed, no unsaved edits -> swap it in silently;
//   4. changed WITH unsaved edits -> touch nothing, report `stale`, and let the
//      action bar offer the reload rather than taking the edits away.
//
// An event that lands while the view is mid-save or mid-load is deferred to
// when it settles, not answered: `showing` and `dirty` are between two states
// there. A save's own echo is the case that forces this — RawEditor's `dirty`
// is briefly *true* right after a PUT (the saved text against the not-yet-
// reloaded payload), so an immediate check would report the view's own write as
// someone else's and raise the banner on every save.
//
// None of the callbacks need to be stable: they are re-read from a ref at event
// time, so a plain arrow in the call site's render is fine.

function same(a, b) {
  return JSON.stringify(a) === JSON.stringify(b)
}

/**
 * For a view that owns its own fetch (Raw, the interactive views).
 *
 * path     — the GET that returns what the view was built from.
 * showing  — what it currently displays, in `select`'s space.
 * select   — payload -> comparable, when the view keeps something derived
 *            rather than the payload itself. Default: the payload.
 * dirty    — unsaved edits.
 * busy     — mid-save or mid-load; checks are deferred until it clears.
 * apply    — hand the view a fresh payload (must NOT blank it first).
 * enabled  — false parks the whole thing (the Basic tab, while another tab is
 *            up and its data is nobody's answer).
 *
 * Returns {stale, onReload} — spread straight into <StickyActionBar>.
 */
export function useConfigSync({
  path,
  showing,
  select = (d) => d,
  dirty = false,
  busy = false,
  apply,
  enabled = true,
}) {
  const [stale, setStale] = useState(false)
  const deferred = useRef(false)
  const running = useRef(false)
  // Read at event time, not closure-capture time — the WS handler below is
  // registered once and would otherwise answer with the first render's state.
  const now = useRef(null)
  now.current = { path, showing, select, dirty, busy, apply, enabled }

  const check = useCallback(async (force) => {
    if (!now.current.enabled) return
    if (!force && (now.current.busy || running.current)) {
      deferred.current = true
      return
    }
    running.current = true
    const path = now.current.path
    let payload
    try {
      payload = await api.get(path)
    } catch {
      // A background peek: the view's own load and save report real failures,
      // and a toast here would fire on every unrelated broadcast.
      return
    } finally {
      running.current = false
    }
    const s = now.current // it may have moved while the GET was in flight
    // Another file or Raw target since: this payload is not the view's answer.
    if (!s.enabled || s.path !== path) return
    if (force) {
      s.apply(payload)
      setStale(false)
      return
    }
    if (s.busy) {
      deferred.current = true
      return
    }
    if (same(s.select(payload), s.showing)) {
      setStale(false)
      return
    }
    if (s.dirty) {
      setStale(true)
      return
    }
    s.apply(payload)
    setStale(false)
  }, [])

  useWs(
    useCallback(
      (msg) => {
        if (msg.type === 'configs_changed') check(false)
      },
      [check]
    )
  )

  // The deferred check, run once the view settles.
  useEffect(() => {
    if (busy || !enabled || !deferred.current) return
    deferred.current = false
    check(false)
  }, [busy, enabled, check])

  return { stale, onReload: useCallback(() => check(true), [check]) }
}

// The Basic tab is the split case: ConfigEditorDrawer owns the payload (it fetches it,
// so a file too broken to render structurally can still open on Raw), while
// "are there unsaved edits" is only knowable inside the Form/Table/TextEditor
// under it. This carries the two halves across that boundary — the shell
// provides, the editor reports its dirty flag and gets back the same
// {stale, onReload} the self-contained views get from useConfigSync.
//
// Null outside a provider on purpose: ModXmlPage renders TableEditor read-only,
// and a shared editor must not require a shell it doesn't have.
export const EditorSyncContext = createContext(null)

export function useEditorSync(dirty) {
  const ctx = useContext(EditorSyncContext)
  const report = ctx?.reportDirty
  useEffect(() => {
    if (!report) return
    report(dirty)
    return () => report(false) // unmounted with the tab; its edits went with it
  }, [report, dirty])
  return ctx ? { stale: ctx.stale, onReload: ctx.onReload } : {}
}
