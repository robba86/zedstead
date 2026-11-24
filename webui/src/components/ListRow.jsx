// The one list row (webui/DESIGN.md "List row"). Slots, left to right:
// `lead` (a RowDot, an ItemIcon, a checkbox or a RowNumber), `name` with an
// optional second line `sub`, `meta` (muted text on the right, such as a
// coordinate or a CountBadge) and `actions` (RowAction buttons).
//
// States: hover is `--bg-hover`; `selected` adds an accent bar on the left,
// as `.data-table tr.row-active`; `dimmed` mutes the text (a zero-point
// event); `disabled` strikes the name through (a disabled object). `compact`
// uses `--row-h-compact`. With `onClick` the row is a keyboard target:
// Enter and Space click it. A RowAction stops its click from reaching the row.
// `nameTip` is a tooltip on the name, such as a class name behind a label.
// `wrap` lets the name and `sub` wrap instead of cutting them with an ellipsis,
// for a check, a fact or a finding whose detail is long. `mono` sets the name
// in the code font, for a command or a class name. `as` picks the
// element, for example `li` inside a list.

import { tooltipAttrs } from './Tooltip.jsx'

// A dot takes its colour from `color` or from a `className` rule. The Map
// Editor passes a class, since its map-data colours live only in
// mapeditor.css (and the leaflet hooks), never in JSX.
export function RowDot({ color, className = '', label }) {
  return (
    <span
      className={className ? `row-dot ${className}` : 'row-dot'}
      style={color ? { background: color } : undefined}
      role={label ? 'img' : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : 'true'}
    />
  )
}

export function RowNumber({ children, className = '', tip }) {
  return (
    <span className={className ? `row-number ${className}` : 'row-number'} {...tooltipAttrs(tip)}>
      {children}
    </span>
  )
}

// The one small square tag for a label on a row, such as `cooldown` or
// `disabled` (webui/DESIGN.md "Badge"). A state uses `.status-pill` instead.
// `keep` holds a long `tip` open so it can be read to the end.
export function RowTag({ children, tip, keep = false }) {
  return (
    <span className="row-tag" {...tooltipAttrs(tip, { keep })}>
      {children}
    </span>
  )
}

// The colour of a finding's severity, as a RowDot in the `lead` slot.
const SEVERITY_COLORS = {
  error: 'var(--danger)',
  warning: 'var(--warning)',
  ok: 'var(--good)',
  info: 'var(--accent)',
}

export function SeverityDot({ severity }) {
  return <RowDot color={SEVERITY_COLORS[severity] ?? 'var(--text-muted)'} label={severity} />
}

export default function ListRow({
  lead,
  name,
  nameTip,
  sub,
  meta,
  actions,
  selected = false,
  dimmed = false,
  disabled = false,
  compact = false,
  wrap = false,
  mono = false,
  as: Tag = 'div',
  onClick,
  className = '',
  ...rest
}) {
  const classes = [
    'list-row',
    sub && 'has-sub',
    compact && 'is-compact',
    wrap && 'is-wrap',
    mono && 'is-mono',
    selected && 'is-selected',
    dimmed && 'is-dimmed',
    disabled && 'is-disabled',
    onClick && 'is-clickable',
    className,
  ]
    .filter(Boolean)
    .join(' ')
  // A wrapped sub can hold blocks (fix buttons, a link row), so it is a div.
  const Text = wrap ? 'div' : 'span'
  const clickable = onClick
    ? {
        role: 'button',
        tabIndex: 0,
        'aria-pressed': selected,
        onClick,
        onKeyDown: (e) => {
          if (e.target !== e.currentTarget) return
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            onClick(e)
          }
        },
      }
    : {}
  return (
    <Tag className={classes} {...clickable} {...rest}>
      {lead && <span className="list-row-lead">{lead}</span>}
      <Text className="list-row-text">
        <span className="list-row-name" {...tooltipAttrs(nameTip)}>
          {name}
        </span>
        {sub && <Text className="list-row-sub">{sub}</Text>}
      </Text>
      {meta !== undefined && meta !== null && meta !== false && <span className="list-row-meta">{meta}</span>}
      {actions && <span className="list-row-actions">{actions}</span>}
    </Tag>
  )
}
