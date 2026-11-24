// A number field with a known min and max, drawn as a slider with a tick at
// each step and a small number box beside it. The box still takes a typed
// value: a decimal such as speedhackDetection 2.5, or a blank that leaves the
// key out so the server uses its default. A 0.0-1.0 decimal reads as 0-100 %,
// while the file keeps the 0.0-1.0 value. FormEditor asks `sliderScale(field)`
// first: a field without a scale keeps the plain number box.
import { useEffect, useRef, useState } from 'react'
import UnitInput from './UnitInput.jsx'
import DefaultPill, { notInFileText } from './DefaultPill.jsx'
import { displayFloat } from '../format.js'
import './RangeField.css'

// More whole steps than this and the ticks run together on a phone.
const MAX_STEPS = 30
// About how many characters of tick labels fit under the slider.
const LABEL_BUDGET = 40
// A tick label's character width at its 11px font, and the least space
// between two labels, in pixels.
const LABEL_CHAR_PX = 6
const LABEL_GAP_PX = 8
// Label spacings that read as round numbers, tried before any other.
const ROUND_STEPS = [1, 2, 5, 10, 20, 25, 50]

// How a field maps onto a slider, or null when it keeps the plain box.
// `factor` turns the file's value into the slider's (100 for a percentage).
export function sliderScale(field) {
  const { min, max, type } = field
  if (type !== 'int' && type !== 'float') return null
  if (!Number.isFinite(min) || !Number.isFinite(max) || max <= min) return null
  if (type === 'float' && min === 0 && max === 1) {
    return { min: 0, max: 100, step: 1, tick: 10, factor: 100, unit: '%' }
  }
  // A whole percentage the file keeps as 0-100 (globals.xml's RestartSpawn).
  if (type === 'int' && field.unit === '%' && min === 0 && max === 100) {
    return { min: 0, max: 100, step: 1, tick: 10, factor: 1, unit: '%' }
  }
  if (Number.isInteger(min) && Number.isInteger(max) && max - min <= MAX_STEPS) {
    return { min, max, step: 1, tick: 1, factor: 1, unit: '' }
  }
  return null
}

// The tick values, and every how-many-th tick carries a label: the smallest
// spacing that divides the tick count (a round one first), so the last tick
// is always labelled.
// `px` is the scale's width on screen (0 before it is measured): labels that
// would touch at that width are thinned out too, so a narrow slider shows
// 0 %, 50 % and 100 % instead of six labels run together.
function scaleTicks(scale, px) {
  const gaps = Math.round((scale.max - scale.min) / scale.tick)
  const ticks = Array.from({ length: gaps + 1 }, (_, i) => scale.min + i * scale.tick)
  const chars = Math.max(...ticks.map((t) => `${t}${scale.unit}`.length))
  const width = chars + 2
  const tooTight = (every) => px > 0 && (px * every) / gaps < chars * LABEL_CHAR_PX + LABEL_GAP_PX
  const fits = (every) => gaps % every === 0 && (gaps / every + 1) * width <= LABEL_BUDGET && !tooTight(every)
  const steps = Array.from({ length: gaps }, (_, i) => i + 1)
  // A round step (0 10 20 30) before an odd one (0 6 12 18).
  const every = steps.find((e) => ROUND_STEPS.includes(e) && fits(e)) ?? steps.find(fits) ?? gaps
  return { ticks, gaps, every }
}

