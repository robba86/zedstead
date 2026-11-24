import { useEffect, useState } from 'react'

// A sidebar section's open/closed state, per browser in localStorage like
// `zsm.sidebar.collapsed`: it describes the viewer, not the server, so it is
// not a URL key (a shared editor link must arrive looking the same for
// everyone). Starts at `defaultOpen` (open) until this browser stores a choice.
export default function useStoredOpen(key, defaultOpen = true) {
  const [open, setOpen] = useState(() => {
    try {
      const stored = window.localStorage.getItem(key)
      return stored === null ? defaultOpen : stored !== '0'
    } catch {
      return defaultOpen
    }
  })
  useEffect(() => {
    try {
      window.localStorage.setItem(key, open ? '1' : '0')
    } catch {
      /* private mode — the panel just forgets */
    }
  }, [key, open])
  return [open, setOpen]
}
