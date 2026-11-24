import { Switch } from '../components/FlagSwitch.jsx'
import Select from '../components/Select.jsx'
import { useCallback, useEffect, useState } from 'react'
import { HiOutlineClock, HiOutlineTrash } from 'react-icons/hi2'
import { api } from '../api'
import BetaBadge from '../components/BetaBadge.jsx'
import PageIntro from '../components/PageIntro.jsx'
import { useWs } from '../ws'
import { useToast } from '../components/Toast.jsx'
import DataTable from '../components/DataTable.jsx'
import { tooltipAttrs } from '../components/Tooltip.jsx'
import { useJobs } from '../jobs'
import { useUrlTabs } from '../urlTabs.js'
import { SCHEDULER_TABS as TABS } from '../pageTabs.js'
import PanelNote from '../components/PanelNote.jsx'
import Tabs from '../components/Tabs.jsx'
import { useConfirm } from '../components/useConfirm.jsx'

// Mirrors TASK_COMMANDS / TASK_TYPE_LABELS in app/server/scheduler.py. The
// API validates against its own copy, so a type added here and not there is
// a 422, not a silent no-op.
// tests/test_scheduler_page_types.py holds the two lists together: a type
// added only to the backend is silent (the picker just never offers it).
const TASK_TYPES = ['restart', 'update', 'backup', 'mod_check', 'announce', 'rcon', 'wipe']
const TASK_TYPE_LABELS = {
  restart: 'Server Restart',
  update: 'Server + Mod Update',
  backup: 'Quick Backup',
  mod_check: 'Mod Update Check',
  announce: 'Announcement',
  rcon: 'RCon Command',
  wipe: 'Server Wipe',
}

// Types whose behaviour comes from `options`, and the single key each reads.
// Mirrors PAYLOAD_TASK_TYPES in app/server/scheduler.py; the backend validates,
// so a mismatch here is a 422 rather than a silently ignored field.
const PAYLOAD_FIELDS = {
  announce: { key: 'message', label: 'Message', placeholder: 'Restarting in 15 minutes!' },
  rcon: { key: 'command', label: 'RCon command', placeholder: '#lock' },
}
const MAX_PAYLOAD_CHARS = 512

// Shown under the type picker. Only types with something non-obvious to say.
const TASK_TYPE_HINTS = {
  update:
    'Stops the server, updates DayZ and every mod, merges the mod XML, then starts it again. ' +
    'Skipped while players are online (Configs \u2192 Updates), takes a quick backup ' +
    'first, and rolls the mods and XML back if the update fails so the server comes up as it was. ' +
    'In system scope it stops the server with sudo, so it needs passwordless sudo to work ' +
    'unattended \u2014 user scope (the default) needs nothing.',
  wipe:
    'Warns players like a restart, stops the server, takes a backup, deletes persistence, then ' +
    'starts the server again. There is no undo beyond your backups. In system scope it stops the ' +
    'server with sudo, so it needs passwordless sudo to work unattended.',
  announce:
    'Broadcasts this text to everyone online, over RCon. Nothing is sent while the server is ' +
    'down \u2014 there is nobody to announce to, and that is not treated as a failure.',
  rcon:
    'Sends one raw RCon command, exactly as typed. Same authority as typing it in a console, ' +
    'just on a schedule. Needs RCon configured and the server up.',
}

// Guided cron builder — UI copy, not domain logic; the backend still validates
// every expression.
const CRON_FIELDS = [
  { key: 'minute', label: 'Minute', def: '0', hint: '* , */5 , 0,30 , 5-10' },
  { key: 'hour', label: 'Hour', def: '*/4', hint: '* , */4 , 0,12 , 9-17' },
  { key: 'day', label: 'Day', def: '*', hint: '* , 1 , */2 , 1,15' },
  { key: 'month', label: 'Month', def: '*', hint: '* , 1 , */3 , 6,12' },
  { key: 'weekday', label: 'Weekday', def: '*', hint: '* , 0=Sun , 1-5 , 0,6' },
]

const DEFAULT_PARTS = { minute: '0', hour: '*/4', day: '*', month: '*', weekday: '*' }

function buildCron(parts) {
  return CRON_FIELDS.map((f) => (parts[f.key] || '').trim() || '*').join(' ')
}

function splitCron(cron) {
  const p = (cron || '').trim().split(/\s+/)
  if (p.length !== 5) return null
  return { minute: p[0], hour: p[1], day: p[2], month: p[3], weekday: p[4] }
}

