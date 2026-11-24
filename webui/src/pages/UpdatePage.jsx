import { Switch } from '../components/FlagSwitch.jsx'
import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { HiOutlinePlay, HiOutlinePuzzlePiece } from 'react-icons/hi2'
import { api } from '../api'
import BetaBadge from '../components/BetaBadge.jsx'
import BootCheckCard from '../components/BootCheckCard.jsx'
import SelfUpdateCard from '../components/SelfUpdateCard.jsx'
import DiffView from '../components/DiffView.jsx'
import PageIntro from '../components/PageIntro.jsx'
import SideDrawer from '../components/SideDrawer.jsx'
import SteamLoginGate from '../components/SteamLoginGate.jsx'
import { NEEDS_STEAM_TIP, useSteamLoggedOut } from '../components/NeedsSteam.jsx'
import { tooltipAttrs } from '../components/Tooltip.jsx'
import { useToast } from '../components/Toast.jsx'
import { useJobs } from '../jobs'
import PanelNote from '../components/PanelNote.jsx'
import FoldCaret from '../components/FoldCaret.jsx'

// What each update step does, keyed by the step keys of GET /api/update/steps
// (app/ops/pipeline.py::build_update_steps). A step the backend adds later
// simply has no tip.
const STEP_TIPS = {
  update_server:
    'Downloads and verifies the DayZ server files with SteamCMD. Mission files you edited are kept, or merged with what DayZ changed.',
  download_mods:
    'Downloads every mod in the mod list from the Steam Workshop with SteamCMD, newer versions included.',
  remove_old_mods:
    'Takes the mod folders out of the server directory. They are kept aside as the previous version, so a broken update can be rolled back.',
  copy_mods: "Copies the downloaded mods into the server directory. Undone if a later step fails.",
  copy_bikeys:
    "Copies each mod's .bikey into the server's keys folder, so players can join with that mod's signed files.",
  merge_xml:
    "Puts the mods' types, events and other mission XML into the mission (Custom config). Undone if a later step fails.",
  finalize:
    "Rewrites the server's service files, takes a snapshot for the Changes view, and posts to Discord when a webhook is set.",
}

const DRY_RUN_TIP = 'Lists in the log which ticked steps would run, without running them. Nothing is downloaded, copied or written.'
const START_AFTER_TIP =
  'Starts the server when the update is done and watches it boot. If it does not come up, the After the update card names the mods that stopped it. Off for a dry run.'

// A step's name with its explanation printed under it. A phone has no hover,
// so the text the steps once kept in a tooltip is on the page instead.
function StepText({ title, desc }) {
  return (
    <span className="step-text">
      <span>{title}</span>
      {desc && <span className="step-desc">{desc}</span>}
    </span>
  )
}

const DIFF_DRAWER_WIDTH_KEY = 'zsm.update-conflicts.drawer.width'

// A merged file's listed values (plans/71), in words.
const VALUE_KIND = {
  both_changed: 'both changed',
  dayz_removed: 'DayZ removed it',
  you_removed: 'you removed it',
}

// The same counts the job log's "Merged … with DayZ build N" line gives.
function mergedCounts(c) {
  const parts = [`took ${c.dayz ?? 0} of DayZ's changes`, `kept ${c.yours ?? 0} of yours`]
  if (c.tweaks) parts.push(`scaled ${c.tweaks} tweaked value(s) from DayZ's new numbers`)
  return `Merged: ${parts.join(', ')}.`
}

// Rows still waiting for a choice first; the order is otherwise the merge's.
function waitingFirst(values) {
  return [...values].sort((a, b) => Number(Boolean(b.waiting)) - Number(Boolean(a.waiting)))
}

function ValueCell({ value }) {
  if (value == null) return <span className="text-muted">(none)</span>
  const text = String(value)
  if (!text.includes('\n') && text.length <= 40) return <code>{text}</code>
  return (
    <details className="update-value">
      <summary>
        <FoldCaret /> <code>{text.split('\n')[0].slice(0, 40)}…</code>
      </summary>
      <pre>{text}</pre>
    </details>
  )
}

