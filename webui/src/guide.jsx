// Guide state: is the drawer open, which step is expanded, and what the user
// has ticked off — plus, since 2026 Q3, what the *server* says is already
// done.
//
// It lives in a provider (mounted in Layout, around the whole shell) rather
// than in the drawer component because the guide is deliberately NOT a page
// any more: its links are navigation, so the drawer has to survive every route
// change, and three surfaces drive the same state — the sidebar's Guide
// button, the Dashboard's Guide card, and the drawer itself.
//
// The hand-ticked half is per-browser localStorage, best-effort like jobs.jsx
// and theme.jsx: no DayzConfig field, because it describes the reader. The
// detected half is GET /api/setup-state, and the two are combined by
// guideSteps.js::mergeProgress — server-done wins, the stored tick wins
// otherwise, and a failed fetch leaves the old behaviour untouched. Until
// then the guide read 0/10 on a fully installed, running server, which is the
// bug this answers.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import { Navigate } from 'react-router-dom'
import { api } from './api'
import { useRookie } from './rookie.jsx'
import { useWs } from './ws'
import {
  DRAWER_KEY,
  OPEN_KEY,
  PROGRESS_KEY,
  STEPS,
  STEP_IDS,
  firstPending,
  mergeProgress,
  readProgress,
  writeStored,
} from './guideSteps.js'

const GuideContext = createContext(null)

// WS events that can change an answer in /api/setup-state: the service and the
// running state (`service_changed`), the mod list (`mods_changed`), and the map,
// which is a config write (`settings_changed`). A finished install broadcasts
// `settings_changed` too — POST /api/update/run ends with it. The Steam login
// is measured in the background and announced with `steam_changed`.
const REFRESH_EVENTS = new Set(['service_changed', 'mods_changed', 'settings_changed', 'steam_changed'])

// A browser that never opened or closed the guide gets it open: the first
// visit is when "what do I do next" is the question, and the Dashboard's Guide
// tile is easy to miss among the others (role 1 user test, 2026 Q4). Closing
// it stores '0', so it stays closed from then on.
function readDrawerOpen() {
  try {
    const stored = localStorage.getItem(DRAWER_KEY)
    return stored === null ? true : stored === '1'
  } catch {
    return false
  }
}

// That first open is a full-page modal, not the drawer: a drawer beside a
// page the reader has never seen competes with it (a tester's feedback, 2026
// Q4). Following a link or "Show beside the page" docks it as the drawer, and
// every later open is the drawer. Nothing is stored for the modal itself: the
// DRAWER_KEY write on mount already makes the next load a later visit.
function isFirstVisit() {
  try {
    return localStorage.getItem(DRAWER_KEY) === null
  } catch {
    return false
  }
}

function readOpenStep() {
  try {
    const stored = localStorage.getItem(OPEN_KEY)
    if (stored && STEP_IDS.has(stored)) return stored
  } catch {
    // ignore — fall through to the first pending step
  }
  // Detection has not happened yet at mount (it waits for the first open), so
  // this is the stored record on its own — the same answer as before.
  return firstPending(readProgress()).id
}

