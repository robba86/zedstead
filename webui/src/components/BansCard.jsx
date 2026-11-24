// Dashboard tile: the active bans (/api/bans), with a link to the Players
// page.

import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { useWs } from '../ws'
import TileSkeleton from './Skeleton.jsx'
import TileStats from './TileStats.jsx'
import PanelNote from './PanelNote.jsx'

// Dashboard tile: the ban list at a glance (GET /api/bans, which already
// returns the same BanStats the Players page shows). Expired bans are called
// out because they are what the page's "Clean up expired" action clears.
const MAX_SHOWN = 6

export default function BansCard() {
  const [data, setData] = useState(null)
  const [failed, setFailed] = useState(false)

  const load = useCallback(async () => {
    try {
      setData(await api.get('/api/bans'))
      setFailed(false)
    } catch {
      setFailed(true)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const handleWsMessage = useCallback(
    (msg) => {
      if (msg.type === 'bans_changed') load()
    },
    [load]
  )
  useWs(handleWsMessage)

  if (failed) return null

  const stats = data?.stats || {}
  // DayZ's own ban.txt, shown beside BattlEye's count, never added to it.
  const inBanTxt = data?.server_banlist?.ids?.length || 0
  const active = (data?.bans || []).filter((b) => b.is_active)

  return (
    <div className="card">
      <div className="card-header">
        <h2>Bans</h2>
        <Link to="/players" className="card-link">
          Manage →
        </Link>
      </div>
      {data === null ? (
        <TileSkeleton stats={3} lines={3} label="Loading bans…" />
      ) : (
        <>
          <TileStats
            items={[
              { label: 'total', value: stats.total ?? 0 },
              { label: 'permanent', value: stats.permanent ?? 0 },
              {
                label: 'expired',
                value: stats.expired ?? 0,
                tone: stats.expired > 0 ? 'warning' : undefined,
              },
              ...(inBanTxt > 0 ? [{ label: 'in ban.txt', value: inBanTxt }] : []),
            ]}
          />
          {active.length === 0 ? (
            <PanelNote>No active bans.</PanelNote>
          ) : (
            <ul className="tile-list">
              {active.slice(0, MAX_SHOWN).map((b) => (
                <li key={b.identifier}>
                  <span className="tile-list-name">{b.player_name || b.identifier}</span>
                  <span className="tile-list-meta">
                    {b.is_permanent ? 'permanent' : b.time_remaining || 'temporary'}
                  </span>
                </li>
              ))}
              {active.length > MAX_SHOWN && (
                <li className="tile-list-more">+{active.length - MAX_SHOWN} more</li>
              )}
            </ul>
          )}
        </>
      )}
    </div>
  )
}
