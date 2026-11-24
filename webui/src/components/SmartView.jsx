// The "Smart" view of the Types page and the Events table: each value as a
// sentence in plain units ("stays on the ground for 80 hours") instead of a
// bare number box. Smart and Raw edit the same row state, so a switch between
// them keeps unsaved edits. This file holds the parts both views share: the
// Smart/Raw switch with its remembered choice, the duration field, a list of
// flags as sentences, and the short summary a closed row shows.
import { useEffect, useMemo, useRef, useState } from 'react'
import { HiOutlinePencilSquare, HiOutlineXMark } from 'react-icons/hi2'
import Segmented from './Segmented.jsx'
import Select from './Select.jsx'
import UnitInput from './UnitInput.jsx'
import { Switch } from './FlagSwitch.jsx'

// The browser's choice, for every editor with a Smart view. About the browser,
// not a server, so no instanceKey (webui/CLAUDE.md "Routes").
const VIEW_KEY = 'zsm.editorView'

function readView() {
  try {
    return localStorage.getItem(VIEW_KEY) === 'raw' ? 'raw' : 'smart'
  } catch {
    return 'smart'
  }
}

// [view, setView]: 'smart' (the default) or 'raw', remembered per browser.
export function useEditorView() {
  const [view, setView] = useState(readView)
  function change(next) {
    setView(next)
    try {
      localStorage.setItem(VIEW_KEY, next)
    } catch {
      // Storage blocked: the choice lasts until the page reloads.
    }
  }
  return [view, change]
}

// The Guided/Fields switch above an editor's rows. Its values stay 'smart'
// and 'raw', the keys a browser saved; Raw is the drawer's file-text tab.
export function ViewSwitch({ view, onChange }) {
  return (
    <Segmented
      label="View"
      value={view}
      onChange={onChange}
      options={[
        { value: 'smart', label: 'Guided', tip: 'Each value as a sentence in hours and minutes.' },
        { value: 'raw', label: 'Fields', tip: 'The values as the file has them, one box each.' },
      ]}
    />
  )
}

// Units a duration can be shown in, largest first.
const UNITS = [
  { value: 86400, label: 'days', short: 'd' },
  { value: 3600, label: 'hours', short: 'h' },
  { value: 60, label: 'minutes', short: 'min' },
  { value: 1, label: 'seconds', short: 's' },
]

// The largest unit that shows *seconds* as a whole number (80 hours, not
// 3.33 days). Zero and a value that is not a number show in seconds.
function bestUnit(seconds) {
  return bestOf(UNITS, seconds)
}

// The same choice among *units*, whose smallest is the file's own unit (1).
function bestOf(units, amount) {
  const n = Number(amount)
  if (!Number.isFinite(n) || n === 0) return 1
  return (units.find((u) => n % u.value === 0) || units[units.length - 1]).value
}

// "80 h", "30 min", "at once" for 0: a duration in a summary line.
export function durationText(seconds) {
  const n = Number(seconds)
  if (!Number.isFinite(n) || String(seconds).trim() === '') return '?'
  if (n === 0) return 'at once'
  const unit = UNITS.find((u) => u.value === bestUnit(n))
  return `${n / unit.value} ${unit.short}`
}

