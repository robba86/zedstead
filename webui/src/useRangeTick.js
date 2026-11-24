// Shift-click range ticking for a checkbox list, as in a file manager. A plain
// click flips one box and becomes the anchor. A shift-click gives every box
// from the anchor to the clicked one the clicked box's new state. Used by the
// Mods page's pickers and its Installed table.
import { useCallback, useRef } from 'react'

// `keys` is the list in the order it is shown, `setSelected` the {key: bool}
// state setter, and `isLocked(key)` names a box the range must leave alone (a
// disabled one). The returned `tick(key, event)` goes in the box's onChange.
// React fires a checkbox's onChange from the click, so the event carries shiftKey.
export default function useRangeTick(keys, setSelected, isLocked) {
  const anchor = useRef(null)
  return useCallback(
    (key, event) => {
      const from = anchor.current
      const shift = !!(event?.shiftKey ?? event?.nativeEvent?.shiftKey)
      anchor.current = key
      setSelected((prev) => {
        const value = !prev[key]
        const next = { ...prev, [key]: value }
        const a = shift && from !== null ? keys.indexOf(from) : -1
        const b = keys.indexOf(key)
        if (a < 0 || b < 0) return next
        for (const k of keys.slice(Math.min(a, b), Math.max(a, b) + 1)) {
          if (!isLocked?.(k)) next[k] = value
        }
        return next
      })
    },
    [keys, setSelected, isLocked],
  )
}
