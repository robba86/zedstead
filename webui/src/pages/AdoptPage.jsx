// /adopt — take over a DayZ server zsm did not install (plans/78 D4).
//
// GET /api/adopt reads the server and runs the checks against it; nothing is
// written until Apply. Apply writes only zsm's own mod list and launch
// settings. Each fix (copy keys, pasted types, the admin's own unit) is its
// own button and its own route, and Apply runs none of them.

import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import BetaBadge from '../components/BetaBadge.jsx'
import PageIntro from '../components/PageIntro.jsx'
import ErrorBanner from '../components/ErrorBanner.jsx'
import { useToast } from '../components/Toast.jsx'
import CommandBlock from '../components/CommandBlock.jsx'
import PanelNote from '../components/PanelNote.jsx'
import { useConfirm } from '../components/useConfirm.jsx'
import ListRow, { SeverityDot } from '../components/ListRow.jsx'

function Lines({ lines }) {
  if (!lines?.length) return null
  return (
    <div className="job-console-log">
      {lines.map((line, i) => (
        <div key={i}>{line}</div>
      ))}
    </div>
  )
}

function loadedLabel(mod) {
  if (mod.server_only) return 'server-side'
  return mod.loaded ? 'yes' : 'no'
}

export default function AdoptPage() {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState('')
  const [lines, setLines] = useState([])
  const confirm = useConfirm()
  const pushToast = useToast()

  const load = useCallback(async () => {
    setError('')
    try {
      setData(await api.get('/api/adopt'))
    } catch (err) {
      setError(err.message || 'Could not read the server')
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  async function run(key, path, body) {
    setBusy(key)
    try {
      const res = await api.post(path, body)
      setLines(res.lines || [])
      await load()
    } catch (err) {
      pushToast(err.message || 'Failed', 'error')
    } finally {
      setBusy('')
    }
  }

  async function handleApply() {
    const ok = await confirm({
      title: 'Adopt this server?',
      message: 'Zedstead writes its mod list and launch settings from what this server runs with. Nothing on the server is moved or changed.',
      confirmLabel: 'Apply',
    })
    if (!ok) return
    await run('apply', '/api/adopt/apply')
  }

  if (error) {
    return (
      <div className="page adopt-page">
        <div className="page-header">
          <h1>Adopt a server</h1>
        </div>
        <ErrorBanner as="p" dismissKey={`adopt:${error}`}>
          {error}
        </ErrorBanner>
      </div>
    )
  }
  if (!data) {
    return (
      <div className="page adopt-page">
        <div className="page-header">
          <h1>Adopt a server</h1>
        </div>
        <PanelNote>Reading the server and running the checks…</PanelNote>
      </div>
    )
  }

  const { report, adopted, findings, offered_units: offered } = data
  const launch = report.launch
  const checks = [...findings.checks].sort((a, b) => Number(a.ok) - Number(b.ok))
  const summary = findings.log?.summary || {}
  const canApply = !adopted && launch && !launch.uses_variables

  return (
    <div className="page adopt-page">
      <div className="page-header">
        <h1>
          Adopt a server <BetaBadge feature="adopt" />
        </h1>
      </div>

      <PageIntro page="/adopt" />

      {adopted && (
        <p className="banner banner-info">
          Zedstead already keeps this server&apos;s mod list, so Apply is off. The report and the fixes below still work.
        </p>
      )}

      <Lines lines={lines} />

      <div className="card">
        <h3>Launch command</h3>
        {launch ? (
          <>
            <PanelNote>
              From the {launch.source_label}: {launch.path}
            </PanelNote>
            <CommandBlock text={launch.command} />
            {launch.uses_variables && (
              <p className="banner banner-warning">
                It reads shell variables, so which mods it loads is not in the file. Start the server once the way
                you always do; its log then records the command.
              </p>
            )}
          </>
        ) : (
          <p className="banner banner-warning">
            No launch command was found (no server log, unit or start script). Start the server once the way you
            always do, then come back.
          </p>
        )}
        <PanelNote>Mission: {report.mission || 'not found'}</PanelNote>
        <button
          className="btn btn-primary"
          disabled={!canApply || !!busy}
          onClick={handleApply}
        >
          {busy === 'apply' ? 'Adopting…' : 'Apply'}
        </button>
      </div>

      <div className="card">
        <h3>Mod folders</h3>
        {report.mods.length === 0 ? (
          <PanelNote>No @ folders in the server directory.</PanelNote>
        ) : (
          <div className="data-table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Folder</th>
                  <th>Workshop ID</th>
                  <th>Loaded</th>
                  <th>Note</th>
                </tr>
              </thead>
              <tbody>
                {report.mods.map((m) => (
                  <tr key={m.name}>
                    <td>{m.name}</td>
                    <td>{m.workshop_id || '–'}</td>
                    <td>{loadedLabel(m)}</td>
                    <td>{m.problem || (m.local ? 'Your own mod (no Workshop ID); never downloaded' : '')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {report.missing.map((name) => (
          <p key={name} className="banner banner-warning">
            {name} is in the launch command and has no folder.
          </p>
        ))}
      </div>

      {report.pasted.length > 0 && (
        <div className="card" id="pasted-types">
          <h3>Mod types already in the mission</h3>
          <PanelNote>
            These mods&apos; types are already pasted into db/types.xml. Merging the mod as well defines each class
            twice. Choose per mod.
          </PanelNote>
          {report.pasted.map((p) => (
            <div key={p.mod} className="inline-form">
              <span>
                {p.mod}: {p.classes.length} class(es)
                {p.changed?.length > 0 &&
                  `; different from the mod's entry (DayZ's own, or a paste you changed, never removed): ${p.changed.join(', ')}`}
              </span>
              <button
                className="btn btn-ghost btn-small"
                disabled={!!busy}
                onClick={() => run(`keep:${p.mod}`, '/api/adopt/pasted', { mod: p.mod, keep: 'mine' })}
              >
                Keep mine
              </button>
              <button
                className="btn btn-ghost btn-small"
                disabled={!!busy}
                onClick={() => run(`mod:${p.mod}`, '/api/adopt/pasted', { mod: p.mod, keep: 'mod' })}
              >
                Remove mine, use the mod&apos;s
              </button>
            </div>
          ))}
        </div>
      )}

      {report.units.length > 0 && (
        <div className="card">
          <h3>Units that run this server</h3>
          {report.units.map((u) => (
            <div key={u.path} className="inline-form">
              <span>
                {u.name} ({u.scope}, {u.zsm ? "Zedstead's" : 'your own'}) {u.path}
              </span>
              {offered.includes(u.name) && (
                <button
                  className="btn btn-ghost btn-small"
                  disabled={!!busy}
                  onClick={() => run(`unit:${u.name}`, '/api/adopt/disable-unit', { name: u.name })}
                >
                  Stop it starting at boot
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      <div className="card">
        <h3>Checks</h3>
        <ul className="doctor-list">
          {checks.map((c) => (
            <ListRow as="li" key={c.label} wrap lead={<SeverityDot severity={c.ok ? 'ok' : 'error'} />} name={c.label} sub={<>{c.detail}
                {c.fix === 'copy_keys' && (
                  <button
                    className="btn btn-ghost btn-small doctor-action"
                    disabled={!!busy}
                    onClick={() => run('keys', '/api/adopt/copy-keys')}
                  >
                    Copy the missing keys
                  </button>
                )}
                {c.fix === 'pasted_types' && (
                  <a className="link-chip" href="#pasted-types">
                    Choose per mod
                  </a>
                )}</>} />
          ))}
        </ul>
        {findings.skipped.map((s) => (
          <PanelNote key={s.label}>
            Skipped {s.label}: it {s.reason}.
          </PanelNote>
        ))}
        {findings.log?.latest_log && (
          <PanelNote>
            Log {findings.log.latest_log}: {summary.error || 0} error(s), {summary.warning || 0} warning(s).{' '}
            <Link to="/analysis?tab=Server+logs">Read it</Link>
          </PanelNote>
        )}
      </div>

    </div>
  )
}
