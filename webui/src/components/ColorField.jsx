import { useState } from 'react'
import { alphaPercent, colorNote, formatColor, parseColor, percentToAlpha, rgbDigits } from '../color.js'
import { tooltipAttrs } from './Tooltip.jsx'

const RGB6 = /^#?([0-9a-fA-F]{6})$/
const PERCENT = /^(\d{1,3})\s*%?$/
const ALPHA_TIP = 'Opacity in percent: 100 is solid, 0 is fully see-through.'

// A colour setting: a swatch that opens the browser's picker, a text box with
// the six red, green and blue digits, and a box with the alpha as a whole percent.
// `format` comes from color.js colorFormat. Every edit keeps the letter case
// and the form the value had in the file, and the tooltip says what that form
// is. While a box holds a half-typed value, nothing is sent. A value that does
// not read as a colour shows its own text, still editable, and a blank swatch.
export default function ColorField({ value, format, onChange, disabled = false, label, help }) {
  const parsed = parseColor(format, value)
  const [rgbDraft, setRgbDraft] = useState(null)
  const [alphaDraft, setAlphaDraft] = useState(null)
  const emit = (hex, alpha) => onChange(formatColor(format, hex, alpha, parsed?.upper))
  const tip = [help, colorNote(format)].filter(Boolean).join(' ')

  const onRgb = (text) => {
    if (!parsed) {
      onChange(text)
      return
    }
    setRgbDraft(text)
    const m = RGB6.exec(text.trim())
    if (m) emit(`#${m[1].toLowerCase()}`, parsed.alpha)
  }

  const onAlpha = (text) => {
    setAlphaDraft(text)
    const m = PERCENT.exec(text.trim())
    const percent = m ? Number(m[1]) : NaN
    if (percent <= 100) emit(parsed.hex, percentToAlpha(percent))
  }

  return (
    <span className="color-field">
      <input
        type="color"
        className="color-field-swatch"
        aria-label={`${label || 'Colour'} picker`}
        value={parsed?.hex || '#000000'}
        disabled={disabled || !parsed}
        style={parsed ? { opacity: Math.max(0.35, parsed.alpha / 255) } : undefined}
        onChange={(e) => {
          setRgbDraft(null)
          emit(e.target.value, parsed?.alpha ?? 255)
        }}
      />
      <input
        className="text-input text-input-narrow color-field-text"
        type="text"
        aria-label={label}
        {...tooltipAttrs(tip, { keep: true })}
        value={rgbDraft ?? (parsed ? rgbDigits(parsed) : value ?? '')}
        disabled={disabled}
        spellCheck={false}
        onChange={(e) => onRgb(e.target.value)}
        onBlur={() => setRgbDraft(null)}
      />
      {parsed && (
        <input
          className="text-input text-input-narrow color-field-alpha"
          type="text"
          aria-label={`${label || 'Colour'} opacity in percent`}
          {...tooltipAttrs(ALPHA_TIP)}
          value={alphaDraft ?? alphaPercent(parsed)}
          disabled={disabled}
          maxLength={4}
          inputMode="numeric"
          spellCheck={false}
          onChange={(e) => onAlpha(e.target.value)}
          onBlur={() => setAlphaDraft(null)}
        />
      )}
    </span>
  )
}
