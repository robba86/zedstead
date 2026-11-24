// The one icon button on a list row or a section header (webui/DESIGN.md
// "Row button"): bare and muted, a border on hover, one size
// (`--icon-btn-size`). `label` is both the screen reader name and the tooltip.
// `danger` turns it red on hover, `active` marks a pressed toggle (an armed
// add mode, a hidden object). It never hides until hover, since a touch
// screen has no hover. A disabled button fires no mouseenter, so a caller
// that must explain why it is disabled wraps it in `<Tooltip className="bare">`.

import { tooltipAttrs } from './Tooltip.jsx'

export function rowActionClass({ danger = false, active = false, className = '' } = {}) {
  return ['row-action', danger && 'is-danger', active && 'is-active', className].filter(Boolean).join(' ')
}

export default function RowAction({
  icon: Icon,
  label,
  onClick,
  danger = false,
  active = false,
  disabled = false,
  className = '',
  ...rest
}) {
  return (
    <button
      type="button"
      className={rowActionClass({ danger, active, className })}
      aria-label={label}
      aria-pressed={active || undefined}
      disabled={disabled}
      onClick={(e) => {
        // A row action never also fires the row's own click (select, fly to).
        e.stopPropagation()
        onClick?.(e)
      }}
      {...tooltipAttrs(label)}
      {...rest}
    >
      {Icon && <Icon aria-hidden="true" />}
    </button>
  )
}
