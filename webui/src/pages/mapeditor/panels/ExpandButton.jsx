// Expand / minimize: hides the app's nav sidebar, info bar and breadcrumbs (and
// a phone's top bar) so the editor fills the window, and puts them back.
//
// Deliberately NOT the Fullscreen API: this is a layout mode, so it survives a
// re-render, needs no user-gesture rules, keeps the browser chrome (and the
// URL, which is where the editor's state lives) visible, and cannot leave the
// page stuck in a state only the browser can exit. The actual hiding is one
// `me-fullscreen` class on <body> (see index.css) — the editor stays in normal
// flow, so every modal, toast and drawer keeps its stacking order.

import { useEditor } from '../state/MapEditorProvider'

export default function ExpandButton() {
  const { ui, dispatch } = useEditor()
  const expanded = ui.expanded

  return (
    <button
      type="button"
      className="me-expand-btn"
      aria-label={expanded ? 'Exit full window' : 'Expand to full window'}
      aria-pressed={expanded}
      data-tooltip-id="app-tip"
      data-tooltip-content={
        expanded ? 'Restore the app menu (Esc)' : 'Expand the editor to the whole window'
      }
      onClick={() => dispatch({ type: 'SET_EXPANDED', payload: !expanded })}
    >
      <svg
        width="13"
        height="13"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        {expanded ? (
          // arrows pointing in
          <>
            <polyline points="4 10 10 10 10 4" />
            <polyline points="20 14 14 14 14 20" />
            <line x1="14" y1="14" x2="21" y2="21" />
            <line x1="10" y1="10" x2="3" y2="3" />
          </>
        ) : (
          // arrows pointing out
          <>
            <polyline points="15 3 21 3 21 9" />
            <polyline points="9 21 3 21 3 15" />
            <line x1="21" y1="3" x2="14" y2="10" />
            <line x1="3" y1="21" x2="10" y2="14" />
          </>
        )}
      </svg>
    </button>
  )
}
