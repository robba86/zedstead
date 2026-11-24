// Dashboard tile: the economy score per category (/api/analysis/economy),
// with a link to the Analysis page.

import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { useToast } from './Toast.jsx'
import TileSkeleton from './Skeleton.jsx'
import TileStats from './TileStats.jsx'
import { formatScore as pct } from '../format.js'
import PanelNote from './PanelNote.jsx'

// Dashboard tile: the economy score from GET /api/analysis/economy.
//
// THIS ONE NEVER RUNS ON A TIMER. The endpoint parses every types.xml in the
// mission through analyze_economy — that is why the tile ships hidden
// (`defaultHidden` in DashboardPage's registry) and fetches once on mount, with
// a manual Refresh for a second look. Don't wire it to a WS event or
// useAutoRefresh.
const WORST_SHOWN = 4

function scoreTone(score) {
  if (score == null) return 'muted'
  if (score >= 0.8) return 'good'
  if (score >= 0.6) return 'warning'
  return 'danger'
}

export default function EconomyCard() {
  const [report, setReport] = useState(null)
  const [failed, setFailed] = useState(false)
  const [busy, setBusy] = useState(false)
  const pushToast = useToast()

  const load = useCallback(async () => {
    setBusy(true)
    try {
      setReport(await api.get('/api/analysis/economy'))
      setFailed(false)
      return true
    } catch {
      setFailed(true)
      return false
    } finally {
      setBusy(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  if (failed) return null

  const worst = Object.entries(report?.category_scores || {})
    .sort((a, b) => a[1] - b[1])
    .slice(0, WORST_SHOWN)

  return (
    <div className="card">
      <div className="card-header">
        <h2>Economy score</h2>
        {/* Keeps its own "Scoring…", which also covers the first load, and
            names what is up to date as RefreshButton does. */}
        <button
          className="btn btn-ghost btn-small"
          onClick={async () => {
            if (await load()) pushToast('Economy score up to date', 'info')
          }}
          disabled={busy}
        >
          {busy ? 'Scoring…' : 'Refresh'}
        </button>
        <Link to="/analysis" className="card-link">
          View details →
        </Link>
      </div>
      {report === null ? (
        <TileSkeleton stats={3} lines={4} label="Reading the mission's types.xml…" />
      ) : (
        <>
          <TileStats
            items={[
              // With the stock types.xml held, vanilla is the baseline: how
              // much of vanilla is unchanged. No tone, since a modded server
              // is meant to differ.
              ...(report.vanilla ? [{ label: 'like vanilla', value: pct(report.vanilla.closeness) }] : []),
              {
                label: report.vanilla ? 'mod items' : 'overall',
                value: pct(report.overall_score),
                tone: scoreTone(report.overall_score),
              },
              { label: 'items', value: report.items_analyzed ?? 0 },
              {
                label: 'with issues',
                value: report.items_with_issues ?? 0,
                tone: report.items_with_issues > 0 ? 'warning' : undefined,
              },
            ]}
          />
          {worst.length === 0 ? (
            <PanelNote>No categories scored.</PanelNote>
          ) : (
            <ul className="tile-list">
              {worst.map(([category, score]) => (
                <li key={category}>
                  <span className="tile-list-name">{category}</span>
                  <span className={`tile-list-meta tile-stat-${scoreTone(score)}`}>{pct(score)}</span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  )
}
