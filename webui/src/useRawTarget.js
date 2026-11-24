import { useState } from 'react'

// The target picker's state for the /api/configs/{id}/raw endpoints, clamped to
// the list it is picking from.
//
// RawEditor does not remount when the file changes — ConfigEditorDrawer keeps
// the Raw tab mounted when another card's Raw (or a cross-link) swaps the
// editor id under it. Plain
// useState would carry the previous file's target name into the next file's
// request, which is answered with 400 "Unknown raw target". Deriving the active
// name each render instead means a name this file doesn't have falls back to
// its first target.
export default function useRawTarget(targets = []) {
  const [picked, setPicked] = useState(targets[0] || '')
  return [targets.includes(picked) ? picked : targets[0] || '', setPicked]
}
