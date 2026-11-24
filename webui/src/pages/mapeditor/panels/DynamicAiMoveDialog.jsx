// "Move here…" on a Dynamic AI location: asked after the map click, every
// time, whether the spawn positions move with the
// trigger. Moving them keeps the location's layout; leaving them is what a
// drag of the trigger's grip does.
//
// A rendered ConfirmModal rather than useConfirm, because the answer is the
// checkbox, not only yes/no — the promise API forwards no children. Portalled
// to <body> like the editor's other dialogs, since `.me-editor` isolates its
// stacking context and the backdrop would sit under Leaflet's panes.

import { Switch } from '../../../components/FlagSwitch.jsx'
import { useState } from 'react'
import { createPortal } from 'react-dom'
import ConfirmModal from '../../../components/ConfirmModal.jsx'

export default function DynamicAiMoveDialog({ move, onConfirm, onCancel }) {
  const [carry, setCarry] = useState(true)
  const count = move.spawnCount
  return createPortal(
    <ConfirmModal
      open
      title={`Move ${move.name}?`}
      message="Its trigger moves to the point you clicked."
      confirmLabel="Move"
      onConfirm={() => onConfirm(carry)}
      onCancel={onCancel}
    >
      <div className="dry-run-toggle">
        <Switch checked={carry} onChange={setCarry}>
          Move its {count} spawn {count === 1 ? 'position' : 'positions'} along
        </Switch>
      </div>
      <p className="modal-message">
        Ticked, they keep their layout around the trigger. Unticked, they stay where they are.
      </p>
    </ConfirmModal>,
    document.body
  )
}
