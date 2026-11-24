// A log view that follows its newest line: line colours, the SteamCMD download
// progress bar, and a Jump to latest button that counts the lines below.

import { Switch } from './FlagSwitch.jsx'
import { useCallback, useEffect, useRef, useState } from 'react'
import FoldCaret from './FoldCaret.jsx'
import { tooltipAttrs } from './Tooltip.jsx'
import CountBadge from './CountBadge.jsx'

const AT_BOTTOM_THRESHOLD = 48

// SteamCMD's download line: "Update state (0x61) downloading, progress: 66.28 (…)".
const PROGRESS_RE = /\bprogress:\s*(\d+(?:\.\d+)?)\s*\(/

// The download progress (0..1) of the newest log line, or null when that line
// is not a SteamCMD progress line.
export function lastProgress(lines) {
  const m = PROGRESS_RE.exec(lines[lines.length - 1] ?? '')
  return m ? Math.min(Math.max(parseFloat(m[1]) / 100, 0), 1) : null
}

// A download bar with its percent in the middle. Spans only, so it can sit
// inside a <button> (the Activity bar). `progress` is 0..1.
export function ProgressBar({ progress, label, className = '' }) {
  return (
    <span
      className={`progress-bar ${className}`.trim()}
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(progress * 100)}
      style={{ '--p': progress }}
    >
      <span className="progress-bar-fill" />
      <span className="progress-bar-text">{(progress * 100).toFixed(1)}%</span>
    </span>
  )
}

// Key Server Log (RPT) lines: bigger text and a color per kind. The texts are
// the same markers app/ops/log_analysis.py reads (ready, clean shutdown, boot).
export const RPT_HIGHLIGHTS = [
  {
    kind: 'good',
    test: /Player connect enabled|Termination successfully completed|SUCCESS: SteamGameServer_Init/,
  },
  { kind: 'bad', test: /\(E\):|Virtual Machine Exception/ },
  {
    kind: 'info',
    test: /^Version |Loading landscape '|under name mission|Storage load took|Destroying game/,
  },
]

// Key SteamCMD lines in a job log: a finished install or Workshop download,
// and SteamCMD's own errors ("ERROR! Failed to install app …").
export const JOB_HIGHLIGHTS = [
  { kind: 'good', test: /^\s*Success[!.] (?:App '\d+' fully installed|Downloaded item)/ },
  { kind: 'bad', test: /^\s*ERROR! / },
  // Quick setup (app/web/routers/quick_setup.py::_part): a step header and its done line.
  { kind: 'step', test: /^--- .+ ---$/ },
  { kind: 'done', test: /^✓ / },
]

// BattlEye's own RCon messages (app/rcon/rcon_manager.py::_handle_raw_message).
// Only kicks and bans are kept; joins and GUID checks are everyday traffic.
export const RCON_HIGHLIGHTS = [{ kind: 'warn', test: /\bkicked\b|\bbanned\b/i }]

// The kind of the first rule that matches `line`, or null.
export function highlightKind(line, rules) {
  return rules.find((h) => h.test.test(line))?.kind ?? null
}

// The log text. Plain lines stay joined in one string; a line that matches
// `highlights` gets its own span. When the newest line is a SteamCMD progress
// line, it gets a span whose size and color follow the percent (`--p`, 0..1);
// the next line that arrives turns it back into plain text.
function LogText({ lines, progress, highlights }) {
  if (progress === null && !highlights) return lines.join('\n')
  const body = progress === null ? lines : lines.slice(0, -1)
  const out = []
  let plain = ''
  body.forEach((line, i) => {
    const sep = i < lines.length - 1 ? '\n' : ''
    const kind = highlights && highlightKind(line, highlights)
    if (!kind) {
      plain += line + sep
      return
    }
    if (plain) out.push(plain)
    plain = ''
    out.push(
      <span key={i} className={`log-key-line log-key-${kind}`}>
        {line}
      </span>,
      sep
    )
  })
  if (plain) out.push(plain)
  if (progress !== null) {
    out.push(
      <span key="progress" className="log-progress-line" style={{ '--p': progress }}>
        {lines[lines.length - 1]}
      </span>
    )
  }
  return out
}

