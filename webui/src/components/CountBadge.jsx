// The one look for a count (webui/DESIGN.md "Count", plan 89 decision D4):
// a muted pill. `value` is a plain count. `parts` is a list of
// `{ value, unit }` joined with " · ", for example `12 events · 972 pts`.
// Numbers get thousands separators. Never put brackets around a count, and
// never use this for a coordinate, a price or a nominal: those are row meta.

const fmt = (n) => (typeof n === 'number' ? n.toLocaleString() : n)

export default function CountBadge({ value, parts, label }) {
  const text = parts
    ? parts.map((p) => (p.unit ? `${fmt(p.value)} ${p.unit}` : fmt(p.value))).join(' · ')
    : fmt(value)
  if (text === undefined || text === null || text === '') return null
  return (
    <span className="count-badge" aria-label={label}>
      {text}
    </span>
  )
}
