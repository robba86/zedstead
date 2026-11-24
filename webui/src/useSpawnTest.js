// A mod's spawn-test data (plans/37), shared by the mod page's Spawn test card
// and the map editor's Mod items tab: GET /api/mods/{name}/spawn-test gives the
// mod's classes, the RAM-based count warning, the active test and the storage
// folder. The first GET per mod parses its PBOs, so callers load it lazily.

import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './api'
import { useWs } from './ws'

// How many classes a suggestion list shows before "N more — keep typing".
export const MAX_CLASS_SUGGESTIONS = 50

/** The classes that contain *query* (lowercased), at most *max*, and how many more matched. */
export function filterClasses(classes, query, max = MAX_CLASS_SUGGESTIONS) {
  const matches = query ? classes.filter((c) => c.toLowerCase().includes(query)) : classes
  const items = matches.slice(0, max)
  return { items, overflow: matches.length - items.length }
}

/**
 * `{ info, error, refresh }` for *modName*, or nothing loaded while it is
 * null. A `mods_changed` message loads it again. An answer for a mod that is
 * no longer the asked one is dropped, so a fast switch between mods never
 * shows the first mod's classes under the second's name.
 */
export function useSpawnTest(modName) {
  const [info, setInfo] = useState(null)
  const [error, setError] = useState('')
  const askedRef = useRef(modName)
  askedRef.current = modName

  const refresh = useCallback(async () => {
    if (!modName) return null
    try {
      const data = await api.get(`/api/mods/${encodeURIComponent(modName)}/spawn-test`)
      if (askedRef.current !== modName) return null
      setInfo(data)
      setError('')
      return data
    } catch (err) {
      if (askedRef.current !== modName) return null
      setError(err.message || 'Failed to load the spawn test')
      return null
    }
  }, [modName])

  useEffect(() => {
    setInfo(null)
    setError('')
    refresh()
  }, [refresh])

  useWs(
    useCallback(
      (msg) => {
        if (msg.type === 'mods_changed') refresh()
      },
      [refresh]
    )
  )

  return { info, error, refresh }
}
