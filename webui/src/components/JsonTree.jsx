// The editable tree for a JSON config value: nested objects and lists fold,
// scalars get a field by kind, and list entries can move, copy or go.

import CountBadge from './CountBadge.jsx'
import FoldCaret from './FoldCaret.jsx'
import { createContext, useContext, useState } from 'react'
import { HiOutlineArrowDown, HiOutlineArrowUp, HiOutlineDocumentDuplicate, HiOutlineTrash } from 'react-icons/hi2'
import ListField from './ListField.jsx'
import FlagSwitch, { Switch } from './FlagSwitch.jsx'
import ColorField from './ColorField.jsx'
import Select, { withCurrent } from './Select.jsx'
import { tooltipAttrs } from './Tooltip.jsx'
import { displayFloat, isFlagKey } from '../format.js'
import { colorFormat } from '../color.js'
import { helpWithKey, humanizeKey } from '../fieldLabel.js'
import { useTrash } from '../trash.jsx'

// Objects and lists of objects inside a JSON settings file (plans/76 step 2),
// rendered as a tree under the form's own fields. Controlled: the parent owns
// `value` and every edit calls onChange(next) with a new copy. The server fits
// what comes back to the shape the file already has (app/web/editors/_nested.py):
// numbers typed here arrive as text and are written as the file's own type.
//
// An object is a list of rows, a nested object or list folds open on a click.
// A list of objects is one folded entry per item with move, copy and remove;
// a new entry is a copy of one that exists, or the payload's template for
// that key (`templates`), never a blank guessed here, since nothing here knows
// which keys the mod needs. An empty list inside an entry with no template of
// its own borrows the first item of the same key in a sibling entry that has
// some (a patrol's empty `Units` from another patrol's), when that item is an
// object or a list; the server fits a new item the same way. Keys are never
// added or removed. A key is shown as words (fieldLabel.js humanizeKey), with
// the key as the file spells it in the tooltip.
//
// `choices` ({key: [options]}, optional) offers names for a key: a text value
// under that key is a <select> (the current value always kept in it), and a
// list of names under it suggests them in its boxes (an XML file's
// `nested_choices`, plans/76 step 3: an event's position, a type's usages).
// An option may be {value, description}: the description shows under it.
//
// `switches` (keys, optional; the payload's `nested_switches`) names objects
// whose 0/1 values are all flags (a type's <flags>): they are drawn as
// switches. Elsewhere a 0/1 under a key that reads like a flag is one too
// (format.js isFlagKey), and a colour key gets a picker (color.js). An object's
// rows are shown inputs first, then lists, then switches; that order is the
// display's only, the object sent back keeps the file's. `trail` is where a
// node sits under the tree's root, for the trash (trash.jsx): JsonTree's
// `trashPath` is the owner's prefix, and without it nothing goes to a trash.

const NAME_KEYS = ['Name', 'name', 'm_Name', 'DisplayName', 'ClassName', 'Classname', 'Type', 'Faction', 'type', 'preset', 'kind']

function isScalar(v) {
  return v === null || typeof v !== 'object'
}

// "int" | "float" | "str" for a list ListField can edit, else null. Read
// once per node (see useState below): an edited number is text until saved,
// and the list must not turn into another widget while it is being typed.
function scalarListItem(list) {
  if (!list.length) return 'str'
  if (list.every((v) => typeof v === 'string')) return 'str'
  if (list.every((v) => typeof v === 'number')) return list.every(Number.isInteger) ? 'int' : 'float'
  return null
}

function entryTitle(entry, index) {
  const key = NAME_KEYS.find((k) => typeof entry[k] === 'string' && entry[k])
  return key ? entry[key] : `Entry ${index + 1}`
}

function entryHint(entry) {
  return Object.entries(entry)
    .filter(([k, v]) => !NAME_KEYS.includes(k) && isScalar(v) && v !== '' && v !== null)
    .slice(0, 3)
    .map(([k, v]) => `${k} ${v}`)
    .join(' · ')
}