// An element's width in pixels, kept current while its column resizes.
function useWidth() {
  const ref = useRef(null)
  const [width, setWidth] = useState(0)
  useEffect(() => {
    const el = ref.current
    if (!el || typeof ResizeObserver === 'undefined') return undefined
    const ro = new ResizeObserver(([entry]) => setWidth(Math.round(entry.contentRect.width)))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  return [ref, width]
}

// A file value times `factor`, without float noise (0.07 * 100).
function toScale(value, factor) {
  return factor === 1 ? value : Number((value * factor).toFixed(2))
}

// A slider or box value back in the file's units, as the string the form holds.
function fromScale(value, factor) {
  return String(factor === 1 ? value : Number((value / factor).toFixed(6)))
}

// A 0.0-1.0 share in a table cell, typed as a percentage: 0.15 shows as
// 15 %, and the row keeps the file's 0.15. TableEditor draws a column with
// `share` (the backend's column flag) with it. The text typed is held while
// the box has focus, so "7." is not cut back to "7" mid-typing. An empty box
// sends '', and text that is no number goes on as typed for the save to name.
export function ShareInput({ value, onChange, ...inputProps }) {
  const [draft, setDraft] = useState(null)
  const raw = String(value ?? '')
  const n = Number(raw)
  const shown = draft ?? (raw.trim() === '' || !Number.isFinite(n) ? raw : String(toScale(n, 100)))
  return (
    <UnitInput
      unit="%"
      inputMode="decimal"
      {...inputProps}
      value={shown}
      onChange={(e) => {
        const text = e.target.value
        setDraft(text)
        const typed = Number(text)
        onChange(text.trim() === '' ? '' : Number.isFinite(typed) ? fromScale(typed, 100) : text)
      }}
      onBlur={() => setDraft(null)}
    />
  )
}

export default function RangeField({ field, scale, value, edited, onChange, tooltip }) {
  // The box's text while the admin types, so "5." is not cut to "5".
  const [draft, setDraft] = useState(null)
  const { factor } = scale
  const number = value === '' || value == null ? NaN : Number(value)
  const unset = !Number.isFinite(number)
  // The server's default, when the backend knows it (serverDZ.cfg's, from
  // Bohemia's wiki). A key the file leaves out puts the hollow knob on it.
  const def = field.default != null && Number.isFinite(Number(field.default)) ? Number(field.default) : null
  const clamp = (v) => Math.min(scale.max, Math.max(scale.min, v))
  const onSlider = clamp(toScale(unset ? (def ?? scale.min / factor) : number, factor))
  const defAt = def == null ? null : clamp(toScale(def, factor))
  const [scaleRef, scalePx] = useWidth()
  const { ticks, gaps, every } = scaleTicks(scale, scalePx)

  // An untouched float is shown short and never sent (format.displayFloat).
  const shown = field.type === 'float' && !edited ? displayFloat(value) : value
  const boxText = draft ?? (unset || factor === 1 ? (shown ?? '') : String(toScale(number, factor)))
  // The longest value the slider can give, in characters: the box never gets
  // narrower than this, so it does not change width while the knob moves.
  const boxCh = Math.max(String(scale.min).length, String(scale.max).length)
  // The box shows the bare default as its placeholder. The "Default" pill sits
  // in the scale, under the default's number.
  const placeholder = field.default != null ? String(toScale(Number(field.default), factor)) : undefined
  // No tick and no pill for a value that is only Bohemia's example.
  const defShare = defAt == null || field.default_is_example ? null : (defAt - scale.min) / (scale.max - scale.min)

  // The box always sits at the slider's right (RangeField.css).
  return (
    <span
      className={['range-field', unset && 'range-field-unset'].filter(Boolean).join(' ')}
      style={{ '--box-ch': boxCh }}
    >
      <span className="range-field-slider">
        <input
          type="range"
          min={scale.min}
          max={scale.max}
          step={scale.step}
          value={onSlider}
          aria-label={field.label}
          aria-valuetext={`${onSlider}${scale.unit}`}
          onChange={(e) => {
            setDraft(null)
            onChange(fromScale(Number(e.target.value), factor))
          }}
        />
        <span
          ref={scaleRef}
          className={['range-field-scale', defShare != null && 'range-field-scale-default'].filter(Boolean).join(' ')}
          aria-hidden="true"
        >
          {ticks.map((t, i) => (
            <span key={t} className="range-field-tick" style={{ left: `${(i / gaps) * 100}%` }}>
              {i % every === 0 && (
                <span className="range-field-label">
                  {t}
                  {scale.unit}
                </span>
              )}
            </span>
          ))}
          {/* Where the default sits, so a set value shows how far it is from it. */}
          {defShare != null && (
            <>
              <span className="range-field-default" style={{ left: `${defShare * 100}%` }} />
              {/* Always there, set value or not, so the field keeps its height while the knob moves. */}
              <DefaultPill className="range-field-default-pill" style={{ '--at': defShare }} />
            </>
          )}
        </span>
      </span>
      <UnitInput
        unit={scale.unit || field.unit}
        className="text-input range-field-box"
        type="number"
        inputMode={field.type === 'int' && factor === 1 ? 'numeric' : 'decimal'}
        step={field.type === 'int' && factor === 1 ? 1 : 'any'}
        min={scale.min}
        max={scale.max}
        aria-label={field.label}
        placeholder={placeholder}
        {...tooltip}
        value={boxText}
        onChange={(e) => {
          const text = e.target.value
          setDraft(text)
          if (text === '') onChange('')
          else if (Number.isFinite(Number(text))) onChange(factor === 1 ? text : fromScale(Number(text), factor))
        }}
        onBlur={() => setDraft(null)}
      />
      {/* The grid's second row, under the slider and the box. */}
      {unset && (
        <span className="range-field-note">
          {def != null
            ? `${notInFileText(field, `${toScale(def, factor)}${scale.unit}`)} Move the knob to set a value.`
            : 'Not in the file. Move the knob to set a value.'}
        </span>
      )}
    </span>
  )
}
