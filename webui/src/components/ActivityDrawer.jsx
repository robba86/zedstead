// The Activity drawer: running and finished jobs, notifications and the
// Key lines pane, each a pane that folds.

import CountBadge from './CountBadge.jsx'
import ListRow, { SeverityDot } from './ListRow.jsx'
import { BetaPill, VERSION_BETA_TIP } from './BetaBadge.jsx'
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import {
  HiOutlineArchiveBox,
  HiOutlineArrowDownTray,
  HiOutlineArrowPath,
  HiOutlineArrowUpTray,
  HiOutlineArrowUturnLeft,
  HiOutlineBeaker,
  HiOutlineBell,
  HiOutlineClock,
  HiOutlineCloudArrowUp,
  HiOutlineCodeBracketSquare,
  HiOutlineCog6Tooth,
  HiOutlineDocumentText,
  HiOutlineKey,
  HiOutlineListBullet,
  HiOutlineMap,
  HiOutlinePuzzlePiece,
  HiOutlineServerStack,
  HiOutlineTrash,
  HiOutlineWrench,
  HiOutlineXMark,
  HiOutlineRocketLaunch,
} from 'react-icons/hi2'
// Heroicons has no pulse-line glyph; Lucide's is the conventional "activity".
import { LuActivity } from 'react-icons/lu'
import { api } from '../api'
import { useWs } from '../ws'
import { useServerStatus } from '../serverStatus'
import { useJobs, RUNNING_STATUSES, isCancelPending } from '../jobs'
import { useCan, useRole } from '../role.jsx'
import { clock24, localRptLine, setServerOffset } from '../serverClock.js'
import LogPane, {
  JOB_HIGHLIGHTS,
  ProgressBar,
  RPT_HIGHLIGHTS,
  lastProgress,
} from './LogPane.jsx'
import { LogDownloadLink } from './LogFindings.jsx'
import Tooltip, { tooltipAttrs } from './Tooltip.jsx'
import { useToastHistory } from './Toast.jsx'
import { useKeyLines } from '../keyLines.js'
import { Link } from 'react-router-dom'
import { useConfirm } from './useConfirm.jsx'
import { useHeightAnimation } from '../useHeightAnimation.js'
import { MOBILE_QUERY, useMediaQuery } from '../useMediaQuery.js'
import PanelNote from './PanelNote.jsx'
import IconButton from './IconButton.jsx'


// Persistent bottom-docked drawer. Mounted once in Layout so its buffers +
// scroll survive route changes. Stacks the live Server Log (RPT tail), the
// Notifications backlog (every toast) and every process (from the global jobs
// store) as collapsible accordions. Everything but the Server Log is cached in
// localStorage for a day, so a reload — or a backend restart — reopens on the
// same activity rather than an empty drawer (see ../persist.js).

const MAX_LINES = 1000

// Drag-to-resize of the whole drawer. Height is the *drawer* height (handle +
// body), persisted across reloads. null = auto (content height, capped by the
// CSS max-height) which is the pre-drag default.
const HEIGHT_KEY = 'zsm.activity.height' // localStorage: px string
const MIN_DRAWER_HEIGHT = 120
// How far a finger moves on the bar before a tap becomes a resize.
const DRAG_SLOP_PX = 6
// Leave the page a usable strip above the drawer.
const maxDrawerHeight = () => Math.max(MIN_DRAWER_HEIGHT, window.innerHeight - 120)

function clampHeight(px) {
  return Math.min(Math.max(px, MIN_DRAWER_HEIGHT), maxDrawerHeight())
}

// A drag may pull the drawer below MIN_DRAWER_HEIGHT, down to the bar alone.
// A release down there closes the drawer and keeps the last usable height.
const CLOSE_DRAG_FLOOR = 40

function dragHeight(px) {
  return Math.min(Math.max(px, CLOSE_DRAG_FLOOR), maxDrawerHeight())
}

