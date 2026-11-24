import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  HiArrowLeft,
  HiArrowRight,
  HiOutlineArrowsRightLeft,
  HiOutlineDocumentText,
} from 'react-icons/hi2'
import RestoreIcon from './RestoreIcon.jsx'
import { api } from '../api'
import Select, { pathOption } from './Select.jsx'
import DiffView from './DiffView.jsx'
import Tooltip, { tooltipAttrs } from './Tooltip.jsx'
import { useConfirm } from './useConfirm.jsx'
import ChangeReport from './ChangeReport.jsx'
import { useCan } from '../role.jsx'
import { humanizeKey } from '../fieldLabel.js'
import RefreshButton from './RefreshButton.jsx'
import IconButton from './IconButton.jsx'
import PanelNote from './PanelNote.jsx'
import { triggerLabel } from './FileVersions.jsx'
import { formatWhen } from '../format.js'

// Analysis → Change history: every saved version as a change on one time
// line, so what was done, by whom and when reads at a glance. A version holds
// a file just before a write, so its time and trigger say when the file
// changed and why; "What changed" diffs it with the next version
// (GET /api/versions/change-diff), "Compare with now" with the live file, and
// Restore puts the file back as it was before the change. The last server
// start and the baseline sit on the line as markers, and "Since" cuts the
// line there and shows the whole setup's report (ChangeReport.jsx) above it.
// It replaced the Config changes and Saved versions tabs on 2026-11-03.

// A trigger as the event it records. A preset or tweak trigger carries its
// id after a colon ("preset_apply:custom:night", "tweak_reset:zombie_count").
const CAUSES = {
  editor: 'Editor save',
  map_editor: 'Map editor save',
  auto_save: 'Save',
  manual: 'Manual snapshot',
  pre_restore: 'Version restored',
  restore_backup: 'Backup restored',
  pre_merge: 'Mod XML merge',
  pre_tweak: 'Quick tweak',
  server_update: 'Server update',
  update_conflict: 'Update conflict',
  upstream_adopt: 'DayZ file adopted',
  bundle_import: 'Bundle import',
  preset_apply: 'Preset applied',
  tweak_apply: 'Quick tweak applied',
  tweak_reset: 'Quick tweak reset',
  outside_edit: 'Changed outside Zedstead',
}

// The dot's colour says what kind of cause a change had: six groups, so the
// colours stay apart (index.css --cause-*). The cause's name sits beside the
// dot as well, so the colour is never the only sign.
const CAUSE_GROUPS = [
  { key: 'editor', label: 'Saved in Zedstead', causes: ['editor', 'map_editor', 'auto_save', 'manual'] },
  { key: 'outside', label: 'Changed outside Zedstead', causes: ['outside_edit'] },
  { key: 'preset', label: 'Preset or quick tweak', causes: ['preset_apply', 'tweak_apply', 'tweak_reset', 'pre_tweak'] },
  { key: 'restore', label: 'Restore', causes: ['pre_restore', 'restore_backup'] },
  { key: 'merge', label: 'Merge or server update', causes: ['pre_merge', 'server_update', 'update_conflict', 'upstream_adopt'] },
  { key: 'import', label: 'Bundle import', causes: ['bundle_import'] },
]
const groupOf = (base) => CAUSE_GROUPS.find((g) => g.causes.includes(base))?.key

const MARKERS = {
  server_start: { label: 'Server started', tip: '"Since: the last server start" shows the changes after this moment.' },
  baseline: { label: 'Baseline set', tip: '"Since: the baseline" shows the changes after this moment.' },
}

const SINCE = [
  { value: '', label: 'All time' },
  // The description shows in the open list, so the picker explains itself
  // without a tooltip.
  { value: 'start', label: 'Since the last server start', description: 'Also shows what changed in the whole setup since then, the mod list included' },
  { value: 'baseline', label: 'Since the baseline', description: 'Also shows what changed in the whole setup since then, the mod list included' },
]
const SINCE_MARKER = { start: 'server_start', baseline: 'baseline' }

