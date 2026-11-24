// Dashboard tile: the latest backups, their size and age, with a link to
// the Backups page.

import { useCallback, useEffect, useState } from 'react'
import RoleLink from './RoleLink.jsx'
import { api } from '../api'
import { formatBytes, formatWhen } from '../format'
import { useWs } from '../ws'
import TileSkeleton from './Skeleton.jsx'
import TileStats from './TileStats.jsx'
import PanelNote from './PanelNote.jsx'

// Dashboard tile: how many backups exist and how fresh the newest ones are.
// Both lists come back newest-first from the API, so [0] is the latest.
// Bundles are a second endpoint (exported server bundles, not backups) and are
// shown as a count only.
export default function BackupsCard() {
  const [data, setData] = useState(null)
  const [bundles, setBundles] = useState([])
  const [failed, setFailed] = useState(false)

  const load = useCallback(async () => {
    try {
      setData(await api.get('/api/backups'))
      setFailed(false)
    } catch {
      setFailed(true)
    }
    try {
      const b = await api.get('/api/bundle')
      setBundles(b.bundles || [])
    } catch {
      // A missing bundle dir is not a reason to hide the backups tile.
      setBundles([])
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const handleWsMessage = useCallback(
    (msg) => {
      if (msg.type === 'backups_changed') load()
    },
    [load]
  )
  useWs(handleWsMessage)

  if (failed) return null

  const quick = data?.quick || []
  const full = data?.full || []
  const latest = [
    quick[0] && { kind: 'Quick', ...quick[0] },
    full[0] && { kind: 'Full', ...full[0] },
  ].filter(Boolean)

  return (
    <div className="card">
      <div className="card-header">
        <h2>Backups</h2>
        <RoleLink to="/backups" className="card-link">
          Manage →
        </RoleLink>
      </div>
      {data === null ? (
        <TileSkeleton stats={3} lines={2} label="Loading backups…" />
      ) : (
        <>
          <TileStats
            items={[
              { label: 'quick', value: quick.length },
              { label: 'full', value: full.length },
              { label: 'bundles', value: bundles.length },
            ]}
          />
          {latest.length === 0 ? (
            <PanelNote>No backups yet.</PanelNote>
          ) : (
            <ul className="tile-list">
              {latest.map((b) => (
                <li key={b.kind}>
                  <span className="tile-list-name">
                    {b.kind} · {formatWhen(b.timestamp)}
                  </span>
                  <span className="tile-list-meta">{formatBytes(b.total_size)}</span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  )
}
