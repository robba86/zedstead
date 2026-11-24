import { forwardRef, useCallback, useRef } from 'react'
import { HiOutlineMagnifyingGlass, HiXMark } from 'react-icons/hi2'

// The × that empties a search box. Shown only while the box has text.
// `onClear` empties the box; the command palette uses this one too.
export function ClearSearchButton({ onClear, className = '' }) {
  return (
    <button
      type="button"
      className={`btn btn-icon search-clear${className ? ` ${className}` : ''}`}
      aria-label="Clear search"
      onClick={onClear}
    >
      <HiXMark aria-hidden="true" />
    </button>
  )
}

// Every search and filter box: an input with a magnifying glass in front, so
// it reads as a search at a glance, and a × at the end once it holds text.
// The props go to the <input>; `boxClassName` sizes the box around it (the box
// is the flex item, not the input).
const SearchInput = forwardRef(function SearchInput({ boxClassName = '', className = 'text-input', ...props }, ref) {
  const inputRef = useRef(null)
  const setRefs = useCallback(
    (node) => {
      inputRef.current = node
      if (typeof ref === 'function') ref(node)
      else if (ref) ref.current = node
    },
    [ref]
  )

  // The × goes through the input itself: set its value natively and fire an
  // `input` event, so React calls the caller's own onChange with a real event.
  // Every caller reads e.target.value, and none needs an onClear prop.
  function clear() {
    const input = inputRef.current
    if (!input) return
    const setValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set
    setValue.call(input, '')
    input.dispatchEvent(new Event('input', { bubbles: true }))
    input.focus()
  }

  const hasText = props.value != null && String(props.value) !== ''
  return (
    <label className={`search-field${boxClassName ? ` ${boxClassName}` : ''}`}>
      <HiOutlineMagnifyingGlass aria-hidden="true" />
      <input ref={setRefs} className={className} spellCheck={false} autoComplete="off" {...props} />
      {hasText && !props.disabled && <ClearSearchButton onClear={clear} />}
    </label>
  )
})

export default SearchInput
