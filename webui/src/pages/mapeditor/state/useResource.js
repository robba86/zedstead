// Minimal fetch-and-invalidate cache for the editor's server data.
//
// Deliberately not react-query: there are eight resources, no pagination, no
// cache policy, and no optimistic mutations to coordinate. Every editor
// mutation is fire-and-forget, confirmed by a WebSocket ping that tells us to
// refetch — that ping/refetch loop IS the data-flow contract, and it needs
// about sixty lines.

import { useCallback, useEffect, useRef, useState } from 'react'
import { meApi } from '../api'

/**
 * @param path     editor path, e.g. '/spawns' (null/undefined = never fetch)
 * @param options  { enabled, initial, onError }
 * @returns { data, error, loading, reload }
 *
 * Switching off (path goes null, or `enabled` goes false) **drops the data**.
 * It used to keep the last body, which is how previewing another map still
 * drew the running mission's spawns, traders and Expansion zones on top of a
 * terrain they have nothing to do with: the provider stops fetching on a
 * non-active terrain, but every layer kept rendering what was already there.
 */
export function useResource(path, { enabled = true, initial = null, onError } = {}) {
  const [data, setData] = useState(initial)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)

  // Guards against a slow response from a previous path/mount overwriting a
  // newer one (map switch fires several of these back to back).
  const seqRef = useRef(0)
  const mountedRef = useRef(true)
  const onErrorRef = useRef(onError)
  onErrorRef.current = onError
  // `initial` is usually a literal ([], null) rebuilt every render, so it is
  // read through a ref — it must not retrigger the effect below.
  const initialRef = useRef(initial)

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  const active = Boolean(path) && enabled

  const reload = useCallback(async () => {
    if (!active) return null
    const seq = ++seqRef.current
    setLoading(true)
    try {
      const body = await meApi.get(path)
      if (!mountedRef.current || seq !== seqRef.current) return null
      setData(body)
      setError(null)
      return body
    } catch (err) {
      if (!mountedRef.current || seq !== seqRef.current) return null
      setError(err)
      onErrorRef.current?.(err)
      return null
    } finally {
      if (mountedRef.current && seq === seqRef.current) setLoading(false)
    }
  }, [path, active])

  useEffect(() => {
    if (active) {
      reload()
      return
    }
    // Bumping the sequence discards a response still in flight for the path we
    // just left — otherwise it would land after the reset and put the data back.
    seqRef.current += 1
    setData(initialRef.current)
    setError(null)
    setLoading(false)
  }, [reload, active])

  return { data, error, loading, reload }
}
