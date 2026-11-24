import { useCallback, useEffect, useRef, useState } from 'react'
import { api, loginHref } from './api'
import { BASE } from './instance'

const MIN_BACKOFF_MS = 1000
const MAX_BACKOFF_MS = 10000

// ONE shared /ws connection for the whole tab. Components consume it via
// useWs(onMessage): handlers are fan-out registered on the shared socket,
// channel subscriptions are reference-counted (LogTail unmounting must not
// kill another component's `rpt` subscription), and the socket closes when
// the last consumer unmounts. Reconnect uses 1s->10s backoff and re-sends
// the active channel subscriptions on open.
let sharedWs = null
let backoff = MIN_BACKOFF_MS
let reconnectTimer = null
let consumers = 0
const messageHandlers = new Set()
const connectionListeners = new Set()
const channelCounts = new Map()

function notifyConnected(value) {
  for (const listener of connectionListeners) listener(value)
}

function connect() {
  reconnectTimer = null
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const ws = new WebSocket(`${protocol}//${window.location.host}${BASE}/ws`)
  sharedWs = ws
  // Per socket, not per tab: a reconnect is a new connect() and starts false.
  let opened = false

  ws.onopen = () => {
    opened = true
    backoff = MIN_BACKOFF_MS
    notifyConnected(true)
    for (const channel of channelCounts.keys()) {
      ws.send(JSON.stringify({ action: 'subscribe', channel }))
    }
  }

  ws.onmessage = (event) => {
    let msg
    try {
      msg = JSON.parse(event.data)
    } catch {
      return // ignore malformed frames
    }
    for (const handler of messageHandlers) handler(msg)
  }

  ws.onclose = (event) => {
    // Read before clearing: teardown() drops the socket first, so one closed
    // on purpose is not mistaken for a failed connect below.
    const current = sharedWs === ws
    if (current) sharedWs = null
    notifyConnected(false)
    // 4401: the session behind this socket was revoked or expired (logout in
    // another tab, a password change, a deleted account). Reconnecting would
    // only be refused again, so go to /login the way api.js does on a 401.
    // 4403: this page's origin is not the panel's; retrying cannot fix that.
    if (event.code === 4401) {
      if (window.location.pathname !== '/login') window.location.href = loginHref()
      return
    }
    if (event.code === 4403) return
    // 4404: this page's instance was removed (plans/62). Its routes now
    // answer 404, so go to the instance the panel started with.
    if (event.code === 4404) {
      window.location.href = '/'
      return
    }
    // A socket refused for want of a session never sees 4401: the server
    // closes it before accepting, uvicorn answers that handshake with HTTP
    // 403, and the browser reports 1006. So after a panel restart (sessions
    // live in memory) this tab kept reconnecting and looked alive, and the
    // next Save was lost to api.js's 401 redirect. Ask the REST API instead;
    // its 401 takes the tab to /login through api.js. One request per failed
    // connect, so it follows the backoff above. A network error or a proxy's
    // 5xx while the panel is still down is nothing to act on: the next
    // attempt asks again.
    if (!opened && current) api.get('/api/auth/me').catch(() => {})
    if (consumers > 0 && !reconnectTimer) {
      reconnectTimer = setTimeout(connect, backoff)
      backoff = Math.min(backoff * 2, MAX_BACKOFF_MS)
    }
  }

  ws.onerror = () => ws.close()
}

function ensureConnected() {
  if (!sharedWs && !reconnectTimer) connect()
}

function teardown() {
  if (reconnectTimer) {
    clearTimeout(reconnectTimer)
    reconnectTimer = null
  }
  const ws = sharedWs
  sharedWs = null
  ws?.close()
}

function sendIfOpen(payload) {
  if (sharedWs?.readyState === WebSocket.OPEN) {
    sharedWs.send(JSON.stringify(payload))
  }
}

export function useWs(onMessage) {
  const onMessageRef = useRef(onMessage)
  const [connected, setConnected] = useState(
    () => sharedWs?.readyState === WebSocket.OPEN
  )

  onMessageRef.current = onMessage

  useEffect(() => {
    const handler = (msg) => onMessageRef.current?.(msg)
    messageHandlers.add(handler)
    connectionListeners.add(setConnected)
    consumers += 1
    setConnected(sharedWs?.readyState === WebSocket.OPEN)
    ensureConnected()

    return () => {
      messageHandlers.delete(handler)
      connectionListeners.delete(setConnected)
      consumers -= 1
      if (consumers === 0) teardown()
    }
  }, [])

  const subscribe = useCallback((channel) => {
    const count = (channelCounts.get(channel) || 0) + 1
    channelCounts.set(channel, count)
    if (count === 1) sendIfOpen({ action: 'subscribe', channel })
  }, [])

  const unsubscribe = useCallback((channel) => {
    const count = (channelCounts.get(channel) || 0) - 1
    if (count <= 0) {
      channelCounts.delete(channel)
      sendIfOpen({ action: 'unsubscribe', channel })
    } else {
      channelCounts.set(channel, count)
    }
  }, [])

  return { connected, subscribe, unsubscribe }
}
