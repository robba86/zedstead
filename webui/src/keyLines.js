// The Activity drawer's Key lines: a short list of the successes, warnings and
// errors that the highlight rules in LogPane.jsx pick out of the Server Log, the
// job logs and BattlEye's RCon messages (`rcon_message`, app/web/panel.py).
// These are selected lines only, never a health check of the server: a line no
// rule knows never shows here. Analysis > Server logs runs the full rules
// (app/ops/log_analysis.py), so the pane links there.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useWs } from './ws'
import {
  JOB_HIGHLIGHTS,
  RCON_HIGHLIGHTS,
  RPT_HIGHLIGHTS,
  highlightKind,
} from './components/LogPane.jsx'
import { rptLineMs } from './serverClock.js'

const MAX_KEY_LINES = 100

// Highlight kind -> row level. `info` and `step` lines stay in their log only.
const LEVEL = { good: 'success', done: 'success', warn: 'warning', bad: 'error' }

// An RPT line starts with its time; two lines that differ only there are one row.
const TS_PREFIX = /^\s*\d{1,2}:\d{2}:\d{2}(?:\.\d+)?\s+/

// The key lines among `lines`, as rows waiting for `add`. `stock[i]` marks a
// line every stock boot prints (app/ops/log_analysis.py::is_stock_line): its
// error is muted as `stock`, so a vanilla server shows no red errors.
function classify(lines, rules, source, extra = {}, stock = []) {
  const out = []
  lines.forEach((line, i) => {
    let level = LEVEL[highlightKind(line, rules)]
    if (level === 'error' && stock[i]) level = 'stock'
    if (level) out.push({ level, message: line, source, ...extra })
  })
  return out
}

// Keeps the Key lines rows. `logs` and `jobs` come from useJobs(); the drawer
// hands Server Log lines to `addRpt`, since it owns that feed.
export function useKeyLines(logs, jobs) {
  const [entries, setEntries] = useState([])
  const nextId = useRef(1)
  const seenSeq = useRef({}) // job id -> the last log seq already classified

  // Oldest first, like the Notifications pane, so auto-scroll shows the newest.
  // A repeat of a row moves it to the end and counts it, so a mod that logs
  // the same error hundreds of times is one row.
  const add = useCallback((items) => {
    if (!items.length) return
    setEntries((prev) => {
      let next = prev
      for (const item of items) {
        const key = `${item.source}|${item.message.replace(TS_PREFIX, '')}`
        const old = next.find((e) => e.key === key)
        const entry = old
          ? { ...old, ...item, count: old.count + 1, at: item.at ?? Date.now() }
          : { ...item, id: nextId.current++, key, count: 1, at: item.at ?? Date.now() }
        next = [...next.filter((e) => e !== old), entry]
      }
      return next.slice(-MAX_KEY_LINES)
    })
  }, [])

  // A server log row shows the line's own moment on the browser's clock as
  // its time, so the message drops the server-time prefix (serverClock.js).
  const addRpt = useCallback(
    (lines, stock) =>
      add(
        classify(lines, RPT_HIGHLIGHTS, 'Server Log', { rpt: true }, stock).map((row) => {
          const at = rptLineMs(row.message)
          return at === null ? row : { ...row, at, message: row.message.replace(TS_PREFIX, '') }
        })
      ),
    [add]
  )

  // New job log lines. `seq` keeps counting when old lines drop off the front,
  // so the new ones are the last (seq - seen) lines.
  useEffect(() => {
    const items = []
    for (const [id, log] of Object.entries(logs)) {
      const seen = seenSeq.current[id] ?? 0
      if (log.seq <= seen) continue
      seenSeq.current[id] = log.seq
      const fresh = log.lines.slice(-Math.min(log.seq - seen, log.lines.length))
      const job = jobs.find((j) => String(j.id) === id)
      items.push(...classify(fresh, JOB_HIGHLIGHTS, job?.name || job?.kind || 'Process', { jobId: job?.id ?? id }))
    }
    add(items)
  }, [logs, jobs, add])

  // BattlEye's messages come unclassified (level ""), and the RCon
  // connection's own state comes with its kind.
  useWs(
    useCallback(
      (msg) => {
        if (msg.type !== 'rcon_message') return
        const level = LEVEL[msg.level || highlightKind(msg.message, RCON_HIGHLIGHTS)]
        if (level) add([{ level, message: msg.message, source: 'RCon' }])
      },
      [add]
    )
  )

  const counts = useMemo(() => {
    const c = { error: 0, warning: 0, success: 0, stock: 0 }
    for (const e of entries) c[e.level] += 1
    return c
  }, [entries])

  const clear = useCallback(() => setEntries([]), [])

  return { entries, counts, addRpt, clear }
}