function autoId(type, cron) {
  return `${type}_custom_${cron.replace(/[^a-zA-Z0-9]+/g, '-')}`.replace(/^-+|-+$/g, '')
}

// scheduler.timer_install_state's four states.
const TIMER_STATE_LABELS = {
  installed: 'Installed',
  outdated: 'Needs reinstall',
  missing: 'Not installed',
  disabled: 'Off',
}

// "Next run" is computed from the cron alone, so it is only a promise when
// the timer on disk is the one the task describes. Unknown state (no
// systemd to read) keeps the computed time.
function nextRunText(task, state) {
  if (!state || state === 'installed') return task.next_run_display
  if (state === 'disabled') return '—'
  if (state === 'missing') return '— (timer not installed)'
  return '— (reinstall to apply)'
}

export default function SchedulerPage() {
  const { tab, setTab } = useUrlTabs(TABS)
  const [tasks, setTasks] = useState([])
  const [presets, setPresets] = useState({})
  const [info, setInfo] = useState({ has_systemd: false, sudo_available: false, privileged_ops_ok: false })
  // Per task: installed / outdated / missing / disabled, read from the unit
  // files. Empty = unknown (Windows, no systemd), which must not read as
  // "not installed".
  const [installState, setInstallState] = useState({})
  const [orphans, setOrphans] = useState([])
  const confirm = useConfirm()
  // The zone cron times are read in: the server's, not the browser's.
  const [timeZone, setTimeZone] = useState('')

  // Builder form state. editingId === null → creating a new task; otherwise
  // editing the task with that (immutable) id.
  const [editingId, setEditingId] = useState(null)
  const [formType, setFormType] = useState('restart')
  const [formId, setFormId] = useState('')
  const [formPayload, setFormPayload] = useState('')
  const [idEdited, setIdEdited] = useState(false)
  const [formEnabled, setFormEnabled] = useState(true)
  const [parts, setParts] = useState(DEFAULT_PARTS)
  const [advanced, setAdvanced] = useState(false)
  const [rawCron, setRawCron] = useState('0 */4 * * *')
  const [cronValid, setCronValid] = useState(null)
  const [cronDescription, setCronDescription] = useState('')
  // Why the backend would refuse the expression (plans/68 B24): the same
  // words a refused save's 422 carries, shown before the user tries.
  const [cronReason, setCronReason] = useState('')

  const pushToast = useToast()
  const { startJob } = useJobs()

  const cron = advanced ? rawCron : buildCron(parts)

  const loadSchedule = useCallback(async () => {
    try {
      const data = await api.get('/api/schedule')
      setTasks(data.tasks)
      setPresets(data.presets)
      setInstallState(data.install_state || {})
      setOrphans(data.orphans || [])
      setTimeZone(data.time_zone || '')
    } catch (err) {
      pushToast(err.message || 'Failed to load schedule', 'error')
    }
  }, [pushToast])

  const loadInfo = useCallback(async () => {
    try {
      setInfo(await api.get('/api/schedule/info'))
    } catch {
      // ignore
    }
  }, [])

  useEffect(() => {
    loadSchedule()
    loadInfo()
  }, [loadSchedule, loadInfo])

  const handleWsMessage = useCallback(
    (msg) => {
      if (msg.type === 'schedule_changed') loadSchedule()
    },
    [loadSchedule]
  )
  useWs(handleWsMessage)

  // Debounced live cron validation whenever the derived expression changes.
  useEffect(() => {
    if (!cron.trim()) {
      setCronValid(null)
      return
    }
    const handle = setTimeout(async () => {
      try {
        const data = await api.post('/api/schedule/validate-cron', { expr: cron })
        setCronValid(data.valid)
        setCronDescription(data.description || '')
        setCronReason(data.reason || '')
      } catch {
        setCronValid(null)
      }
    }, 300)
    return () => clearTimeout(handle)
  }, [cron])

  // Auto-generate a sanitized id while creating, until the user hand-edits it.
  useEffect(() => {
    if (editingId !== null || idEdited) return
    setFormId(autoId(formType, cron))
  }, [formType, cron, idEdited, editingId])

  function resetForm() {
    setEditingId(null)
    setFormType('restart')
    setFormId('')
    setFormPayload('')
    setIdEdited(false)
    setFormEnabled(true)
    setParts(DEFAULT_PARTS)
    setAdvanced(false)
    setRawCron('0 */4 * * *')
  }

  function toggleAdvanced() {
    if (!advanced) {
      setRawCron(buildCron(parts))
      setAdvanced(true)
    } else {
      const sp = splitCron(rawCron)
      if (sp) setParts(sp)
      setAdvanced(false)
    }
  }

  function startEdit(task) {
    setTab('New task')
    setEditingId(task.id)
    setFormType(task.task_type)
    setFormId(task.id)
    setFormPayload(task.options?.[PAYLOAD_FIELDS[task.task_type]?.key] || '')
    setIdEdited(true)
    setFormEnabled(task.enabled)
    const sp = splitCron(task.cron)
    if (sp) {
      setParts(sp)
      setAdvanced(false)
    } else {
      setRawCron(task.cron)
      setAdvanced(true)
    }
  }

  function applyPreset(key) {
    const preset = presets[key]
    if (!preset) return
    setFormType(preset.task_type)
    const sp = splitCron(preset.cron)
    if (sp) {
      setParts(sp)
      setAdvanced(false)
    } else {
      setRawCron(preset.cron)
      setAdvanced(true)
    }
    if (editingId === null) {
      setFormId(preset.id)
      setIdEdited(true)
    }
  }

  async function handleSubmit() {
    if (!cron.trim()) return
    const field = PAYLOAD_FIELDS[formType]
    // The backend refuses an empty payload anyway; stopping here keeps the
    // error next to the field instead of in a toast.
    if (field && !formPayload.trim()) {
      pushToast(`A ${TASK_TYPE_LABELS[formType].toLowerCase()} task needs a ${field.key}.`, 'error')
      return
    }
    const options = field ? { [field.key]: formPayload.trim() } : {}
    try {
      if (editingId === null) {
        if (!formId.trim()) return
        await api.post('/api/schedule', {
          id: formId.trim(),
          task_type: formType,
          cron,
          options,
        })
      } else {
        await api.put(`/api/schedule/${encodeURIComponent(editingId)}`, {
          task_type: formType,
          cron,
          enabled: formEnabled,
          options,
        })
      }
      resetForm()
      setTab('Tasks')
      loadSchedule()
    } catch (err) {
      pushToast(err.message || 'Failed to save task', 'error')
    }
  }

  async function toggleEnabled(task) {
    try {
      await api.put(`/api/schedule/${encodeURIComponent(task.id)}`, { enabled: !task.enabled })
      // The open edit form sends `enabled` on Save, which undid this toggle.
      if (task.id === editingId) setFormEnabled(!task.enabled)
      loadSchedule()
    } catch (err) {
      pushToast(err.message || 'Failed to update task', 'error')
    }
  }

  async function handleRemoveTask(id) {
    const ok = await confirm({ title: 'Remove scheduled task?', message: `Remove task ${id}?`, confirmLabel: 'Remove', danger: true })
    if (!ok) return
    try {
      await api.del(`/api/schedule/${encodeURIComponent(id)}`)
      if (editingId === id) resetForm()
      loadSchedule()
    } catch (err) {
      pushToast(err.message || 'Failed to remove task', 'error')
    }
  }

  // Install writes systemd units, so it asks first, as Remove does.
  async function handleInstallTimers() {
    const ok = await confirm({
      title: 'Install timers?',
      message:
        'Writes a timer for the tasks in the list and registers it with the system, so the tasks run on their schedule from now on.',
      confirmLabel: 'Install timers',
    })
    if (!ok) return
    try {
      await startJob({
        endpoint: '/api/schedule/install-timers',
        name: 'Install timers',
        kind: 'timers_install',
        onStatusChange: (status) => {
          if (status === 'success' || status === 'failed') loadSchedule()
        },
      })
    } catch (err) {
      pushToast(err.message || 'Failed to install timers', 'error')
    }
  }

  async function handleRemoveTimers() {
    const ok = await confirm({
      title: 'Remove timers?',
      message: 'Removes the installed systemd timers, so no task runs until you install them again. The tasks stay in the list.',
      confirmLabel: 'Remove timers',
      danger: true,
    })
    if (!ok) return
    try {
      await startJob({
        endpoint: '/api/schedule/remove-timers',
        name: 'Remove timers',
        kind: 'timers_remove',
        onStatusChange: (status) => {
          if (status === 'success' || status === 'failed') loadSchedule()
        },
      })
    } catch (err) {
      pushToast(err.message || 'Failed to remove timers', 'error')
    }
  }

  // Prefer the scope-aware gate; fall back to legacy sudo_available.
  const privilegedOk =
    info.privileged_ops_ok !== undefined ? info.privileged_ops_ok : info.sudo_available

  const stateCounts = Object.values(installState).reduce((acc, st) => {
    acc[st] = (acc[st] || 0) + 1
    return acc
  }, {})
  const outOfDate = [
    stateCounts.missing && `${stateCounts.missing} not installed`,
    stateCounts.outdated && `${stateCounts.outdated} need reinstalling`,
    orphans.length > 0 && `${orphans.length} left over from deleted tasks`,
  ].filter(Boolean)

  return (
    <div className="page scheduler-page">
      <div className="page-header">
        <h1>
          Scheduler <BetaBadge feature="scheduler" />
        </h1>
        <div className="action-bar">
          {info.has_systemd && info.scope && (
            <span
              className="status-pill status-pill-neutral"
              {...tooltipAttrs('systemd service scope')}
            >
              {info.scope} scope
            </span>
          )}
        </div>
      </div>

      <PageIntro page="/scheduler" />

      {!info.has_systemd && (
        <div className="banner banner-warning">No systemd detected — timers cannot be installed here.</div>
      )}
      {info.has_systemd && !privilegedOk && (
        <div className="banner banner-warning">
          {info.privileged_ops_reason ||
            'sudo is not available non-interactively. Run zsm service install-timers from a terminal.'}
        </div>
      )}

      {outOfDate.length > 0 && (
        <div className="banner banner-warning">
          Timers are out of date ({outOfDate.join(', ')}) — Install timers to apply your changes.{' '}
          <button
            className="btn btn-ghost btn-small"
            disabled={!privilegedOk}
            onClick={handleInstallTimers}
          >
            Install timers
          </button>
        </div>
      )}

      <Tabs tabs={TABS} active={tab} onSelect={setTab} />

      {tab === 'New task' && (
      <div className="card">
        <h2>{editingId === null ? 'New task' : `Edit task: ${editingId}`}</h2>

        {/* Labelled like the cron fields below: a filled-in ID with no label
            read as a value of unknown meaning. */}
        <div className="cron-builder-grid">
          <div className="cron-field">
            <label className="cron-field-label" htmlFor="task-type">Type</label>
            <Select
              id="task-type"
              value={formType}
              onChange={setFormType}
              options={TASK_TYPES.map((t) => ({ value: t, label: TASK_TYPE_LABELS[t] }))}
            />
          </div>
          <div className="cron-field">
          <label className="cron-field-label" htmlFor="task-id">Task ID</label>
          <input
            id="task-id"
            className="text-input"
            value={formId}
            disabled={editingId !== null}
            onChange={(e) => {
              setFormId(e.target.value)
              setIdEdited(true)
            }}
            placeholder="e.g. restart-1"
          />
          </div>
        </div>
        {PAYLOAD_FIELDS[formType] && (
          <label className="modal-field">
            {PAYLOAD_FIELDS[formType].label}
            <input
              className="field"
              value={formPayload}
              maxLength={MAX_PAYLOAD_CHARS}
              onChange={(e) => setFormPayload(e.target.value)}
              placeholder={PAYLOAD_FIELDS[formType].placeholder}
            />
            <span className="hint">
              One line, up to {MAX_PAYLOAD_CHARS} characters. It is stored in Zedstead's config and
              read back when the task fires — it never goes into the generated unit file.
            </span>
          </label>
        )}
        {TASK_TYPE_HINTS[formType] && <p className="hint">{TASK_TYPE_HINTS[formType]}</p>}
        {editingId !== null && (
          <PanelNote>Task ID is the immutable key — to rename, remove and recreate.</PanelNote>
        )}

        {advanced ? (
          <div className="inline-form">
            <input
              className="text-input"
              value={rawCron}
              onChange={(e) => setRawCron(e.target.value)}
              placeholder="Cron expression (minute hour day month weekday)"
            />
          </div>
        ) : (
          <div className="cron-builder-grid">
            {CRON_FIELDS.map((f) => (
              <label key={f.key} className="cron-field">
                <span className="cron-field-label">{f.label}</span>
                <input
                  className="text-input"
                  value={parts[f.key]}
                  onChange={(e) => setParts({ ...parts, [f.key]: e.target.value })}
                  placeholder={f.def}
                />
                <span className="text-muted cron-field-hint">{f.hint}</span>
              </label>
            ))}
          </div>
        )}

        <div className="inline-form">
          <button className="btn btn-ghost btn-small" onClick={toggleAdvanced}>
            {advanced ? 'Guided fields' : 'Advanced (raw cron)'}
          </button>
          <code className="cron-preview">{cron}</code>
        </div>

        {cronValid !== null && (
          <p className={cronValid ? 'cron-description' : 'banner banner-warning'}>
            {cronValid
              ? `${cronDescription}${timeZone ? ` (server time, ${timeZone})` : ''}`
              : `Invalid cron expression${cronReason ? `: ${cronReason}` : ''}`}
          </p>
        )}

        {editingId !== null && (
          <div className="cron-enabled-toggle">
            <Switch checked={formEnabled} onChange={setFormEnabled}>
              Enabled
            </Switch>
          </div>
        )}

        <div className="inline-form">
          <button className="btn btn-primary" onClick={handleSubmit} disabled={cronValid === false}>
            {editingId === null ? 'Add task' : 'Save changes'}
          </button>
          {editingId !== null && (
            <button
              className="btn btn-ghost"
              onClick={() => {
                resetForm()
                setTab('Tasks')
              }}
            >
              Cancel
            </button>
          )}
        </div>

        {editingId === null && (
          <>
            <PanelNote>Presets:</PanelNote>
            <div className="inline-form scheduler-presets">
              {Object.entries(presets).map(([key, preset]) => (
                <button key={key} className="btn btn-ghost btn-small" onClick={() => applyPreset(key)}>
                  {preset.label}
                </button>
              ))}
            </div>
          </>
        )}
      </div>
      )}

      {tab === 'Tasks' && (
      <div className="card">
        {/* The timer buttons act on these tasks, so they sit on their card, not in the page header. */}
        <div className="card-header">
          <h2>Scheduled tasks</h2>
          <div className="action-bar">
            <button className="btn btn-ghost" disabled={!privilegedOk} onClick={handleInstallTimers}>
              <HiOutlineClock aria-hidden="true" />
              Install timers
            </button>
            <button
              className="btn btn-ghost"
              disabled={!privilegedOk}
              onClick={handleRemoveTimers}
            >
              <HiOutlineTrash aria-hidden="true" />
              Remove timers
            </button>
          </div>
        </div>
        {timeZone && <p className="hint">Times are the server's time ({timeZone}).</p>}
        {/* cards: a phone shows each task as a card (index.css .table-cards),
            the ID and the Edit/Remove buttons on top, so the nine columns
            fit without a sideways scroll. */}
        <DataTable
          cards
          rowKey={(t) => t.id}
          emptyMessage="No scheduled tasks."
          rows={tasks}
          columns={[
            { key: 'id', label: 'ID', className: 'cell-name' },
            { key: 'task_type', label: 'Type', render: (t) => TASK_TYPE_LABELS[t.task_type] || t.task_type },
            {
              key: 'payload',
              label: 'Message / command',
              render: (t) => t.options?.[PAYLOAD_FIELDS[t.task_type]?.key] || '—',
            },
            { key: 'cron', label: 'Cron' },
            { key: 'description', label: 'Schedule' },
            {
              key: 'next_run_display',
              label: 'Next run',
              render: (t) => nextRunText(t, installState[t.id]),
            },
            {
              key: 'install_state',
              label: 'Timer',
              render: (t) => TIMER_STATE_LABELS[installState[t.id]] || '—',
            },
            {
              key: 'enabled',
              label: 'Enabled',
              render: (t) => (
                <button className="btn btn-ghost btn-small" onClick={() => toggleEnabled(t)}>
                  {t.enabled ? 'Disable' : 'Enable'}
                </button>
              ),
            },
            {
              key: 'actions',
              label: '',
              className: 'cell-actions',
              render: (t) => (
                <div className="inline-form">
                  <button className="btn btn-ghost btn-small" onClick={() => startEdit(t)}>
                    Edit
                  </button>
                  <button className="btn btn-danger btn-small" onClick={() => handleRemoveTask(t.id)}>
                    Remove
                  </button>
                </div>
              ),
            },
          ]}
        />
      </div>
      )}

    </div>
  )
}