function count(v) {
  if (Array.isArray(v)) return v.length === 1 ? '1 entry' : `${v.length} entries`
  const n = Object.keys(v).length
  return n === 1 ? '1 key' : `${n} keys`
}

// What a scalar is drawn as, decided once per node: an edited number is
// text until saved, and a switch set to 0 must not turn into a number box.
function scalarKind(value, field, flag) {
  if (typeof value === 'boolean') return 'bool'
  if ((flag && (value === 0 || value === 1)) || isFlagKey(field, value)) return 'flag'
  if (colorFormat(field, value)) return 'color'
  if (typeof value === 'number') return 'number'
  return 'text'
}

// The shape of a row in an object, for sorting: inputs, lists, switches.
function rowRank(value, key, flag) {
  if (Array.isArray(value)) return 1
  if (isScalar(value)) {
    const k = scalarKind(value, key, flag)
    return k === 'bool' || k === 'flag' ? 2 : 0
  }
  return 1
}

// The payload's `nested_help` ({key: what it does}), for the tooltips of
// every key in the tree. Absent, a tooltip names only the key as filed.
const KeyHelp = createContext(null)

// A key's tooltip: what it does, then the file's own spelling. A list item's
// `field` is its list's key, and only a string names one.
function useKeyTip(field) {
  const help = useContext(KeyHelp)
  return typeof field === 'string' ? helpWithKey(help?.[field] || '', field) : undefined
}

// A row's or fold's name: the key as words, its help and the key in the tooltip.
function KeyName({ name }) {
  return <span {...tooltipAttrs(useKeyTip(name))}>{humanizeKey(name)}</span>
}

function Scalar({ value, onChange, disabled, label, options, field, flag }) {
  const [kind] = useState(() => scalarKind(value, field, flag))
  const [color] = useState(() => colorFormat(field, value))
  // A float as the file has it (0.05000000074505806) is shown short until
  // it is edited; untouched, nothing is sent and the file keeps its own.
  const [touched, setTouched] = useState(false)
  const tip = useKeyTip(field)
  if (kind === 'text' && options) {
    const current = value == null ? '' : String(value)
    return (
      <Select
        className="text-input text-input-narrow"
        aria-label={label}
        value={current}
        disabled={disabled}
        options={withCurrent(options, current)}
        onChange={onChange}
      />
    )
  }
  if (kind === 'bool') {
    return (
      <Switch label={label} checked={!!value} disabled={disabled} onChange={onChange} help={tip}>
        {typeof field === 'string' ? humanizeKey(field) : null}
      </Switch>
    )
  }
  if (kind === 'flag') {
    return (
      <FlagSwitch value={value} label={label} disabled={disabled} onChange={onChange} help={tip}>
        {typeof field === 'string' ? humanizeKey(field) : null}
      </FlagSwitch>
    )
  }
  if (kind === 'color') {
    return (
      <ColorField value={value} format={color} label={label} disabled={disabled} onChange={onChange} />
    )
  }
  return (
    <input
      className="text-input text-input-narrow"
      type={kind === 'number' ? 'number' : 'text'}
      step={kind === 'number' ? 'any' : undefined}
      aria-label={label}
      value={(touched ? value : displayFloat(value)) ?? ''}
      disabled={disabled}
      onChange={(e) => {
        setTouched(true)
        onChange(e.target.value)
      }}
    />
  )
}

// A list long enough to want the whole row rather than one column.
function isWideList(v) {
  return Array.isArray(v) && v.every(isScalar) && (v.length > 6 || v.join(' ').length > 80)
}

