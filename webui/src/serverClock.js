// The Activity drawer shows one clock: the browser's, in 24-hour form. A
// server log line starts with the server's own local time ("9:31:33"), which
// differs from the browser's when the two are in other time zones (night test
// 2026-11-22: a UTC container beside a CET browser). GET /api/logs/rpt sends
// the server's UTC offset, and these helpers move a line's time to the
// browser's zone. Until the offset is known, a line keeps its own time.

// An RPT line's leading time; an hour before 10:00 has one digit.
const RPT_TIME = /^(\s*)(\d{1,2}):(\d{2}):(\d{2})(?:\.\d+)?(\s+)/

let serverOffsetMinutes = null

// Records the server's UTC offset in minutes (east of UTC is positive).
export function setServerOffset(minutes) {
  serverOffsetMinutes = Number.isFinite(minutes) ? minutes : null
}

// A time as HH:MM:SS on a 24-hour clock, the drawer's one format.
export function clock24(date) {
  return date.toLocaleTimeString(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hourCycle: 'h23',
  })
}

// The moment an RPT line's time names, in epoch ms, or null without a time or
// an offset. The line carries no date, so it is taken as the latest such time
// that is not more than a minute in the future.
export function rptLineMs(line) {
  const m = RPT_TIME.exec(line)
  if (!m || serverOffsetMinutes === null) return null
  const now = new Date()
  const utcSeconds = Number(m[2]) * 3600 + Number(m[3]) * 60 + Number(m[4]) - serverOffsetMinutes * 60
  const day = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate())
  let ms = day + utcSeconds * 1000
  while (ms > now.getTime() + 60_000) ms -= 86_400_000
  while (ms < now.getTime() - 86_400_000) ms += 86_400_000
  return ms
}

// The line with its leading server time replaced by the browser's.
export function localRptLine(line) {
  const ms = rptLineMs(line)
  if (ms === null) return line
  return line.replace(RPT_TIME, (_all, lead, _h, _m, _s, gap) => `${lead}${clock24(new Date(ms))}${gap}`)
}