function loadHeight() {
  try {
    const raw = parseInt(localStorage.getItem(HEIGHT_KEY) || '', 10)
    return Number.isFinite(raw) ? clampHeight(raw) : null
  } catch {
    return null
  }
}

// The Server Log status dot reflects the actual server, not the socket:
//   ws down          -> red "Disconnected" (we can't know the server state)
//   server running   -> green "Live"
//   server stopped   -> grey "Stopped"
function ServerLogDot({ connected, running }) {
  let cls = 'stopped'
  let label = 'Server stopped'
  if (!connected) {
    cls = 'disconnected'
    label = 'Disconnected'
  } else if (running) {
    cls = 'live'
    label = 'Live'
  }
  return <span className={`server-log-dot ${cls}`} {...tooltipAttrs(label)} />
}

// A job's icon says what kind of work it is. Keyed by the backend's job kind
// (the second argument of every `state.jobs.start(...)` in app/web/routers):
// an exact kind first, then its first word, so `backup_create` and
// `backup_restore` share `backup` while `backup_push` gets its own.
const JOB_ICONS = {
  update: HiOutlineArrowPath,
  quick: HiOutlineRocketLaunch,
  update_rollback: HiOutlineArrowUturnLeft,
  mods: HiOutlinePuzzlePiece,
  mods_xml_merge: HiOutlineCodeBracketSquare,
  expansion_templates: HiOutlineArrowDownTray,
  expansion_mission: HiOutlineDocumentText,
  backup: HiOutlineArchiveBox,
  backup_push: HiOutlineCloudArrowUp,
  bundle_export: HiOutlineArrowUpTray,
  bundle_import: HiOutlineArrowDownTray,
  timers: HiOutlineClock,
  map: HiOutlineMap,
  steam: HiOutlineKey,
  cleanup: HiOutlineTrash,
  reinstall: HiOutlineWrench,
  spawn: HiOutlineBeaker,
}

function jobIcon(kind) {
  if (!kind) return HiOutlineCog6Tooth
  return JOB_ICONS[kind] || JOB_ICONS[kind.split('_')[0]] || HiOutlineCog6Tooth
}

// Running/pending accordions float to the top; finished ones sit below,
// newest first.
function jobRank(job) {
  return RUNNING_STATUSES.has(job.status) ? 0 : 1
}

// A log line wants a clock, not a date — but the backlog is cached for a day
// now (persist.js), so a row from before today has to say which day it was or
// "14:20" reads as twenty past two this afternoon.
// One 24-hour clock in the browser's zone for every pane (serverClock.js).
function clockTime(ms) {
  const at = new Date(ms)
  const time = clock24(at)
  if (at.toDateString() === new Date().toDateString()) return time
  return `${at.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })} ${time}`
}

// One line of the Notifications pane. A toast that carried an onClick (a job
// deep-link, a fix page) keeps it here, so the row is a real <button> — the
// same reason the nav scrim is one: it has to be reachable by keyboard.
// `onClick` is passed in rather than read off the entry: a row restored from
// localStorage has none (a closure doesn't serialize) and gets one rebuilt from
// its `jobId` — but only while that job is still on show.
// Which instance a job or a notification is about. The drawer and its
// backlog follow the user from one instance's page to the next, so with more
// than one instance every row names its server. Nothing for a row without an
// instance, or on a panel with only one.
function InstanceBadge({ id, instances, current }) {
  if (!id || !instances || instances.length < 2) return null
  const label = instances.find((row) => row.id === id)?.label || id
  const tip = id === current ? `Instance ${label} (this page)` : `Instance ${label}`
  return (
    <span className="status-pill status-pill-neutral status-pill-plain instance-badge" {...tooltipAttrs(tip)}>
      {label}
    </span>
  )
}

