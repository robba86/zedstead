import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { api } from './api'
import { useWs } from './ws'
import { ACTIVITY_TTL_MS, readCache, writeCache } from './persist'
import { useToast } from './components/Toast.jsx'
import { useRole } from './role.jsx'

// Global jobs store. One per app (mounted once around the shell in Layout).
// Holds every process the user has started or reattached to, plus its log
// buffer, so process output survives navigation AND page refresh:
//   - hydrate on mount from GET /api/jobs (+ ?log_after= per job) so a refresh
//     re-shows in-progress and recently-finished jobs with their logs;
//   - merge incremental job_log / job_status WS pushes (broadcast to all
//     sockets) deduped by seq;
//   - fire a completion toast (deep-linking to the drawer accordion) when a
//     tracked job finishes;
//   - cache the finished jobs + a tail of their logs in localStorage
//     (`persist.js`, 24h) so the drawer survives a backend restart too.
// The ActivityDrawer renders these as stacked accordions; pages call startJob
// instead of holding their own jobId.

const JobsContext = createContext(null)

const RUNNING = new Set(['pending', 'running'])
const TERMINAL = new Set(['success', 'failed', 'cancelled'])

const SHOWN_KEY = 'zsm.jobs.shown' // localStorage: ids of currently-shown jobs
const EXPANDED_KEY = 'zsm.jobs.expanded' // localStorage: { id: bool }
const CACHE_KEY = 'zsm.jobs.cache' // localStorage: { jobs, logs } of finished jobs
const DRAWER_KEY = 'zsm.activity.open' // localStorage: '1' | '0'

// What a reload restores without asking the server. The backend keeps only the
// last 50 jobs and forgets all of them when it restarts — which is exactly when
// the previous run's log is worth reading — so the drawer keeps its own
// bounded copy. Sized to stay well inside a localStorage quota: ~15 x 200 lines.
const MAX_CACHED_JOBS = 15
const MAX_CACHED_LINES = 200

// Cap per-job log retention in the browser. The backend rings each job's log
// at 2000 lines (app/web/jobs.py `_LOG_MAXLEN`) but broadcasts EVERY line, so
// an unbounded client buffer grows without limit — and re-copying it per line
// makes streaming a long SteamCMD job quadratic. Matching the backend's 2000
// keeps a page reload from appearing to gain history. Mirrors the RPT cap in
// ActivityDrawer.jsx (`MAX_LINES`).
const MAX_LOG_LINES = 2000

// Only *finished* jobs are cached. A running one comes back from GET /api/jobs,
// which is the only thing that actually knows whether it is still going; a
// restored "running" accordion would be a lie the drawer could never correct.
function loadJobCache(install) {
  const cached = readCache(CACHE_KEY, ACTIVITY_TTL_MS, null)
  // A cache made under another install id is another server's jobs: a
  // reinstall, or a second server on the same host and port.
  const data = cached?.install === install ? cached : null
  const cutoff = (Date.now() - ACTIVITY_TTL_MS) / 1000 // job.created_at is epoch SECONDS
  const jobs = (Array.isArray(data?.jobs) ? data.jobs : []).filter(
    (j) => j?.id && TERMINAL.has(j.status) && (j.created_at || 0) > cutoff
  )
  const logs = {}
  for (const j of jobs) {
    const entry = data.logs?.[j.id]
    if (entry) logs[j.id] = { lines: entry.lines || [], seq: entry.seq || 0 }
  }
  return { jobs, logs }
}

// A job's result may carry a `notice` the admin must act on (a mod install
// while the server runs), which turns the success toast into a warning.
function statusToast(job) {
  if (job.status === 'success' && job.result?.notice) {
    return { text: `✓ ${job.name} finished. ${job.result.notice}`, level: 'warning' }
  }
  if (job.status === 'success') return { text: `✓ ${job.name} finished`, level: 'info' }
  if (job.status === 'failed') return { text: `✕ ${job.name} failed`, level: 'error' }
  if (job.status === 'cancelled') {
    return { text: `${job.name} ${job.kill_requested ? 'killed' : 'cancelled'}`, level: 'info' }
  }
  return null
}

