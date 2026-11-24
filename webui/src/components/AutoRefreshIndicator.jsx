import { tooltipAttrs } from './Tooltip.jsx'

// The visible half of useAutoRefresh: a ring that fills as the next refresh
// approaches, with a play/pause glyph in the middle. The glyph shows the
// ACTION (▶ = click to resume, ❚❚ = click to pause), the ring shows the state.
//
// `blocked` is the caller saying "not now" (a job is mutating the server) —
// distinct from the user's own pause, so resuming isn't silently undone and the
// tooltip can say which one is holding the timer.

const R = 9
const C = 2 * Math.PI * R

export default function AutoRefreshIndicator({
  paused,
  blocked = false,
  blockedReason = 'Paused while a process is running',
  progress = 0,
  secondsLeft = 0,
  interval = 60000,
  onToggle,
  compact = false,
}) {
  const everySec = Math.round(interval / 1000)
  const label = paused ? 'Off' : blocked ? 'Paused' : `${secondsLeft}s`
  const tip = blocked
    ? paused
      ? `${blockedReason}, and auto-refresh is off — click to turn it on.`
      : `${blockedReason}. Auto-refresh resumes when it finishes — click to turn it off.`
    : paused
      ? `Auto-refresh is off — click to re-check every ${everySec}s.`
      : `Auto-refresh every ${everySec}s — next in ${secondsLeft}s. Click to pause.`

  const cls = ['auto-refresh', paused ? 'is-paused' : blocked ? 'is-blocked' : 'is-running']
  if (compact) cls.push('auto-refresh-compact')

  return (
    <button
      type="button"
      className={cls.join(' ')}
      onClick={onToggle}
      aria-pressed={!paused}
      {...tooltipAttrs(tip)}
    >
      <svg className="auto-refresh-ring" viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
        <circle className="ar-track" cx="12" cy="12" r={R} />
        <circle
          className="ar-progress"
          cx="12"
          cy="12"
          r={R}
          transform="rotate(-90 12 12)"
          style={{ strokeDasharray: C, strokeDashoffset: C * (1 - progress) }}
        />
        {/* Keyed on `paused` alone, never on `blocked`: the glyph is the
            button's action (the user's own on/off), while the ring and the
            label report what the timer is actually doing right now. */}
        {paused ? (
          <path className="ar-glyph" d="M10 8.4 L15.6 12 L10 15.6 Z" />
        ) : (
          <>
            <rect className="ar-glyph" x="9.4" y="8.4" width="1.9" height="7.2" rx="0.6" />
            <rect className="ar-glyph" x="12.7" y="8.4" width="1.9" height="7.2" rx="0.6" />
          </>
        )}
      </svg>
      {!compact && <span className="auto-refresh-label">{label}</span>}
    </button>
  )
}
