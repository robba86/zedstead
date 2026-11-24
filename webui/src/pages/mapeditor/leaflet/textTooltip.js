// Every map tooltip is plain text, and every one is bound through here.
//
// Leaflet puts a *string* tooltip into the page with innerHTML
// (DivOverlay._updateContent). Most of ours carry names zsm did not write:
// event names a mod's cfgeventspawns.xml merged into the mission, TraderPlus
// and garage names from the mod's own JSON, Expansion's markers, patrols and
// zones from its settings files. Escaping here shows such a name exactly as it
// reads, markup characters included, and keeps the newlines the tooltip CSS
// lays out with `white-space: pre-line`.
//
// tests/test_map_editor_tooltips.py fails on a `.bindTooltip(` anywhere else.

// With the extension, so the Node test in that file can import this module.
import { escapeHtml } from './html.js'

/** `layer.bindTooltip(text, options)`, with *text* shown as text. */
export function bindTextTooltip(layer, text, options) {
  return layer.bindTooltip(escapeHtml(text), options)
}
