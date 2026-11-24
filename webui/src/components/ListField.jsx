import { useState } from 'react'
import { inputAttrsForType } from '../format.js'
import { tooltipAttrs } from './Tooltip.jsx'
import { useTrash } from '../trash.jsx'
import Select from './Select.jsx'

// A list of names or numbers, one small chip per item (plans/76 step 1). The
// field is {type:"list", item:"str"|"int"|"float", value:[...]} and the value
// goes back as an array: the server types each item and keeps the ones left
// as they were exactly as the file had them. Order is the file's and is kept;
// a repeated item is allowed, since some lists (monthly temperatures) repeat.
// Controlled: the parent owns `value`, every change calls onChange(nextArray).
// With `suggestions` (names the backend knows: a type's usages, an event's
// positions) the list is a creatable multi-select instead: pick names, or type
// one that is not offered. Those lists cannot repeat an item or edit one in
// place; a removed chip still goes to the trash. The add box is set apart (dashed, "+ Add…")
// so it does not read as one more item. `trashPath` is where the list sits
// in the editor's state (trash.jsx): a removed item can be put back until
// the editor saves or resets. Text typed into the add box is added when the
// box loses focus as well as on Enter or Add, so an item typed and never
// confirmed is not missing from the save; the creatable select does the same
// (Select.jsx).
export default function ListField({ value, item = 'str', onChange, disabled = false, help, label, suggestions, trashPath }) {
  const trash = useTrash()
  const items = Array.isArray(value) ? value : []
  const [draft, setDraft] = useState('')
  const attrs = inputAttrsForType(item)

  function setItem(i, next) {
    onChange(items.map((v, n) => (n === i ? next : v)))
  }

  function remove(i) {
    if (trash && trashPath) trash.add({ label: label || 'List item', path: trashPath, index: i, item: items[i] })
    onChange(items.filter((_, n) => n !== i))
  }

  function add() {
    const v = draft.trim()
    if (!v) return
    onChange([...items, v])
    setDraft('')
  }

  if (suggestions?.length) {
    const picked = items.map((v) => String(v ?? ''))
    return (
      <Select
        multi
        creatable
        className="text-input list-field-select"
        aria-label={label || 'List'}
        help={help}
        disabled={disabled}
        placeholder="+ Add…"
        value={picked}
        options={[...new Set([...picked, ...suggestions])]}
        onChange={(next) => {
          picked.forEach((v, i) => {
            if (!next.includes(v) && trash && trashPath) trash.add({ label: label || 'List item', path: trashPath, index: i, item: items[i] })
          })
          onChange(next.map((v) => items[picked.indexOf(v)] ?? v))
        }}
      />
    )
  }

  return (
    <div className="list-field" {...tooltipAttrs(help, { keep: true })}>
      {items.map((v, i) => (
        <span className="list-field-item" key={i}>
          <input
            className="text-input"
            {...attrs}
            size={Math.max(3, String(v ?? '').length)}
            aria-label={`${label || 'Item'} ${i + 1}`}
            value={v ?? ''}
            disabled={disabled}
            onChange={(e) => setItem(i, e.target.value)}
          />
          {!disabled && (
            <button
              type="button"
              className="list-field-remove"
              aria-label={`Remove item ${i + 1}`}
              {...tooltipAttrs('Remove')}
              onClick={() => remove(i)}
            >
              ×
            </button>
          )}
        </span>
      ))}
      {!items.length && <span className="list-field-empty">Empty</span>}
      {!disabled && (
        <span className="list-field-item list-field-add">
          <input
            className="text-input"
            {...attrs}
            size={8}
            placeholder="+ Add…"
            aria-label={`Add to ${label || 'list'}`}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={add}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault()
                add()
              }
            }}
          />
          {draft.trim() && (
            <button type="button" className="btn btn-primary btn-small" onClick={add}>
              Add
            </button>
          )}
        </span>
      )}
    </div>
  )
}
