import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import { useCan } from '../role.jsx'
import { formatWhen } from '../format.js'
import { useServerStatus } from '../serverStatus.jsx'
import PanelNote from './PanelNote.jsx'
import ListRow from './ListRow.jsx'

// The whole setup since the last server start or since the baseline: the map,
// serverDZ.cfg settings, the mod list and changed files (GET /api/changes and
// /api/changes/baseline, app/ops/changeset_ops.py). The Timeline shows it
// above its list when "Since" is set, because the mod list and files without
// saved versions are not on the line itself. It was the Config changes tab
// until 2026-11-03.

// Row labels for the categories app/ops/changeset_ops.py::_diff_snapshots gives
// a ChangeItem. A `*_header` item ("serverDZ.cfg: 1 change(s)") is not a row:
// it heads the rows after it, as format_change_report prints it in bold for
// `zsm changes`. An id with no label shows none, never itself.
const CHANGE_LABELS = {
  mission: 'Map',
  mods_add: 'Mod added',
  mods_remove: 'Mod removed',
  mods_rename: 'Mod renamed',
  cfg: 'Setting',
  files_new: 'New file',
  files_removed: 'Removed file',
  files: 'Changed file',
  files_detail: '',
}

// The flat list split at each header. Rows before the first header (the map
// change) go in a group without a heading.
function groupChanges(changes) {
  const groups = [{ heading: null, rows: [] }]
  for (const c of changes) {
    if (c.category.endsWith('_header')) groups.push({ heading: c.description, rows: [] })
    else groups[groups.length - 1].rows.push(c)
  }
  return groups.filter((g) => g.heading || g.rows.length)
}

// When the "Since last start" snapshot was taken, and whether the running
// server started after it. The card gave no date, so a snapshot two days and
// ~14 starts old read as current (test pass 1, F51). Every zsm start path
// takes one now (changeset_ops.take_start_snapshot); a restart systemd did on
// its own does not, which is what the second sentence is for.
function SnapshotAge({ at }) {
  const { status, receivedAt } = useServerStatus()
  if (!at) return null
  const taken = new Date(at).getTime()
  const uptime = status?.running ? status?.uptime_seconds : null
  // A minute of slack: the snapshot is taken just before the start.
  const startedAfter = uptime != null && receivedAt && receivedAt - uptime * 1000 > taken + 60000
  return (
    <p className="hint">
      Compared with the snapshot taken {formatWhen(at)}, when the server was last started by Zedstead.
      {startedAfter &&
        ' The server has started again since then without Zedstead (a crash or a restart systemd did itself), so some of these changes may already be live.'}
    </p>
  )
}

// `since` is 'start' or 'baseline'. `onBaseline` tells the Timeline that the
// baseline moved, so its marker and filter follow.
export default function ChangeReport({ since, pushToast, onBaseline }) {
  const isAdmin = useCan('admin')
  const [report, setReport] = useState(null)
  const path = since === 'baseline' ? '/api/changes/baseline' : '/api/changes'

  const load = useCallback(() => {
    setReport(null)
    api
      .get(path)
      .then(setReport)
      .catch((err) => pushToast(err.message || 'Failed to load changes', 'error'))
  }, [path, pushToast])

  useEffect(() => {
    load()
  }, [load])

  async function baseline(method) {
    try {
      await (method === 'set' ? api.post('/api/changes/baseline') : api.del('/api/changes/baseline'))
      pushToast(method === 'set' ? 'Baseline captured.' : 'Baseline cleared.', 'info')
      load()
      onBaseline?.()
    } catch (err) {
      pushToast(err.message || `Failed to ${method} the baseline`, 'error')
    }
  }

  return (
    <div className="change-report">
      <div className="page-header">
        <h3>{since === 'baseline' ? 'Whole setup since the baseline' : 'Whole setup since the last start'}</h3>
        {since === 'baseline' && isAdmin && (
          <div className="inline-form">
            <button className="btn btn-ghost btn-small" onClick={() => baseline('set')}>
              Set baseline now
            </button>
            <button className="btn btn-ghost btn-small" onClick={() => baseline('clear')}>
              Clear baseline
            </button>
          </div>
        )}
      </div>
      <p className="hint">The map, server settings, the mod list and every config file, compared as a whole.</p>
      {since === 'start' && report?.available && <SnapshotAge at={report.snapshot_time} />}
      {!report ? (
        <PanelNote>Loading…</PanelNote>
      ) : !report.available ? (
        <PanelNote>
          {since === 'baseline' ? 'No baseline set yet.' : 'No snapshot available yet: Zedstead takes one at each server start.'}
        </PanelNote>
      ) : report.changes.length === 0 ? (
        <PanelNote>No changes detected.</PanelNote>
      ) : (
        groupChanges(report.changes).map((g, gi) => (
          <div key={gi}>
            {g.heading && <h3 className="log-subhead">{g.heading}</h3>}
            <ul className="doctor-list">
              {g.rows.map((c, i) => (
                <ListRow as="li" key={i} wrap name={CHANGE_LABELS[c.category] ?? ''} sub={c.description} />
              ))}
            </ul>
          </div>
        ))
      )}
    </div>
  )
}
