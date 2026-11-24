// Per-browser cache with an expiry. Best-effort like every other localStorage
// use in the app (theme.jsx, dashboardLayout.js, jobs.jsx): a blocked, full or
// corrupt storage must never break a render, so every path falls back to the
// caller's default instead of throwing.
//
// Values are wrapped in `{ at, v }` and a read past `maxAgeMs` deletes the key,
// so a cache ages out on its own instead of growing forever. `at` is the *write*
// time — a list that keeps being rewritten stays fresh, which is why callers
// whose entries carry their own timestamps (a toast's `at`, a job's
// `created_at`) also drop stale *entries* on load; this TTL is the backstop for
// the blob as a whole.
//
// Everything the Activity drawer shows EXCEPT the Server Log rides on this (see
// jobs.jsx and components/Toast.jsx). The Server Log is deliberately not
// cached: it is a tail of a file the server re-reads on every load, so a stored
// copy could only ever be a stale duplicate of what `GET /api/logs/rpt`
// returns anyway.

// How long the Activity drawer's state stays interesting. Long enough that
// yesterday's failed run is still readable this morning, short enough that the
// drawer isn't a permanent archive.
export const ACTIVITY_TTL_MS = 24 * 60 * 60 * 1000 // 24h

export function readCache(key, maxAgeMs = ACTIVITY_TTL_MS, fallback = null) {
  try {
    const raw = localStorage.getItem(key)
    if (!raw) return fallback
    const env = JSON.parse(raw)
    if (!env || typeof env !== 'object' || !('v' in env)) return fallback
    if (!Number.isFinite(env.at) || Date.now() - env.at > maxAgeMs) {
      localStorage.removeItem(key)
      return fallback
    }
    return env.v
  } catch {
    return fallback
  }
}

export function writeCache(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify({ at: Date.now(), v: value }))
  } catch {
    // storage full/unavailable — caching is best-effort
  }
}
