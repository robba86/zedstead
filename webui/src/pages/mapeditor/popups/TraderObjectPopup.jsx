// Leaflet popup for a TraderPlus trade object — a static prop placed around a
// trader. Decorative only: it has no stock and no Id.

import { useState } from 'react'
import { meApi } from '../api'
import { useToast } from '../../../components/Toast.jsx'
import { useConfirm } from '../../../components/useConfirm.jsx'

const HINTS = {
  object_name:
    'Classname of the static prop placed at the trader. Decorative only — sets the scene around the trader NPC (e.g. crates, vending machines, signs).',
  x: 'Object world X (west-east). Use izurvive.com to look up coordinates by clicking on the map.',
  y: 'Object vertical position (height). 0 = drop to terrain. Set above terrain for floating props (signs hung on walls, decorations on shelves).',
  z: 'Object world Z (south-north). Use izurvive.com to look up coordinates by clicking on the map.',
  orientation:
    'Object facing direction (yaw, in degrees). 0 = north, 90 = east, 180 = south, 270 = west.',
}

export default function TraderObjectPopup({ obj, onClose }) {
  const [objectName, setObjectName] = useState(obj.object_name)
  const [x, setX] = useState(obj.game_x.toFixed(2))
  const [y, setY] = useState(obj.game_y.toFixed(2))
  const [z, setZ] = useState(obj.game_z.toFixed(2))
  const [orientation, setOrientation] = useState(String(obj.orientation))
  const [saving, setSaving] = useState(false)
  const pushToast = useToast()
  const confirm = useConfirm()

  async function save() {
    const nums = { game_x: parseFloat(x), game_y: parseFloat(y), game_z: parseFloat(z) }
    if (Object.values(nums).some(Number.isNaN)) {
      pushToast('Invalid coordinates — X, Y, Z must be numbers.', 'error')
      return
    }
    setSaving(true)
    try {
      await meApi.put(`/trader-objects/${obj.index}`, {
        ...nums,
        orientation: parseFloat(orientation),
        object_name: objectName.trim(),
      })
    } catch (err) {
      pushToast(`Save failed: ${err.message}`, 'error')
    } finally {
      setSaving(false)
    }
  }

  async function remove() {
    const ok = await confirm({
      title: 'Delete trader object?',
      message: `Remove "${obj.object_name}"?`,
      confirmLabel: 'Delete',
      danger: true,
    })
    if (!ok) return
    try {
      await meApi.del(`/trader-objects/${obj.index}`)
      onClose()
    } catch (err) {
      pushToast(`Delete failed: ${err.message}`, 'error')
    }
  }

  return (
    <div className="spawn-popup trader-popup">
      <div className="popup-title trader-object-title">{obj.object_name}</div>
      <div className="popup-form">
        <div className="popup-field">
          <label
            className="popup-field-label"
            data-tooltip-id="app-tip"
            data-tooltip-content={HINTS.object_name}
          >
            Object Name
          </label>
          <input
            className="popup-input"
            type="text"
            value={objectName}
            onChange={(e) => setObjectName(e.target.value)}
          />
        </div>

        <div className="popup-separator" />
        {[
          ['X', x, setX, HINTS.x],
          ['Y (height)', y, setY, HINTS.y],
          ['Z', z, setZ, HINTS.z],
        ].map(([label, value, setter, hint]) => (
          <div key={label} className="popup-field">
            <label
              className="popup-field-label"
              data-tooltip-id="app-tip"
              data-tooltip-content={hint}
            >
              {label}
            </label>
            <input
              className="popup-input"
              type="number"
              step="any"
              value={value}
              onChange={(e) => setter(e.target.value)}
            />
          </div>
        ))}
        <div className="popup-field">
          <label
            className="popup-field-label"
            data-tooltip-id="app-tip"
            data-tooltip-content={HINTS.orientation}
          >
            Orientation
          </label>
          <input
            className="popup-input"
            type="number"
            step="any"
            min="-360"
            max="360"
            value={orientation}
            onChange={(e) => setOrientation(e.target.value)}
          />
        </div>
      </div>

      <div className="popup-actions">
        <button
          type="button"
          className="btn btn-primary btn-small"
          disabled={saving}
          onClick={save}
        >
          {saving ? 'Saving...' : 'Save'}
        </button>
        <button type="button" className="btn btn-danger btn-small" onClick={remove}>
          Delete
        </button>
      </div>
    </div>
  )
}
