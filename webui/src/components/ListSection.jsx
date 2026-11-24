// The one section header over a list (webui/DESIGN.md "Section header"):
// small caps, muted, one height. The CountBadge sits right after the title;
// `actions` (RowAction buttons) and `link` (a LinkOut) sit at the right end.
//
// `lead` is an optional mark before the title, such as a RowDot in the colour
// the map draws that section in.
// `count` is a number, or a list of `{ value, unit }` parts for CountBadge.
// A `foldable` section shows a FoldCaret and hides its children when closed.
// Pass `open` and `onOpenChange` to own the state; the Map Editor passes the
// pair from `useStoredOpen(key)`, never a URL key. Without them the section
// keeps its own state and starts at `defaultOpen`. A `sticky` header stays at
// the top of its scroll box, below `--list-sticky-top`, while the list scrolls.

import { useId, useState } from 'react'

import CountBadge from './CountBadge.jsx'
import FoldCaret from './FoldCaret.jsx'

export default function ListSection({
  title,
  lead,
  count,
  actions,
  link,
  foldable = false,
  sticky = false,
  open: openProp,
  onOpenChange,
  defaultOpen = true,
  className = '',
  children,
}) {
  const [ownOpen, setOwnOpen] = useState(defaultOpen)
  const open = openProp ?? ownOpen
  const setOpen = onOpenChange ?? setOwnOpen
  const bodyId = useId()
  const shown = !foldable || open
  const badge = Array.isArray(count) ? <CountBadge parts={count} /> : <CountBadge value={count} />
  return (
    <section className={['list-section', sticky && 'is-sticky', className].filter(Boolean).join(' ')}>
      <div className="list-section-head">
        {foldable ? (
          <button
            type="button"
            className="list-section-title list-section-toggle"
            aria-expanded={open}
            aria-controls={bodyId}
            onClick={() => setOpen(!open)}
          >
            <FoldCaret open={open} />
            {lead}
            <span>{title}</span>
            {badge}
          </button>
        ) : (
          <h3 className="list-section-title">
            {lead}
            <span>{title}</span>
            {badge}
          </h3>
        )}
        {(actions || link) && (
          <span className="list-section-actions">
            {actions}
            {link}
          </span>
        )}
      </div>
      {shown && children != null && children !== false && (
        <div className="list-section-body" id={bodyId}>
          {children}
        </div>
      )}
    </section>
  )
}
