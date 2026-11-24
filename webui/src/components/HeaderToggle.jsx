import { tooltipAttrs } from './Tooltip.jsx'

// A file's master switch (BattlEye, Weather), first among its card's inputs:
// a full-size button that says the state, and a click flips it (saved with
// the form, like any field). The Presets page's weather card reuses it with a
// `note` of its own, since a click there saves at once.
export default function HeaderToggle({ field, value, onChange, note = 'Save writes it.', disabled = false }) {
  const on = !!value
  // The label may already say "Enable BattlEye"; the button names the thing.
  const name = field.label.replace(/^Enable\s+/i, '')
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      className={`btn ${on ? 'btn-good' : 'btn-ghost'}`}
      disabled={disabled}
      {...tooltipAttrs(
        `${field.help ? `${field.help}\n\n` : ''}Click to turn ${name} ${on ? 'off' : 'on'}; ${note}`,
        { keep: true }
      )}
      onClick={() => onChange(!on)}
    >
      {on ? `${name} on` : `Enable ${name}`}
    </button>
  )
}