const WEEKS = 4
const PAGE = 100
// Changes per request (GET /api/versions/timeline caps it at 2000).
const PAGE_SIZE = 2000

// Who made a change (config_versions VersionEntry.actor). A version saved
// before Zedstead recorded it has none.
const UNKNOWN = '(not recorded)'
const whoOf = (e) => e.actor || UNKNOWN

function causeOf(trigger) {
  const [base, ...rest] = String(trigger || '').split(':')
  return { base, label: CAUSES[base] || humanizeKey(base), detail: rest.length ? humanizeKey(rest[rest.length - 1]) : '' }
}

const dayKey = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
const dayTitle = (d) => d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' })
const timeOf = (d) => d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })

// Only the weeks that hold a change, WEEKS at a time, newest page first and
// Older/Newer to step back, so a change half a year old is a few clicks away
// and empty weeks never fill the view. Monday first: one group per week with
// its dates above it, a cell per day with its date in it, the cell's shade by
// the number of changes. The tip says what changed that day; a tap picks the
// day and a second tap clears it. The weeks wrap two by two on a phone.
const mondayOf = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate() - ((d.getDay() + 6) % 7))

function weekTitle(monday) {
  const sunday = new Date(monday.getFullYear(), monday.getMonth(), monday.getDate() + 6)
  const year = sunday.getFullYear() === new Date().getFullYear() ? {} : { year: 'numeric' }
  const short = (d, opts = {}) => d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', ...opts })
  return `${short(monday)} – ${short(sunday, year)}`
}

function dayTip(date, info) {
  if (!info) return `${dayTitle(date)}\nNo changes`
  const files = Object.entries(info.files)
    .sort((a, b) => b[1] - a[1])
    .map(([f, n]) => (n > 1 ? `${f} (${n})` : f))
  return [
    dayTitle(date),
    `${info.n} change${info.n === 1 ? '' : 's'}: ${files.join(', ')}`,
    `Causes: ${[...info.causes].join(', ')}`,
    `By: ${[...info.who].join(', ')}`,
  ].join('\n')
}

