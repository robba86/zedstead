import { useMemo, useState } from 'react'
import { HiPlus, HiXMark } from 'react-icons/hi2'
import CountBadge from './CountBadge.jsx'
import IconButton from './IconButton.jsx'
import SearchInput from './SearchInput.jsx'
import { useTrash } from '../trash.jsx'

// Editable string-list widget: add-input, per-item checkboxes, "select all",
// "remove selected", a remove button on each row, a filter box (shown once the
// list gets long), plus an optional quick-add pool (e.g. the ignorelist common
// base-building items) whose rows each carry an add button.
// Controlled — the parent owns `values`; every mutation calls
// onChange(nextArray). `initialFilter` seeds the filter box once (a deep link's
// ?q=). `trashPath` (trash.jsx) lets Remove selected be undone until the editor
// saves; `name` labels those entries.
// Nothing typed is left behind by a save: the add box adds its text when it
// loses focus. A pool item moves into the list only by its own button or Add
// all: ticked pool items that moved on a press outside the widget confused a
// phone tester (2026 Q4), so the pool has no ticks.
export default function MultiSelectList({
  values,
  onChange,
  label = null,
  addPlaceholder = 'Add classname…',
  pool = null,
  poolLabel = 'Quick add',
  initialFilter = '',
  trashPath,
  name,
}) {
  const trash = useTrash()
  const [filter, setFilter] = useState(initialFilter)
  const [checked, setChecked] = useState(() => new Set())
  const [addValue, setAddValue] = useState('')

  const needle = filter.trim().toLowerCase()
  const visible = useMemo(
    () => values.filter((v) => !needle || v.toLowerCase().includes(needle)),
    [values, needle],
  )
  const poolAvailable = useMemo(
    () => (pool ? pool.filter((p) => !values.includes(p)) : []),
    [pool, values],
  )

  function toggle(v) {
    setChecked((prev) => {
      const next = new Set(prev)
      if (next.has(v)) next.delete(v)
      else next.add(v)
      return next
    })
  }

  function add() {
    const v = addValue.trim()
    if (!v || values.includes(v)) {
      setAddValue('')
      return
    }
    onChange([...values, v])
    setAddValue('')
  }

  // Removes the names in `gone` (a Set), through the trash when the editor
  // has one, and drops them from the ticks.
  function remove(gone) {
    if (!gone.size) return
    if (trash && trashPath) {
      // Highest index first, so Restore all (latest first) puts back the
      // lowest first and every item lands at its own index again.
      values
        .map((v, i) => [v, i])
        .filter(([v]) => gone.has(v))
        .reverse()
        .forEach(([v, i]) => trash.add({ label: name || label || 'List item', path: trashPath, index: i, item: v }))
    }
    onChange(values.filter((v) => !gone.has(v)))
    setChecked((prev) => new Set([...prev].filter((v) => !gone.has(v))))
  }

  const allVisibleChecked = visible.length > 0 && visible.every((v) => checked.has(v))

  return (
    <div className="multi-select">
      {label && <div className="multi-select-label">{label}</div>}

      <div className="multi-select-add">
        <input
          className="text-input"
          placeholder={addPlaceholder}
          value={addValue}
          onChange={(e) => setAddValue(e.target.value)}
          onBlur={add}
          onKeyDown={(e) => e.key === 'Enter' && add()}
        />
        <button className="btn btn-primary btn-small" onClick={add}>
          Add
        </button>
      </div>

      {/* Also shown while a filter is set, or a seeded one would hide items
          with no box to clear it from. */}
      {(values.length > 8 || filter) && (
        <SearchInput
          boxClassName="search-field-wide multi-select-filter"
          placeholder="Filter…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
      )}

      <div className="multi-select-toolbar">
        <label className="multi-select-selall">
          <input
            type="checkbox"
            checked={allVisibleChecked}
            onChange={(e) => setChecked(e.target.checked ? new Set(visible) : new Set())}
          />
          Select all
        </label>
        <CountBadge
          parts={[
            { value: values.length, unit: values.length === 1 ? 'item' : 'items' },
            ...(checked.size > 0 ? [{ value: checked.size, unit: 'selected' }] : []),
          ]}
        />
        <button className="btn btn-danger btn-small" disabled={!checked.size} onClick={() => remove(checked)}>
          Remove selected
        </button>
      </div>

      <ul className="multi-select-list">
        {visible.length === 0 && <li className="text-muted">No items.</li>}
        {visible.map((v) => (
          <li key={v}>
            <label>
              <input type="checkbox" checked={checked.has(v)} onChange={() => toggle(v)} />
              <span>{v}</span>
            </label>
            <IconButton icon={HiXMark} label={`Remove ${v}`} danger onClick={() => remove(new Set([v]))} />
          </li>
        ))}
      </ul>

      {pool && poolAvailable.length > 0 && (
        <div className="multi-select-pool">
          <div className="multi-select-label">{poolLabel}</div>
          <ul className="multi-select-list">
            {poolAvailable.map((p) => (
              <li key={p}>
                <span>{p}</span>
                <IconButton icon={HiPlus} label={`Add ${p} to the list`} onClick={() => onChange([...values, p])} />
              </li>
            ))}
          </ul>
          <button className="btn btn-ghost btn-small" onClick={() => onChange([...values, ...poolAvailable])}>
            {`Add all (${poolAvailable.length})`}
          </button>
        </div>
      )}
    </div>
  )
}
