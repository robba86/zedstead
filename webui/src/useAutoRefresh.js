import { useCallback, useEffect, useRef, useState } from 'react'

// Shared "re-run this fetch on a timer, and show the timer" hook.
//
// Countdown is wall-clock (Date.now() against a start stamp), not an
// accumulated tick count, so a backgrounded tab — where setInterval is
// throttled — resumes with the right elapsed time instead of drifting.
//
// Three ways the timer stops:
//   - `paused`  — the user clicked pause. Persisted per-browser under
//                 `storageKey` (localStorage, best-effort try/catch like
//                 jobs.jsx / theme.jsx). Not a DayzConfig field: it describes
//                 this viewer's tab, not the server.
//   - `enabled: false` — the caller says now is a bad time (a job is mutating
//                 the server). Not persisted, and it does not clobber the
//                 user's own pause state.
//   - in-flight — `fn` is awaited, so a slow report can't stack up calls.
// Whenever it resumes the countdown restarts from zero.

// One tick per second: the countdown is only ever shown in whole seconds, and
// the consumers (DoctorPage, DoctorCard) re-render their whole tree on it.
const TICK_MS = 1000

function readPaused(storageKey) {
  if (!storageKey) return false
  try {
    return localStorage.getItem(storageKey) === '0'
  } catch {
    return false
  }
}

export default function useAutoRefresh(fn, { interval = 60000, enabled = true, storageKey } = {}) {
  const [paused, setPausedState] = useState(() => readPaused(storageKey))
  const [elapsed, setElapsed] = useState(0)

  const fnRef = useRef(fn)
  fnRef.current = fn
  const startRef = useRef(Date.now())
  const inFlightRef = useRef(false)

  const active = enabled && !paused

  const reset = useCallback(() => {
    startRef.current = Date.now()
    setElapsed(0)
  }, [])

  const setPaused = useCallback(
    (value) => {
      setPausedState((prev) => {
        const next = typeof value === 'function' ? value(prev) : value
        try {
          if (storageKey) localStorage.setItem(storageKey, next ? '0' : '1')
        } catch {
          // storage full/unavailable — persistence is best-effort
        }
        return next
      })
    },
    [storageKey]
  )
  const toggle = useCallback(() => setPaused((p) => !p), [setPaused])

  // Run now, off-cycle. Also the tick's own runner, so a manual refresh and a
  // timed one share the in-flight guard and both restart the countdown.
  const runNow = useCallback(async () => {
    if (inFlightRef.current) return
    inFlightRef.current = true
    try {
      await fnRef.current()
    } catch {
      // the caller owns error reporting; the timer keeps going regardless
    } finally {
      inFlightRef.current = false
      reset()
    }
  }, [reset])

  // Restart the countdown whenever the timer resumes (including on mount,
  // where the page has just done its own initial load).
  useEffect(() => {
    if (active) reset()
  }, [active, reset])

  useEffect(() => {
    if (!active) return undefined
    const id = setInterval(() => {
      const since = Date.now() - startRef.current
      if (since >= interval) runNow()
      // Same whole second (a throttled tab fires late and bunched) ⇒ same
      // state object ⇒ React skips the re-render.
      else setElapsed((prev) => (Math.floor(prev / 1000) === Math.floor(since / 1000) ? prev : since))
    }, TICK_MS)
    return () => clearInterval(id)
  }, [active, interval, runNow])

  const progress = active ? Math.min(1, elapsed / interval) : 0
  const secondsLeft = Math.max(0, Math.ceil((interval - elapsed) / 1000))

  return { paused, setPaused, toggle, active, progress, secondsLeft, interval, reset, runNow }
}