function ActivityCalendar({ byDay, day, onDay, page, onPage }) {
  // Picks today and steps back to the newest weeks, so the line shows only
  // today's changes.
  // All (shows every day again, while a day is picked), then Today.
  const todayButton = (
    <>
    {day && (
      <button type="button" className="btn btn-ghost btn-small" onClick={() => onDay('')} {...tooltipAttrs('Show every day')}>
        All
      </button>
    )}
    <button
      type="button"
      className="btn btn-primary btn-small"
      aria-pressed={day === dayKey(new Date())}
      onClick={() => {
        onDay(dayKey(new Date()))
        onPage(null)
      }}
    >
      Today
    </button>
    </>
  )
  const today = new Date()
  const top = Math.max(1, ...Object.values(byDay).map((i) => i.n))
  // Every week with a change, newest first.
  const active = [...new Set(Object.keys(byDay).map((k) => dayKey(mondayOf(new Date(`${k}T00:00`)))))]
    .sort()
    .reverse()
  if (!active.length)
    return (
      <div className="timeline-cal-nav">
        {todayButton}
        <span className="timeline-cal-range">No changes to show in the calendar.</span>
      </div>
    )
  const pages = Math.ceil(active.length / WEEKS)
  // No page stepped to yet: the page that holds the picked day (a reload
  // with ?day= opens on it), else the newest.
  const dayPage = day ? Math.floor(Math.max(0, active.indexOf(dayKey(mondayOf(new Date(`${day}T00:00`))))) / WEEKS) : 0
  const at = Math.min(page ?? dayPage, pages - 1)
  const shownWeeks = active.slice(at * WEEKS, at * WEEKS + WEEKS).reverse()
  const weeks = []
  for (const key of shownWeeks) {
    const monday = new Date(`${key}T00:00`)
    const days = []
    for (let i = 0; i < 7; i++) {
      const date = new Date(monday.getFullYear(), monday.getMonth(), monday.getDate() + i)
      days.push({ key: dayKey(date), date, info: byDay[dayKey(date)], future: date > today })
    }
    weeks.push({ monday, days })
  }
  // Four shades of one hue, by the share of the busiest day.
  const level = (n) => (n ? Math.min(4, Math.ceil((n / top) * 4)) : 0)
  const first = at * WEEKS + 1
  const last = Math.min(active.length, first + WEEKS - 1)
  return (
    <>
    <div className="timeline-cal-nav">
      {todayButton}
      <div className="timeline-cal-steps">
        <IconButton icon={HiArrowLeft} label="Older weeks" disabled={at >= pages - 1} onClick={() => onPage(at + 1)} />
        <IconButton icon={HiArrowRight} label="Newer weeks" disabled={at === 0} onClick={() => onPage(at - 1)} />
      </div>
      <span className="timeline-cal-range">
        {active.length === 1 ? '1 week with changes' : `Weeks ${first}–${last} of ${active.length} with changes, newest last`}
      </span>
    </div>
    <div className="timeline-calendar" role="group" aria-label="Changes per day, weeks with changes only">
      {weeks.map((w) => (
        <div key={dayKey(w.monday)} className="timeline-week">
          <div className="timeline-week-title">{weekTitle(w.monday)}</div>
          <div className="timeline-week-days">
            {w.days.map((d) => (
              <button
                key={d.key}
                type="button"
                className={`timeline-cal-day timeline-cal-level-${level(d.info?.n)}${day === d.key ? ' timeline-cal-active' : ''}`}
                disabled={d.future}
                aria-pressed={day === d.key}
                aria-label={dayTip(d.date, d.info).replaceAll('\n', '. ')}
                onClick={() => onDay(day === d.key ? '' : d.key)}
                {...tooltipAttrs(d.future ? undefined : dayTip(d.date, d.info))}
              >
                <span className="timeline-cal-weekday">
                  {d.date.toLocaleDateString(undefined, { weekday: 'narrow' })}
                </span>
                <span className="timeline-cal-date">{d.date.getDate()}</span>
              </button>
            ))}
          </div>
        </div>
      ))}
    </div>
    </>
  )
}

// The version that holds the file as it was before this change: a folded
// copy lives on in the newer version that holds the same content.
const heldIn = (e) => e.repeat_of ?? e.version
const NO_VERSION =
  'Zedstead keeps no version of this edit yet, so there is nothing to compare or restore. It keeps one at its next save of this file.'

// The version picker in the Restore dialog: every stored version of this
// file (same path), newest first, except those that hold the current
// content, with the version before this change picked first. The pick goes
// into `choice.version`, which restore() reads once the dialog closes.
function RestorePick({ e, choice }) {
  const first = choice.version
  const [entries, setEntries] = useState(null)
  const [value, setValue] = useState(String(first))

  useEffect(() => {
    let live = true
    api
      .get(`/api/versions?${new URLSearchParams({ file: e.file_name })}`)
      .then((r) => live && setEntries((r.entries || []).filter((v) => v.source_path === e.source_path)))
      .catch(() => live && setEntries([]))
    return () => {
      live = false
    }
  }, [e.file_name, e.source_path])

  const options = useMemo(() => {
    const kept = (entries || []).filter((v) => v.version === first || !v.identical)
    // A list that failed or lacks this change's version still offers that one.
    if (!kept.some((v) => v.version === first)) kept.unshift({ version: first })
    return kept.map((v) => ({
      value: String(v.version),
      label: v.timestamp ? `v${v.version} · ${formatWhen(v.timestamp)}` : `v${v.version}`,
      description:
        v.version === first
          ? 'The file as it was before this change'
          : [triggerLabel(v.trigger), v.actor && `by ${v.actor}`].filter(Boolean).join(' '),
    }))
  }, [entries, first])

  return (
    <label className="modal-field">
      Version to put back
      <Select
        className="text-input"
        options={options}
        value={value}
        disabled={entries == null}
        placeholder="Reading versions…"
        onChange={(next) => {
          setValue(next)
          choice.version = Number(next)
        }}
      />
    </label>
  )
}

