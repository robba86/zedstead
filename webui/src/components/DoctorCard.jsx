// Dashboard tile: the Doctor checks (/api/doctor) and the log findings,
// with a link to the Doctor page.

import { useCallback, useEffect, useRef, useState } from 'react'
import DetailText from './DetailText.jsx'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { useJobs } from '../jobs'
import useAutoRefresh from '../useAutoRefresh.js'
import AutoRefreshIndicator from './AutoRefreshIndicator.jsx'
import TileSkeleton from './Skeleton.jsx'
import { useWs } from '../ws'
import PanelNote from './PanelNote.jsx'
import ListRow, { SeverityDot } from './ListRow.jsx'
import ServiceSetupCard from './ServiceSetupCard.jsx'
import { useCan } from '../role.jsx'

// Dashboard card: the failing half of GET /api/doctor. Full findings (with the
// deep links that fix them) live on /doctor — this is only the "is anything
// wrong right now" glance. Reloads when the service changes, same as the page,
// plus its own timer (paused while a job mutates the server, for the same
// reason the page pauses — see DoctorPage).
const AUTO_REFRESH_MS = 60000
const AUTO_REFRESH_KEY = 'zsm.dashboard.doctor.autorefresh'
// The check label from app/commands/doctor.py::_check_service.
const SERVICE_CHECK = 'Service'

export default function DoctorCard() {
  const [doctor, setDoctor] = useState(null)
  const [failed, setFailed] = useState(false)
  const { jobRunning } = useJobs()
  const isAdmin = useCan('admin')

  const load = useCallback(async () => {
    try {
      setDoctor(await api.get('/api/doctor'))
      setFailed(false)
    } catch {
      // The card keeps its last known state; `failed` only decides what the
      // body says while there is no state yet — a skeleton would otherwise sit
      // there for good on a backend that keeps refusing.
      setFailed(true)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const auto = useAutoRefresh(load, {
    interval: AUTO_REFRESH_MS,
    enabled: !jobRunning,
    storageKey: AUTO_REFRESH_KEY,
  })

  const wasRunning = useRef(jobRunning)
  useEffect(() => {
    if (wasRunning.current && !jobRunning) load()
    wasRunning.current = jobRunning
  }, [jobRunning, load])

  const handleWsMessage = useCallback(
    (msg) => {
      if (msg.type === 'service_changed') load()
    },
    [load]
  )
  useWs(handleWsMessage)

  const failing = (doctor?.checks ?? []).filter((c) => !c.ok)
  // The latest log's own findings, shown beside the checks' (test pass 1,
  // F48): "All checks passing" above a log with an error read as all clear.
  const log = doctor?.log_summary
  const logErrors = log?.error ?? 0
  const logWarnings = log?.warning ?? 0
  const logLine =
    logErrors || logWarnings
      ? `The latest log has ${[
          logErrors && `${logErrors} error${logErrors === 1 ? '' : 's'}`,
          logWarnings && `${logWarnings} warning${logWarnings === 1 ? '' : 's'}`,
        ]
          .filter(Boolean)
          .join(' and ')}.`
      : null

  return (
    <div className="card doctor-card">
      <div className="card-header">
        <h2>Doctor</h2>
        {doctor?.summary && (doctor.summary.warning > 0 || doctor.summary.error > 0) && (
          <span className="doctor-detail">
            {doctor.summary.error} err / {doctor.summary.warning} warn
          </span>
        )}
        <AutoRefreshIndicator
          compact
          paused={auto.paused}
          blocked={jobRunning}
          progress={auto.progress}
          secondsLeft={auto.secondsLeft}
          interval={auto.interval}
          onToggle={auto.toggle}
        />
        <Link to="/doctor" className="card-link">
          View details →
        </Link>
      </div>
      {doctor === null ? (
        // "All checks passing." before the first answer is a claim, not a
        // placeholder — say nothing is known yet instead.
        failed ? (
          <PanelNote>Checks unavailable — retrying.</PanelNote>
        ) : (
          <TileSkeleton lines={3} label="Running checks…" />
        )
      ) : failing.length === 0 ? (
        logLine ? (
          <p className="doctor-detail">
            Health checks passing. {logLine}{' '}
            <Link to="/doctor#log-analyzer">See the log findings →</Link>
          </p>
        ) : (
          <p className="doctor-ok">All health checks passing.</p>
        )
      ) : (
        <>
          <ul className="doctor-list">
            {failing.map((check) => (
              <ListRow
                as="li"
                key={check.label}
                wrap
                lead={<SeverityDot severity={check.severity} />}
                name={check.label}
                sub={
                  <>
                    <DetailText text={check.detail} />
                    {/* The service buttons sit under the finding they fix
                        (2026 Q4: they replaced the Service Setup tile). */}
                    {check.label === SERVICE_CHECK && isAdmin && <ServiceSetupCard inline />}
                  </>
                }
              />
            ))}
          </ul>
          {logLine && (
            <p className="doctor-detail">
              {logLine} <Link to="/doctor#log-analyzer">See the log findings →</Link>
            </p>
          )}
        </>
      )}
    </div>
  )
}
