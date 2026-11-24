// Leaflet popup for one Expansion AI roaming location (AILocationSettings.json).
//
// A roaming location is where Expansion's roaming AI may spawn: a named circle
// with a type and an on/off flag. All four are editable here, plus the two
// actions the map can offer — copy this location somewhere else, or remove it.
//
// `Type` is a free text field on purpose: it is one of Expansion's own location
// kinds, zsm does not know the set, and a `<select>` of guesses would be worse
// than the string the file already has. That is also why a new location is
// always a copy of an existing one rather than a blank form.

import { Switch } from '../../../components/FlagSwitch.jsx'
import { useState } from 'react'
import { meApi } from '../api'
import { useToast } from '../../../components/Toast.jsx'
import { useConfirm } from '../../../components/useConfirm.jsx'

const HINTS = {
  name: 'Identifies the location in the file — Expansion builds it from the nearest classname.',
  type: "Expansion's own location kind (Village, Military, …). Copied as-is; Zedstead does not validate it.",
  radius: 'How far from the centre AI may roam, in metres. Drag the white edge handle to resize.',
  enabled: 'Off leaves the location in the file but stops AI roaming there.',
}

export default function ExpansionRoamingPopup({ location, onCopy, onClose }) {
  const [name, setName] = useState(location.name)
  const [type, setType] = useState(location.type || '')
  const [radius, setRadius] = useState(String(location.radius))
  const [enabled, setEnabled] = useState(Boolean(location.enabled))
  const [saving, setSaving] = useState(false)
  const pushToast = useToast()
  const confirm = useConfirm()

  async function save() {
    const value = parseFloat(radius)
    if (Number.isNaN(value)) {
      pushToast('Radius must be a number.', 'error')
      return
    }
    setSaving(true)
    try {
      await meApi.put(`/expansion/ai/locations/${location.index}`, {
        name: name.trim(),
        type: type.trim(),
        radius: value,
        enabled,
      })
      onClose()
    } catch (err) {
      pushToast(`Save failed: ${err.message}`, 'error')
    } finally {
      setSaving(false)
    }
  }

  async function remove() {
    const ok = await confirm({
      title: 'Delete roaming location?',
      message: `Delete "${location.name}"? AI will no longer roam there.`,
      confirmLabel: 'Delete',
      danger: true,
    })
    if (!ok) return
    try {
      await meApi.del(`/expansion/ai/locations/${location.index}`)
      onClose()
    } catch (err) {
      pushToast(`Delete failed: ${err.message}`, 'error')
    }
  }

  return (
    <div className="spawn-popup safezone-popup">
      <div className="popup-title safezone-title">{location.name}</div>
      <div className="popup-form">
        <div className="popup-field">
          <label
            className="popup-field-label"
            data-tooltip-id="app-tip"
            data-tooltip-content={HINTS.name}
          >
            Name
          </label>
          <input className="popup-input" value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="popup-field">
          <label
            className="popup-field-label"
            data-tooltip-id="app-tip"
            data-tooltip-content={HINTS.type}
          >
            Type
          </label>
          <input className="popup-input" value={type} onChange={(e) => setType(e.target.value)} />
        </div>
        <div className="popup-field">
          <label
            className="popup-field-label"
            data-tooltip-id="app-tip"
            data-tooltip-content={HINTS.radius}
          >
            Radius (metres)
          </label>
          <input
            className="popup-input"
            type="number"
            step="1"
            min="1"
            value={radius}
            onChange={(e) => setRadius(e.target.value)}
          />
        </div>
        <div className="popup-field">
          <label
            className="popup-field-label"
            data-tooltip-id="app-tip"
            data-tooltip-content={HINTS.enabled}
          >
            Enabled
          </label>
          <Switch label="Enabled" checked={enabled} onChange={setEnabled} />
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
        <button type="button" className="btn btn-ghost btn-small" onClick={onCopy}>
          Copy here…
        </button>
        <button type="button" className="btn btn-danger btn-small" onClick={remove}>
          Delete
        </button>
      </div>
    </div>
  )
}
