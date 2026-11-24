import { useRef, useState } from 'react'
import { RUNNING_STATUSES, useJobs } from '../jobs'
import { api } from '../api'
import { useSteamCheck } from './NeedsSteam.jsx'

// Shown when /api/status reports steam_ok === false. Steam Guard codes are
// sent only *after* a login is attempted, so this is a two-phase flow:
//
//   Phase 1 — POST /api/steam/request-guard with username+password. A
//   Guard-protected account fails the codeless attempt (Steam then sends a
//   code) and we reveal the required code field; a non-Guard account logs in
//   straight away. Not a job, so the expected failure never toasts. The
//   response's `factor` says where the code went ('email' | 'mobile' | null) —
//   an account on the Steam mobile authenticator gets no email at all, so the
//   prompt must not send the user looking for one. Hard failures (wrong
//   password, rate limit) come back as a 400 and toast as errors instead of
//   asking for a code that was never sent.
//
//   Phase 2 — POST /api/steam/login (a job) with username+password+code. The
//   job log is redacted server-side; its output + completion toast are handled
//   by the global Activity drawer.
//
// Both phases wait on SteamCMD, phase 2 for as long as its job runs (up to two
// minutes at a Guard prompt), so the submit button stays disabled with a
// spinner until then. A second click would start a second SteamCMD login.
//
// Credentials are never stored beyond the cached SteamCMD session.
export default function SteamLoginCard({ pushToast, onLoggedIn }) {
  const [open, setOpen] = useState(false)
  const [phase, setPhase] = useState('creds') // 'creds' | 'code'
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [guardCode, setGuardCode] = useState('')
  const [factor, setFactor] = useState(null) // 'email' | 'mobile' | null
  const [busy, setBusy] = useState(false) // a POST is in flight
  const [loginJobId, setLoginJobId] = useState(null)
  // Set before the first await: two clicks in one task both run the handler
  // before React has re-rendered the button as disabled.
  const sending = useRef(false)
  const { startJob, jobs } = useJobs()
  // A stale "logged out" (a login made in a terminal) is cleared from here.
  const { checking, check } = useSteamCheck()
  // The job's own status, as the Activity drawer shows it.
  const loginRunning =
    loginJobId !== null && jobs.some((j) => j.id === loginJobId && RUNNING_STATUSES.has(j.status))
  const working = busy || loginRunning

  // Runs `send` unless a request or the login job is still under way.
  async function submit(e, send) {
    e.preventDefault()
    if (sending.current || working) return
    sending.current = true
    setBusy(true)
    try {
      await send()
    } catch (err) {
      pushToast(err.message || 'Steam login failed', 'error')
    } finally {
      sending.current = false
      setBusy(false)
    }
  }

  function reset() {
    setPhase('creds')
    setPassword('')
    setGuardCode('')
    setFactor(null)
  }

  // Phase 1: trigger the Steam Guard code (or log in directly if no Guard).
  function handleRequestGuard(e) {
    return submit(e, async () => {
      const res = await api.post('/api/steam/request-guard', { username, password })
      if (res.logged_in) {
        pushToast('Steam login successful — credentials cached.', 'info')
        reset()
        onLoggedIn?.()
      } else {
        setFactor(res.factor || null)
        setPhase('code')
        pushToast(
          res.factor === 'mobile'
            ? 'Steam wants a two-factor code — read it in the Steam mobile app.'
            : res.factor === 'email'
              ? 'Steam Guard code sent — check your email.'
              : 'Steam wants a Steam Guard code.',
          'info',
        )
      }
    })
  }

  // Phase 2: verify with the Steam Guard code.
  function handleVerify(e) {
    return submit(e, async () => {
      const id = await startJob({
        endpoint: '/api/steam/login',
        body: { username, password, guard_code: guardCode },
        name: 'Steam login',
        kind: 'steam_login',
        onStatusChange: (status) => {
          if (status === 'success') {
            reset()
            onLoggedIn?.()
          }
        },
      })
      setLoginJobId(id ?? null)
    })
  }

  return (
    <div className="banner banner-warning">
      <div className="page-header">
        <span>
          Steam is not logged in — mods and server updates need cached Steam credentials.
        </span>
        <span className="action-bar">
          <button className="btn btn-ghost btn-small" onClick={check} disabled={checking}>
            {checking ? 'Checking…' : 'Check again'}
          </button>
          <button
            className="btn btn-primary btn-small"
            onClick={() => {
              if (open) reset()
              setOpen(!open)
            }}
          >
            {open ? 'Hide login' : 'Log in'}
          </button>
        </span>
      </div>
      {open && phase === 'creds' && (
        <form className="inline-form" onSubmit={handleRequestGuard}>
          <input
            className="text-input"
            placeholder="Steam username"
            value={username}
            autoComplete="off"
            onChange={(e) => setUsername(e.target.value)}
            required
          />
          <input
            className="text-input"
            type="password"
            placeholder="Password"
            value={password}
            autoComplete="new-password"
            onChange={(e) => setPassword(e.target.value)}
            required
          />
          <button className="btn btn-primary btn-small" type="submit" disabled={working}>
            {working ? <Busy label="Contacting Steam…" /> : 'Continue'}
          </button>
        </form>
      )}
      {open && phase === 'code' && (
        <form className="inline-form" onSubmit={handleVerify}>
          <span className="page-intro" style={{ width: '100%' }}>
            {factor === 'mobile' ? (
              <>
                Steam asked <strong>{username}</strong> for a two-factor code — open
                the Steam mobile app and read the current Steam Guard code. No email
                is sent for accounts using the mobile authenticator.
              </>
            ) : factor === 'email' ? (
              <>
                A Steam Guard code was emailed for <strong>{username}</strong>. Enter
                it to finish signing in. No email? Check spam, and note that Steam
                reuses a recent code instead of sending a new one.
              </>
            ) : (
              <>
                Steam wants a Steam Guard code for <strong>{username}</strong> — from
                the Steam mobile app if you use the authenticator, otherwise from the
                email Steam sent.
              </>
            )}
          </span>
          <input
            className="text-input"
            placeholder="Steam Guard code"
            value={guardCode}
            autoComplete="off"
            autoCapitalize="characters"
            onChange={(e) => setGuardCode(e.target.value.toUpperCase())}
            required
            autoFocus
          />
          <button className="btn btn-primary btn-small" type="submit" disabled={working}>
            {working ? <Busy label="Logging in…" /> : 'Log in to Steam'}
          </button>
          <button
            type="button"
            className="btn btn-small"
            disabled={working}
            onClick={() => setPhase('creds')}
          >
            Back
          </button>
        </form>
      )}
    </div>
  )
}

// A busy button's label: the spinner, then what is happening.
function Busy({ label }) {
  return (
    <>
      <span className="spinner" aria-hidden="true" />
      {label}
    </>
  )
}