function EventRow({ e, onRestored }) {
  const confirm = useConfirm()
  // Restore is an admin write; both diffs are reads (passwords are hidden below admin).
  const isAdmin = useCan('admin')
  // Per view ('change' or 'now'): null until read, '' when nothing visible changed.
  const [diffs, setDiffs] = useState({})
  const [open, setOpen] = useState(null)
  const cause = causeOf(e.trigger)

  async function toggle(view) {
    if (open === view) return setOpen(null)
    setOpen(view)
    if (diffs[view] != null) return
    try {
      const q = new URLSearchParams({ file: e.file_name, source_path: e.source_path })
      let url
      if (view === 'now') {
        q.set('version', heldIn(e))
        url = `/api/versions/diff?${q}`
      } else {
        // A pending outside change has no version: the route diffs Zedstead's last write against the live file.
        if (e.version != null) q.set('version', e.version)
        url = `/api/versions/change-diff?${q}`
      }
      const text = (await api.get(url)).diff || ''
      setDiffs((d) => ({ ...d, [view]: text }))
    } catch (err) {
      setDiffs((d) => ({ ...d, [view]: `Could not read the change: ${err.message || err}` }))
    }
  }

  async function restore() {
    // RestorePick writes the picked version here; it starts at this change's.
    const choice = { version: heldIn(e) }
    const ok = await confirm({
      title: `Put ${e.file_name} back as it was before this change?`,
      message:
        'This replaces the whole file with the version picked below, so every later change to this file goes ' +
        'too, by anyone. The first one is the file as it was before this change, and "Compare with now" shows ' +
        'what goes. The current file is kept as a new version first, so the restore shows here and can be undone too.',
      body: <RestorePick e={e} choice={choice} />,
      confirmLabel: 'Restore',
      danger: true,
    })
    if (!ok) return
    const { version } = choice
    try {
      const result = await api.post('/api/versions/restore', { file: e.file_name, version, source_path: e.source_path })
      onRestored(
        result.restored ? `Restored ${e.file_name} to version ${version}.` : `Version ${version} is no longer stored. Nothing was restored.`,
        result.restored ? 'info' : 'error'
      )
    } catch (err) {
      onRestored(err.message || 'Restore failed', 'error')
    }
  }

  const diff = open ? diffs[open] : null

  return (
    <li className="timeline-item">
      <span className="timeline-time">{timeOf(e.date)}</span>
      <span className={`timeline-dot cause-${groupOf(cause.base) || 'other'}`} aria-hidden="true" />
      <div className="timeline-body">
        <div className="timeline-head">
          <strong className="timeline-file" {...tooltipAttrs(e.source_path)}>
            {e.file_name}
          </strong>
          <span className="timeline-cause">
            {cause.label}
            {cause.detail && `: ${cause.detail}`}
          </span>
          {e.actor && <span className="timeline-who">by {e.actor}</span>}
          <div className="timeline-actions">
          <button type="button" className="btn btn-ghost btn-small" onClick={() => toggle('change')} aria-expanded={open === 'change'}>
            <HiOutlineDocumentText aria-hidden="true" />
            {open === 'change' ? 'Hide change' : 'What changed'}
          </button>
          {/* A pending outside edit has no version yet: its two buttons stay, disabled, so every row's buttons line up. */}
          <Tooltip className="bare" text={e.version == null ? NO_VERSION : ''}>
            <button
              type="button"
              className="btn btn-ghost btn-small"
              onClick={() => toggle('now')}
              aria-expanded={open === 'now'}
              disabled={e.version == null}
              {...(e.version != null && tooltipAttrs('The file as it was before this change, against the file now.'))}
            >
              <HiOutlineArrowsRightLeft aria-hidden="true" />
              {open === 'now' ? 'Hide comparison' : 'Compare with now'}
            </button>
          </Tooltip>
          {isAdmin && (
            <Tooltip className="bare" text={e.version == null ? NO_VERSION : ''}>
              <button
                type="button"
                className="btn btn-ghost btn-small"
                onClick={restore}
                disabled={e.version == null}
                {...(e.version != null &&
                  tooltipAttrs(
                    'Put the whole file back as it was before this change: later changes to this file go too. ' +
                      'The current file is kept as a version first.'
                  ))}
              >
                <RestoreIcon />
                Restore
              </button>
            </Tooltip>
          )}
          </div>
        </div>
        {e.note && <div className="timeline-note">{e.note}</div>}
        {e.trigger === 'outside_edit' && (
          <div
            className="timeline-note"
            {...tooltipAttrs(
              'Zedstead did not write this change: a text editor, another program or a game update did. ' +
                'The time is when the file was last modified. ' +
                (e.pending
                  ? 'No version holds it yet: Zedstead keeps one at its next save of this file.'
                  : 'Zedstead noticed it at its next save of this file and kept a version of it.')
            )}
          >
            {e.pending ? 'Not kept as a version yet' : 'Noticed at the next Zedstead save'}
          </div>
        )}
        {e.repeat_of != null && (
          <div
            className="timeline-note"
            {...tooltipAttrs(
              'The file later held this exact content again, so its copy was removed automatically and the ' +
                `newer v${e.repeat_of} keeps it. The change itself still shows here, and its diff still works.`
            )}
          >
            Copy folded into v{e.repeat_of}: identical content
          </div>
        )}
        {open &&
          (diff == null ? (
            <PanelNote>Reading…</PanelNote>
          ) : diff ? (
            <DiffView text={diff} />
          ) : (
            <PanelNote>
              {open === 'now'
                ? 'The file now matches the file before this change: only comments, spacing or the order of lines differ, if anything.'
                : 'No visible change: only comments, spacing or the order of lines differ.'}
            </PanelNote>
          ))}
      </div>
    </li>
  )
}

