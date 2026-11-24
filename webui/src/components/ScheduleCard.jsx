// Dashboard tile: the scheduled tasks (/api/schedule), with a link to the
// Scheduler page.

import { useCallback, useEffect, useState } from 'react'
import RoleLink from './RoleLink.jsx'
import { api } from '../api'
import { useWs } from '../ws'
import TileSkeleton from './Skeleton.jsx'
import TileStats from './TileStats.jsx'
import { tooltipAttrs } from './Tooltip.jsx'
import PanelNote from './PanelNote.jsx'

// Dashboard tile: the scheduled tasks and when they next run. `description`
// (a human-readable cron) and `next_run_display` are computed by GET /api/schedule
// — don't re-derive either client-side.
const MAX_SHOWN = 6

export default function ScheduleCard() {
  const [tasks, setTasks] = useState(null)
  const [failed, setFailed] = useState(false)

  const load = useCallback(async () => {
    try {
      const data = await api.get('/api/schedule')
      setTasks(data.tasks || [])
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
      if (msg.type === 'schedule_changed') load()
    },
    [load]
  )
  useWs(handleWsMessage)

  if (failed) return null

  const enabled = (tasks || []).filter((t) => t.enabled)

  return (
    <div className="card">
      <div className="card-header">
        <h2>Scheduled tasks</h2>
        <RoleLink to="/scheduler" className="card-link">
          Manage →
        </RoleLink>
      </div>
      {tasks === null ? (
        <TileSkeleton stats={2} lines={3} label="Loading scheduled tasks…" />
      ) : (
        <>
          <TileStats
            items={[
              { label: 'enabled', value: enabled.length, tone: enabled.length > 0 ? 'good' : 'muted' },
              { label: 'total', value: tasks.length },
            ]}
          />
          {tasks.length === 0 ? (
            <PanelNote>Nothing scheduled.</PanelNote>
          ) : (
            <ul className="tile-list">
              {tasks.slice(0, MAX_SHOWN).map((t) => (
                <li key={t.id} className={t.enabled ? '' : 'tile-list-off'}>
                  <span className="tile-list-name" {...tooltipAttrs(t.description || t.cron)}>
                    {t.id}
                  </span>
                  <span className="tile-list-meta">
                    {t.enabled ? t.next_run_display || t.cron : 'disabled'}
                  </span>
                </li>
              ))}
              {tasks.length > MAX_SHOWN && (
                <li className="tile-list-more">+{tasks.length - MAX_SHOWN} more</li>
              )}
            </ul>
          )}
        </>
      )}
    </div>
  )
}
