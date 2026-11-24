// The sidebar's menu: Rookie mode, every page, or the viewer's own menu. It hides
// nothing else, so every page stays open by URL, by a link and by the search.
// The choice is about the viewer, so it is per browser and has no instanceKey.
//
// Rookie mode is the default: a browser with no stored choice gets the short
// sidebar. "Show all pages" stores the other choice. The first change in
// "Edit menu" stores the viewer's own list of pages, which replaces both modes
// until the reset button clears it.
import { createContext, useCallback, useContext, useMemo, useState } from 'react'
import { DRAWER_KEY } from './guideSteps.js'
import { instanceKey } from './instance'

const ROOKIE_KEY = 'zsm.rookie'
const MENU_KEY = 'zsm.menu'
// Per instance: Quick setup belongs to one server, so dismissing it on one
// leaves it in the menu of a new instance.
const QUICK_SETUP_DISMISSED_KEY = 'zsm.quickSetupDismissed'

const RookieContext = createContext({
  rookie: true,
  setRookie: () => {},
  menu: null,
  setMenu: () => {},
  resetMenu: () => {},
  quickSetupDismissed: false,
  setQuickSetupDismissed: () => {},
})

function readStored(key) {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}

function writeStored(key, value) {
  try {
    if (value === null) localStorage.removeItem(key)
    else localStorage.setItem(key, value)
  } catch {
    // Best effort: the choice then lasts until the page reloads.
  }
}

// The stored own menu as a list of paths, or null when there is none or it is unreadable.
function readMenu() {
  try {
    const list = JSON.parse(readStored(MENU_KEY))
    return Array.isArray(list) && list.every((p) => typeof p === 'string') ? list : null
  } catch {
    return null
  }
}

// Holds the menu choice for the whole app and stores each change.
export function RookieProvider({ children }) {
  const [rookie, setRookieState] = useState(() => readStored(ROOKIE_KEY) !== '0')
  const [menu, setMenuState] = useState(readMenu)
  const [quickSetupDismissed, setDismissedState] = useState(
    () => readStored(instanceKey(QUICK_SETUP_DISMISSED_KEY)) === '1'
  )

  const setRookie = useCallback((next) => {
    writeStored(ROOKIE_KEY, next ? '1' : '0')
    // A rookie who never opened the Guide does not get the first-visit modal
    // on the next load after this switch.
    if (!next && readStored(DRAWER_KEY) === null) writeStored(DRAWER_KEY, '0')
    setRookieState(next)
  }, [])

  // *paths*: the pages the own menu shows, in no particular order.
  const setMenu = useCallback((paths) => {
    writeStored(MENU_KEY, JSON.stringify(paths))
    setMenuState(paths)
  }, [])

  // Back to the default: no own menu, and Rookie mode on.
  const resetMenu = useCallback(() => {
    writeStored(MENU_KEY, null)
    setMenuState(null)
    writeStored(ROOKIE_KEY, '1')
    setRookieState(true)
  }, [])

  // Dismiss takes Quick setup out of the menu and, unless the viewer made an
  // own menu, shows every page: an admin who skips the guided start wants the
  // whole panel. Undoing it puts Quick setup back and leaves the mode alone.
  const setQuickSetupDismissed = useCallback(
    (next) => {
      writeStored(instanceKey(QUICK_SETUP_DISMISSED_KEY), next ? '1' : null)
      setDismissedState(next)
      if (next && !menu) setRookie(false)
    },
    [menu, setRookie]
  )

  const value = useMemo(
    () => ({ rookie, setRookie, menu, setMenu, resetMenu, quickSetupDismissed, setQuickSetupDismissed }),
    [rookie, setRookie, menu, setMenu, resetMenu, quickSetupDismissed, setQuickSetupDismissed]
  )
  return <RookieContext.Provider value={value}>{children}</RookieContext.Provider>
}

// `{ rookie, setRookie, menu, setMenu, resetMenu, quickSetupDismissed,
// setQuickSetupDismissed }`. `menu` is the own list of paths, or null; while it
// is set, `rookie` decides nothing.
export function useRookie() {
  return useContext(RookieContext)
}