// The admin's tools for the store itself: a version of a file by hand (after
// an edit outside Zedstead, or before a risky change) and the clean-up. They were
// on the Saved versions tab.
function VersionTools({ pushToast, onChanged }) {
  const confirm = useConfirm()
  const [candidates, setCandidates] = useState([])
  const [file, setFile] = useState('')

  useEffect(() => {
    api
      .get('/api/versions/candidates')
      .then((data) => setCandidates(data.files || []))
      .catch(() => {})
  }, [])

  async function snapshot(ev) {
    ev.preventDefault()
    try {
      const data = await api.post('/api/versions/snapshot', { file })
      pushToast(data.created ? `Created v${data.version} of ${data.file_name}.` : 'File unchanged: no snapshot needed.', 'info')
      onChanged()
    } catch (err) {
      pushToast(err.message || 'Snapshot failed', 'error')
    }
  }

  async function cleanup() {
    const ok = await confirm({
      title: 'Delete old versions?',
      message:
        'This deletes every stored version except the newest 5 of each file, and every older copy ' +
        'of content a newer version also holds. Deleted versions cannot be restored.',
      confirmLabel: 'Delete',
      danger: true,
    })
    if (!ok) return
    try {
      const data = await api.post('/api/versions/cleanup', { keep: 5 })
      pushToast(`Removed ${data.removed} old version(s), keeping ${data.keep} per file.`, 'info')
      onChanged()
    } catch (err) {
      pushToast(err.message || 'Cleanup failed', 'error')
    }
  }

  return (
    <form className="timeline-tools" onSubmit={snapshot}>
      <Select
        value={file}
        onChange={setFile}
        placeholder="Snapshot a file…"
        options={candidates.map((f) => pathOption(f))}
        aria-label="File to snapshot"
        help="Pick a config file to keep a version of now, then press Create snapshot."
      />
      <Tooltip
        className="bare"
        text={
          file
            ? `Keep a version of ${file.split(/[\\/]/).pop()} as it is now, for example after an edit outside Zedstead or before a risky change. Nothing is kept when the file matches its newest version.`
            : 'Pick a file to the left first.'
        }
      >
        <button className="btn btn-primary btn-small" type="submit" disabled={!file}>
          Create snapshot
        </button>
      </Tooltip>
      <button
        className="btn btn-ghost btn-small"
        type="button"
        onClick={cleanup}
        {...tooltipAttrs(
          'Delete every saved version except the newest 5 of each file. Asks first. Deleted versions cannot be restored, and their changes leave this list.'
        )}
      >
        Clean up (keep 5 per file)
      </button>
    </form>
  )
}

