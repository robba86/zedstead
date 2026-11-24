import JsonTree from './JsonTree.jsx'
import { tooltipAttrs } from './Tooltip.jsx'
import { helpWithKey, humanizeKey } from '../fieldLabel.js'

// A row's nested parts (plans/76): one titled JsonTree per key of `nested`,
// rendered in the full-width row under a table row that TableEditor and the
// Types page open with a caret. `choices` is the payload's `nested_choices`,
// `templates` its `nested_templates`, `switches` its `nested_switches` (keys
// whose 0/1 values are drawn as switches), `help` its `nested_help` (what
// each key does, for the tooltips). `trashPath` is the owner's prefix
// for a removed entry (trash.jsx); each part adds its key. The parts sit in a
// grid: small ones side by side, a long one across. A part's title is its key
// as words, with the key in the tooltip (fieldLabel.js), or `titles[key]`
// where the Smart view names it in plain words (SmartView.jsx).
export default function NestedParts({ nested, choices, templates, switches, help, titles, disabled, onChange, trashPath }) {
  return (
    <div className="table-nested-parts">
      {Object.keys(nested).map((key) => (
        <div className={`table-nested-part${partIsWide(nested[key]) || switches?.includes(key) ? ' table-nested-part-wide' : ''}`} key={key}>
          <h3 {...tooltipAttrs(helpWithKey(help?.[key] || '', key))}>{titles?.[key] || humanizeKey(key)}</h3>
          <JsonTree
            value={nested[key]}
            name={key}
            choices={choices}
            templates={templates}
            switches={switches}
            help={help}
            trashPath={trashPath && [...trashPath, key]}
            disabled={disabled}
            onChange={(next) => onChange(key, next)}
          />
        </div>
      ))}
    </div>
  )
}

// A list of objects, or a long object, takes the whole width.
function partIsWide(v) {
  if (Array.isArray(v)) return v.some((x) => x && typeof x === 'object') || v.length > 6
  return !!v && typeof v === 'object' && Object.keys(v).length > 8
}
