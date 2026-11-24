import { Switch } from './FlagSwitch.jsx'
import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { modLink } from '../crossLinks.js'
import { formatDateTime } from '../format.js'
import { useJobs } from '../jobs'
import { useCan } from '../role.jsx'
import { useWs } from '../ws'
import ConfirmModal from './ConfirmModal.jsx'
import { tooltipAttrs } from './Tooltip.jsx'
import { useToast } from './Toast.jsx'

// How the server's boot after an update went (plans/73), in words.
const OUTCOME = {
  crashed: 'crashed',
  not_joinable: 'never became joinable',
  not_started: 'could not be started',
}

function failed(outcome) {
  return outcome === 'crashed' || outcome === 'not_joinable'
}

function day(ts) {
  return ts ? new Date(ts * 1000).toLocaleDateString() : 'unknown'
}

function Suspects({ suspects }) {
  if (!suspects?.length) return null
  return (
    <ul className="boot-check-suspects">
      {suspects.map((s) => (
        <li key={s.mod}>
          <Link className="mod-link" to={modLink(s.mod)}>{s.mod}</Link>
          {s.before_game_update && (
            <span className="text-muted">
              {' '}installed copy from {day(s.time_updated)}, older than this DayZ build
            </span>
          )}
          {s.lines?.length > 0 && <pre className="boot-check-lines">{s.lines.join('\n')}</pre>}
        </li>
      ))}
    </ul>
  )
}

function Unattributed({ lines }) {
  if (!lines?.length) return null
  return (
    <>
      <p className="hint">These errors name files that no installed mod's files match:</p>
      <pre className="boot-check-lines">{lines.join('\n')}</pre>
    </>
  )
}

