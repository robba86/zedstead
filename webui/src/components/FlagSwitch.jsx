import { tooltipAttrs } from './Tooltip.jsx'
import HelpButton from './HelpButton.jsx'
import { useMediaQuery } from '../useMediaQuery.js'

// The app's on/off control: a pill switch instead of a checkbox (2026 Q4).
// `checked` in, onChange(nextBool) out, never an event. `children` is the
// visible label, written beside the switch (the old <label><input> pair); the
// track itself shows the state, so there is no On/Off text. `label` is the
// accessible name for a switch whose label is elsewhere (a table column). A
// checkbox stays a checkbox only where it picks rows or items for a batch
// action (Select all, mod tiles, list rows). On a touch screen a tap is the
// toggle itself, so `help` moves off the switch onto a "?" beside it.
// `badge` is a pill after the label (DefaultPill): "Default" when the switch
// is on the value the server uses without the key, "Before 1.30" on an old key.
export function Switch({ checked, onChange, disabled = false, label, help, badge, className = '', children, ...rest }) {
  const touch = useMediaQuery('(hover: none)')
  const button = (
    <button
      type="button"
      role="switch"
      aria-checked={!!checked}
      aria-label={label}
      className={`flag-switch${children ? ' flag-switch-labelled' : ''}${className ? ` ${className}` : ''}`}
      disabled={disabled}
      {...tooltipAttrs(touch ? undefined : help, { keep: true })}
      {...rest}
      onClick={() => onChange?.(!checked)}
    >
      <span className="switch-track" aria-hidden="true" />
      {children && <span className="flag-switch-label">{children}</span>}
      {badge}
    </button>
  )
  if (!touch || !help) return button
  return (
    <span className="switch-with-help">
      {button}
      <HelpButton text={help} label={`Help: ${typeof children === 'string' ? children : label || 'this setting'}`} />
    </span>
  )
}

// A 0/1 setting as an on/off switch (format.js isFlagKey decides when). The
// value goes back as the number 0 or 1, never true/false: Expansion and the CE
// files store flags as ints, and the server keeps the file's type. A boolean
// value (a real JSON bool) round-trips as a boolean.
export default function FlagSwitch({ value, onChange, ...props }) {
  const on = value === true || Number(value) === 1
  const next = typeof value === 'boolean' ? !on : on ? 0 : 1
  return <Switch checked={on} onChange={() => onChange(next)} {...props} />
}
