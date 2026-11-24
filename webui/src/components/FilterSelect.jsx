// The list filters of a page, as a web shop's collection page shows them:
// `Filters` is the row with its "Filter" title, and each `FilterSelect` in it
// is one labelled picker on the app's Select. "All" is the first choice.
// `options` are plain values or {value, label, description}; a description
// shows under the choice in the open list. `value` is '' for All.
// `available` (optional): the values that still list rows with the page's
// other filters (the facets). Any other choice is greyed out in the list,
// except the active one, so a filter that later lists nothing can be changed.
// Greyed choices go last, so the ones that list rows come first (2026-11-15).
import { useId } from 'react'
import Select from './Select.jsx'

const NONE_LEFT = 'Nothing in the list matches this, with the other filters as they are.'

export function Filters({ children }) {
  return (
    <div className="filters" role="group" aria-label="Filter">
      <span className="filters-title">Filter</span>
      {children}
    </div>
  )
}

export default function FilterSelect({ label, value, options, onChange, available }) {
  const id = useId()
  const all = [{ value: '', label: 'All' }, ...options.map((o) => (typeof o === 'object' ? o : { value: o, label: o }))]
  const choices = all.map((o) => {
    const empty = o.value !== '' && o.value !== value && available != null && !available.has(o.value)
    if (!empty) return o
    return { ...o, disabled: true, description: [o.description, NONE_LEFT].filter(Boolean).join(' ') }
  })
  // A stable sort: each part keeps its own order.
  choices.sort((x, y) => Number(!!x.disabled) - Number(!!y.disabled))
  return (
    <label className="filter-select" htmlFor={id}>
      <span className="filter-select-label">{label}</span>
      <Select id={id} className="text-input-narrow" value={value} options={choices} onChange={onChange} />
    </label>
  )
}
