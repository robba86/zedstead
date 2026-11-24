// The last hour of server metrics (/api/metrics/summary + /recent) as three
// sparklines: FPS, memory and players. Shared by Analysis → Performance and
// the Dashboard's Performance tile.

import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import PanelNote from './PanelNote.jsx'
import Sparkline from './Sparkline.jsx'

// Loads the last hour once on mount. `load` reloads it and returns true on
// success, false on failure (the RefreshButton contract). `failed` is true
// only after a failed load, so a caller can hide itself.
export function useLastHourMetrics() {
  const [summary, setSummary] = useState(null)
  const [recent, setRecent] = useState([])
  const [failed, setFailed] = useState(false)

  const load = useCallback(async () => {
    try {
      const [s, r] = await Promise.all([
        api.get('/api/metrics/summary?hours=1'),
        api.get('/api/metrics/recent?hours=1'),
      ])
      setSummary(s)
      setRecent(r.snapshots || [])
      setFailed(false)
      return true
    } catch {
      setFailed(true)
      return false
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  return { summary, recent, failed, load }
}

// The three sparklines, or a note when no snapshot is recorded yet.
export default function PerformanceSparklines({ summary, recent }) {
  if (summary && summary.samples === 0) return <PanelNote>No metrics recorded yet.</PanelNote>

  const series = (key) => recent.map((s) => s[key]).filter((v) => v != null)

  return (
    <div className="status-grid">
      <div>
        <PanelNote>FPS (avg {summary?.fps_avg?.toFixed(1) ?? '—'})</PanelNote>
        <Sparkline values={series('fps')} />
      </div>
      <div>
        <PanelNote>Memory MB (avg {summary?.memory_avg?.toFixed(0) ?? '—'})</PanelNote>
        <Sparkline values={series('memory_mb')} stroke="#d98c5b" />
      </div>
      <div>
        <PanelNote>Players (peak {summary?.players_peak ?? '—'})</PanelNote>
        <Sparkline values={series('players')} stroke="#7fd95b" />
      </div>
    </div>
  )
}
