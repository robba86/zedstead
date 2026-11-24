// Leaflet popup for one player spawn position.
//
// Same portal mechanism as SpawnPopup — see the comment there. Coordinates are
// edited as game X/Z and saved through the /game endpoint, which is the one
// case where game coords are the user's own input rather than something the
// client derived.

import { useState } from 'react'
import { meApi } from '../api'
import { keepUntouched } from '../coords'
import { useToast } from '../../../components/Toast.jsx'
import { useConfirm } from '../../../components/useConfirm.jsx'
import { SECTION_COLORS } from '../leaflet/usePlayerSpawnLayer'

export default function PlayerSpawnPopup({ section, groupIndex, groupName, pos, onClose }) {
  const [x, setX] = useState(pos.game_x.toFixed(1))
  const [z, setZ] = useState(pos.game_z.toFixed(1))
  const [saving, setSaving] = useState(false)
  const pushToast = useToast()
  const confirm = useConfirm()

  const base = `/player-spawns/${encodeURIComponent(section)}/groups/${groupIndex}/positions/${
    pos.index
  }`

  async function save() {
    // Untouched, a field sends the loaded value, not its 0.1 rounding, or a
    // no-op Save moved the point.
    const xVal = keepUntouched(x, pos.game_x, 1)
    const zVal = keepUntouched(z, pos.game_z, 1)
    if (Number.isNaN(xVal) || Number.isNaN(zVal)) {
      pushToast('Invalid coordinates — X and Z must be numbers.', 'error')
      return
    }
    setSaving(true)
    try {
      await meApi.put(`${base}/game`, { x: xVal, z: zVal })
      onClose()
    } catch (err) {
      pushToast(`Save failed: ${err.message}`, 'error')
      setSaving(false)
    }
  }

  async function remove() {
    const ok = await confirm({
      title: 'Delete spawn position?',
      message: `Remove this position from "${groupName}"?`,
      confirmLabel: 'Delete',
      danger: true,
    })
    if (!ok) return
    try {
      await meApi.del(base)
      onClose()
    } catch (err) {
      pushToast(`Delete failed: ${err.message}`, 'error')
    }
  }

  return (
    <div className="spawn-popup player-spawn-popup">
      <div className="popup-title" style={{ color: SECTION_COLORS[section] || '#4caf50' }}>
        {section}: {groupName}
      </div>
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
        <button type="button" className="btn btn-danger btn-small" onClick={remove}>
          Delete
        </button>
      </div>
    </div>
  )
}
