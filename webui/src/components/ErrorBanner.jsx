import { useState } from 'react'
import { tooltipAttrs } from './Tooltip.jsx'

// A `banner banner-error` with a × in its upper right corner. Closing it is
// remembered in this browser (localStorage, best-effort like persist.js) under
// `dismissKey`, so a caller puts in the key whatever makes it a different
// error: the message text, a restart count. The same error stays closed across
// reloads; a new one shows again. Without a `dismissKey` there is no ×.
//
// `reopenAfterMs`: a closed banner shows again after that long if the problem
// is still there. For a warning about the machine's state (the panel reachable
// without TLS, systemd giving up on the server), where closing it for good
// would hide a problem that has not been fixed.
//
// `as` is the element ('div' by default, 'p' where a page had a <p>).
//
// `variant` picks the colour: 'error' by default, or 'warning' / 'info' for a
// closable note that is not an error (the Map Editor's two trader systems).

const CLOSED_KEY = 'zsm.banners.closed'
// What the two state warnings use: back a day after they were closed.
export const REOPEN_DAILY_MS = 24 * 60 * 60 * 1000
// The newest keys kept; an old message nobody sees again ages out of the list.
const MAX_CLOSED = 50

// { key: time it was closed }.
function readClosed() {
  try {
    const map = JSON.parse(localStorage.getItem(CLOSED_KEY) || '{}')
    return map && typeof map === 'object' && !Array.isArray(map) ? map : {}
  } catch {
    return {}
  }
}

function remember(key) {
  try {
    const entries = Object.entries(readClosed()).filter(([k]) => k !== key)
    entries.push([key, Date.now()])
    localStorage.setItem(CLOSED_KEY, JSON.stringify(Object.fromEntries(entries.slice(-MAX_CLOSED))))
  } catch {
    // storage full/unavailable: the banner still closes for this page view
  }
}

function isClosed(closed, key, reopenAfterMs) {
  const at = closed[key]
  if (!Number.isFinite(at)) return false
  return reopenAfterMs == null || Date.now() - at < reopenAfterMs
}

export default function ErrorBanner({ dismissKey, reopenAfterMs, as: Tag = 'div', variant = 'error', className = '', children }) {
  const [closed, setClosed] = useState(() => readClosed())
  if (dismissKey != null && isClosed(closed, String(dismissKey), reopenAfterMs)) return null
  const close = () => {
    remember(String(dismissKey))
    setClosed(readClosed())
  }
  return (
    <Tag className={`banner banner-${variant}${dismissKey != null ? ' banner-closable' : ''} ${className}`.trim()}>
      {children}
      {dismissKey != null && (
        <button type="button" className="banner-close" aria-label="Close" {...tooltipAttrs('Close')} onClick={close}>
          ×
        </button>
      )}
    </Tag>
  )
}