export default function LogPane({
  title,
  icon: Icon,
  lines,
  expanded,
  onToggleExpand,
  statusNode,
  actions,
  children,
  // What an empty pane says. "yet" only while something can still write.
  emptyText = 'No log output yet.',
  // Optional [{ kind, test: RegExp }]: lines to show bigger and colored.
  highlights,
}) {
  const [autoScroll, setAutoScroll] = useState(true)
  const [newCount, setNewCount] = useState(0)
  const preRef = useRef(null)
  const atBottomRef = useRef(true)
  const prevLenRef = useRef(lines.length)
  // Scroll events fired by our own scrollTop writes must not unpin the view.
  const programmaticRef = useRef(0)

  const snap = useCallback(() => {
    const el = preRef.current
    if (!el) return
    programmaticRef.current += 1
    el.scrollTop = el.scrollHeight
    // Content laid out this frame may still be growing; re-pin next frame so a
    // burst of lines can't leave us stranded above the true bottom.
    requestAnimationFrame(() => {
      const e = preRef.current
      if (!e) return
      programmaticRef.current += 1
      e.scrollTop = e.scrollHeight
    })
  }, [])

  const scrollToBottom = useCallback(() => {
    atBottomRef.current = true
    setNewCount(0)
    snap()
  }, [snap])

  // React to new lines: snap down when pinned + auto-scroll on, else count them.
  useEffect(() => {
    const grew = lines.length - prevLenRef.current
    prevLenRef.current = lines.length
    if (!expanded) return
    if (autoScroll && atBottomRef.current) {
      snap()
    } else if (grew > 0) {
      setNewCount((n) => n + grew)
    }
  }, [lines, expanded, autoScroll, snap])

  // Expanding (or turning auto-scroll back on) snaps to the latest output.
  useEffect(() => {
    if (expanded && autoScroll) requestAnimationFrame(scrollToBottom)
  }, [expanded, autoScroll, scrollToBottom])

  const onScroll = useCallback(() => {
    const el = preRef.current
    if (!el) return
    // Ignore scroll events triggered by our own snap() writes — they fire
    // async and can carry a stale (mid-growth) scrollHeight that would falsely
    // read as "not at bottom" and unpin the view.
    if (programmaticRef.current > 0) {
      programmaticRef.current -= 1
      atBottomRef.current = true
      return
    }
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < AT_BOTTOM_THRESHOLD
    atBottomRef.current = atBottom
    if (atBottom) setNewCount(0)
  }, [])

  const progress = lastProgress(lines)

  const onHeaderClick = useCallback(
    (e) => {
      if (e.target.closest('.log-pane-actions :is(button, input, label, a, select)')) return
      onToggleExpand()
    },
    [onToggleExpand]
  )

  return (
    <div className={`log-pane${expanded ? ' expanded' : ''}`}>
      <div className="log-pane-header" onClick={onHeaderClick}>
        <button
          type="button"
          className="log-pane-toggle"
          aria-expanded={expanded}
          aria-label={expanded ? `Collapse ${title}` : `Expand ${title}`}
        >
          <span className="log-pane-caret" aria-hidden="true">
            <FoldCaret open={expanded} />
          </span>
          {Icon && <Icon className="log-pane-icon" aria-hidden="true" />}
          <span className="log-pane-title">{title}</span>
          {statusNode}
        </button>
        {/* While SteamCMD reports progress: beside the title on a desktop, on
            a line of its own on a phone. Gone with the next log line. */}
        {progress !== null && (
          <ProgressBar progress={progress} label={`${title} download progress`} />
        )}
        <div className="log-pane-actions">
          <div className="log-pane-autoscroll" {...tooltipAttrs('Auto-scroll to newest lines')}>
            <Switch checked={autoScroll} onChange={setAutoScroll}>
              auto-scroll
            </Switch>
          </div>
          {expanded && newCount > 0 && (
            <button className="server-log-jump" onClick={scrollToBottom}>
              Jump to latest <CountBadge value={newCount} />
            </button>
          )}
          {actions}
        </div>
      </div>
      {expanded &&
        (children ? (
          <div ref={preRef} className="log-tail log-pane-body log-pane-rows" onScroll={onScroll}>
            {children}
          </div>
        ) : (
          <pre ref={preRef} className="log-tail log-pane-body" onScroll={onScroll}>
            {lines.length ? <LogText lines={lines} progress={progress} highlights={highlights} /> : emptyText}
          </pre>
        ))}
    </div>
  )
}
