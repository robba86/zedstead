// "Copy into a manual file and move": the question asked before a mod's spawn
// position is changed on the map.
//
// A mod's positions sit in its START/END MOD block, which every re-configure
// rebuilds from the mod's own file, so a change written there would go back.
// The server can copy the mod's cfgeventspawns file into a manual XML file
// first (the Mod XML page's Take over: the copy is applied instead of the
// mod's file) and write the change into the copy; one click does both.
//
// Which file is copied comes from the merge record
// (GET /spawns-copy-source). Only when the record does not name exactly one
// does the dialog list the mod's files for the admin to pick.
//
// `useCopyIntoManualFile()` returns `{ askCopy, copyDialog }`: render
// `copyDialog` once, and `await askCopy({ mod, eventName, action })`, which
// resolves to the `copy` value for `spawnPath` (a file path) or null when
// cancelled. Portalled to <body>: `.me-editor` isolates its stacking context.

import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import ConfirmModal from '../../components/ConfirmModal.jsx'
import Select, { pathOption } from '../../components/Select.jsx'
import { meApi } from './api'

const VERBS = { move: 'move', delete: 'delete', save: 'save' }

function CopyDialog({ ask, onDone }) {
  const { mod, eventName, action } = ask
  const verb = VERBS[action] || 'move'
  const [found, setFound] = useState(null) // {source, sources} | {error}
  const [picked, setPicked] = useState('')

  useEffect(() => {
    let live = true
    meApi
      .get(`/spawns-copy-source?mod=${encodeURIComponent(mod)}`)
      .then((data) => live && setFound(data))
      .catch((err) => live && setFound({ error: err.message }))
    return () => {
      live = false
    }
  }, [mod])

  const source = found?.source || picked
  const sources = found?.sources || []
  const needsPick = found && !found.error && !found.source

  let body
  if (!found) body = <p className="modal-message">Finding the file the merge took from {mod}…</p>
  else if (found.error) body = <p className="modal-message">Could not ask which file to copy: {found.error}</p>
  else if (!sources.length) {
    body = (
      <p className="modal-message">
        No cfgeventspawns file of {mod} was found to copy. Re-apply the mod's XML on Configs →
        Custom config, then try again.
      </p>
    )
  } else if (needsPick) {
    body = (
      <div className="modal-field">
        <label htmlFor="copy-source">The merge record does not say which of {mod}'s files this came from. Copy:</label>
        <Select
          id="copy-source"
          className="text-input"
          value={picked}
          placeholder="Choose a file"
          options={sources.map((f) => pathOption(f))}
          onChange={setPicked}
        />
      </div>
    )
  } else {
    body = (
      <p className="modal-message">
        Copies <code>{found.source}</code>.
      </p>
    )
  }

  return createPortal(
    <ConfirmModal
      open
      title={`Copy into a manual file and ${verb}?`}
      message={
        `This position of ${eventName} comes from ${mod}. A re-configure puts the mod's positions back, so ` +
        `a change made in the mod's block would be undone. The mod's file is copied into a manual XML file ` +
        `first, as Take over on Configs → Custom config does: your copy is applied in place of the ` +
        `mod's file, and the mod's later changes to that file stop arriving. Then the ${verb} is saved into your copy.`
      }
      confirmLabel={`Copy into a manual file and ${verb}`}
      confirmDisabled={!source}
      onConfirm={() => onDone(source)}
      onCancel={() => onDone(null)}
    >
      {body}
    </ConfirmModal>,
    document.body
  )
}

export function useCopyIntoManualFile() {
  const [ask, setAsk] = useState(null)
  const resolveRef = useRef(null)

  const askCopy = useCallback(
    (question) =>
      new Promise((resolve) => {
        resolveRef.current?.(null)
        resolveRef.current = resolve
        setAsk(question)
      }),
    []
  )

  const onDone = useCallback((value) => {
    resolveRef.current?.(value)
    resolveRef.current = null
    setAsk(null)
  }, [])

  const copyDialog = ask ? <CopyDialog key={`${ask.mod}:${ask.eventName}`} ask={ask} onDone={onDone} /> : null
  return { askCopy, copyDialog }
}