function ObjectNode({ value, onChange, disabled, path, field, trail, choices, templates, switches }) {
  const set = (key, next) => onChange({ ...value, [key]: next })
  const flags = !!switches?.includes(field)
  // Sorted once: a row must not jump to another place while it is edited.
  const [order] = useState(() =>
    Object.keys(value)
      .map((k, i) => [k, i])
      .sort(([a, i], [b, j]) => rowRank(value[a], a, flags) - rowRank(value[b], b, flags) || i - j)
      .map(([k]) => k)
  )
  const keys = [...order.filter((k) => k in value), ...Object.keys(value).filter((k) => !order.includes(k))]
  if (!keys.length) return <span className="list-field-empty">Empty</span>
  const child = (key) => (
    <Node
      value={value[key]}
      onChange={(n) => set(key, n)}
      disabled={disabled}
      path={`${path}.${key}`}
      field={key}
      trail={trail && [...trail, key]}
      choices={choices}
      templates={templates}
      switches={switches}
      flag={flags}
    />
  )
  return (
    <ul className={`step-list json-rows${flags ? ' json-rows-flags' : ''}`}>
      {keys.map((key) =>
        isFolded(value[key]) ? (
          <li key={key} className="json-row-wide">
            {/* One fold alone is opened: a click to reach the only thing there is. */}
            <details className="json-fold" open={keys.length === 1 || undefined}>
              <summary>
                <FoldCaret />
                <KeyName name={key} /> <CountBadge value={count(value[key])} />
              </summary>
              {child(key)}
            </details>
          </li>
        ) : (
          <li key={key} className={isWideList(value[key]) ? 'json-row-wide' : undefined}>
            {/* A switch carries its key as its own label. */}
            {rowRank(value[key], key, flags) !== 2 && <KeyName name={key} />}
            {child(key)}
          </li>
        )
      )}
    </ul>
  )
}

// A value that gets its own fold rather than a row: any object, and any list
// that ListField cannot edit.
function isFolded(v) {
  if (isScalar(v)) return false
  if (!Array.isArray(v)) return true
  return v.some((item) => !isScalar(item)) || scalarListItem(v) === null
}

function EntryControls({ list, index, onChange, disabled, trail, label }) {
  const trash = useTrash()
  if (disabled) return null
  const move = (to) => {
    const next = [...list]
    const [item] = next.splice(index, 1)
    next.splice(to, 0, item)
    onChange(next)
  }
  const remove = () => {
    if (trash && trail) trash.add({ label, path: trail, index, item: list[index] })
    onChange(list.filter((_, n) => n !== index))
  }
  // A button inside <summary> would also fold the entry open or shut.
  const act = (fn) => (e) => {
    e.preventDefault()
    fn()
  }
  const button = (tip, aria, icon, fn, opts = {}) => (
    <button
      type="button"
      className={`btn btn-icon${opts.danger ? ' btn-icon-danger' : ''}`}
      aria-label={aria}
      {...tooltipAttrs(tip)}
      disabled={opts.disabled}
      onClick={act(fn)}
    >
      {icon}
    </button>
  )
  return (
    <span className="json-entry-actions">
      {button('Move up', `Move entry ${index + 1} up`, <HiOutlineArrowUp />, () => move(index - 1), { disabled: index === 0 })}
      {button('Move down', `Move entry ${index + 1} down`, <HiOutlineArrowDown />, () => move(index + 1), {
        disabled: index === list.length - 1,
      })}
      {button('Add a copy of this entry below it', `Copy entry ${index + 1}`, <HiOutlineDocumentDuplicate />, () =>
        onChange([...list.slice(0, index + 1), structuredClone(list[index]), ...list.slice(index + 1)])
      )}
      {button('Remove (restore it from Removed until you save)', `Remove entry ${index + 1}`, <HiOutlineTrash />, remove, {
        danger: true,
      })}
    </span>
  )
}

// {key: first item} of each list of objects or lists some entry of `list`
// holds items in: what an empty list under the same key in another entry
// borrows. A list of names is left out; ListField already grows it.
function borrowedTemplates(list) {
  const out = {}
  for (const entry of list) {
    if (!entry || typeof entry !== 'object' || Array.isArray(entry)) continue
    for (const [k, v] of Object.entries(entry)) {
      if (!(k in out) && Array.isArray(v) && v.length && !isScalar(v[0])) out[k] = v[0]
    }
  }
  return out
}

