// Desktop notifications: the server's toasts, sent to the computer's own
// notification area while the Zedstead tab is hidden. Off by default.
//
// Per-browser like the Appearance card (theme.jsx): the browser grants the
// permission per site and per device, so a config.json field would turn it on
// for every admin's phone at once. The choice lives in localStorage
// (zsm.desktopNotify), best-effort: a blocked storage reads as off.
//
// A browser offers the Notification API only in a secure context (HTTPS or
// localhost). On plain http over a LAN address `desktopSupport()` says so, and
// the Settings switch stays disabled with that reason beside it.

const STORAGE_KEY = 'zsm.desktopNotify'

/** Why this browser cannot show desktop notices, or '' when it can. */
export function desktopSupport() {
  if (typeof window === 'undefined' || !('Notification' in window)) {
    return 'This browser has no desktop notifications.'
  }
  if (!window.isSecureContext) {
    return 'The browser allows desktop notifications only over HTTPS or on localhost.'
  }
  if (Notification.permission === 'denied') {
    return 'This browser blocks notifications from this site. Allow them in the site settings.'
  }
  return ''
}

/** True when this browser has the switch on and the permission granted. */
export function desktopEnabled() {
  try {
    return (
      !desktopSupport() &&
      Notification.permission === 'granted' &&
      localStorage.getItem(STORAGE_KEY) === '1'
    )
  } catch {
    return false
  }
}

/**
 * Turns desktop notices on or off for this browser. Turning on asks for the
 * permission first, so call it from a click (Firefox and Safari ignore the
 * request otherwise). Resolves to the state that now applies.
 */
export async function setDesktopEnabled(on) {
  try {
    if (!on) {
      localStorage.removeItem(STORAGE_KEY)
      return false
    }
    if (desktopSupport()) return false
    const permission =
      Notification.permission === 'granted' ? 'granted' : await Notification.requestPermission()
    if (permission !== 'granted') return false
    localStorage.setItem(STORAGE_KEY, '1')
    return true
  } catch {
    return false
  }
}

/**
 * Shows `message` as a desktop notice when the switch is on and the tab is
 * hidden. A visible tab already shows the toast, so a notice there is noise.
 * The tag makes the OS replace an identical notice from a second open tab
 * instead of stacking both. A click focuses the tab and runs `onClick`.
 */
export function showDesktopNotice(message, onClick) {
  if (!document.hidden || !desktopEnabled()) return
  try {
    const notice = new Notification('Zedstead', { body: message, tag: message, icon: '/favicon.svg' })
    notice.onclick = () => {
      window.focus()
      onClick?.()
      notice.close()
    }
  } catch {
    // Android Chrome refuses `new Notification` outside a service worker:
    // the toast is still there when the tab comes back.
  }
}
