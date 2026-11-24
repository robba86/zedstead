import { cloneElement, useState } from 'react'
import { api } from '../api'
import Tooltip from './Tooltip.jsx'
import { useToast } from './Toast.jsx'
import { useServerStatus } from '../serverStatus'

// Controls whose action runs SteamCMD (a server download, a Workshop
// download) are greyed out while Steam is known to be logged out.
export const NEEDS_STEAM_TIP =
  'Needs a Steam login: this downloads through SteamCMD. Log in, or check the login again, on the Steam login card.'

// True only when the panel measured Steam as logged out. Null (not checked
// yet, e.g. right after the panel started) is not: that would block someone
// who is logged in.
export function useSteamLoggedOut() {
  const { status } = useServerStatus()
  return status?.steam_ok === false
}

// Measures the Steam login again (GET /api/steam/status?check=1). The
// answer reaches every open page through the `steam_changed` broadcast, so a
// stale "logged out" (a login made with `zsm steam-login` in a terminal)
// never leaves the greyed-out controls stuck.
export function useSteamCheck() {
  const [checking, setChecking] = useState(false)
  const pushToast = useToast()
  async function check() {
    setChecking(true)
    try {
      await api.get('/api/steam/status?check=1')
    } catch (err) {
      pushToast(err.message || 'Could not check the Steam login', 'error')
    } finally {
      setChecking(false)
    }
  }
  return { checking, check }
}

// Wraps one button. Logged out, the button is disabled and its tooltip says
// why (wrapped, since a disabled button fires no mouseenter). Otherwise it
// renders as before, with `tip` as its own tooltip when given.
export default function NeedsSteam({ children, tip, keep = false, className }) {
  const loggedOut = useSteamLoggedOut()
  if (!loggedOut) {
    return (
      <Tooltip text={tip} keep={keep} className={className}>
        {children}
      </Tooltip>
    )
  }
  return (
    <Tooltip text={NEEDS_STEAM_TIP} className="bare">
      {cloneElement(children, { disabled: true })}
    </Tooltip>
  )
}

// NeedsSteam for one of several buttons in a list, only `when` its action
// runs SteamCMD.
export function NeedsSteamIf({ when, children }) {
  return when ? <NeedsSteam>{children}</NeedsSteam> : children
}
