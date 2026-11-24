import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { api } from './api'
import { useWs } from './ws'
import { instanceKey } from './instance'

// Global server-telemetry store. Hydrates from sessionStorage instantly (so
// cached fields paint on the first frame across navigation/reload), fetches
// /api/status once, subscribes to the `status` WS channel, and merges each
// push into state + cache. Mounted once around Layout so every authed view can
// read it via useServerStatus().

const ServerStatusContext = createContext(null)
// A push that changes nothing still moves `receivedAt`, which InfoBar reads to
// call the status stale (15 s), but only this often: every 5 s push used to
// redraw every component that reads the status, a stopped server included.
const UNCHANGED_RECEIVED_MS = 10000

function sameValue(a, b) {
  if (a === b) return true
  if (a && b && typeof a === 'object' && typeof b === 'object') return JSON.stringify(a) === JSON.stringify(b)
  return false
}
// Per instance (plans/62): pvp's tab must not paint main's last status.
const STORAGE_KEY = instanceKey('zsm.serverStatus')

function readCache() {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY)
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

function writeCache(value) {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(value))
  } catch {
    // sessionStorage full/unavailable — non-fatal, cache is best-effort
  }
}

export function ServerStatusProvider({ children }) {
  const cached = readCache()
  const [status, setStatus] = useState(cached?.data ?? null)
  const [receivedAt, setReceivedAt] = useState(cached?.receivedAt ?? 0)
  // A failed /api/status is not the same thing as a stopped server. Swallowing
  // it renders an indistinguishable "Stopped", so keep it and let views say so.
  const [error, setError] = useState(null)

  const statusRef = useRef(status)
  const receivedRef = useRef(receivedAt)

  // Merge (not replace) so WS pushes — which omit steam_ok — don't drop the
  // steam_ok captured at hydration. A push equal to what is shown keeps the
  // same object, so nothing that reads it redraws (2026 Q4, a slow VM).
  const apply = useCallback((incoming) => {
    const prev = statusRef.current
    const changed = !prev || Object.keys(incoming).some((k) => !sameValue(prev[k], incoming[k]))
    const now = Date.now()
    if (!changed && now - receivedRef.current < UNCHANGED_RECEIVED_MS) return
    const merged = changed ? { ...(prev || {}), ...incoming } : prev
    statusRef.current = merged
    receivedRef.current = now
    if (changed) setStatus(merged)
    setReceivedAt(now)
    writeCache({ data: merged, receivedAt: now })
  }, [])

  const refresh = useCallback(
    () =>
      api
        .get('/api/status')
        .then((data) => {
          setError(null)
          apply(data)
        })
        .catch((err) => setError(err?.message || 'Status unavailable')),
    [apply]
  )

  useEffect(() => {
    refresh() // one-shot hydration (includes steam_ok)
  }, [refresh])

  const handleWs = useCallback(
    (msg) => {
      if (msg.type === 'status' && msg.data) {
        setError(null)
        apply(msg.data)
      }
      // start/stop/restart/regenerate/remove/boot broadcast service_changed —
      // refresh immediately instead of waiting for the next 5s poll tick.
      else if (msg.type === 'service_changed') refresh()
      // The server measures the Steam login at startup, after a job that ran
      // SteamCMD and on "Check again" — never on a page load (plans/51 D).
      else if (msg.type === 'steam_changed') apply({ steam_ok: msg.logged_in })
    },
    [apply, refresh]
  )
  const { connected, subscribe, unsubscribe } = useWs(handleWs)

  useEffect(() => {
    subscribe('status')
    return () => unsubscribe('status')
  }, [subscribe, unsubscribe])

  const value = useMemo(
    () => ({ status, receivedAt, connected, error, refresh }),
    [status, receivedAt, connected, error, refresh]
  )
  return <ServerStatusContext.Provider value={value}>{children}</ServerStatusContext.Provider>
}

export function useServerStatus() {
  const ctx = useContext(ServerStatusContext)
  if (!ctx) throw new Error('useServerStatus must be used within a ServerStatusProvider')
  return ctx
}
