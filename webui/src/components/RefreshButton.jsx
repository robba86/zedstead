import { useState } from 'react'
import { HiOutlineArrowPath } from 'react-icons/hi2'
import { useToast } from './Toast.jsx'

// The one Refresh button: a list that has no live event reloads through it.
// A reload that brought the same data changed nothing on screen, so the
// button read as broken; it now says "Refreshing…" while it runs and toasts
// "<what> up to date" after, naming the list (a bare "Up to date" did not
// say what). `onRefresh` returns true on success, or false when it failed
// and already showed its own error (then no toast).
export default function RefreshButton({ onRefresh, what, className = 'btn btn-ghost btn-small', icon = false, label = 'Refresh', ...rest }) {
  const [busy, setBusy] = useState(false)
  const pushToast = useToast()

  async function refresh() {
    setBusy(true)
    try {
      if ((await onRefresh()) !== false) pushToast(`${what} up to date`, 'info')
    } finally {
      setBusy(false)
    }
  }

  return (
    <button className={className} type="button" onClick={refresh} disabled={busy} aria-busy={busy} {...rest}>
      {icon && <HiOutlineArrowPath aria-hidden="true" />}
      {busy ? 'Refreshing…' : label}
    </button>
  )
}
