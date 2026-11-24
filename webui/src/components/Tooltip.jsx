import { Link } from 'react-router-dom'

// Thin wrapper over `react-tooltip` (floating-ui positioning + collision
// detection). Children get anchored to the shared <ReactTooltip id="app-tip">
// mounted once in Layout.jsx; behavior (delay, place, etc.) is configured on
// that instance. Renders children untouched when there is no `text`, so fields
// without help behave exactly as before.
//
// A second shared instance, `app-tip-rich`, renders React instead of a string —
// see `richTooltipAttrs` below for when to reach for it.
//
// `text` may contain newlines (the backend's field help has them); the bubble
// renders them via `white-space: pre-line` (see .app-tooltip in index.css).
/**
 * Anchor attributes for elements that can't be wrapped — spread onto an
 * `<input>` so tabbing into it opens the same bubble the label shows on hover
 * (focus/blur are wired on the shared instance in Layout.jsx).
 *
 * Deliberately no `aria-label`: it would override the field's visible label for
 * screen readers. react-tooltip handles the accessible description itself.
 *
 * `keep`: the bubble stays while the pointer moves into it, so its text can be
 * read to the end and selected (the "?" help tips). Without it a tip closes the
 * moment the pointer leaves its anchor and never sits in the way.
 *
 * `place`: the side the bubble prefers (react-tooltip's default is top). It
 * still flips when that side has no room.
 */
export function tooltipAttrs(text, { keep = false, place } = {}) {
  if (!text) return {}
  const attrs = { 'data-tooltip-id': keep ? 'app-tip-keep' : 'app-tip', 'data-tooltip-content': text }
  if (place) attrs['data-tooltip-place'] = place
  return attrs
}

// --- rich (markup) tooltips -------------------------------------------------
//
// The plain variant above hands react-tooltip a *string*, so a URL inside it is
// text — not something you can click. The rich variant anchors to a second
// shared instance (`app-tip-rich`, also in Layout.jsx) whose `render` turns a
// small JSON payload back into React, so a tip can carry real links. It works
// only because that instance is `clickable`: the bubble keeps pointer events
// and survives the pointer travelling into it.
//
// Payload — an array of sections, each with a title and a list of items:
//   { title, items: [{ label, detail, to, href }] }
//     `to`    in-app route, rendered as <Link> (no full page reload)
//     `href`  external URL, rendered as a new-tab <a>
//     neither plain text
// Sections without items are dropped, so an empty payload yields no attributes
// at all and the caller can test the result to decide whether to render its
// trigger.
export function richTooltipAttrs(sections) {
  const filled = (sections || []).filter((s) => s && s.items && s.items.length)
  return filled.length
    ? { 'data-tooltip-id': 'app-tip-rich', 'data-tooltip-content': JSON.stringify(filled) }
    : {}
}

/**
 * Content renderer for the shared rich instance (Layout.jsx's `render` prop).
 *
 * `content` is whatever `richTooltipAttrs` put in the data attribute. A payload
 * that fails to parse renders nothing rather than throwing — a malformed tip
 * must not take the page down with it.
 */
export function RichTooltipContent({ content }) {
  let sections
  try {
    sections = JSON.parse(content || '[]')
  } catch {
    return null
  }
  if (!Array.isArray(sections) || !sections.length) return null

  return (
    <div className="tip-rich">
      {sections.map((section, i) => (
        <div className="tip-section" key={i}>
          {section.title && <div className="tip-section-title">{section.title}</div>}
          <ul className="tip-list">
            {(section.items || []).map((item, j) => (
              <li className="tip-item" key={j}>
                {item.to ? (
                  <Link to={item.to}>{item.label}</Link>
                ) : item.href ? (
                  <a href={item.href} target="_blank" rel="noreferrer">
                    {item.label}
                  </a>
                ) : (
                  <span>{item.label}</span>
                )}
                {item.detail && <span className="tip-detail">{item.detail}</span>}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  )
}

export default function Tooltip({ text, children, className, keep = false }) {
  if (!text) return children

  return (
    <span
      className={`tooltip-trigger${className ? ` ${className}` : ''}`}
      {...tooltipAttrs(text, { keep })}
      aria-label={text}
    >
      {children}
    </span>
  )
}
