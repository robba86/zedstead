import SearchInput from './SearchInput.jsx'

// Filter input + "Showing X of Y" counter, used by the config/settings/table
// views. Presentational — the parent owns the value and does the filtering
// (see fuzzy.js). Escape or the box's × clears, matching the map editor's search inputs.
export default function FilterBox({
  value,
  onChange,
  placeholder = 'Filter…',
  shown = null,
  total = null,
  label = 'items',
}) {
  const showCount = shown !== null && total !== null && value.trim() !== ''

  return (
    <div className="filter-box">
      <SearchInput
        type="search"
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Escape') {
            e.preventDefault()
            onChange('')
          }
        }}
      />
      {showCount && (
        <span className="hint">
          Showing {shown} of {total} {label}
        </span>
      )}
    </div>
  )
}