export default function UpdatePage() {
  const [steps, setSteps] = useState([])
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState({})
  const [dryRun, setDryRun] = useState(false)
  // Off by default: the page has always left a stopped server stopped.
  const [startAfter, setStartAfter] = useState(false)
  const [jobResult, setJobResult] = useState(null)
  const [busy, setBusy] = useState(false)
  const [conflicts, setConflicts] = useState([])
  // { file, text } for the conflict whose diff is open in the drawer.
  const [diff, setDiff] = useState(null)
  const pushToast = useToast()
  const { startJob } = useJobs()
  const steamLoggedOut = useSteamLoggedOut()
  // A step that runs SteamCMD (`needs_steam` from GET /api/update/steps).
  const blocked = (step) => steamLoggedOut && step.needs_steam

  // Stock mission files a DayZ update changed after this server had changed
  // them (plans/67). A scheduled update makes them too, so read on load.
  const loadConflicts = useCallback(async () => {
    let rows = []
    try {
      rows = (await api.get('/api/update/conflicts')).conflicts
    } catch {
      rows = []
    }
    setConflicts(rows)
    // A settled file's diff has nothing left to compare.
    setDiff((open) => (open && rows.some((c) => c.file === open.file) ? open : null))
  }, [])

  const onJobStatus = useCallback((status, job) => {
    if (status === 'success' || status === 'failed') {
      setJobResult(job.result)
      loadConflicts()
    }
  }, [loadConflicts])

  const loadSteps = useCallback(async () => {
    try {
      const data = await api.get('/api/update/steps')
      setSteps(data.steps)
      setSelected((prev) => {
        if (Object.keys(prev).length) return prev
        const next = {}
        for (const step of data.steps) next[step.key] = true
        return next
      })
    } catch (err) {
      pushToast(err.message || 'Failed to load update steps', 'error')
    } finally {
      setLoading(false)
    }
  }, [pushToast])

  useEffect(() => {
    loadSteps()
    loadConflicts()
  }, [loadSteps, loadConflicts])

  function toggleStep(key) {
    setSelected((prev) => ({ ...prev, [key]: !prev[key] }))
  }

  async function handleRun() {
    setBusy(true)
    setJobResult(null)
    try {
      await startJob({
        endpoint: '/api/update/run',
        body: { steps: runnableKeys, dry_run: dryRun, start_after: startAfter && !dryRun },
        name: 'Update pipeline',
        kind: 'update',
        onStatusChange: onJobStatus,
      })
    } catch (err) {
      pushToast(err.message || 'Failed to start update', 'error')
    } finally {
      setBusy(false)
    }
  }

  async function handleRollback() {
    if (!jobResult) return
    const rollbackKeys = jobResult.results
      .filter((r) => r.status === 'success')
      .map((r) => r.key)
      .filter((key) => steps.find((s) => s.key === key)?.undoable)
    if (!rollbackKeys.length) return

    setBusy(true)
    setJobResult(null)
    try {
      await startJob({
        endpoint: '/api/update/rollback',
        body: { steps: rollbackKeys },
        name: 'Update rollback',
        kind: 'update_rollback',
        onStatusChange: onJobStatus,
      })
    } catch (err) {
      pushToast(err.message || 'Rollback failed to start', 'error')
    } finally {
      setBusy(false)
    }
  }

  async function handleDiff(file) {
    // The open row's button closes the drawer again, as in FileVersions.
    if (diff?.file === file) {
      setDiff(null)
      return
    }
    try {
      const data = await api.get(`/api/update/conflicts/diff?${new URLSearchParams({ file })}`)
      setDiff({ file, text: data.diff })
    } catch (err) {
      pushToast(err.message || 'Diff failed', 'error')
    }
  }

  async function handleValues(file, keep, ids) {
    setBusy(true)
    try {
      await startJob({
        endpoint: '/api/update/conflicts/values',
        body: { file, keep, ids },
        name: 'Settle update values',
        kind: 'update_conflicts',
        onStatusChange: (status) => {
          if (status === 'success' || status === 'failed') loadConflicts()
        },
      })
    } catch (err) {
      pushToast(err.message || 'Failed to start', 'error')
    } finally {
      setBusy(false)
    }
  }

  async function handleResolve(keep, files) {
    setBusy(true)
    try {
      await startJob({
        endpoint: '/api/update/conflicts/resolve',
        body: files ? { keep, files } : { keep },
        name: 'Settle update conflicts',
        kind: 'update_conflicts',
        onStatusChange: (status) => {
          if (status === 'success' || status === 'failed') loadConflicts()
        },
      })
    } catch (err) {
      pushToast(err.message || 'Failed to start', 'error')
    } finally {
      setBusy(false)
    }
  }

  // An empty list runs every step on the server, so Run needs at least one.
  const runnableKeys = steps.filter((s) => selected[s.key] && !blocked(s)).map((s) => s.key)

  const hasFailure = jobResult?.results?.some((r) => r.status === 'failed')
  const whole = conflicts.filter((c) => !c.merged)
  const merged = conflicts.filter((c) => c.merged)
  const openMerged = merged.some((c) => c.file === diff?.file)

  return (
    <div className="page update-page">
      <div className="page-header">
        <h1>Update</h1>
        <div className="action-bar">
          <Link
            className="btn btn-ghost"
            to="/mods?tab=Installed"
            {...tooltipAttrs('The mod list the update downloads and copies, disabled mods included.')}
          >
            <HiOutlinePuzzlePiece aria-hidden="true" />
            Installed mods
          </Link>
        </div>
      </div>

      <PageIntro page="/update" />

      <SteamLoginGate />

      <BootCheckCard />

      {merged.length > 0 && (
        <div className="card update-conflicts">
          <h2>
            Merged with DayZ's update <BetaBadge feature="update-merge" />
          </h2>
          <p className="hint">
            A DayZ update changed these stock mission files, which you had changed too. Each of
            DayZ's changes came in and each of yours stayed. The values below were changed by both
            sides, or removed by one of them: check them here. Both versions are saved in{' '}
            <Link to="/analysis?tab=Change+history">Analysis → Change history</Link>. Which side a value both
            changed starts as is set by{' '}
            <Link to="/settings?q=mission_update_conflict">mission_update_conflict</Link>.
          </p>
          {merged.map((c) => (
            <div key={c.file} className="update-merged-file">
              <div className="update-merged-head">
                <strong>{c.file}</strong>
                <span className="text-muted">
                  {c.build_before && c.build_after ? ` build ${c.build_before} → ${c.build_after}` : ''}
                </span>
                <span className="update-merged-actions">
                  <button className="btn btn-ghost btn-small" onClick={() => handleDiff(c.file)}>
                    Diff
                  </button>
                  <button className="btn btn-ghost btn-small" disabled={busy} onClick={() => handleResolve('mine', [c.file])}>
                    Use my whole file
                  </button>
                  <button className="btn btn-ghost btn-small" disabled={busy} onClick={() => handleResolve('dayz', [c.file])}>
                    Use DayZ's whole file
                  </button>
                </span>
              </div>
              <p className="text-muted">{mergedCounts(c)}</p>
              <div className="data-table-wrap">
                <table className="results-table">
                  <thead>
                    <tr>
                      <th>Entry</th>
                      <th>Value</th>
                      <th>Was</th>
                      <th>Yours</th>
                      <th>DayZ's</th>
                      <th>Now</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {waitingFirst(c.values).map((v) => {
                      const other = v.live === 'mine' ? 'dayz' : 'mine'
                      return (
                        <tr key={v.id}>
                          <td>{v.entry}</td>
                          <td>{v.field}</td>
                          <td><ValueCell value={v.base} /></td>
                          <td><ValueCell value={v.mine} /></td>
                          <td><ValueCell value={v.theirs} /></td>
                          <td>
                            {v.waiting ? "DayZ's, waiting" : v.live === 'mine' ? 'yours' : "DayZ's"}
                            <div className="text-muted">{VALUE_KIND[v.kind] || v.kind}</div>
                          </td>
                          <td>
                            <div className="update-value-actions">
                              {v.waiting ? (
                                <>
                                  <button className="btn btn-small" disabled={busy} onClick={() => handleValues(c.file, 'mine', [v.id])}>
                                    Use mine
                                  </button>
                                  <button className="btn btn-small" disabled={busy} onClick={() => handleValues(c.file, 'dayz', [v.id])}>
                                    Keep DayZ's
                                  </button>
                                </>
                              ) : (
                                <>
                                  <button className="btn btn-small" disabled={busy} onClick={() => handleValues(c.file, 'ok', [v.id])}>
                                    OK
                                  </button>
                                  <button className="btn btn-ghost btn-small" disabled={busy} onClick={() => handleValues(c.file, other, [v.id])}>
                                    {other === 'mine' ? 'Use mine' : "Use DayZ's"}
                                  </button>
                                </>
                              )}
                            </div>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
              {c.values.length > 1 && (
                <button className="btn btn-small" disabled={busy} onClick={() => handleValues(c.file, 'ok', c.values.map((v) => v.id))}>
                  Leave all as they are
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      {whole.length > 0 && (
        <div className="card update-conflicts">
          <h2>
            Mission files to check <BetaBadge feature="update-merge" />
          </h2>
          <p className="hint">
            A DayZ update changed these stock mission files, and they were not stock on this server
            either. DayZ's new file is live now. Diff shows what separates it from the version you
            had, which is also saved in <Link to="/analysis?tab=Change+history">Analysis → Change history</Link>.
            If only your mods changed a file, keep DayZ's: the mods are merged back in either way.
            To stop being asked, set <Link to="/settings?q=mission_update_conflict">mission_update_conflict</Link>.
          </p>
          <div className="data-table-wrap">
            <table className="results-table">
              <tbody>
                {whole.map((c) => (
                  <tr key={c.file} className={diff?.file === c.file ? 'row-active' : undefined}>
                    <td>{c.file}</td>
                    <td>{c.build_before && c.build_after ? `build ${c.build_before} → ${c.build_after}` : ''}</td>
                    <td>
                      <button className="btn btn-ghost btn-small" onClick={() => handleDiff(c.file)}>
                        Diff
                      </button>{' '}
                      <button className="btn btn-small" disabled={busy} onClick={() => handleResolve('mine', [c.file])}>
                        Keep mine
                      </button>{' '}
                      <button className="btn btn-small" disabled={busy} onClick={() => handleResolve('dayz', [c.file])}>
                        Keep DayZ's
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {whole.length > 1 && (
            <button className="btn" disabled={busy} onClick={() => handleResolve('dayz', whole.map((c) => c.file))}>
              Keep DayZ's for all
            </button>
          )}
        </div>
      )}

      {diff && (
        <SideDrawer
          title={diff.file}
          badge={openMerged ? 'Merged vs yours' : "DayZ's vs yours"}
          subtitle={
            openMerged
              ? '+ only in your version · − only in the merged file · mod blocks left out'
              : "+ only in your version · − only in DayZ's new file · mod blocks left out"
          }
          storageKey={DIFF_DRAWER_WIDTH_KEY}
          onClose={() => setDiff(null)}
        >
          {diff.text ? (
            <DiffView text={diff.text} />
          ) : (
            <PanelNote>
              The two differ only in mod blocks, which the merge puts back either way. Keeping
              DayZ's loses nothing of yours.
            </PanelNote>
          )}
          {/* Below the diff and sticky to the drawer floor, as on the Mod XML
              page: the choice comes after reading. */}
          <div className="sticky-action-bar">
            <button className="btn btn-ghost" disabled={busy} onClick={() => handleResolve('mine', [diff.file])}>
              {openMerged ? 'Use my whole file' : 'Keep mine'}
            </button>
            <button className="btn" disabled={busy} onClick={() => handleResolve('dayz', [diff.file])}>
              {openMerged ? "Use DayZ's whole file" : "Keep DayZ's"}
            </button>
          </div>
        </SideDrawer>
      )}

      <SelfUpdateCard />

      <div className="card">
        <div className="card-header">
          <h2>Steps</h2>
          <div className="action-bar">
            <button className="btn btn-primary" disabled={busy || loading || !runnableKeys.length} onClick={handleRun}>
              <HiOutlinePlay aria-hidden="true" />
              Run
            </button>
          </div>
        </div>
        {loading ? (
          <PanelNote>Loading update steps…</PanelNote>
        ) : steps.length === 0 ? (
          <PanelNote>No update steps available.</PanelNote>
        ) : (
          <ul className="step-list">
            {steps.map((step) => (
              <li key={step.key} className={blocked(step) ? 'step-blocked' : undefined}>
                <Switch
                  checked={!!selected[step.key] && !blocked(step)}
                  disabled={blocked(step)}
                  onChange={() => toggleStep(step.key)}
                >
                  <StepText title={step.label} desc={blocked(step) ? NEEDS_STEAM_TIP : STEP_TIPS[step.key]} />
                </Switch>
              </li>
            ))}
          </ul>
        )}
        <div className="dry-run-toggle">
          <Switch checked={dryRun} onChange={setDryRun}>
            <StepText title="Dry run" desc={DRY_RUN_TIP} />
          </Switch>
        </div>
        <div className="dry-run-toggle">
          <Switch checked={startAfter && !dryRun} disabled={dryRun} onChange={setStartAfter}>
            <StepText title="Start the server afterwards" desc={START_AFTER_TIP} />
          </Switch>
        </div>
      </div>

      {jobResult && (
        <div className="card">
          <h2>Results</h2>
          {jobResult.game && (
            <p>DayZ updated: build {jobResult.game.before} → {jobResult.game.after}.</p>
          )}
          {/* Wrapped like every other table in the app: the third column is a
              raw error string, which is exactly what overflows a phone. */}
          <div className="data-table-wrap">
            <table className="results-table">
              <tbody>
                {jobResult.results.map((r) => (
                  <tr key={r.key}>
                    <td>{r.label}</td>
                    <td className={`result-status result-status-${r.status}`}>{r.status}</td>
                    <td>{r.error || ''}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {hasFailure && (
            <button className="btn btn-danger" disabled={busy} onClick={handleRollback}>
              Roll back completed steps
            </button>
          )}
        </div>
      )}
    </div>
  )
}
