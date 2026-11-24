// Dashboard tile: the last hour of server metrics as sparklines, with a link
// to Analysis → Performance for the peaks and the 7-day table.

import { useCallback } from 'react'
import { Link } from 'react-router-dom'
import { useWs } from '../ws'
import PerformanceSparklines, { useLastHourMetrics } from './PerformanceSparklines.jsx'
import RefreshButton from './RefreshButton.jsx'
import TileSkeleton from './Skeleton.jsx'

// The notifier broadcasts `metrics_recorded` after each snapshot (one per
// metrics_interval), so the tile follows it live without a timer.
// `service_changed` reloads it at once on a start or stop.
export default function PerformanceCard() {
  const { summary, recent, failed, load } = useLastHourMetrics()

  const handleWsMessage = useCallback(
    (msg) => {
      if (msg.type === 'metrics_recorded' || msg.type === 'service_changed') load()
    },
    [load]
  )
  useWs(handleWsMessage)

  if (failed) return null

  return (
    <div className="card">
      <div className="card-header">
        <h2>Performance (last hour)</h2>
        <RefreshButton what="Performance" onRefresh={load} />
        <Link to="/analysis?tab=Performance" className="card-link">
          View details →
        </Link>
      </div>
      {summary === null ? (
        <TileSkeleton lines={3} label="Reading metrics…" />
      ) : (
        <PerformanceSparklines summary={summary} recent={recent} />
      )}
    </div>
  )
}
