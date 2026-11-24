// Box-selection status bar plus the bulk-delete action.
//
// Two bars in the vanilla editor (#selection-bar and #delete-bar) purely so
// the delete button could sit on its own red strip; kept as one component with
// two rows.

import { useEditor } from '../state/MapEditorProvider'

export default function SelectionBar() {
  const { selection, selectionCount } = useEditor()
  if (!selectionCount) return null

  return (
    <>
      <div className="me-selection-bar">
        <span>
          <strong>{selectionCount}</strong> points selected
        </span>
        <span className="me-selection-hint">
          Drag any selected marker to move all &middot; Shift+select to add &middot; Esc to
          deselect
        </span>
        <button type="button" className="me-selection-deselect" onClick={() => selection.clear()}>
          Deselect
        </button>
      </div>
      <div className="me-delete-bar">
        <button
          type="button"
          className="me-selection-delete"
          onClick={() => selection.deleteSelected()}
        >
          Delete selected
        </button>
      </div>
    </>
  )
}