// The Update page's "After the update" card: the last boot after an update,
// the suspects of a failed one with the offer to set them aside, and the mods
// set aside after earlier updates (GET /api/update/triage).
export default function BootCheckCard() {
  const [data, setData] = useState(null)
  const [picked, setPicked] = useState([])
  const [offer, setOffer] = useState(null)
  const [backupFirst, setBackupFirst] = useState(true)
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)
  const canAdmin = useCan('admin')
  const pushToast = useToast()
  const { startJob } = useJobs()

  const load = useCallback(async () => {
    try {
      const body = await api.get('/api/update/triage')
      setData(body)
      setOffer(body.offer)
      setPicked(body.offerable || [])
      setBackupFirst(!body.offer?.backup?.fresh)
    } catch {
      setData(null)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  useWs((msg) => {
    if (msg.type === 'update_triage_changed' || msg.type === 'mods_changed') load()
  })

  // The dependents depend on which suspects are ticked, so ask again.
  async function pick(mod, on) {
    const next = on ? [...picked, mod] : picked.filter((m) => m !== mod)
    setPicked(next)
    if (!next.length) return
    try {
      const params = new URLSearchParams()
      for (const m of next) params.append('mods', m)
      setOffer((await api.get(`/api/update/triage?${params}`)).offer)
    } catch {
      // Keep the last offer; the job works the dependents out again anyway.
    }
  }

  async function apply() {
    setConfirming(false)
    setBusy(true)
    try {
      await startJob({
        endpoint: '/api/update/triage/apply',
        body: { mods: picked, backup_first: backupFirst },
        name: 'Set mods aside',
        kind: 'update_triage',
        onStatusChange: (status) => {
          if (status === 'success' || status === 'failed') load()
        },
      })
    } catch (err) {
      pushToast(err.message || 'Could not set the mods aside', 'error')
    } finally {
      setBusy(false)
    }
  }

  async function dismiss() {
    try {
      await api.del('/api/update/triage')
      load()
    } catch (err) {
      pushToast(err.message || 'Could not dismiss the report', 'error')
    }
  }

  if (!data || (!data.report && !data.set_aside?.length)) return null
  const { report, occasion, set_aside: aside } = data
  const recheck = report?.recheck
  const total = (offer?.mods?.length || 0) + (offer?.dependents?.length || 0)

  return (
    <div className="card boot-check">
      <div className="boot-check-head">
        <h2>After the update</h2>
        {canAdmin && report && (
          <button className="btn btn-ghost btn-small" onClick={dismiss}>
            Dismiss
          </button>
        )}
      </div>
      {report && (
        <>
          {/* The report stays until the next update or Dismiss, so without
              its time it read as news days later (night test 2026-11-22). */}
          {report.since && <p className="hint">Boot of {formatDateTime(report.since * 1000)}</p>}
          {failed(report.outcome) ? (
            <p className="boot-check-failed">
              The server {OUTCOME[report.outcome]} {occasion}. {report.detail}
            </p>
          ) : (
            <p>
              The server came back {occasion} and was joinable {report.seconds}s after it started.
            </p>
          )}
          {report.world_damage && (
            <p className="banner banner-warning">
              The server could not read {report.unreadable_items} saved items on this boot (bases,
              vehicles, gear). They leave the world at its next save.{' '}
              {report.backup ? (
                <>
                  To undo it, stop the server and restore the newest backup, {report.backup.name}, on
                  the <Link to="/backups">Backups</Link> page.
                </>
              ) : (
                'No backup of this mission exists to restore.'
              )}
            </p>
          )}
          <Suspects suspects={report.suspects} />
          <Unattributed lines={report.unattributed} />
          {failed(report.outcome) && !report.suspects?.length && !report.unattributed?.length && (
            <p className="hint">
              The server log names no script file{report.rpt ? ` (${report.rpt})` : ''}. Read it in{' '}
              <Link to="/analysis?tab=Server+logs">Analysis → Server logs</Link>.
            </p>
          )}

          {recheck && (
            <div className="boot-check-recheck">
              <p>
                Set aside: {(report.set_aside || []).join(', ')}. Restarted without them, the server{' '}
                {recheck.outcome === 'joinable'
                  ? `was joinable after ${recheck.seconds}s.`
                  : `${OUTCOME[recheck.outcome] || recheck.outcome} again. ${recheck.detail || ''}`}
              </p>
              <Suspects suspects={recheck.suspects} />
              <Unattributed lines={recheck.unattributed} />
            </div>
          )}

          {canAdmin && data.offerable?.length > 0 && (
            <div className="boot-check-offer">
              <h3>Set them aside until their authors catch up</h3>
              <ul className="boot-check-picks">
                {data.offerable.map((mod) => (
                  <li key={mod}>
                    <label>
                      <input
                        type="checkbox"
                        checked={picked.includes(mod)}
                        onChange={(e) => pick(mod, e.target.checked)}
                      />
                      {mod}
                    </label>
                  </li>
                ))}
              </ul>
              {picked.length > 0 && offer?.dependents?.length > 0 && (
                <p>These need them and are set aside with them: {offer.dependents.join(', ')}.</p>
              )}
              <p className="boot-check-warning">{offer?.warning}</p>
              <p className="hint">
                {offer?.backup
                  ? `Newest backup: ${offer.backup.name} (${new Date(offer.backup.timestamp).toLocaleString()})${
                      offer.backup.fresh ? ', taken during this update.' : ', from before this update.'
                    }`
                  : 'There is no backup of this mission yet.'}
              </p>
              <div className="dry-run-toggle">
                <Switch checked={backupFirst} onChange={setBackupFirst}>
                  Take a backup first
                </Switch>
              </div>
              <button
                className="btn btn-danger"
                disabled={busy || !picked.length}
                onClick={() => setConfirming(true)}
              >
                Set {total || picked.length} mod{(total || picked.length) === 1 ? '' : 's'} aside and restart
              </button>
            </div>
          )}
        </>
      )}

      {aside?.length > 0 && (
        <div className="boot-check-aside">
          <h3>Set aside after an update</h3>
          <p className="hint">
            Zedstead says when one of these has updated, and never enables it by itself. Enable one on the{' '}
            <Link to="/mods">Mods</Link> page when you want to try it again.
          </p>
          <ul>
            {aside.map((e) => (
              <li key={e.mod}>
                <Link className="mod-link" to={modLink(e.mod)}>{e.mod}</Link>
                <span className="text-muted"> DayZ build {e.build || '?'}, {e.at}</span>
                {e.updated_at ? (
                  <span className="status-pill status-pill-success">updated {day(e.updated_at)}</span>
                ) : (
                  <span
                    className="status-pill status-pill-neutral"
                    {...tooltipAttrs('Its author has not published an update since it was set aside.')}
                  >
                    waiting
                  </span>
                )}
                {!e.disabled && <span className="status-pill status-pill-neutral">enabled again</span>}
              </li>
            ))}
          </ul>
        </div>
      )}

      <ConfirmModal
        open={confirming}
        title="Set mods aside?"
        message={offer?.warning}
        confirmLabel="Set aside and restart"
        danger
        onConfirm={apply}
        onCancel={() => setConfirming(false)}
      >
        <p className="modal-message">
          Disables {[...picked, ...(offer?.dependents || [])].join(', ')}
          {backupFirst ? ' after taking a backup' : ''}, then restarts the server and checks it once.
        </p>
      </ConfirmModal>
    </div>
  )
}
