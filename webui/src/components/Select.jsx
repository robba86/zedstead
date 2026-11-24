import { forwardRef } from 'react'
import ReactSelect, { components } from 'react-select'
import CreatableSelect from 'react-select/creatable'
import { HiChevronDown, HiXMark } from 'react-icons/hi2'
import { tooltipAttrs } from './Tooltip.jsx'
import DefaultPill from './DefaultPill.jsx'
import { useMediaQuery } from '../useMediaQuery.js'

// The app's one value picker: react-select, unstyled, so index.css decides the
// look ("Select (react-select)" there). It stands in for a native <select>.
//
// `options` is a list of strings/numbers, {value, label, description, disabled}
// objects, or groups {label, options: [...]}. `optionHelp` ({value: text}, a
// schema's `choice_help`) gives plain values their description. An option's
// meaning goes in its description, shown under it in the open list, never in
// `help` (webui/CLAUDE.md "Pickers"). `value` is the option's value
// (a string; for `multi`, an array of them) and onChange gets the same, never
// an event. A value that matches no option shows `placeholder`; a caller that
// must keep a hand-edited bad value visible puts it in `options` itself.
//
// `className` goes on the outer box, which IS the field (`text-input`,
// `text-input-narrow`, `popup-input`, …): the classes that sized the native
// select size this one. `help` is the tooltip (tooltipAttrs with keep) and
// `helpPlace` its side, for a picker whose open menu the tip would cover; `id`
// the input's id for a <label htmlFor>. `creatable` accepts typed text that is
// not an option; with `multi`, text typed and left without Enter is added
// when focus leaves (react-select clears it on blur, so a list item typed and
// never confirmed was missing from the save). A list of more than SEARCH_FROM
// options can be typed into. A list too long to render whole passes
// `onSearch` (hears the typed text) and `filterOption={null}`, and narrows
// `options` itself. `defaultHint` is the value the server uses when the file
// leaves the key out: that option reads "(default)" in the open list, and the
// closed field shows a "Default" pill before it while it is the one picked.
// The menu is portalled to <body>, so a table's or drawer's overflow never
// clips it, and placed `fixed`, so a scrolled drawer body does not shift it.

const SEARCH_FROM = 8

// On a touch screen a tap on a picker that already holds a value opens the
// list without the keyboard, which covered half the list. An empty picker
// still takes typing, and so do the ones that need it: a multi or creatable
// one, and a long list that narrows itself through `onSearch`.
const TOUCH_QUERY = '(hover: none)'

// The file's own value stays in a closed list even when it is not a valid
// one, so a hand-edited bad value is shown to be corrected instead of being
// silently swapped for the first option.
// `choices` may be plain values or {value, description} options.
export function withCurrent(choices, current) {
  const value = current == null ? '' : String(current)
  return choices.some((c) => String(c && typeof c === 'object' ? c.value : c) === value) ? choices : [value, ...choices]
}

// One option as react-select wants it: {value, label, description?}, the
// description from `help` ({value: text}) when the option brings none.
function toOption(o, help) {
  if (o && typeof o === 'object') {
    if (Array.isArray(o.options)) return { label: o.label, options: o.options.map((x) => toOption(x, help)) }
    const value = String(o.value)
    return { ...o, value, label: o.label ?? value, description: o.description ?? help?.[value] }
  }
  const value = String(o)
  return { value, label: value, description: help?.[value] }
}

// A file path as an option: the file name on the row, its folder under it in
// the small description text, so a long path never makes the open list scroll
// sideways. The closed field shows `shown` whole (`selectedLabel`), so two
// files that share a name stay told apart. `shown` is the path as the page
// names it, when that is shorter than the value it sends.
export function pathOption(path, shown = path) {
  const parts = String(shown).split(/[\\/]/).filter(Boolean)
  const name = parts.pop() ?? String(shown)
  return { value: String(path), label: name, description: parts.length ? `in ${parts.join('/')}` : undefined, selectedLabel: String(shown) }
}

function flat(options) {
  return options.flatMap((o) => (o.options ? o.options : [o]))
}

