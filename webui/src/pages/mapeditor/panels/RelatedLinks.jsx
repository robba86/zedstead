// "Related" block at the bottom of the editor sidebar: the pages a user
// typically wants next, varying with the active tab.
//
// Lives in the sidebar rather than on the breadcrumb row because full-window
// mode hides the app chrome entirely (index.css `.me-fullscreen`) — a link up
// there would vanish exactly when the editor is most in use.

import { Link } from 'react-router-dom'
import FoldCaret from '../../../components/FoldCaret.jsx'
import { useEditor } from '../state/MapEditorProvider'
import { LINKS, RELATED_BY_TAB, RELATED_COMMON } from '../../../crossLinks'

export default function RelatedLinks() {
  const { url, traderplus } = useEditor()

  const keys = [...(RELATED_BY_TAB[url.tab] || []), ...RELATED_COMMON]
  // TraderPlus files don't exist without the mod — linking to their editors
  // would land on an empty form.
  const entries = keys
    .map((key) => ({ key, ...LINKS[key] }))
    .filter((entry) => entry.to && (traderplus || !entry.traderplus))

  return (
    <details className="me-related">
      <summary>
        <FoldCaret />
        Related pages
      </summary>
      <ul className="me-related-list">
        {entries.map((entry) => (
          <li key={entry.key}>
            <Link to={entry.to} data-tooltip-id="app-tip" data-tooltip-content={entry.tip}>
              {entry.label}
            </Link>
          </li>
        ))}
      </ul>
    </details>
  )
}
