// Unsaved edits on a paged table that saves one row at a time (Types,
// TraderPlus Prices).
//
// The edits are keyed by a row id that survives a reload, and a reload keeps
// them: paging, a filter, a search and another row's Save all re-fetch the
// rows, and each used to clear every edit on the page without a word. Only the
// row's own Save or Reset drops its edits (`dropRow`). An edited row the
// current page does not show is still unsaved, so the page says how many
// (`hiddenEditsNote`), and leaving the page asks (useUnsavedChanges).

import { useCallback, useState } from 'react'
import { useUnsavedChanges } from './useUnsavedChanges.js'

export function useRowEdits() {
  const [edits, setEdits] = useState({}) // row id -> {field: value}
  const setEdit = useCallback((id, key, value) => {
    setEdits((prev) => ({ ...prev, [id]: { ...prev[id], [key]: value } }))
  }, [])
  const dropRow = useCallback((id) => {
    setEdits(({ [id]: _dropped, ...rest }) => rest)
  }, [])
  // Not pageWide: the drawer's Raw and Versions tabs (BespokeEditorDrawer)
  // unmount the table, so a tab switch asks too.
  useUnsavedChanges(Object.keys(edits).length > 0)
  return { edits, setEdit, dropRow }
}

// "2 edited rows are not shown …", or null when every edited row is on screen.
// `editMaps` are the pages' edit objects (Types keeps the cells and the nested
// trees apart), `shownIds` the ids of the rows on screen.
export function hiddenEditsNote(editMaps, shownIds) {
  const shown = new Set(shownIds)
  const hidden = new Set(editMaps.flatMap((map) => Object.keys(map)).filter((id) => !shown.has(id)))
  if (!hidden.size) return null
  return `${hidden.size === 1 ? '1 edited row is' : `${hidden.size} edited rows are`} not shown by this page or filter. Their edits are kept; go back to the row to save it.`
}
