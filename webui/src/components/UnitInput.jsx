// A text or number input with its unit ("seconds", "meters") in muted text at
// the right, inside the box. The field's `unit` comes from the backend
// (app/web/editors/_help.py::unit_attr). Without a unit it is the bare input.
// The unit is the input's description, so a screen reader reads it too.
//
// `defaultHint` is the value the server uses when the file leaves the key
// out. An empty box shows a "Default" pill at the left and the hint after it
// as the placeholder. A box that holds the default itself keeps the pill, and
// any other value takes it away (atDefault). A field with a hint always gets
// the wrapper, also while it holds another value: a tree that changed with the
// value would remount the input mid-typing.
import { useId } from 'react'
import DefaultPill, { atDefault } from './DefaultPill.jsx'

// The most characters of unit room the box keeps. A longer unit (a mod's
// currency name) is cut with an ellipsis (index.css .unit-input-unit), so it
// never runs over the value. A screen reader still reads the whole unit.
const MAX_UNIT_CH = 10

export default function UnitInput({ unit, defaultHint, ...inputProps }) {
  const id = useId()
  const hasHint = defaultHint != null && defaultHint !== ''
  if (!unit && !hasHint) return <input {...inputProps} />
  const pill = atDefault(inputProps.value, defaultHint)
  const className = ['unit-input', !unit && 'unit-input-no-unit', pill && 'unit-input-default']
    .filter(Boolean)
    .join(' ')
  const describedBy = [pill && `${id}-default`, unit && id].filter(Boolean).join(' ') || undefined
  return (
    <span className={className} style={{ '--unit-ch': unit ? Math.min(unit.length, MAX_UNIT_CH) : 0 }}>
      {pill && (
        <DefaultPill className="unit-input-pill" id={`${id}-default`} />
      )}
      <input
        {...inputProps}
        placeholder={hasHint ? String(defaultHint) : inputProps.placeholder}
        aria-describedby={describedBy}
      />
      {unit && (
        <span className="unit-input-unit" id={id}>
          {unit}
        </span>
      )}
    </span>
  )
}
