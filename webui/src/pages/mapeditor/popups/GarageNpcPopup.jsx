// Leaflet popup for a TraderPlus garage: the NPC and its parking spot, edited
// together because they are one record.

import { useState } from 'react'
import { meApi } from '../api'
import { keepUntouched } from '../coords'
import { useToast } from '../../../components/Toast.jsx'
import { useConfirm } from '../../../components/useConfirm.jsx'

const HINTS = {
  class_name:
    'Classname of the survivor model OR static object to use as the garage NPC (e.g. SurvivorM_Peter, Land_Garage_Office). Static objects skip the Clothes list — they spawn as the prop verbatim.',
  nx: 'NPC world X (west-east). Use izurvive.com to look up coordinates by clicking on the map.',
  ny: 'NPC vertical position (height). Often left at 0 — DayZ snaps NPCs to terrain. Override only if the NPC needs to stand on an elevated platform (e.g. ship deck, balcony).',
  nz: 'NPC world Z (south-north). Use izurvive.com to look up coordinates by clicking on the map.',
  orientation:
    'NPC facing direction (yaw, in degrees). 0 = facing north, 90 = east, 180 = south, 270 = west.',
  px: 'Vehicle spawn X (west-east). Place 5–15 m clear of any wall, prop, or terrain feature so the vehicle has room to deploy without colliding.',
  py: 'Vehicle spawn vertical position. Usually 0 — DayZ drops the vehicle to terrain at the parking position. Override only for elevated spawn points.',
  pz: 'Vehicle spawn Z (south-north). Place 5–15 m clear of any wall, prop, or terrain feature so the vehicle has room to deploy without colliding.',
  park_orientation:
    'Vehicle facing direction on deploy (yaw, in degrees). Point the vehicle the way it should drive away — 0 = north, 90 = east, 180 = south, 270 = west.',
}

export default function GarageNpcPopup({ npc, onClose }) {
  const [className, setClassName] = useState(npc.class_name)
  const [fields, setFields] = useState({
    nx: npc.game_x.toFixed(2),
    ny: npc.game_y.toFixed(2),
    nz: npc.game_z.toFixed(2),
    orientation: String(npc.orientation),
    px: npc.park_game_x.toFixed(2),
    py: npc.park_game_y.toFixed(2),
    pz: npc.park_game_z.toFixed(2),
    park_orientation: String(npc.park_orientation),
  })
  const [saving, setSaving] = useState(false)
  const pushToast = useToast()
  const confirm = useConfirm()

  const set = (key) => (e) => setFields((prev) => ({ ...prev, [key]: e.target.value }))

  const numberField = (key, label) => (
    <div key={key} className="popup-field">
      <label
        className="popup-field-label"
        data-tooltip-id="app-tip"
        data-tooltip-content={HINTS[key]}
      >
        {label}
      </label>
      <input
        className="popup-input"
        type="number"
        step="any"
        {...(key.endsWith('orientation') ? { min: -360, max: 360 } : {})}
        value={fields[key]}
        onChange={set(key)}
      />
    </div>
  )

  async function save() {
    const positions = {
      game_x: keepUntouched(fields.nx, npc.game_x, 2),
      game_y: keepUntouched(fields.ny, npc.game_y, 2),
      game_z: keepUntouched(fields.nz, npc.game_z, 2),
      park_game_x: keepUntouched(fields.px, npc.park_game_x, 2),
      park_game_y: keepUntouched(fields.py, npc.park_game_y, 2),
      park_game_z: keepUntouched(fields.pz, npc.park_game_z, 2),
    }
    if (Object.values(positions).some(Number.isNaN)) {
      pushToast('Invalid coordinates — all position fields must be numbers.', 'error')
      return
    }
    setSaving(true)
    try {
      await meApi.put(`/garage/${npc.index}`, {
        class_name: className.trim(),
        ...positions,
        orientation: parseFloat(fields.orientation),
        park_orientation: parseFloat(fields.park_orientation),
      })
    } catch (err) {
      pushToast(`Save failed: ${err.message}`, 'error')
    } finally {
      setSaving(false)
    }
  }

  async function remove() {
    const ok = await confirm({
      title: 'Delete garage NPC?',
      message: `Remove "${npc.class_name}" and its parking spot?`,
      confirmLabel: 'Delete',
      danger: true,
    })
    if (!ok) return
    try {
      await meApi.del(`/garage/${npc.index}`)
      onClose()
    } catch (err) {
      pushToast(`Delete failed: ${err.message}`, 'error')
    }
  }

  return (
    <div className="spawn-popup garage-popup">
      <div className="popup-title garage-title">{npc.class_name}</div>
      <div className="popup-form">
        <div className="popup-field">
          <label
            className="popup-field-label"
            data-tooltip-id="app-tip"
            data-tooltip-content={HINTS.class_name}
          >
            Class Name
          </label>
          <input
            className="popup-input"
            type="text"
            value={className}
            onChange={(e) => setClassName(e.target.value)}
          />
        </div>

        <div className="popup-separator" />
        <div className="popup-field-group">
          <label className="popup-field-label popup-group-label">NPC Position</label>
        </div>
        {numberField('nx', 'X')}
        {numberField('ny', 'Y (height)')}
        {numberField('nz', 'Z')}
        {numberField('orientation', 'Orientation')}

        <div className="popup-separator" />
        <div className="popup-field-group">
          <label className="popup-field-label popup-group-label">Parking Position</label>
        </div>
        {numberField('px', 'X')}
        {numberField('py', 'Y (height)')}
        {numberField('pz', 'Z')}
        {numberField('park_orientation', 'P. Orientation')}
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
