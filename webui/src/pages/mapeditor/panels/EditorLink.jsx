// The editor's ↗ "open the file that owns this" button.
//
// One component so the icon, the tooltip wiring and the class stay identical
// wherever it appears (event rows, event detail, trader section headers).

import { Link } from 'react-router-dom'

import { ArrowIcon } from '../../../components/LinkOut.jsx'

export default function EditorLink({ to, tip, label, children }) {
  return (
    <Link
      className={children ? 'me-link-btn me-link-btn-text' : 'me-link-btn'}
      to={to}
      data-tooltip-id="app-tip"
      data-tooltip-content={tip}
      aria-label={label}
    >
      {children}
      <ArrowIcon />
    </Link>
  )
}