export function JobsProvider({ children }) {
  const pushToast = useToast()
  // Finished processes + their logs from the last day, back before the first
  // paint so the drawer isn't empty while GET /api/jobs is in flight.
  const { install } = useRole()
  const [boot] = useState(() => loadJobCache(install))
  const [jobs, setJobs] = useState(boot.jobs)
  const [logs, setLogs] = useState(boot.logs) // id -> { lines: string[], seq: number }
  const [expanded, setExpanded] = useState(() => readCache(EXPANDED_KEY, ACTIVITY_TTL_MS, {}) || {})
  const [drawerOpen, setDrawerOpenState] = useState(() => {
    try {
      return localStorage.getItem(DRAWER_KEY) === '1'
    } catch {
      return false
    }
  })
  const [focusJobId, setFocusJobId] = useState(null)
  // id -> true from the click until the server confirms (job.cancel_requested)
  // or the job ends. Cancelling is slow — the job only stops at its next
  // checkpoint — so the button has to read as busy immediately.
  const [cancelling, setCancelling] = useState({})

  const listenersRef = useRef(new Map()) // id -> onStatusChange(status, job)
  // Restored jobs already finished before this page load — never re-toast them.
  const notifiedRef = useRef(new Set(boot.jobs.map((j) => j.id))) // toasted ids
  const knownIdsRef = useRef(new Set(boot.jobs.map((j) => j.id))) // ids we show —
  // updated synchronously so WS frames for a just-started job aren't dropped
  // before setState flushes
  // Ids seen finished. A job never goes back from finished to running: the
  // catch-up GET trackJob sends can be answered while the job still runs and
  // arrive *after* the WS push that finished it, which left a 28 ms backup's
  // row on "Running" until a reload (test pass 1, F59).
  const finishedRef = useRef(
    new Set(boot.jobs.filter((j) => TERMINAL.has(j.status)).map((j) => j.id))
  )
  const logsRef = useRef(logs)
  logsRef.current = logs
  // Flipped once GET /api/jobs has answered. Until then `jobs` is only what the
  // cache restored, and writing that back would prune away jobs the server is
  // about to hand us.
  const hydratedRef = useRef(false)

  // Cache what the drawer shows — the shown ids, which accordions are open, and
  // the finished jobs with (a tail of) their output — so a reload, a new tab or
  // a backend restart still shows the last runs instead of an empty drawer.
  // Debounced: a job under way appends a log line per WS frame, and the write
  // only has to land once output pauses. The Server Log is NOT cached (see
  // persist.js).
  useEffect(() => {
    if (!hydratedRef.current) return
    const t = setTimeout(() => {
      const finished = jobs.filter((j) => TERMINAL.has(j.status)).slice(-MAX_CACHED_JOBS)
      const keptLogs = {}
      for (const j of finished) {
        const cur = logs[j.id]
        if (!cur) continue
        keptLogs[j.id] = { seq: cur.seq, lines: cur.lines.slice(-MAX_CACHED_LINES) }
      }
      writeCache(CACHE_KEY, { install, jobs: finished, logs: keptLogs })
      writeCache(SHOWN_KEY, jobs.map((j) => j.id))
      // Prune to the jobs still on show, or the map grows for every job ever run.
      const openState = {}
      for (const j of jobs) if (expanded[j.id] !== undefined) openState[j.id] = !!expanded[j.id]
      writeCache(EXPANDED_KEY, openState)
    }, 800)
    return () => clearTimeout(t)
  }, [jobs, logs, expanded, install])

  const setDrawerOpen = useCallback((v) => {
    setDrawerOpenState((prev) => {
      const next = typeof v === 'function' ? v(prev) : v
      try {
        localStorage.setItem(DRAWER_KEY, next ? '1' : '0')
      } catch {
        // best-effort
      }
      return next
    })
  }, [])
  const toggleDrawer = useCallback(() => setDrawerOpen((v) => !v), [setDrawerOpen])

  // Open the drawer and expand + scroll to a job's accordion (toast target).
  const focusJob = useCallback(
    (id) => {
      setDrawerOpen(true)
      setExpanded((e) => ({ ...e, [id]: true }))
      setFocusJobId(id)
    },
    [setDrawerOpen]
  )
  const clearFocus = useCallback(() => setFocusJobId(null), [])

  const clearCancelling = useCallback((id) => {
    setCancelling((prev) => {
      if (!prev[id]) return prev
      const n = { ...prev }
      delete n[id]
      return n
    })
  }, [])

  // Returns false when *incoming* is stale (see finishedRef) and was dropped,
  // so callers don't announce a status the row will not show.
  const upsertJob = useCallback((incoming) => {
    knownIdsRef.current.add(incoming.id)
    if (finishedRef.current.has(incoming.id) && !TERMINAL.has(incoming.status)) return false
    if (TERMINAL.has(incoming.status)) finishedRef.current.add(incoming.id)
    setJobs((prev) => {
      const idx = prev.findIndex((j) => j.id === incoming.id)
      if (idx === -1) return [...prev, incoming]
      const next = [...prev]
      next[idx] = { ...next[idx], ...incoming }
      return next
    })
    return true
  }, [])

  const appendLog = useCallback((id, entries) => {
    if (!entries || !entries.length) return
    setLogs((prev) => {
      const cur = prev[id] || { lines: [], seq: 0 }
      let seq = cur.seq
      const added = []
      for (const e of entries) {
        if (e.seq > seq) {
          added.push(e.line)
          seq = e.seq
        }
      }
      if (!added.length) return prev
      const lines = [...cur.lines, ...added]
      return {
        ...prev,
        [id]: {
          // `seq` is the dedup cursor and must keep advancing even when lines
          // are dropped off the front — never derive it from lines.length.
          lines: lines.length > MAX_LOG_LINES ? lines.slice(lines.length - MAX_LOG_LINES) : lines,
          seq,
        },
      }
    })
  }, [])

  // Notify a page listener + fire the one-shot completion toast for a job.
  const fireStatus = useCallback(
    (job) => {
      const cb = listenersRef.current.get(job.id)
      if (cb) {
        try {
          cb(job.status, job)
        } catch {
          // page callback errors must not break the store
        }
      }
      if (TERMINAL.has(job.status)) {
        clearCancelling(job.id)
        if (!notifiedRef.current.has(job.id)) {
          notifiedRef.current.add(job.id)
          const t = statusToast(job)
          // `jobId` is the deep-link's serializable half: the Notifications
          // backlog outlives this page load, `onClick` does not.
          if (t) {
            pushToast(t.text, t.level, 8000, {
              onClick: () => focusJob(job.id),
              jobId: job.id,
              instance: job.instance ?? null,
            })
          }
        }
      }
    },
    [pushToast, focusJob, clearCancelling]
  )

  // REST catch-up: fetch the job dict + any log lines after our last seq.
  const hydrateJob = useCallback(
    async (id, { silent = false } = {}) => {
      try {
        const seq = logsRef.current[id]?.seq ?? 0
        const data = await api.get(`/api/jobs/${id}?log_after=${seq}`)
        const { log, ...job } = data
        const current = upsertJob(job)
        if (log) appendLog(id, log)
        if (current && !silent) fireStatus(job)
      } catch {
        // transient — WS pushes and later polls will catch up
      }
    },
    [upsertJob, appendLog, fireStatus]
  )

  // Register an already-started job id (POST done by the caller).
  const trackJob = useCallback(
    (id, meta = {}) => {
      if (!id) return
      upsertJob({ id, name: meta.name || 'Process', kind: meta.kind || '', status: 'pending' })
      if (meta.onStatusChange) listenersRef.current.set(id, meta.onStatusChange)
      setExpanded((e) => ({ ...e, [id]: true }))
      setDrawerOpen(true)
      hydrateJob(id, { silent: false })
      return id
    },
    [upsertJob, hydrateJob, setDrawerOpen]
  )

  // POST an action that returns { job_id } and start tracking it. Throws on
  // request failure so the caller can toast + reset its own busy flag.
  const startJob = useCallback(
    async ({ endpoint, body, name, kind, onStatusChange }) => {
      const data = await api.post(endpoint, body)
      const id = data?.job_id
      if (id) trackJob(id, { name, kind, onStatusChange })
      return id
    },
    [trackJob]
  )

  const cancelJob = useCallback(
    (id) => {
      setCancelling((prev) => (prev[id] ? prev : { ...prev, [id]: true }))
      // On failure (already finished / gone) drop the flag — the status update
      // that would otherwise clear it may never arrive.
      return api.post(`/api/jobs/${id}/cancel`).catch(() => clearCancelling(id))
    },
    [clearCancelling]
  )

  // Force-stop for a cancel that is stuck (POST /api/jobs/{id}/kill). The
  // drawer only offers it after a cancel was requested, behind a confirm.
  const killJob = useCallback(
    (id) =>
      api.post(`/api/jobs/${id}/kill`).catch((err) => {
        pushToast(err.message || 'Could not kill the process', 'error')
      }),
    [pushToast]
  )

  const dismissJob = useCallback((id) => {
    listenersRef.current.delete(id)
    notifiedRef.current.delete(id)
    knownIdsRef.current.delete(id)
    clearCancelling(id)
    setJobs((prev) => prev.filter((j) => j.id !== id))
    setLogs((prev) => {
      const n = { ...prev }
      delete n[id]
      return n
    })
    setExpanded((prev) => {
      const n = { ...prev }
      delete n[id]
      return n
    })
  }, [clearCancelling])

  const toggleExpanded = useCallback(
    (id) => setExpanded((e) => ({ ...e, [id]: !e[id] })),
    []
  )

  const handleWs = useCallback(
    (msg) => {
      if (msg.type === 'job_log') {
        if (knownIdsRef.current.has(msg.job_id)) {
          appendLog(msg.job_id, [{ seq: msg.seq, line: msg.line }])
        }
      } else if (msg.type === 'job_status' && msg.job) {
        if (knownIdsRef.current.has(msg.job.id) && upsertJob(msg.job)) {
          fireStatus(msg.job)
        }
      }
    },
    [appendLog, upsertJob, fireStatus]
  )
  useWs(handleWs)

  // One-shot hydration: re-show running + previously-shown jobs after a reload.
  useEffect(() => {
    let cancelled = false
    api
      .get('/api/jobs')
      .then((list) => {
        if (cancelled || !Array.isArray(list)) return
        const shownIds = readCache(SHOWN_KEY, ACTIVITY_TTL_MS, []) || []
        const show = list.filter((j) => RUNNING.has(j.status) || shownIds.includes(j.id))
        if (!show.length) return
        // Merge, never replace: the cache may hold finished jobs the backend
        // has already dropped, and those accordions must survive hydration.
        setJobs((prev) => {
          const next = [...prev]
          for (const j of show) {
            const idx = next.findIndex((p) => p.id === j.id)
            if (idx === -1) next.push(j)
            else if (TERMINAL.has(next[idx].status) && !TERMINAL.has(j.status)) continue
            else next[idx] = { ...next[idx], ...j }
          }
          return next
        })
        show.forEach((j) => {
          if (TERMINAL.has(j.status)) finishedRef.current.add(j.id)
        })
        // Don't re-toast jobs that already finished before this page loaded.
        show.forEach((j) => {
          if (TERMINAL.has(j.status)) notifiedRef.current.add(j.id)
        })
        setExpanded((prev) => {
          const e = { ...prev }
          show.forEach((j) => {
            if (e[j.id] === undefined) e[j.id] = RUNNING.has(j.status)
          })
          return e
        })
        show.forEach((j) => hydrateJob(j.id, { silent: true }))
      })
      .catch(() => {})
      .finally(() => {
        if (!cancelled) hydratedRef.current = true
      })
    return () => {
      cancelled = true
    }
  }, [hydrateJob])

  const jobRunning = jobs.some((j) => RUNNING.has(j.status))

  const value = {
    jobs,
    logs,
    cancelling,
    expanded,
    drawerOpen,
    focusJobId,
    jobRunning,
    startJob,
    trackJob,
    cancelJob,
    killJob,
    dismissJob,
    toggleExpanded,
    setDrawerOpen,
    toggleDrawer,
    focusJob,
    clearFocus,
  }
  return <JobsContext.Provider value={value}>{children}</JobsContext.Provider>
}

export function useJobs() {
  const ctx = useContext(JobsContext)
  if (!ctx) throw new Error('useJobs must be used within a JobsProvider')
  return ctx
}

// A cancel is pending while either half says so: the optimistic click flag
// (instant, this tab) or the server's cancel_requested (survives a refresh and
// covers a cancel fired from another tab).
export function isCancelPending(job, cancelling) {
  return !!(job && (cancelling?.[job.id] || job.cancel_requested))
}

export { RUNNING as RUNNING_STATUSES, TERMINAL as TERMINAL_STATUSES }