export function GuideProvider({ children }) {
  // Rookie mode: a first visit starts closed, so a rookie has one thing to do
  // first. Nothing is stored or opened until the viewer acts or leaves Rookie mode.
  const { rookie } = useRookie()
  const holdRef = useRef(isFirstVisit() && rookie)
  const [open, setOpen] = useState(() => (holdRef.current ? false : readDrawerOpen()))
  const [modal, setModal] = useState(() => (holdRef.current ? false : isFirstVisit()))
  const [stored, setStored] = useState(readProgress)
  const [detected, setDetected] = useState(null) // null = never fetched / unreachable
  const [openId, setOpenId] = useState(readOpenStep)

  useEffect(() => {
    writeStored(PROGRESS_KEY, stored)
  }, [stored])
  useEffect(() => {
    writeStored(OPEN_KEY, openId)
  }, [openId])
  useEffect(() => {
    if (holdRef.current && rookie === false) {
      holdRef.current = false
      if (isFirstVisit()) {
        setOpen(true)
        setModal(true)
      }
    }
  }, [rookie])
  useEffect(() => {
    if (holdRef.current && !open) return
    holdRef.current = false
    writeStored(DRAWER_KEY, open ? '1' : '0')
  }, [open])

  // Another tab ticking a step off should not leave this one showing a stale
  // count (the drawer and the Dashboard card are often both on screen).
  useEffect(() => {
    function onStorage(e) {
      if (!e.key || e.key === PROGRESS_KEY) setStored(readProgress())
    }
    window.addEventListener('storage', onStorage)
    return () => window.removeEventListener('storage', onStorage)
  }, [])

  // A failed fetch leaves `detected` null, which mergeProgress treats as "no
  // opinion" — the guide then behaves exactly like the build before this.
  const loadSetupState = useCallback(() => {
    api
      .get('/api/setup-state')
      .then((data) => setDetected(data?.steps || null))
      .catch(() => {})
  }, [])

  // Once per page load, on first need: the drawer's first open, or the
  // Dashboard's Guide card mounting (ensureDetected). The provider is mounted
  // for the whole session, and a guide nobody looks at still costs no
  // request. The card used to wait for the drawer, so a fully installed box
  // read "0 of 12 done" until someone opened it (test pass 1, F3). That wait
  // bought nothing once plans/51 D made GET /api/setup-state cheap: it no
  // longer runs SteamCMD, only a `systemctl is-enabled`.
  const fetchedRef = useRef(false)
  const ensureDetected = useCallback(() => {
    if (fetchedRef.current) return
    fetchedRef.current = true
    loadSetupState()
  }, [loadSetupState])
  useEffect(() => {
    if (open) ensureDetected()
  }, [open, ensureDetected])

  const handleWs = useCallback(
    (msg) => {
      if (fetchedRef.current && REFRESH_EVENTS.has(msg.type)) loadSetupState()
    },
    [loadSetupState]
  )
  useWs(handleWs)

  const progress = useMemo(() => mergeProgress(stored, detected), [stored, detected])

  // Every way in after the first visit is the drawer, so each of these also
  // ends the modal.
  const openGuide = useCallback((stepId) => {
    if (stepId) setOpenId(stepId)
    setModal(false)
    setOpen(true)
  }, [])

  const toggleGuide = useCallback(() => {
    setModal(false)
    setOpen((v) => !v)
  }, [])
  const closeGuide = useCallback(() => {
    setModal(false)
    setOpen(false)
  }, [])
  const dockGuide = useCallback(() => setModal(false), [])

  // Clicking the button a step is already in undoes it; whatever the result,
  // jump to the next step still waiting on the user.
  //
  // A step the server reports done is NOT the user's to un-tick: the tick
  // would come straight back on the next fetch, which reads as a bug. So the
  // click writes nothing and only moves them on — the tick is no longer theirs
  // to own. Writes go to the stored record only; `progress` stays derived.
  const mark = useCallback(
    (id, state) => {
      const step = STEPS.find((s) => s.id === id)
      const serverOwns = !!(step?.detect && detected?.[step.detect]?.done)
      setStored((cur) => {
        const next = { ...cur }
        if (!serverOwns) {
          if (cur[id] === state) delete next[id]
          else next[id] = state
        }
        const merged = mergeProgress(next, detected)
        const pending = STEPS.find((s) => !merged[s.id])
        setOpenId(pending ? pending.id : id)
        return serverOwns ? cur : next
      })
    },
    [detected]
  )

  // A page whose own action finished a hand-ticked step ticks it: a map switch
  // or Quick setup's map is a pick, even when it lands on Chernarus, which
  // GET /api/setup-state cannot tell from a stock install (test pass 2, P15).
  // Unlike `mark`, it never un-ticks and never moves the open step.
  const markDone = useCallback((id) => {
    setStored((cur) => (cur[id] === 'done' ? cur : { ...cur, [id]: 'done' }))
  }, [])

  // "Start over" clears what the user ticked. It cannot clear what the server
  // can see, and pretending otherwise would just re-tick on the next fetch —
  // so detected steps stay done and the guide opens at the first step that is
  // genuinely still pending.
  const reset = useCallback(() => {
    setStored({})
    setOpenId(firstPending(mergeProgress({}, detected)).id)
  }, [detected])

  const value = useMemo(
    () => ({
      open,
      modal,
      openGuide,
      closeGuide,
      dockGuide,
      toggleGuide,
      progress,
      detected,
      refreshDetected: loadSetupState,
      ensureDetected,
      mark,
      markDone,
      reset,
      openId,
      setOpenId,
    }),
    [
      open,
      modal,
      openGuide,
      closeGuide,
      dockGuide,
      toggleGuide,
      progress,
      detected,
      loadSetupState,
      ensureDetected,
      mark,
      markDone,
      reset,
      openId,
    ]
  )

  return <GuideContext.Provider value={value}>{children}</GuideContext.Provider>
}

export function useGuide() {
  return useContext(GuideContext)
}

/**
 * Route element for the old /wizard path.
 *
 * The guide is a drawer now, but the path is still reachable from outside the
 * SPA's own tables — `zsm:wizard` links name it, and users have
 * bookmarks. So the route opens the drawer and hands the window back to the
 * Dashboard instead of 404ing.
 */
export function GuideRoute() {
  const { openGuide } = useGuide()
  useEffect(() => {
    openGuide()
  }, [openGuide])
  return <Navigate to="/" replace />
}
