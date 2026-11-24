import { useEffect, useState } from 'react'
import { HiOutlineCheckCircle } from 'react-icons/hi2'
import { instanceKey } from '../instance'
import { useServerStatus } from '../serverStatus'
import { tooltipAttrs } from './Tooltip.jsx'
import { useToast } from './Toast.jsx'
import SteamLoginCard from './SteamLoginCard.jsx'
import { useSteamCheck } from './NeedsSteam.jsx'

// Self-contained Steam-login banner for every page with a control that
// NeedsSteam greys out, so the login, or a fresh check of it, is on the same
// page as the greyed control. Reads steam_ok off the shared server status —
// the same answer the Dashboard shows, kept live by the `steam_changed`
// broadcast — so opening a page never runs SteamCMD (plans/51 D). The server
// measures the login at startup, after a job that ran SteamCMD, and when
// "Check Steam login" asks.
//
// true: a green "logged in" banner, until it is closed. false: the login
// card. null: not measured yet (a panel that just started), so offer the
// check rather than a login form.
//
// Closing the green banner is remembered in this browser, and forgotten when
// Steam is next seen logged out, so the next login shows it again.
const OK_CLOSED_KEY = instanceKey('zsm.steam.loggedInBanner.closed')

function readOkClosed() {
  try {
    return localStorage.getItem(OK_CLOSED_KEY) === '1'
  } catch {
    return false
  }
}

function writeOkClosed(closed) {
  try {
    if (closed) localStorage.setItem(OK_CLOSED_KEY, '1')
    else localStorage.removeItem(OK_CLOSED_KEY)
  } catch {
    // storage unavailable: the banner still closes for this page view
  }
}

export default function SteamLoginGate({ onLoggedIn }) {
  const { status, refresh } = useServerStatus()
  const { checking, check } = useSteamCheck()
  const pushToast = useToast()
  const [okClosed, setOkClosed] = useState(readOkClosed)
  const loggedIn = status ? status.steam_ok : undefined

  useEffect(() => {
    if (loggedIn === false && okClosed) {
      writeOkClosed(false)
      setOkClosed(false)
    }
  }, [loggedIn, okClosed])

  if (loggedIn === true) {
    if (okClosed) return null
    return (
      <div className="banner banner-success banner-closable">
        <HiOutlineCheckCircle className="banner-icon" aria-hidden="true" />
        <span>Steam is logged in. Server and mod downloads can run.</span>
        <button
          type="button"
          className="banner-close"
          aria-label="Close"
          {...tooltipAttrs('Close')}
          onClick={() => {
            writeOkClosed(true)
            setOkClosed(true)
          }}
        >
          ×
        </button>
      </div>
    )
  }
  if (loggedIn === null) {
    return (
      <p className="hint">
        Steam login not checked yet.{' '}
        <button className="btn btn-small" onClick={check} disabled={checking}>
          {checking ? 'Checking…' : 'Check Steam login'}
        </button>
      </p>
    )
  }
  if (loggedIn !== false) return null
  return (
    <SteamLoginCard
      pushToast={pushToast}
      onLoggedIn={() => {
        refresh()
        onLoggedIn?.()
      }}
    />
  )
}
