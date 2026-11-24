import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { ACTIVITY_TTL_MS, readCache, writeCache } from '../persist'
import { tooltipAttrs } from './Tooltip.jsx'

const ToastContext = createContext(null)
// Second context on purpose: `useToast()` stays the bare push function every
// call site already destructures, so nothing outside the drawer changes.
const ToastHistoryContext = createContext(null)

let nextId = 1

// A toast is gone in 5-8s, which is easy to miss while looking at another page
// or scrolled inside a log. Every one is therefore also appended here and shown
// in the Activity drawer's Notifications pane until cleared. Bounded — it is a
// backlog to glance at, not an audit trail; the authoritative record of a job
// is its own accordion.
const MAX_HISTORY = 200

// A hovered toast, once left, stays at least this long.
const MIN_LEFT_MS = 1500

const HISTORY_KEY = 'zsm.activity.notifications' // localStorage, ACTIVITY_TTL_MS

// A row's `onClick` is a closure and cannot be stored; `jobId` is its
// serializable half, so a restored row can still deep-link into the job's
// accordion (the drawer rebuilds the handler — see ActivityDrawer).
function storableRow(e) {
  return { id: e.id, message: e.message, level: e.level, at: e.at, jobId: e.jobId, instance: e.instance }
}

function loadHistory() {
  const rows = readCache(HISTORY_KEY, ACTIVITY_TTL_MS, [])
  if (!Array.isArray(rows)) return []
  const cutoff = Date.now() - ACTIVITY_TTL_MS
  return rows.filter((e) => e && typeof e.message === 'string' && e.at > cutoff)
}

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([])
  const [history, setHistory] = useState(() => {
    const rows = loadHistory()
    // Restored ids were handed out by an earlier page load; keep counting above
    // them so a fresh toast can't collide with a row already in the list.
    for (const e of rows) if (e.id >= nextId) nextId = e.id + 1
    return rows
  })
  // id -> {timer, deadline, left}: the auto-close of each toast. A hover
  // pauses it (`left` keeps the time that was left) and leaving starts it again.
  const timers = useRef(new Map())
  // The instance a toast is about, unless the caller names one (a job's own).
  // This provider sits above RoleProvider, so the Activity drawer sets it
  // from the page's instance; the backlog outlives a switch of instance and
  // its badge says which server each row was about.
  const instanceRef = useRef(null)
  const setDefaultInstance = useCallback((id) => {
    instanceRef.current = id || null
  }, [])

  // Debounced so a burst of toasts (a job's start + finish, a failed reload
  // retrying) serializes the backlog once, not per entry.
  useEffect(() => {
    const t = setTimeout(() => writeCache(HISTORY_KEY, history.map(storableRow)), 500)
    return () => clearTimeout(t)
  }, [history])

  const dismiss = useCallback((id) => {
    setToasts((prev) => prev.filter((t) => t.id !== id))
    const entry = timers.current.get(id)
    if (entry) {
      clearTimeout(entry.timer)
      timers.current.delete(id)
    }
  }, [])

  // Close toast *id* after *ms*.
  const schedule = useCallback(
    (id, ms) => {
      timers.current.set(id, { timer: setTimeout(() => dismiss(id), ms), deadline: Date.now() + ms })
    },
    [dismiss]
  )

  // The pointer on a toast holds it open; leaving gives back the time that was
  // left, never under MIN_LEFT_MS, so it does not vanish the moment you leave.
  const pause = useCallback((id) => {
    const entry = timers.current.get(id)
    if (!entry || entry.left != null) return
    clearTimeout(entry.timer)
    entry.left = Math.max(MIN_LEFT_MS, entry.deadline - Date.now())
  }, [])

  const resume = useCallback(
    (id) => {
      const entry = timers.current.get(id)
      if (entry?.left != null) schedule(id, entry.left)
    },
    [schedule]
  )

  const clearHistory = useCallback(() => setHistory([]), [])

  const push = useCallback(
    (message, level = 'info', timeout = 5000, options = {}) => {
      const id = nextId++
      const entry = {
        id,
        message,
        level,
        at: Date.now(),
        onClick: options.onClick,
        jobId: options.jobId,
        instance: options.instance === undefined ? instanceRef.current : options.instance,
      }
      setToasts((prev) => [...prev, entry])
      // Same object in both lists: dismissing the floating toast must not touch
      // the history entry, and the history keeps the toast's click target alive
      // (a job deep-link is still worth following minutes later).
      setHistory((prev) => {
        const next = [...prev, entry]
        return next.length > MAX_HISTORY ? next.slice(next.length - MAX_HISTORY) : next
      })
      if (timeout > 0) schedule(id, timeout)
      return id
    },
    [schedule]
  )

  const historyValue = useMemo(
    () => ({ entries: history, clear: clearHistory, setDefaultInstance }),
    [history, clearHistory, setDefaultInstance]
  )

  return (
    <ToastContext.Provider value={push}>
      <ToastHistoryContext.Provider value={historyValue}>{children}</ToastHistoryContext.Provider>
      <div className="toast-stack">
        {toasts.map((t) => (
          <div
            key={t.id}
            className={`toast toast-${t.level}${t.onClick ? ' toast-clickable' : ''}`}
            onClick={() => {
              t.onClick?.()
              dismiss(t.id)
            }}
            onMouseEnter={() => pause(t.id)}
            onMouseLeave={() => resume(t.id)}
            role="alert"
          >
            {t.message}
            {t.onClick && <span className="toast-cta"> ›</span>}
            {/* The banners' × (index.css .banner-close). It only closes: a
                clickable toast's target is not followed. */}
            <button
              type="button"
              className="banner-close"
              aria-label="Close"
              {...tooltipAttrs('Close')}
              onClick={(e) => {
                e.stopPropagation()
                dismiss(t.id)
              }}
            >
              ×
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}

export function useToast() {
  const push = useContext(ToastContext)
  if (!push) throw new Error('useToast must be used within a ToastProvider')
  return push
}

// `{ entries, clear, setDefaultInstance }` — every toast of the last ACTIVITY_TTL_MS, oldest
// first (restored rows carry `jobId` and `instance` but no `onClick`). Only the Activity
// drawer's Notifications pane consumes it.
export function useToastHistory() {
  const value = useContext(ToastHistoryContext)
  if (!value) throw new Error('useToastHistory must be used within a ToastProvider')
  return value
}