export default function ChangeTimeline({ pushToast }) {
  const isAdmin = useCan('admin')
  const [data, setData] = useState(null)
  const [file, setFile] = useState('')
  const [cause, setCause] = useState('')
  const [who, setWho] = useState('')
  // The picked day lives in ?day=, so a reload or a shared link keeps it.
  const [params, setParams] = useSearchParams()
  const day = /^\d{4}-\d{2}-\d{2}$/.test(params.get('day') || '') ? params.get('day') : ''
  const setDay = useCallback(
    (value) =>
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev)
          if (value) next.set('day', value)
          else next.delete('day')
          return next
        },
        { replace: true }
      ),
    [setParams]
  )
  // ?since= cuts the line at the last server start or the baseline.
  const since = SINCE_MARKER[params.get('since')] ? params.get('since') : ''
  const setSince = useCallback(
    (value) =>
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev)
          if (value) next.set('since', value)
          else next.delete('since')
          return next
        },
        { replace: true }
      ),
    [setParams]
  )
  // null until Older/Newer is used: then the calendar opens on the day's page.
  const [calPage, setCalPage] = useState(null)
  const [shown, setShown] = useState(PAGE)

  // The newest PAGE_SIZE changes first; "Load older changes" adds the next
  // page, and a picked file loads its whole line on its own, so no saved
  // version is out of reach behind the page size.
  const [fileData, setFileData] = useState(null)
  const [loadingOlder, setLoadingOlder] = useState(false)
  const [reloads, setReloads] = useState(0)
  const load = useCallback(() => {
    setReloads((n) => n + 1)
    return api
      .get(`/api/versions/timeline?limit=${PAGE_SIZE}`)
      .then((next) => {
        setData(next)
        return true
      })
      .catch((err) => {
        pushToast(err.message || 'Failed to load the timeline', 'error')
        return false
      })
  }, [pushToast])

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    setFileData(null)
    if (!file) return
    const q = new URLSearchParams({ source_path: file, limit: PAGE_SIZE })
    api
      .get(`/api/versions/timeline?${q}`)
      .then(setFileData)
      .catch((err) => pushToast(err.message || 'Failed to load the file history', 'error'))
  }, [file, reloads, pushToast])

  async function loadOlder() {
    setLoadingOlder(true)
    try {
      const page = await api.get(`/api/versions/timeline?limit=${PAGE_SIZE}&offset=${data.events.length}`)
      setData((d) => ({ ...d, events: [...d.events, ...page.events], more: page.more }))
    } catch (err) {
      pushToast(err.message || 'Failed to load older changes', 'error')
    } finally {
      setLoadingOlder(false)
    }
  }

  const toEvent = (e) => ({ ...e, date: new Date(e.timestamp), cause: causeOf(e.trigger).base })
  const allEvents = useMemo(() => (data?.events || []).map(toEvent), [data])
  const events = useMemo(() => (file ? (fileData?.events || []).map(toEvent) : allEvents), [file, fileData, allEvents])
  const fileOptions = useMemo(() => {
    const paths = [...new Set([...allEvents.map((e) => e.source_path), ...(file ? [file] : [])])]
    // The file name is the label. Files that share a name get the fewest
    // parent folders that tell them apart under it, never the whole path.
    const parts = paths.map((p) => p.split(/[\\/]/).filter(Boolean))
    const where = (i) => {
      const twins = parts.filter((q) => q[q.length - 1] === parts[i][parts[i].length - 1])
      if (twins.length < 2) return undefined
      const dirs = parts[i].slice(0, -1)
      for (let k = 1; k <= dirs.length; k++) {
        const tail = dirs.slice(-k).join('/')
        if (twins.filter((q) => q.slice(0, -1).slice(-k).join('/') === tail).length === 1)
          // The folder that tells it apart, and the file's own folder: "in …/dayz-server/…/db".
          return k > 2 ? `in …/${dirs[dirs.length - k]}/…/${dirs[dirs.length - 1]}` : `in …/${tail}`
      }
      return `in ${dirs.join('/')}`
    }
    return [
      { value: '', label: 'Every file' },
      ...paths
        .map((p, i) => ({ value: p, label: parts[i][parts[i].length - 1], description: where(i) }))
        .sort((a, b) => a.label.localeCompare(b.label) || (a.description || '').localeCompare(b.description || '')),
    ]
  }, [allEvents, file])
  const causeOptions = useMemo(() => {
    const bases = [...new Set(events.map((e) => e.cause))]
    return [{ value: '', label: 'Every cause' }, ...bases.map((b) => ({ value: b, label: causeOf(b).label }))]
  }, [events])

  const whoOptions = useMemo(() => {
    const names = [...new Set(events.map(whoOf))].sort((a, b) => a.localeCompare(b))
    return [{ value: '', label: 'Everyone' }, ...names.map((n) => ({ value: n, label: n }))]
  }, [events])

  const sinceMarker = since && (data?.markers || []).find((m) => m.kind === SINCE_MARKER[since])
  const sinceTime = sinceMarker ? new Date(sinceMarker.timestamp) : null
  const filtered = events.filter(
    (e) =>
      (!file || e.source_path === file) &&
      (!cause || e.cause === cause) &&
      (!who || whoOf(e) === who) &&
      (!sinceTime || e.date >= sinceTime)
  )
  // Per day: the number of changes, per file, and their causes (the
  // calendar's shades and tips).
  const byDay = {}
  for (const e of filtered) {
    const info = (byDay[dayKey(e.date)] ||= { n: 0, files: {}, causes: new Set(), who: new Set() })
    info.who.add(whoOf(e))
    info.n += 1
    info.files[e.file_name] = (info.files[e.file_name] || 0) + 1
    info.causes.add(causeOf(e.trigger).label)
  }

  // Changes and markers on one line, newest first, grouped by day.
  const markers =
    file || cause || who
      ? []
      : (data?.markers || [])
          .map((m) => ({ ...m, date: new Date(m.timestamp), marker: true }))
          .filter((m) => !sinceTime || m.date >= sinceTime)
  const rows = [...filtered, ...markers]
    .filter((r) => !day || dayKey(r.date) === day)
    .sort((a, b) => b.date - a.date)
  const visible = rows.slice(0, shown)
  const groups = []
  for (const r of visible) {
    const key = dayKey(r.date)
    if (!groups.length || groups[groups.length - 1].key !== key) groups.push({ key, date: r.date, rows: [] })
    groups[groups.length - 1].rows.push(r)
  }

  const restored = useCallback(
    (message, kind) => {
      pushToast(message, kind)
      load()
    },
    [pushToast, load]
  )

  if (!data) return <PanelNote>Loading…</PanelNote>

  return (
    <div className="card">
      <div className="page-header">
        <h2>Change history</h2>
        <div className="timeline-tools">
          {isAdmin && <VersionTools pushToast={pushToast} onChanged={load} />}
          <RefreshButton
            what="Change history"
            onRefresh={load}
            {...tooltipAttrs('Read the list again, to show changes made since the page opened.')}
          />
        </div>
      </div>
      <PanelNote>
        Every change to a configuration file, newest first, edits made outside Zedstead included. Open a change to see
        what it did, compare it with the file now, or put the file back as it was before it. Each config editor also
        has a Versions tab with the history of its own file. A file picked in the filters below shows its whole
        history, however old.
      </PanelNote>
      <div className="inline-form timeline-filters">
        <Select
          value={since}
          onChange={setSince}
          options={SINCE}
          aria-label="Since"
        />
        <Select
          value={file}
          onChange={setFile}
          options={fileOptions}
          aria-label="File"
        />
        <Select
          value={cause}
          onChange={setCause}
          options={causeOptions}
          aria-label="Cause"
        />
        <Select
          value={who}
          onChange={setWho}
          options={whoOptions}
          aria-label="Who"
        />
      </div>
      <ul className="timeline-legend" aria-label="Dot colours">
        {CAUSE_GROUPS.filter((g) => events.some((e) => g.causes.includes(e.cause))).map((g) => (
          <li key={g.key}>
            <span className={`timeline-dot cause-${g.key}`} aria-hidden="true" />
            {g.label}
          </li>
        ))}
      </ul>
      <ActivityCalendar byDay={byDay} day={day} onDay={setDay} page={calPage} onPage={setCalPage} />
      {day && (
        <div className="timeline-day-picked" role="status">
          <span>
            Showing {dayTitle(new Date(`${day}T00:00`))}: {byDay[day]?.n || 0} change
            {byDay[day]?.n === 1 ? '' : 's'}
          </span>
        </div>
      )}
      {since && <ChangeReport key={since} since={since} pushToast={pushToast} onBaseline={load} />}
      {since && !sinceTime && (
        <PanelNote>
          {since === 'baseline' ? 'No baseline is set, so the list shows every change.' : 'No server start is recorded yet, so the list shows every change.'}
        </PanelNote>
      )}
      {rows.length === 0 ? (
        <PanelNote>No changes match.</PanelNote>
      ) : (
        groups.map((g) => (
          <section key={g.key} className="timeline-day">
            <h3 className="timeline-day-title">{dayTitle(g.date)}</h3>
            <ul className="timeline-list">
              {g.rows.map((r) =>
                r.marker ? (
                  <li key={`m-${r.kind}`} className="timeline-item timeline-marker" {...tooltipAttrs(MARKERS[r.kind]?.tip)}>
                    <span className="timeline-time">{timeOf(r.date)}</span>
                    <span className="timeline-dot" aria-hidden="true" />
                    <div className="timeline-body">
                      <strong>{MARKERS[r.kind]?.label || r.kind}</strong>
                    </div>
                  </li>
                ) : (
                  <EventRow key={`${r.source_path}:${r.version ?? 'pending'}`} e={r} onRestored={restored} />
                )
              )}
            </ul>
          </section>
        ))
      )}
      {rows.length > shown && (
        <button type="button" className="btn btn-ghost btn-small" onClick={() => setShown((n) => n + PAGE)}>
          Show {Math.min(PAGE, rows.length - shown)} more of {rows.length - shown}
        </button>
      )}
      {rows.length <= shown && !file && data.more && (
        <button type="button" className="btn btn-ghost btn-small" onClick={loadOlder} disabled={loadingOlder}>
          {loadingOlder ? 'Loading…' : 'Load older changes'}
        </button>
      )}
    </div>
  )
}
