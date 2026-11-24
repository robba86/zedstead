// Dashboard tile: who is connected now (/api/players/online), while the
// server runs.
import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { formatWhen } from '../format'
import { useServerStatus } from '../serverStatus'
import { useWs } from '../ws'
import TileSkeleton from './Skeleton.jsx'
import TileStats from './TileStats.jsx'
import PanelNote from './PanelNote.jsx'

// Dashboard tile: who is on the server right now, from RCON.
//
// Live names are RCON-only by design. `GET /api/players/online` 409s when no
// RCON password/port is configured, and this renders null on that — the
// dashboard's empty-card mechanism then hides the tile and marks it
// unavailable in the Add-tile menu. Don't add an A2S fallback: a bare head
// count without names has nothing the status tile's Max Players field doesn't
// already carry.
//
// A **stopped server** is not that case, and used to be indistinguishable from
// it: RCON can't answer, so the request hung (berconpy retried the closed port
// indefinitely) and the tile skeletoned for as long as it took. It now reads
// `running` off the shared status store
// and drops the skeleton the moment the server is known to be down, showing
// the one thing that *is* knowable then: who played recently, from the session
// log (`GET /api/players/summaries`, parsed from disk, no RCON involved).
const MAX_SHOWN = 12
const HISTORY_DAYS = 7

export default function PlayersCard() {
  const { status } = useServerStatus()
  const [players, setPlayers] = useState(null)
  const [failed, setFailed] = useState(false)
  // `null` = not asked yet. Only fetched while the server is down.
  const [recent, setRecent] = useState(null)
  // The endpoint's own verdict, for the window before the status store has
  // answered (or if the two ever disagree).
  const [saysStopped, setSaysStopped] = useState(false)

  // `running` is undefined until the status store answers — only an explicit
  // false means stopped, so a slow first status can't blank the tile. The
  // fetch effect keys off `canBeLive` rather than `running` itself, or the
  // undefined -> true transition on first paint would fetch twice.
  const canBeLive = status?.running !== false
  const stopped = !canBeLive || saysStopped

  const load = useCallback(async () => {
    try {
      const data = await api.get('/api/players/online')
      setSaysStopped(data.reason === 'server_stopped')
      setPlayers(data.players || [])
      setFailed(false)
    } catch {
      setFailed(true)
    }
  }, [])

  // The request is made even when the server is known to be down: it is cheap
  // now (the backend answers `server_stopped` without touching RCON) and it is
  // the only thing that reports "RCON not configured" — the case that must
  // still hide the tile rather than claim the server is merely stopped.
  useEffect(() => {
    setRecent(null) // re-read the history each time the server goes down
    load()
  }, [canBeLive, load])

  useEffect(() => {
    if (!stopped || recent !== null) return
    api
      .get(`/api/players/summaries?days=${HISTORY_DAYS}`)
      .then((data) => setRecent(data.summaries || []))
      .catch(() => setRecent([]))
  }, [stopped, recent])

  // Join/leave events already stream on the ws — no timer needed.
  // `service_changed` is here too, and both are unconditional: an rcon_event
  // can only come from a live connection, so either message is proof the
  // offline body is out of date even if the status store hasn't caught up.
  const handleWsMessage = useCallback(
    (msg) => {
      if (msg.type === 'rcon_event' || msg.type === 'service_changed') load()
    },
    [load]
  )
  useWs(handleWsMessage)

  // Only a failure hides the tile — the first fetch shows a skeleton, so the
  // grid keeps this slot instead of popping the card in when RCON answers.
  if (failed) return null

  const lobby = (players || []).filter((p) => p.lobby).length

  return (
    <div className="card">
      <div className="card-header">
        <h2>Players online</h2>
        <Link to="/players" className="card-link">
          Manage →
        </Link>
      </div>
      {stopped ? (
        <>
          <TileStats
            items={[
              { label: 'online', value: 0, tone: 'muted' },
              { label: `seen (${HISTORY_DAYS}d)`, value: recent === null ? '—' : recent.length },
            ]}
          />
          <PanelNote>Server is stopped — a live player list needs it running.</PanelNote>
          {recent !== null && recent.length > 0 && (
            <ul className="tile-list">
              {recent.slice(0, MAX_SHOWN).map((s) => (
                <li key={s.guid}>
                  <span className="tile-list-name">{s.name}</span>
                  <span className="tile-list-meta">{formatWhen(s.last_seen)}</span>
                </li>
              ))}
              {recent.length > MAX_SHOWN && (
                <li className="tile-list-more">+{recent.length - MAX_SHOWN} more</li>
              )}
            </ul>
          )}
        </>
      ) : players === null ? (
        <TileSkeleton stats={2} lines={4} label="Loading players online…" />
      ) : (
        <>
          <TileStats
            items={[
              { label: 'online', value: players.length, tone: players.length > 0 ? 'good' : 'muted' },
              { label: 'in lobby', value: lobby },
            ]}
          />
          {players.length === 0 ? (
            <PanelNote>Nobody is connected.</PanelNote>
          ) : (
            <ul className="tile-list">
              {players.slice(0, MAX_SHOWN).map((p) => (
                <li key={p.guid || p.index}>
                  <span className="tile-list-name">{p.name}</span>
                  <span className="tile-list-meta">{p.ping} ms</span>
                </li>
              ))}
              {players.length > MAX_SHOWN && (
                <li className="tile-list-more">+{players.length - MAX_SHOWN} more</li>
              )}
            </ul>
          )}
        </>
      )}
    </div>
  )
}
