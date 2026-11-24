import { tooltipAttrs } from './Tooltip.jsx'

// A horizontal bar chart of one series, without a chart library (the
// Sparkline rule): one row per item, its name, a bar and its value as text, so
// the numbers stay readable without the bars and nothing rests on colour.
// rows = [{ key, label, value, text?, tip? }]. `max` is the full-length value
// (a score's 1, or the largest row when left out). Each whole row carries its
// tip, so the hover target is bigger than the bar.
// With `onPick`, each row is a button that filters a list elsewhere:
// onPick(key) on a click, onPick(null) on a click on the picked row
// (`active`), and the other rows dim while one is picked.
export default function BarChart({ title, rows, max, label, active = null, onPick }) {
  const top = max ?? Math.max(0, ...rows.map((r) => r.value))
  const content = (r) => (
    <>
      <span className="bar-chart-label">{r.label}</span>
      <span className="bar-chart-track" aria-hidden="true">
        {r.value > 0 && (
          <span className="bar-chart-bar" style={{ width: `${top ? Math.min(100, (r.value / top) * 100) : 0}%` }} />
        )}
      </span>
      <span className="bar-chart-value">{r.text ?? r.value}</span>
    </>
  )
  return (
    <figure className={`bar-chart${active != null ? ' has-active' : ''}`}>
      {title && <figcaption className="bar-chart-title">{title}</figcaption>}
      <ul className="bar-chart-rows" aria-label={label || title}>
        {rows.map((r) =>
          onPick ? (
            <li key={r.key}>
              <button
                type="button"
                className={`bar-chart-row bar-chart-pick${active === r.key ? ' is-active' : ''}`}
                aria-pressed={active === r.key}
                onClick={() => onPick(active === r.key ? null : r.key)}
                {...tooltipAttrs(
                  `${r.tip || r.label}\n${active === r.key ? 'Click again to show every item.' : 'Click to show only these in the list below.'}`
                )}
              >
                {content(r)}
              </button>
            </li>
          ) : (
            <li key={r.key} className="bar-chart-row" {...tooltipAttrs(r.tip)}>
              {content(r)}
            </li>
          )
        )}
      </ul>
    </figure>
  )
}
