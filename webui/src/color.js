// Colour values in mod settings files, for ColorField.jsx. Three forms, each
// written back in the form it came in, with its alpha and its letter case:
//   "rrggbbaa"     Expansion's chat and HUD colours (a key ending in Color)
//   "0xAARRGGBB"   cfggameplay.json
//   signed int     ARGB packed into an int (Expansion Book's IconColor;
//                  the map layer's expansion_position_state._argb_to_hex)
// parseColor returns {hex: "#rrggbb", alpha: 0-255, format, upper} or null.

const HEX8 = /^[0-9a-fA-F]{8}$/
const HEX0X = /^0[xX][0-9a-fA-F]{8}$/

// The key decides first: it ends in Color or Colour, or in ColorStr like
// cfggameplay's hitDirectionIndicatorColorStr, which a plain "Color$" missed,
// so the 0xAARRGGBB form drew a text box. Then the value must read as a colour.
// A text colour may also carry the word inside the key (Expansion's
// StaminaBarColorHalf, StaminaBarColorLow): its 8-digit value decides then.
// A whole number counts only under a key that ends in the word, since any
// count would read as an ARGB int.
export function colorFormat(key, value) {
  const name = String(key || '')
  const endsInColor = /colou?r(str)?$/i.test(name)
  if (!endsInColor && !/colou?r/i.test(name)) return null
  if (typeof value === 'number') return endsInColor && Number.isInteger(value) ? 'argb-int' : null
  if (typeof value !== 'string') return null
  if (HEX0X.test(value)) return '0x-argb'
  if (HEX8.test(value)) return 'rgba-hex'
  return null
}

const pad = (n) => n.toString(16).padStart(2, '0')

export function parseColor(format, value) {
  let r, g, b, a
  if (format === 'rgba-hex') {
    if (!HEX8.test(String(value))) return null
    const n = parseInt(value, 16) >>> 0
    ;[r, g, b, a] = [(n >>> 24) & 255, (n >>> 16) & 255, (n >>> 8) & 255, n & 255]
  } else if (format === '0x-argb') {
    if (!HEX0X.test(String(value))) return null
    const n = parseInt(value.slice(2), 16) >>> 0
    ;[a, r, g, b] = [(n >>> 24) & 255, (n >>> 16) & 255, (n >>> 8) & 255, n & 255]
  } else if (format === 'argb-int') {
    const num = Number(value)
    if (!Number.isInteger(num)) return null
    const n = num >>> 0
    ;[a, r, g, b] = [(n >>> 24) & 255, (n >>> 16) & 255, (n >>> 8) & 255, n & 255]
  } else {
    return null
  }
  return { hex: `#${pad(r)}${pad(g)}${pad(b)}`, alpha: a, upper: /[A-F]/.test(String(value)) }
}

// The six digits the colour field shows, in the value's own letter case.
export function rgbDigits(parsed) {
  const rgb = parsed.hex.slice(1)
  return parsed.upper ? rgb.toUpperCase() : rgb
}

// The colour field's tooltip: how the file stores the value it hides.
const STORED_AS = {
  'rgba-hex': 'RRGGBBAA',
  '0x-argb': '0xAARRGGBB',
  'argb-int': 'a whole number (ARGB)',
}
export function colorNote(format) {
  const stored = STORED_AS[format]
  if (!stored) return ''
  return `Red, green and blue as six hex digits. The box beside it is the opacity in percent: 100 is solid, 0 is fully see-through. The file stores the colour as ${stored}, and Zedstead writes that form back on save.`
}

// The alpha box's text: the 0-255 alpha as a whole percent, 0-100.
export function alphaPercent(parsed) {
  return String(Math.round((parsed.alpha / 255) * 100))
}

// A whole percent 0-100 back to the 0-255 alpha (50 gives 128, hex 80).
export function percentToAlpha(percent) {
  return Math.round((percent * 255) / 100)
}

// The picker's "#rrggbb" back into the value's own form, alpha kept.
export function formatColor(format, hex, alpha, upper) {
  const rgb = hex.slice(1)
  const a = pad(alpha)
  const cased = (s) => (upper ? s.toUpperCase() : s.toLowerCase())
  if (format === 'rgba-hex') return cased(rgb + a)
  if (format === '0x-argb') return '0x' + cased(a + rgb)
  // argb-int: back to a signed 32-bit int, as Expansion writes it.
  return parseInt(a + rgb, 16) | 0
}
