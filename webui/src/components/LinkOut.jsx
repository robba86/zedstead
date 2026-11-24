// The one "open in editor" arrow (webui/DESIGN.md "Link out"). It sits once
// at the right end of a section header, drawn as a RowAction so every icon
// control on a list has one look. Build `to` with crossLinks.js
// (`editorLink`, `configLink`, `MAP_EDITOR_LINKS` …), never by hand.
// `ArrowIcon` is shared with the Map Editor's older `panels/EditorLink.jsx`.

import { Link } from 'react-router-dom'

import { rowActionClass } from './RowAction.jsx'
import { tooltipAttrs } from './Tooltip.jsx'

export function ArrowIcon() {
  return (
    <svg
      width="12"
      height="12"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M7 17 17 7" />
      <path d="M8 7h9v9" />
    </svg>
  )
}

// `tip` is a longer tooltip than the screen reader `label`, for example
// "Open TraderPlusGeneralConfig.json — the traders and their stock".
export default function LinkOut({ to, label, tip }) {
  return (
    <Link
      className={rowActionClass({ className: 'link-out' })}
      to={to}
      aria-label={label}
      {...tooltipAttrs(tip || label)}
    >
      <ArrowIcon />
    </Link>
  )
}
