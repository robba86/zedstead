// Contents of the Leaflet popup for a single spawn position.
//
// Rendered through createPortal into the element Leaflet was handed by
// bindPopup, so this is ordinary JSX with ordinary state instead of the old
// innerHTML template plus six manual addEventListener calls. Leaflet keeps
// owning the popup frame (auto-pan, tip, close button); React owns what is
// inside it.
//
// The caller keys this component on event+index, so switching markers remounts
// it and the form reseeds from the new position.

import { Switch } from '../../../components/FlagSwitch.jsx'
import { useState } from 'react'
import { meApi } from '../api'
import { keepUntouched } from '../coords'
import { useToast } from '../../../components/Toast.jsx'
import { isModOwner, ownerNote, spawnPath } from '../spawnOwner'
import SpawnIcon from '../icons/SpawnIcon.jsx'
import EventFileLinks from '../panels/EventFileLinks.jsx'

// A few events (StaticTrain, StaticSantaCrash, …) place their object at the
// height in the file's `y`. The map has no heights, so a move keeps it.
export const HEIGHT_KEPT =
  'The position moved but kept its height (y). Check it in Configs → Event Spawns: a wrong height puts the object in the air or under the ground.'

export default function SpawnPopup({ event, pos, askCopy, onClose, onOpenDetail }) {
  // Event-level metadata is only present when the event also exists in
  // events.xml; a cfgeventspawns-only entry has no nominal/active.
  const hasEventMeta = event.nominal != null
  // A mod's position is put back by the next re-configure, so a change to it
  // first asks to copy the mod's file into a manual file (askCopy), and the
  // change goes into the copy. The event's own settings (events.xml) are not
  // the mod's block and save as before.
  const fromMod = isModOwner(pos.owner)
  const note = ownerNote(pos.owner)

  const [x, setX] = useState(pos.game_x.toFixed(1))
  const [z, setZ] = useState(pos.game_z.toFixed(1))
  const [angle, setAngle] = useState(String(pos.angle))
  const [nominal, setNominal] = useState(hasEventMeta ? String(event.nominal) : '')
  const [active, setActive] = useState(Boolean(event.active))
  const [saving, setSaving] = useState(false)
  const pushToast = useToast()

  async function save() {
    const xVal = keepUntouched(x, pos.game_x, 1)
    const zVal = keepUntouched(z, pos.game_z, 1)
    const angleVal = parseFloat(angle)
    if (Number.isNaN(xVal) || Number.isNaN(zVal) || Number.isNaN(angleVal)) {
      pushToast('Invalid values — X, Z, and Angle must be numbers.', 'error')
      return
    }

    const eventUpdates = {}
    if (hasEventMeta) {
      const nominalVal = parseInt(nominal, 10)
      if (Number.isNaN(nominalVal) || nominalVal < 0) {
        pushToast('Nominal must be a non-negative integer.', 'error')
        return
      }
      if (nominalVal !== event.nominal) eventUpdates.nominal = nominalVal
      if (active !== Boolean(event.active)) eventUpdates.active = active
    }

    // Only a position that really changes needs the mod's file copied.
    const posChanged = xVal !== pos.game_x || zVal !== pos.game_z || angleVal !== pos.angle
    let copy
    if (fromMod && posChanged) {
      copy = await askCopy({ mod: pos.owner, eventName: event.name, action: 'save' })
      if (copy == null) return
    }

    setSaving(true)
    try {
      if (!fromMod || posChanged) {
        const moved = await meApi.put(spawnPath(event, pos.index, '/game', copy), {
          x: xVal,
          z: zVal,
          angle: angleVal,
        })
        if (moved?.height_kept) pushToast(HEIGHT_KEPT, 'warning')
      }
      if (Object.keys(eventUpdates).length) {
        await meApi.put(`/events/${encodeURIComponent(event.name)}`, eventUpdates)
      }
      onClose()
    } catch (err) {
      pushToast(`Save failed: ${err.message}`, 'error')
      setSaving(false)
    }
  }

  async function remove() {
    let copy
    if (fromMod) {
      copy = await askCopy({ mod: pos.owner, eventName: event.name, action: 'delete' })
      if (copy == null) return
    }
    try {
      await meApi.del(spawnPath(event, pos.index, '', copy))
      onClose()
    } catch (err) {
      pushToast(`Delete failed: ${err.message}`, 'error')
    }
  }

  return (
    <div className="spawn-popup">
      <div
        className="popup-title clickable"
        role="button"
        tabIndex={0}
        onClick={() => onOpenDetail(event.name)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') onOpenDetail(event.name)
        }}
      >
        <SpawnIcon name={event.name} />
        {event.name}
      </div>

      {note && <div className="popup-note">{note}</div>}

      <div className="popup-form">
        <div className="popup-field">
          <label className="popup-field-label">X</label>
          <input
            className="popup-input"
            type="number"
            step="any"
            value={x}
            onChange={(e) => setX(e.target.value)}
          />
        </div>
        <div className="popup-field">
          <label className="popup-field-label">Z</label>
          <input
            className="popup-input"
            type="number"
            step="any"
            value={z}
            onChange={(e) => setZ(e.target.value)}
          />
        </div>
        {pos.height != null && (
          <div className="popup-field">
            <label className="popup-field-label">Height</label>
            <span className="popup-value">{pos.height}</span>
          </div>
        )}
        <div className="popup-field">
          <label className="popup-field-label">Angle</label>
          <input
            className="popup-input"
            type="number"
            step="any"
            min="-1"
            max="360"
            value={angle}
            onChange={(e) => setAngle(e.target.value)}
          />
        </div>

        {hasEventMeta && (
          <>
            <div className="popup-separator" />
            <div className="popup-field">
              <label className="popup-field-label">Nominal</label>
              <input
                className="popup-input"
                type="number"
                step="1"
                min="0"
                value={nominal}
                onChange={(e) => setNominal(e.target.value)}
              />
            </div>
            <div className="popup-field">
              <label className="popup-field-label">Active</label>
              <Switch label="Active" checked={active} onChange={setActive} />
            </div>
          </>
        )}
      </div>

      <div className="popup-actions">
        <button
          type="button"
          className="btn btn-primary btn-small"
          disabled={saving}
          onClick={save}
        >
          {saving ? '…' : 'Save'}
        </button>
        <button
          type="button"
          className="btn btn-ghost btn-small"
          onClick={() => onOpenDetail(event.name)}
        >
          Edit
        </button>
        <button
          type="button"
          className="btn btn-danger btn-small"
          onClick={remove}
        >
          Delete
        </button>
      </div>

      <div className="popup-links">
        <EventFileLinks name={event.name} />
      </div>
    </div>
  )
}
