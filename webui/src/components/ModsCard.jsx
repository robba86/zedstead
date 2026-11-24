// Dashboard tile: the installed mods (/api/mods), with a link to the Mods
// page.

import { useCallback, useEffect, useState } from 'react'
import RoleLink from './RoleLink.jsx'
import { api } from '../api'
import { modLink } from '../crossLinks.js'
import { useWs } from '../ws'
import TileSkeleton from './Skeleton.jsx'
import TileStats from './TileStats.jsx'
import PanelNote from './PanelNote.jsx'

// Dashboard tile: every listed mod, no "+N more" cut-off (the list scrolls,
// .tile-list max-height). Names link to their own mod page through
// crossLinks.js::modLink — never hand-build a /mods/:id URL.

export default function ModsCard() {
  const [mods, setMods] = useState(null)
  const [spawnTest, setSpawnTest] = useState(null)
  const [failed, setFailed] = useState(false)

  const load = useCallback(async () => {
    try {
      const data = await api.get('/api/mods')
      setMods(data.mods || [])
      setSpawnTest(data.spawn_test || null)
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
      if (msg.type === 'mods_changed') load()
    },
    [load]
  )
  useWs(handleWsMessage)

  if (failed) return null

  const installed = (mods || []).filter((m) => m.installed)
  const disabled = (mods || []).filter((m) => !m.enabled)

  return (
    <div className="card">
      <div className="card-header">
        <h2>Mods</h2>
        <RoleLink to="/mods" className="card-link">
          Manage →
        </RoleLink>
      </div>
      {mods === null ? (
        <TileSkeleton stats={3} lines={5} label="Loading mods…" />
      ) : (
        <>
          <TileStats
            items={[
              { label: 'installed', value: installed.length },
              { label: 'listed', value: mods.length },
              {
                label: 'disabled',
                value: disabled.length,
                tone: disabled.length > 0 ? 'muted' : undefined,
              },
            ]}
          />
          {/* Only while a spawn test is armed — it changes what spawns on the
              server, so it must be visible from the dashboard, and it links to
              the mod page that can end it. One test at a time, app-wide. */}
          {spawnTest && (
            <p className="tile-notice">
              Spawn test:{' '}
              <RoleLink className="mod-link" to={modLink(spawnTest.mod)} fallback={spawnTest.mod}>
                {spawnTest.mod}
              </RoleLink>{' '}
              — {spawnTest.count} x <code>{spawnTest.classname}</code>
              {spawnTest.mode === 'only_x' && ' (Only X — the economy is rewritten)'}
            </p>
          )}
          {mods.length === 0 ? (
            <PanelNote>No mods installed.</PanelNote>
          ) : (
            <ul className="tile-list">
              {mods.map((m) => (
                <li key={m.name} className={m.enabled ? '' : 'tile-list-off'}>
                  <RoleLink
                    className="tile-list-name mod-link"
                    to={modLink(m.id || m.name)}
                    fallback={<span className="tile-list-name">{m.name}</span>}
                  >
                    {m.name}
                  </RoleLink>
                  <span className="tile-list-meta">
                    {!m.installed ? 'not installed' : m.enabled ? '' : 'disabled'}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  )
}
