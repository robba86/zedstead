// The one help line and empty state inside a panel or a list
// (webui/DESIGN.md "Help line" and "Empty state"): left-aligned, muted, one
// size. `link` is an optional `{ to, label, tip }` link at the end of the line,
// with `to` built by crossLinks.js. `warn` colours it as a warning, such as a
// setting that stops DayZ from reading the list. The top of a page uses
// PageIntro instead.

import { Link } from 'react-router-dom'

import { ArrowIcon } from './LinkOut.jsx'
import { tooltipAttrs } from './Tooltip.jsx'

export default function PanelNote({ children, link, warn = false, className = '' }) {
  return (
    <p className={['panel-note', warn && 'is-warn', className].filter(Boolean).join(' ')}>
      {children}
      {link && (
        <>
          {' '}
          <Link className="panel-note-link" to={link.to} {...tooltipAttrs(link.tip)}>
            {link.label}
            <ArrowIcon />
          </Link>
        </>
      )}
    </p>
  )
}
