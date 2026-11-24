import Select from '../components/Select.jsx'
import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api, LOGIN_NOTICE_KEY } from '../api'
import { INSTANCE_ID, instanceHref, instanceOptionLabel } from '../instance'
import Wordmark from '../components/Wordmark.jsx'
import { TRADEMARK_NOTICE } from '../legal.js'

function readLoginNotice() {
  try {
    return sessionStorage.getItem(LOGIN_NOTICE_KEY) || ''
  } catch {
    return ''
  }
}

// The name is **optional here and required by arithmetic** (plans/43 step 5b).
//
// An install that never created accounts, or has exactly one, authenticates a
// blank name against that single account — which is what kept this form
// working through the migration. With two or more, a blank name is ambiguous
// and the backend refuses it, so without this field nobody but the first
// account could sign in.
//
// The hint below is static on purpose. Telling the user "this panel has
// several accounts, so a name is required" would answer, to someone who is
// not signed in, a question they have no business asking.
//
// **Two-factor sign-in is two submits, not two pages** (TODO B5). The form
// does not know whether an account has a second factor until it has sent the
// password — and it must not: answering that before the password is checked
// would tell an unauthenticated caller which accounts are worth attacking.
// So the first submit comes back 401 with `totp_required`, the code box
// appears, and the second submit sends all three fields. The password is sent
// again rather than held server-side, which is what keeps this stateless —
// there is no half-finished login sitting in memory waiting to be claimed.
//
// **A panel serving several instances asks which to open, after the password**
// (plans/62 D10). The answer to a successful sign-in lists them; asking before
// it would tell anybody which servers the panel runs. The session is the
// panel's whichever is picked, so this only decides where the browser lands,
// and with one instance the step does not appear. `?i=` (set when a session
// ran out on an instance's page) is the one offered first.
// The full badge, not the sidebar's small mark: this is the only thing on
// screen that says which app you are signing in to. Masked in CSS rather than
// <img>, so it takes a theme token. The name above it is the drawn wordmark,
// the one the sidebar shows; the h1 keeps it the page's heading.
function LoginBrand() {
  return (
    <>
      <h1 className="login-title">
        <Wordmark long />
      </h1>
      <div className="login-badge" aria-hidden="true" />
    </>
  )
}

// Bohemia's trademark notice at the foot of the card, on both steps of the
// sign-in, so it is on screen before anyone signs in.
function LoginLegal() {
  return <p className="login-legal">{TRADEMARK_NOTICE}</p>
}

export default function LoginPage({ onLogin }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [needsCode, setNeedsCode] = useState(false)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  // After a sign-in that named several instances: { instances, startup }.
  const [choice, setChoice] = useState(null)
  const [picked, setPicked] = useState('')
  const [params] = useSearchParams()
  const codeRef = useRef(null)
  const pickRef = useRef(null)
  // Left by api.js when a write was refused for want of a session: the change
  // is lost, and this is the only place left to say so. Shown once, cleared in
  // an effect rather than in the initializer, because StrictMode renders twice
  // and the second read must still find it.
  const [notice] = useState(readLoginNotice)

  useEffect(() => {
    try {
      sessionStorage.removeItem(LOGIN_NOTICE_KEY)
    } catch {
      // best-effort, like the write in api.js
    }
  }, [])

  // Focus the code box the moment it appears: the user has just been sent to
  // their phone and comes back to a form whose cursor is nowhere useful.
  useEffect(() => {
    if (needsCode) codeRef.current?.focus()
  }, [needsCode])

  useEffect(() => {
    if (choice) pickRef.current?.focus()
  }, [choice])

  async function handleSubmit(e) {
    e.preventDefault()
    if (choice) {
      // A full load: the page starts on that instance with nothing carried
      // over from the sign-in form.
      window.location.assign(instanceHref(picked, choice.startup))
      return
    }
    setError('')
    setBusy(true)
    try {
      const body = await api.post('/api/auth/login', { username, password, code })
      if (body?.instances?.length > 1) {
        const ids = body.instances.map((row) => row.id)
        const wanted = [params.get('i'), INSTANCE_ID, body.startup].find((id) => ids.includes(id))
        setPicked(wanted || ids[0])
        setChoice({ instances: body.instances, startup: body.startup })
        return
      }
      onLogin()
    } catch (err) {
      if (err.body?.totp_required) {
        setNeedsCode(true)
        // A stale code must not sit in the box for the next attempt — the
        // backend refuses a code twice, so resubmitting the same six digits
        // reads as "2FA is broken" rather than "that one is spent".
        setCode('')
      }
      setError(err.message || 'Login failed')
    } finally {
      setBusy(false)
    }
  }

  // Nothing to send on the first step without a password; nothing to send on
  // the second without six digits.
  const canSubmit = choice ? !!picked : needsCode ? code.trim().length > 0 : password.length > 0

  if (choice) {
    return (
      <div className="login-page">
        <form className="login-form" onSubmit={handleSubmit}>
          <LoginBrand />
          <p className="login-subtitle">Which server do you want to manage?</p>
          <Select
            ref={pickRef}
            className="field"
            name="instance"
            aria-label="Instance"
            value={picked}
            onChange={setPicked}
            options={choice.instances.map((row) => ({ value: row.id, label: instanceOptionLabel(row) }))}
          />
          <p className="login-hint">You can switch at any time from the sidebar.</p>
          <button type="submit" className="btn btn-primary" disabled={!canSubmit}>
            Open
          </button>
          <LoginLegal />
        </form>
      </div>
    )
  }

  return (
    <div className="login-page">
      <form className="login-form" onSubmit={handleSubmit}>
        <LoginBrand />
        <p className="login-subtitle">Sign in to manage your DayZ server.</p>
        {notice && (
          <div className="login-error" role="alert">
            {notice}
          </div>
        )}
        <input
          type="text"
          name="username"
          autoComplete="username"
          placeholder="Username"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          autoFocus
        />
        <input
          type="password"
          name="password"
          autoComplete="current-password"
          placeholder="Password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        {needsCode ? (
          <>
            <input
              ref={codeRef}
              type="text"
              name="code"
              // `one-time-code` is what lets a phone offer the code from its
              // SMS/authenticator, and inputMode brings up the number pad.
              autoComplete="one-time-code"
              inputMode="numeric"
              pattern="[0-9]*"
              maxLength={6}
              placeholder="6-digit code"
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))}
            />
            <p className="login-hint">
              From your authenticator app. If it keeps refusing, check the phone&apos;s
              clock is set automatically.
            </p>
          </>
        ) : (
          <p className="login-hint">
            Leave the name blank if this app still uses a single password.
          </p>
        )}
        {error && <div className="login-error">{error}</div>}
        <button type="submit" className="btn btn-primary" disabled={busy || !canSubmit}>
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
        <LoginLegal />
      </form>
    </div>
  )
}
