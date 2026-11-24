import { tooltipAttrs } from './Tooltip.jsx'

// A setting's choices side by side, the current one filled. It replaces a
// single button whose label was either the current state or the action: read
// cold, neither says which, and a toggle has no other way to show the choice it
// is not on.
//
// options = [{ value, label, tip? }]. Never On/Off: an on/off setting is a
// Switch (FlagSwitch.jsx).
// Choosing the active option does nothing, so onChange only ever hears a change.
// `keepTips` keeps an option's tip open while the pointer moves into it, for a
// tip long enough to read to the end.
export default function Segmented({ value, options, onChange, label, disabled = false, keepTips = false }) {
  return (
    <span className="segmented" role="group" aria-label={label}>
      {options.map((o) => {
        const active = o.value === value
        const className = ['segmented-option', active && 'segmented-active']
          .filter(Boolean)
          .join(' ')
        return (
          <button
            key={String(o.value)}
            type="button"
            className={className}
            aria-pressed={active}
            disabled={disabled}
            onClick={() => !active && onChange(o.value)}
            {...tooltipAttrs(o.tip, { keep: keepTips })}
          >
            {o.label}
          </button>
        )
      })}
    </span>
  )
}
