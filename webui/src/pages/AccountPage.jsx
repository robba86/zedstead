import { useState } from 'react'
import { api } from '../api'
import BetaBadge from '../components/BetaBadge.jsx'
import PageIntro from '../components/PageIntro.jsx'
import { useToast } from '../components/Toast.jsx'
import { useRole } from '../role.jsx'
import PanelNote from '../components/PanelNote.jsx'

// Your own account: the password you sign in with, and your second factor.
//
// **This page exists because /settings is admin-only** (plans/43 step 5c). The
// "Change web password" form used to live there, which since roles were
// enforced meant a moderator had to ask an admin to rotate their password —
// i.e. one of them saying it out loud. Both things here act on the caller's
// own row and nobody else's, so the whole page is `viewer` in routes.js and
// its three routes are in `permissions.SESSION_WRITES`.
//
// What is deliberately *not* here: turning off somebody else's second factor.
// An account whose phone is lost cannot sign in to fix itself, so the recovery
// path is `zsm users totp disable <name>` — a web route that could strip
// another admin's second factor would make every admin session exactly as
// strong as the weakest admin's password.

function PasswordCard() {
  const pushToast = useToast()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [busy, setBusy] = useState(false)

  async function handleSubmit(e) {
    e.preventDefault()
    if (!current || !next) return
    setBusy(true)
    try {
      await api.post('/api/settings/password', { current, new: next })
      setCurrent('')
      setNext('')
      // "Your other sessions", not "other sessions": on a panel with several
      // accounts this only ends yours, and the old wording read as though it
      // had just signed everybody else out.
      pushToast('Password changed. Your other sessions were signed out.', 'info')
    } catch (err) {
      pushToast(err.message || 'Failed to change password', 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="card">
      <h2>Password</h2>
      <PanelNote>
        The password you sign in with. Changing it signs out your other sessions and
        leaves everybody else alone.
      </PanelNote>
      <form className="inline-form" onSubmit={handleSubmit}>
        <input
          className="text-input"
          type="password"
          autoComplete="current-password"
          value={current}
          onChange={(e) => setCurrent(e.target.value)}
          placeholder="Current password"
        />
        <input
          className="text-input"
          type="password"
          autoComplete="new-password"
          value={next}
          onChange={(e) => setNext(e.target.value)}
          placeholder="New password"
        />
        <button className="btn btn-primary" type="submit" disabled={busy || !current || !next}>
          Change password
        </button>
      </form>
    </div>
  )
}

function TotpCard({ enabled, onChange }) {
  const pushToast = useToast()
  // `setup` holds what /totp/start returned. Its presence is what puts the
  // card into enrollment mode — there is no separate step counter to drift.
  const [setup, setSetup] = useState(null)
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  function reset() {
    setSetup(null)
    setPassword('')
    setCode('')
    setError('')
  }

  async function run(fn) {
    setError('')
    setBusy(true)
    try {
      await fn()
    } catch (err) {
      setError(err.message || 'Failed')
    } finally {
      setBusy(false)
    }
  }

  const start = () =>
    run(async () => {
      const data = await api.post('/api/auth/totp/start', { password })
      setSetup(data)
      setPassword('')
    })

  const confirm = () =>
    run(async () => {
      await api.post('/api/auth/totp/enable', { code })
      reset()
      onChange(true)
      pushToast('Two-factor sign-in is on. Keep that app — you need it to sign in.', 'info')
    })

  const disable = () =>
    run(async () => {
      await api.post('/api/auth/totp/disable', { password, code })
      reset()
      onChange(false)
      pushToast('Two-factor sign-in is off. Your password alone signs you in.', 'info')
    })

  return (
    <div className="card">
      <div className="page-header">
        <h2>
          Two-factor sign-in <BetaBadge feature="two-factor" />
        </h2>
        {/* The job pill, reused: a second set of badge classes for one
            word would be a second vocabulary for the same idea. */}
        <span
          className={`status-pill ${enabled ? 'status-pill-success' : 'status-pill-neutral'}`}
        >
          {enabled ? 'On' : 'Off'}
        </span>
      </div>
      <PanelNote>
        A 6-digit code from an authenticator app, on top of your password. Off by
        default, and per account — turning it on here does not affect anybody else.
      </PanelNote>

      {error && <p className="login-error">{error}</p>}

      {/* Enrollment, step two: the key is on screen and a code proves the app
          took it. Nothing is saved until it does — a key the phone never
          received is an account nobody can sign in to. */}
      {setup ? (
        <>
          <p>
            Add this key to your authenticator app, then enter the code it shows.
          </p>
          <pre className="totp-key">{setup.formatted}</pre>
          <p className="hint">
            Most apps call this &ldquo;enter a setup key&rdquo; or &ldquo;manual
            entry&rdquo;. There is no QR code here on purpose — drawing one would mean
            adding a library to every install for one picture.
          </p>
          <form
            className="inline-form"
            onSubmit={(e) => {
              e.preventDefault()
              confirm()
            }}
          >
            <input
              className="text-input text-input-narrow"
              autoComplete="one-time-code"
              inputMode="numeric"
              maxLength={6}
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))}
              placeholder="Code"
              autoFocus
            />
            <button className="btn btn-primary" type="submit" disabled={busy || !code}>
              Turn on
            </button>
            <button className="btn btn-ghost" type="button" onClick={reset}>
              Cancel
            </button>
          </form>
        </>
      ) : (
        <form
          className="inline-form"
          onSubmit={(e) => {
            e.preventDefault()
            if (enabled) disable()
            else start()
          }}
        >
          {/* Both directions ask for the password, and for symmetrical
              reasons: somebody at your unlocked browser who turns it off has
              let themselves in, and somebody who enrols *their* phone has
              locked you out of an account you cannot repair from inside. */}
          <input
            className="text-input"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Your password"
          />
          {/* Turning it off also needs a code from the app, or a stolen
              session plus the password would be enough to remove it. */}
          {enabled && (
            <input
              className="text-input text-input-narrow"
              autoComplete="one-time-code"
              inputMode="numeric"
              maxLength={6}
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))}
              placeholder="Code"
            />
          )}
          <button
            className={`btn ${enabled ? 'btn-danger' : 'btn-primary'}`}
            type="submit"
            disabled={busy || !password || (enabled && !code)}
          >
            {enabled ? 'Turn off' : 'Set up'}
          </button>
        </form>
      )}

      {enabled && !setup && (
        <p className="hint">
          Lost the phone? There is no way back in from this page — ask whoever has
          shell access to run <code>zsm users totp disable &lt;name&gt;</code>.
        </p>
      )}

      {/* plans/68 2P-W3: the recovery login has no second factor, and the
          admin account made from it keeps it set to its own password
          (`users.linked_fallback_hash`). Said here, where 2FA is turned on. */}
      {!setup && (
        <p className="hint">
          While <code>users.json</code> cannot be read, the app&rsquo;s original
          single password (the fallback) signs in as admin without a code. If your
          admin account was made from it, that is your password;{' '}
          <code>zsm web --set-password</code> on the server, then an app restart,
          gives the fallback one of its own until you next change yours.
        </p>
      )}
    </div>
  )
}

export default function AccountPage() {
  const { username, role, totp, setTotp } = useRole()

  return (
    <div className="page">
      <div className="page-header">
        <h1>Your account</h1>
      </div>

      <PageIntro
        page="/account"
        text={
          `Signed in as ${username || 'a single shared password'}` +
          (role ? ` (${role}).` : '.') +
          ' Everything on this page changes your own account and nobody else’s.'
        }
      />

      <PasswordCard />
      <TotpCard enabled={!!totp} onChange={setTotp} />
    </div>
  )
}