// The open list shows the label with its description under it; the closed
// field shows `selectedLabel` when the option has one (see pathOption).
// `isDefault` marks the server's default: "(default)" after its label in the
// list, and the "Default" pill before it in the closed field.
function formatOptionLabel(option, { context }, isDefault) {
  if (context !== 'menu') {
    const shown = option.selectedLabel ?? option.label
    if (!isDefault) return shown
    return (
      <>
        <DefaultPill className="rs-default-pill" />
        {shown}
      </>
    )
  }
  const label = isDefault ? `${option.label} (default)` : option.label
  if (!option.description) return label
  return (
    <span className="rs-option-text">
      {label}
      <span className="rs-option-description">{option.description}</span>
    </span>
  )
}

// The outer box carries the tooltip anchor: react-select passes no data-*
// prop through by itself.
function Container(props) {
  return <components.SelectContainer {...props} innerProps={{ ...props.innerProps, ...props.selectProps.containerAttrs }} />
}

function DropdownIndicator(props) {
  return (
    <components.DropdownIndicator {...props}>
      <HiChevronDown aria-hidden="true" />
    </components.DropdownIndicator>
  )
}

function MultiValueRemove(props) {
  return (
    <components.MultiValueRemove {...props}>
      <HiXMark aria-hidden="true" />
    </components.MultiValueRemove>
  )
}

const PARTS = { SelectContainer: Container, DropdownIndicator, MultiValueRemove, IndicatorSeparator: null }

const Select = forwardRef(function Select(
  {
    value,
    onChange,
    options = [],
    optionHelp,
    className = 'text-input',
    id,
    name,
    help,
    helpPlace,
    disabled = false,
    required = false,
    placeholder = 'Choose…',
    multi = false,
    creatable = false,
    searchable,
    clearable = false,
    filterOption,
    onSearch,
    defaultHint,
    'aria-label': ariaLabel,
  },
  ref,
) {
  const opts = options.map((o) => toOption(o, optionHelp))
  const all = flat(opts)
  const find = (v) => all.find((o) => o.value === String(v)) || (creatable && v !== '' ? toOption(v) : null)
  const selected = multi ? (Array.isArray(value) ? value : []).map(find).filter(Boolean) : find(value ?? '')
  const Picker = creatable ? CreatableSelect : ReactSelect
  const touch = useMediaQuery(TOUCH_QUERY)
  const keyboardFree = touch && !multi && !creatable && !onSearch && selected != null && value !== ''
  return (
    <Picker
      ref={ref}
      unstyled
      className={`rs ${className}`}
      classNamePrefix="rs"
      components={PARTS}
      containerAttrs={tooltipAttrs(help, { keep: true, place: helpPlace })}
      inputId={id}
      name={name}
      aria-label={ariaLabel}
      options={opts}
      value={selected}
      isMulti={multi}
      isDisabled={disabled}
      required={required}
      isClearable={clearable || (multi && selected.length > 1)}
      isSearchable={keyboardFree ? false : (searchable ?? (creatable || multi || all.length > SEARCH_FROM))}
      isOptionDisabled={(o) => !!o.disabled}
      placeholder={placeholder}
      formatOptionLabel={(o, meta) =>
        formatOptionLabel(o, meta, !multi && defaultHint != null && o.value === String(defaultHint))
      }
      formatCreateLabel={(text) => `Use "${text}"`}
      closeMenuOnSelect={!multi}
      menuPortalTarget={typeof document === 'undefined' ? null : document.body}
      menuPosition="fixed"
      menuPlacement="auto"
      noOptionsMessage={() => (creatable ? 'Type to add' : 'Nothing matches')}
      onChange={(next) => onChange(multi ? (next || []).map((o) => o.value) : next ? next.value : '')}
      {...(filterOption !== undefined && { filterOption })}
      onInputChange={
        multi && creatable
          ? (_text, meta) => {
              const typed = meta.action === 'input-blur' ? (meta.prevInputValue || '').trim() : ''
              const current = (Array.isArray(value) ? value : []).map(String)
              if (typed && !current.includes(typed)) onChange([...current, typed])
            }
          : onSearch
            ? (text, meta) => onSearch(meta.action === 'input-change' ? text : '')
            : undefined
      }
    />
  )
})

export default Select