function ArrayNode({ value, onChange, disabled, path, field, trail, choices, templates, switches }) {
  const [item] = useState(() => scalarListItem(value))
  // `templates[field]`: what a new entry of this list of objects looks like
  // (the payload's `nested_templates`). Without it an empty list could only be
  // read as a list of names, and a list of objects had no way to grow but Copy.
  // A template of objects or lists says nothing about a list of numbers under
  // the same key (a position's [x, y, z] inside a list of positions).
  const given = templates?.[field]
  const template = given !== undefined && !isScalar(given) && value.length && value.every(isScalar) ? undefined : given
  // Each entry's own lists borrow from their siblings; an explicit template wins.
  const inner = value.some((v) => v && typeof v === 'object' && !Array.isArray(v))
    ? { ...borrowedTemplates(value), ...templates }
    : templates
  const add = !disabled && template && (
    <button
      type="button"
      className="btn btn-ghost btn-small json-add"
      onClick={() => onChange([...value, structuredClone(template)])}
    >
      Add
    </button>
  )
  if (!template && item && value.every(isScalar)) {
    return (
      <ListField
        value={value}
        item={item}
        label={path}
        onChange={onChange}
        disabled={disabled}
        trashPath={trail}
        suggestions={item === 'str' ? choices?.[field] : undefined}
      />
    )
  }
  if (!value.length) {
    return (
      <span className="json-empty">
        <span className="list-field-empty">Empty</span>
        {add}
      </span>
    )
  }
  const set = (i, next) => onChange(value.map((v, n) => (n === i ? next : v)))
  return (
    <div className="json-list">
      {value.map((entry, i) =>
        entry && typeof entry === 'object' && !Array.isArray(entry) ? (
          // The first entry starts open, so a list shows what its entries hold.
          <details className="json-fold json-entry" key={i} open={i === 0 || undefined}>
            <summary>
              <FoldCaret />
              <span className="json-entry-title">{entryTitle(entry, i)}</span>
              <span className="json-count">{entryHint(entry)}</span>
              <EntryControls list={value} index={i} onChange={onChange} disabled={disabled} trail={trail} label={`${path}: ${entryTitle(entry, i)}`} />
            </summary>
            <Node value={entry} onChange={(n) => set(i, n)} disabled={disabled} path={`${path}[${i + 1}]`} trail={trail && [...trail, i]} choices={choices} templates={inner} switches={switches} />
          </details>
        ) : (
          <div className="json-entry json-entry-flat" key={i}>
            <Node value={entry} onChange={(n) => set(i, n)} disabled={disabled} path={`${path}[${i + 1}]`} field={field} trail={trail && [...trail, i]} choices={choices} templates={templates} switches={switches} />
            <EntryControls list={value} index={i} onChange={onChange} disabled={disabled} trail={trail} label={`${path} ${i + 1}`} />
          </div>
        )
      )}
      {add}
    </div>
  )
}

// `field` is the key the value sits under (the list's key for a list item).
// `flag` says the value sits in a `switches` object.
function Node({ value, onChange, disabled, path, field, trail, choices, templates, switches, flag }) {
  const props = { onChange, disabled, path, field, trail, choices, templates, switches }
  if (Array.isArray(value)) return <ArrayNode value={value} {...props} />
  if (value && typeof value === 'object') return <ObjectNode value={value} {...props} />
  return <Scalar value={value} onChange={onChange} disabled={disabled} label={path} options={choices?.[field]} field={field} flag={flag} />
}

// `help` is the payload's `nested_help` (optional): what each key does.
export default function JsonTree({ value, onChange, disabled = false, name, choices, templates, switches, help, trashPath }) {
  return (
    <KeyHelp.Provider value={help ?? null}>
    <div className="json-tree">
      <Node
        value={value}
        onChange={onChange}
        disabled={disabled}
        path={name}
        field={name}
        trail={trashPath}
        choices={choices}
        templates={templates}
        switches={switches}
      />
    </div>
    </KeyHelp.Provider>
  )
}