// One line of the Activity log or the Key lines pane: its level as a dot, its
// time, its badges and its message. With `onClick` the row opens what it is
// about (a job's accordion, the Doctor page), and a › says so.
function ToastRow({ entry, onClick, badge }) {
  return (
    <ListRow
      wrap
      compact
      dimmed={entry.level === 'stock'}
      lead={
        <>
          <SeverityDot severity={entry.level === 'success' ? 'ok' : entry.level} />
          <span className="toast-log-time">{clockTime(entry.at)}</span>
        </>
      }
      name={
        <>
          {badge}
          {badge && ' '}
          {entry.message}
        </>
      }
      actions={onClick ? <span className="toast-cta" aria-hidden="true">›</span> : null}
      onClick={onClick || undefined}
    />
  )
}

// `version` is the running Zedstead version, shown at the bar's right end with
// its Beta pill while it is 0.x.
export default function ActivityDrawer({ version }) {
  const {
    jobs,
    logs,
    cancelling,
    expanded,
    drawerOpen,
    toggleDrawer,
    toggleExpanded,
    cancelJob,
    killJob,
    dismissJob,
    focusJob,
    focusJobId,
    clearFocus,
  } = useJobs()
  const canAdmin = useCan('admin')
  const confirm = useConfirm()
  // Jobs are the panel's, one at a time across every instance (plans/62 D3),
  // so the drawer lists them all and badges each with the server it is about.
  const { instance, instances } = useRole()
  const badge = (id) => <InstanceBadge id={id} instances={instances} current={instance?.id} />

  const confirmKill = useCallback(
    async (job) => {
      const ok = await confirm({
        title: `Kill "${job.name || 'process'}"?`,
        message:
          'Cancel is still waiting for the current step to finish. Kill stops the job now: ' +
          'SteamCMD and any other process it started are force-killed, and the step is left ' +
          'part-way. A file copy or XML merge that is already running finishes in the ' +
          'background. Re-run the job afterwards to complete it.',
        confirmLabel: 'Kill process',
        danger: true,
      })
      if (ok) killJob(job.id)
    },
    [confirm, killJob]
  )
  const { status } = useServerStatus()
  const { entries: toastLog, clear: clearToastLog, setDefaultInstance } = useToastHistory()
  // A toast a page pushes is about the page's instance.
  useEffect(() => setDefaultInstance(instance?.id), [instance?.id, setDefaultInstance])

  const [rptLines, setRptLines] = useState([])
  // Collapsed until the server starts or stops: an install job opens the
  // drawer, and an empty Server Log above it pushed the job's own log down and
  // the page out of sight (role 1 user test, 2026 Q4). A change of the
  // running state (start, stop, restart) opens it; the first status reading
  // does not, so a page load never opens it by itself.
  const [serverLogExpanded, setServerLogExpanded] = useState(false)
  const lastRunning = useRef(undefined)
  const runningNow = status?.running
  useEffect(() => {
    if (runningNow === undefined) return
    if (lastRunning.current !== undefined && lastRunning.current !== runningNow) {
      setServerLogExpanded(true)
    }
    lastRunning.current = runningNow
  }, [runningNow])
  // Collapsed by default: the backlog is a place to look things up, and the
  // count in its header says whether there is anything to look up.
  const [toastLogExpanded, setToastLogExpanded] = useState(false)
  const [keyLinesExpanded, setKeyLinesExpanded] = useState(false)
  const keyLines = useKeyLines(logs, jobs)
  const { addRpt } = keyLines
  const [height, setHeight] = useState(loadHeight)
  const [dragging, setDragging] = useState(false)

  // Publish the drawer's live pixel height to `--bottom-height` so pages can
  // reserve exactly enough bottom padding to never sit behind it (collapsed
  // 34px handle vs. open drawer up to ~62vh). The sticky action bar rides on
  // the same var, so this covers it too. A ResizeObserver catches every cause
  // of a height change (open/close, job accordions, body resize, drag-resize)
  // without us enumerating them.
  const drawerRef = useRef(null)
  useHeightAnimation(drawerRef, drawerOpen)
  useLayoutEffect(() => {
    const el = drawerRef.current
    if (!el) return
    const publish = () =>
      document.documentElement.style.setProperty('--bottom-height', `${el.offsetHeight}px`)
    publish()
    const ro = new ResizeObserver(publish)
    ro.observe(el)
    return () => {
      ro.disconnect()
      document.documentElement.style.removeProperty('--bottom-height')
    }
  }, [])

  // Drag the top edge to resize. Pointer capture keeps the drag alive over the
  // log panes / iframes underneath; the ResizeObserver above re-publishes
  // `--bottom-height` on every frame of the drag, so page padding and the
  // sticky action bar track the drawer live.
  const dragRef = useRef(null)
  const onHandleDown = useCallback(
    (e) => {
      if (!drawerOpen) return
      const el = drawerRef.current
      if (!el) return
      e.preventDefault()
      e.currentTarget.setPointerCapture(e.pointerId)
      dragRef.current = { startY: e.clientY, startHeight: el.offsetHeight }
      setDragging(true)
    },
    [drawerOpen]
  )

  const onHandleMove = useCallback((e) => {
    const drag = dragRef.current
    if (!drag) return
    // Dragging up (smaller clientY) makes the bottom-docked drawer taller.
    // Kept on the drag too: a release that comes before the next render
    // would otherwise store the height from before the move.
    drag.height = dragHeight(drag.startHeight + (drag.startY - e.clientY))
    setHeight(drag.height)
  }, [])

  const endDrag = useCallback(
    (e) => {
      const drag = dragRef.current
      if (!drag) return
      dragRef.current = null
      setDragging(false)
      if (e?.pointerId != null && e.currentTarget.hasPointerCapture?.(e.pointerId)) {
        e.currentTarget.releasePointerCapture(e.pointerId)
      }
      let stored = drag.height ?? height
      // Dragged all the way down: close, and reopen at a usable height.
      if (stored != null && stored < MIN_DRAWER_HEIGHT) {
        stored = clampHeight(drag.startHeight)
        setHeight(stored)
        if (drawerOpen) toggleDrawer()
      }
      try {
        if (stored != null) localStorage.setItem(HEIGHT_KEY, String(stored))
      } catch {
        /* storage unavailable — resize still applies for this session */
      }
    },
    [height, drawerOpen, toggleDrawer]
  )

  // On a phone the whole bar is the handle (the 7px edge is too thin for a
  // finger, index.css hides it there). A tap still opens and closes the
  // drawer: only a move past DRAG_SLOP_PX turns the touch into a resize, and
  // a drag up on a closed bar opens it at the height it is dragged to.
  const isMobile = useMediaQuery(MOBILE_QUERY)
  const barDrag = useRef(null)
  const skipClick = useRef(false)
  const onBarDown = useCallback(
    (e) => {
      skipClick.current = false
      if (!isMobile || !drawerRef.current) return
      // Captured at once, so a fast drag that leaves the bar in one move is
      // still the bar's (a touch is captured anyway, a mouse is not).
      e.currentTarget.setPointerCapture(e.pointerId)
      barDrag.current = { startY: e.clientY, startHeight: drawerRef.current.offsetHeight, moved: false }
    },
    [isMobile]
  )

  const onBarMove = useCallback(
    (e) => {
      const drag = barDrag.current
      if (!drag) return
      if (!drag.moved) {
        if (Math.abs(e.clientY - drag.startY) < DRAG_SLOP_PX) return
        drag.moved = true
        dragRef.current = drag
        setDragging(true)
        if (!drawerOpen) toggleDrawer()
      }
      onHandleMove(e)
    },
    [drawerOpen, toggleDrawer, onHandleMove]
  )

  const onBarUp = useCallback(
    (e) => {
      const drag = barDrag.current
      barDrag.current = null
      if (!drag?.moved) return
      // The finger lifts on the bar, so the browser still sends it a click.
      skipClick.current = true
      endDrag(e)
    },
    [endDrag]
  )

  const onBarClick = useCallback(() => {
    if (skipClick.current) {
      skipClick.current = false
      return
    }
    toggleDrawer()
  }, [toggleDrawer])

  // Double-click the handle to go back to the auto (content) height.
  const resetHeight = useCallback(() => {
    setHeight(null)
    try {
      localStorage.removeItem(HEIGHT_KEY)
    } catch {
      /* ignore */
    }
  }, [])

  // A shrinking viewport must not leave the drawer taller than the window.
  useEffect(() => {
    const onResize = () => setHeight((h) => (h == null ? h : clampHeight(h)))
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  // Seed scrollback once from the newest .RPT tail.
  useEffect(() => {
    api
      .get('/api/logs/rpt?lines=200')
      .then((data) => {
        setServerOffset(data.utc_offset_minutes)
        setRptLines((data.lines || []).map(localRptLine))
        addRpt(data.lines || [], data.stock)
      })
      .catch(() => {})
  }, [addRpt])

  const handleWs = useCallback((msg) => {
    if (msg.type !== 'rpt_line') return
    setRptLines((prev) => {
      const next = [...prev, localRptLine(msg.line)]
      return next.length > MAX_LINES ? next.slice(next.length - MAX_LINES) : next
    })
    addRpt([msg.line], [msg.stock])
  }, [addRpt])
  const { connected, subscribe, unsubscribe } = useWs(handleWs)

  useEffect(() => {
    subscribe('rpt')
    return () => unsubscribe('rpt')
  }, [subscribe, unsubscribe])

  // Toast "jump to accordion" — expand + scroll the focused job into view.
  useEffect(() => {
    if (!focusJobId) return
    const el = document.getElementById(`job-acc-${focusJobId}`)
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    clearFocus()
  }, [focusJobId, clearFocus, drawerOpen])

  const running = !!status?.running
  // A restored notification only deep-links while its job is still on show.
  const shownJobIds = useMemo(() => new Set(jobs.map((j) => j.id)), [jobs])
  const runningCount = jobs.filter((j) => RUNNING_STATUSES.has(j.status)).length
  const sortedJobs = [...jobs].sort(
    (a, b) => jobRank(a) - jobRank(b) || (b.created_at || 0) - (a.created_at || 0)
  )
  // The first running job's SteamCMD download progress, for the closed bar.
  const barProgress =
    sortedJobs
      .filter((j) => RUNNING_STATUSES.has(j.status))
      .map((j) => lastProgress(logs[j.id]?.lines || []))
      .find((p) => p !== null) ?? null

  return (
    <div
      ref={drawerRef}
      className={`server-log-drawer activity-drawer${drawerOpen ? '' : ' collapsed'}${
        dragging ? ' resizing' : ''
      }`}
      style={drawerOpen && height != null ? { height: `${height}px` } : undefined}
    >
      {drawerOpen && (
        <div
          className="activity-resize-handle"
          role="separator"
          aria-orientation="horizontal"
          aria-label="Resize activity drawer"
          {...tooltipAttrs('Drag to resize — double-click to reset')}
          onPointerDown={onHandleDown}
          onPointerMove={onHandleMove}
          onPointerUp={endDrag}
          onPointerCancel={endDrag}
          onDoubleClick={resetHeight}
        />
      )}
      {/* The whole bar is the drawer's toggle, with no caret of its own: it is
          the one control most often reached for. .server-log-header carries the
          styling that sets this row apart from the .log-pane-header accordions
          stacked below it. */}
      <button
        type="button"
        className="server-log-header"
        onClick={onBarClick}
        onPointerDown={onBarDown}
        onPointerMove={onBarMove}
        onPointerUp={onBarUp}
        onPointerCancel={onBarUp}
        aria-expanded={drawerOpen}
        aria-label={drawerOpen ? 'Collapse activity' : 'Expand activity'}
      >
        <LuActivity className="server-log-icon" aria-hidden="true" />
        <span className="server-log-title">Activity</span>
        {runningCount > 0 && (
          <span className="status-pill status-pill-running status-pill-plain" {...tooltipAttrs(`${runningCount} process(es) running`)}>
            {runningCount} running
          </span>
        )}
        {keyLines.counts.error > 0 && (
          <span
            className="status-pill status-pill-failed"
            {...tooltipAttrs('Errors in Key lines. Open Activity to read them.')}
          >
            {keyLines.counts.error} {keyLines.counts.error === 1 ? 'error' : 'errors'}
          </span>
        )}
        {!drawerOpen && barProgress !== null && (
          <ProgressBar
            progress={barProgress}
            label="Download progress"
            className="activity-progress"
          />
        )}
        {version && (
          <span className="activity-version">
            v{version}
            {/* Zedstead is beta for as long as it is 0.x. */}
            {version.startsWith('0.') && <BetaPill tip={VERSION_BETA_TIP} />}
          </span>
        )}
      </button>

      {drawerOpen && (
        <div className="activity-body">
          <LogPane
            title="Server Log"
            icon={HiOutlineServerStack}
            lines={rptLines}
            highlights={RPT_HIGHLIGHTS}
            expanded={serverLogExpanded}
            onToggleExpand={() => setServerLogExpanded((v) => !v)}
            statusNode={<ServerLogDot connected={connected} running={running} />}
            actions={
              <>
                <LogDownloadLink iconOnly />
                <IconButton icon={HiOutlineTrash} label="Clear the server log view" onClick={() => setRptLines([])} />
              </>
            }
          />

          {/* Key lines: the successes, warnings and errors that the highlight
              rules pick out of the logs and RCon (keyLines.js). Selected
              lines, not a health check, so the empty text and the tip point
              to the full log check. */}
          <LogPane
            title="Key lines"
            icon={HiOutlineListBullet}
            lines={keyLines.entries}
            expanded={keyLinesExpanded}
            onToggleExpand={() => setKeyLinesExpanded((v) => !v)}
            statusNode={
              <span className="key-lines-counts">
                {[
                  ['error', 'status-pill-failed', 'error', 'errors'],
                  ['warning', 'status-pill-warning', 'warning', 'warnings'],
                  ['success', 'status-pill-success', 'success', 'successes'],
                  ['stock', 'status-pill-neutral', 'stock line', 'stock lines'],
                ].map(
                  ([level, cls, one, many]) =>
                    keyLines.counts[level] > 0 && (
                      <span key={level} className={`status-pill ${cls}`}>
                        {keyLines.counts[level]} {keyLines.counts[level] === 1 ? one : many}
                      </span>
                    )
                )}
              </span>
            }
            actions={
              <IconButton icon={HiOutlineTrash} label="Clear the key lines" onClick={keyLines.clear} disabled={!keyLines.entries.length} />
            }
          >
            {keyLines.entries.length ? (
              keyLines.entries.map((entry) => (
                <ToastRow
                  key={entry.id}
                  entry={entry}
                  badge={
                    <>
                      <span className="status-pill status-pill-neutral">{entry.source}</span>
                      {entry.level === 'stock' && (
                        <span
                          className="status-pill status-pill-neutral"
                          {...tooltipAttrs('Every stock DayZ server prints this line. It is harmless.')}
                        >
                          stock
                        </span>
                      )}
                      {entry.count > 1 && <CountBadge value={`×${entry.count}`} />}
                    </>
                  }
                  onClick={
                    entry.rpt
                      ? () => setServerLogExpanded(true)
                      : shownJobIds.has(entry.jobId)
                        ? () => focusJob(entry.jobId)
                        : null
                  }
                />
              ))
            ) : (
              <PanelNote>
                No key lines yet. These are selected lines only:{' '}
                <Link to="/analysis?tab=Server+logs">Analysis → Server logs</Link> checks the whole server log.
              </PanelNote>
            )}
          </LogPane>

          {/* Every toast this session, in the order they popped. Toasts vanish
              on a timer and are the only place some results are reported (a
              save, a failed request, a server-pushed notification), so the
              drawer keeps them readable after the fact. */}
          <LogPane
            title="Notifications"
            icon={HiOutlineBell}
            lines={toastLog}
            expanded={toastLogExpanded}
            onToggleExpand={() => setToastLogExpanded((v) => !v)}
            statusNode={
              toastLog.length > 0 && (
                <span
                  className="status-pill status-pill-neutral"
                  {...tooltipAttrs(`${toastLog.length} notification(s) this session`)}
                >
                  {toastLog.length}
                </span>
              )
            }
            actions={
              <IconButton icon={HiOutlineTrash} label="Clear the notifications" onClick={clearToastLog} disabled={!toastLog.length} />
            }
          >
            {toastLog.length ? (
              toastLog.map((entry) => (
                <ToastRow
                  key={entry.id}
                  entry={entry}
                  badge={badge(entry.instance)}
                  onClick={
                    entry.onClick ||
                    (shownJobIds.has(entry.jobId) ? () => focusJob(entry.jobId) : null)
                  }
                />
              ))
            ) : (
              <PanelNote>No notifications yet.</PanelNote>
            )}
          </LogPane>

          {sortedJobs.map((job) => {
            const running = RUNNING_STATUSES.has(job.status)
            // Cancelling only takes effect at the job's next checkpoint. When
            // that checkpoint never comes, a second click on the same button
            // offers a confirmed Kill instead of leaving it stuck.
            const cancelPending = running && isCancelPending(job, cancelling)
            // Only a job that has actually started can be killed (the server
            // 404s a pending one); it is pending for a moment at most.
            const canKill = cancelPending && job.status === 'running' && !job.kill_requested
            return (
              <div key={job.id} id={`job-acc-${job.id}`} className="log-pane-wrap">
                <LogPane
                  title={job.name || job.kind || 'Process'}
                  icon={jobIcon(job.kind)}
                  lines={logs[job.id]?.lines || []}
                  highlights={JOB_HIGHLIGHTS}
                  // A finished job's empty log said "No log output yet."
                  emptyText={
                    job.status === 'running' || job.status === 'pending'
                      ? 'No log output yet.'
                      : 'This job wrote no log.'
                  }
                  expanded={!!expanded[job.id]}
                  onToggleExpand={() => toggleExpanded(job.id)}
                  statusNode={
                    <>
                      {badge(job.instance)}
                      {cancelPending ? (
                        <span className="status-pill status-pill-cancelling">cancelling</span>
                      ) : (
                        <span className={`status-pill status-pill-${job.status}`}>{job.status}</span>
                      )}
                    </>
                  }
                  actions={
                    // Cancel and Kill are admin writes; a viewer still sees
                    // the job (the WS broadcasts to every session), not the
                    // buttons. Dismiss only hides it in this browser.
                    running ? (
                      canAdmin && (
                      <Tooltip
                        text={
                          canKill
                            ? 'Cancel requested — the job stops at its next step. Stuck? Kill it now.'
                            : cancelPending
                              ? 'Stopping…'
                              : ''
                        }
                        className="bare"
                      >
                        <button
                          className={`btn btn-small ${canKill ? 'btn-danger' : 'btn-ghost'}`}
                          onClick={() => (canKill ? confirmKill(job) : cancelJob(job.id))}
                          disabled={cancelPending && !canKill}
                        >
                          {canKill ? 'Kill…' : cancelPending ? 'Cancelling…' : 'Cancel'}
                        </button>
                      </Tooltip>
                      )
                    ) : (
                      <IconButton icon={HiOutlineXMark} label="Dismiss" onClick={() => dismissJob(job.id)} />
                    )
                  }
                />
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