// A duration the file keeps in seconds, typed in the unit the admin picks.
// `value` is the seconds as the row holds them (a string while typed);
// onChange gets the seconds as a string, or '' for an empty box, which the
// save then refuses. A typed decimal ("1.5" hours) is rounded to whole seconds
// unless `whole` is false (a float field such as cfgweather's times).
// `base` is the file's own unit in seconds: 60 for messages.xml, which keeps
// minutes. The picker then offers that unit and the larger ones, and `value`,
// `defaultHint` and onChange are all in the file's unit.
// `defaultHint` (seconds) is UnitInput's Default pill, shown in the unit
// picked, and `inputProps` go to the box (a tooltip). FormEditor uses it for
// every field whose unit is seconds.
export function DurationField({ value, onChange, disabled, label, whole = true, base = 1, defaultHint, inputProps }) {
  // Each unit as a count of the file's unit (hours = 60 when the file keeps minutes).
  const units = useMemo(
    () => UNITS.filter((u) => u.value >= base).map((u) => ({ ...u, value: u.value / base })),
    [base],
  )
  const bestUnit = (v) => bestOf(units, v)
  const hasHint = defaultHint != null && defaultHint !== ''
  const [unit, setUnit] = useState(() => bestUnit(String(value ?? '').trim() === '' && hasHint ? defaultHint : value))
  const [text, setText] = useState(() => show(value, unit))
  // The seconds this field last sent: a value from outside (Reset, a reload)
  // differs from it and resets the box and the unit.
  const sent = useRef(String(value))
  useEffect(() => {
    if (String(value) === sent.current) return
    sent.current = String(value)
    const u = bestUnit(value)
    setUnit(u)
    setText(show(value, u))
  }, [value])

  function type(next) {
    setText(next)
    const trimmed = next.trim()
    const n = Number(trimmed)
    // Text that is not a number goes on as typed, so the save names the field.
    const scaled = whole ? Math.round(n * unit) : Number((n * unit).toFixed(6))
    const seconds = trimmed === '' ? '' : Number.isFinite(n) ? String(scaled) : next
    sent.current = seconds
    onChange(seconds)
  }

  // One joined box (`inline-form-joined`): the unit is a picker that belongs
  // to the number, unlike a UnitInput's fixed unit inside the box.
  return (
    <span className="inline-form inline-form-joined">
      <UnitInput
        {...inputProps}
        className="text-input text-input-narrow"
        inputMode="decimal"
        aria-label={label}
        defaultHint={hasHint ? show(defaultHint, unit) : undefined}
        value={text}
        disabled={disabled}
        onChange={(e) => type(e.target.value)}
      />
      <Select
        aria-label={`${label}: unit`}
        className="text-input text-input-narrow"
        value={String(unit)}
        disabled={disabled}
        options={units.map((u) => ({ value: String(u.value), label: u.label }))}
        onChange={(v) => {
          // The box keeps its seconds: 80 hours becomes 4800 minutes.
          const u = Number(v)
          setUnit(u)
          const n = Number(value)
          if (Number.isFinite(n) && String(value).trim() !== '') setText(show(value, u))
        }}
      />
    </span>
  )
}

function show(seconds, unit) {
  const n = Number(seconds)
  if (String(seconds).trim() === '' || !Number.isFinite(n)) return String(seconds ?? '')
  return String(Math.round((n / unit) * 100) / 100)
}

// A part's flags as switches with sentence labels. `items` is
// [{ key, text }], `flags` the part as the row holds it ({key: 0|1}), `help`
// the payload's nested_help. A change sends the whole part back with the key
// set to 0 or 1, never true/false (webui/CLAUDE.md "Config editors").
export function FlagSentences({ items, flags, help, disabled, onChange }) {
  return (
    <ul className="step-list">
      {items.map(({ key, text }) => (
        <li key={key}>
          <Switch
            checked={Number(flags?.[key]) === 1}
            help={help?.[key]}
            disabled={disabled}
            onChange={(on) => onChange({ ...flags, [key]: on ? 1 : 0 })}
          >
            {text}
          </Switch>
        </li>
      ))}
    </ul>
  )
}

// A Smart row's summary cell: the summary is one button that opens the row's
// form. A pencil leads it while the row is closed and an X while it is open.
export function SummaryToggle({ summary, open, onToggle }) {
  const Icon = open ? HiOutlineXMark : HiOutlinePencilSquare
  return (
    <div className="table-nested-summary-cell">
      <button type="button" className="btn btn-ghost btn-small table-nested-summary" aria-expanded={open} onClick={onToggle}>
        <Icon aria-hidden="true" className="table-nested-summary-icon" />
        <span className="table-nested-summary-text">{summary}</span>
      </button>
    </div>
  )
}
