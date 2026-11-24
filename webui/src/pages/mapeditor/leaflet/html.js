// Markup the map editor hands to Leaflet, which puts it into the page with
// innerHTML: a divIcon's `html` (DivIcon.createIcon), a tooltip's text, the
// grid's labels.
//
// Build such markup with the `html` tag and every value interpolated into it
// is escaped, so a colour, a heading or a number read from a file can only
// ever be text inside an attribute or an element, whatever the backend did
// or did not clean. Markup the bundle owns (an icon's SVG body from
// markerIcons.js, a nested icon built with this same tag) passes unescaped only
// when wrapped in raw().
//
// tests/test_map_editor_tooltips.py fails on a template literal that builds
// markup with an interpolation anywhere in pages/mapeditor/ without this tag.

const ESCAPES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }

/** *text* as HTML that displays as exactly that text. */
export function escapeHtml(text) {
  return String(text ?? '').replace(/[&<>"']/g, (c) => ESCAPES[c])
}

class Raw {
  constructor(markup) {
    this.markup = String(markup)
  }
}

/** Markup the bundle owns, let through the `html` tag as it is. */
export function raw(markup) {
  return new Raw(markup)
}

/** Tagged template: the literal parts as written, every value escaped. */
export function html(strings, ...values) {
  return strings.reduce((out, part, i) => {
    const value = values[i - 1]
    return out + (value instanceof Raw ? value.markup : escapeHtml(value)) + part
  })
}
