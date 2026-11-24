// Leaflet popup for a TraderPlus safe zone.
//
// The field names do not match the JSON, deliberately: TraderPlus stores a
// safe zone as a 2D top-down rectangle, so its "Y" is the game world's Z, and
// the display name lives under BI's misspelled "SafeZoneStatut" key. The
// tooltips say so rather than the labels lying about it.

import { useState } from 'react'
import { meApi } from '../api'
import { keepUntouched } from '../coords'
import { useToast } from '../../../components/Toast.jsx'
import { useConfirm } from '../../../components/useConfirm.jsx'

const HINTS = {
  name: 'Display name shown in safe-zone notifications. Must be unique across all SafeAreaLocation entries — duplicate names break the SZ notification system. The JSON key is BI’s misspelling "SafeZoneStatut" (kept as-is for compat).',
  x: 'Game-world X (west-east) at the safe zone center. Use izurvive.com to look up coordinates by clicking on the map.',
  z: "Game-world Z (south-north) at the safe zone center. Stored as 'Y' in the JSON because TraderPlus treats the SafeAreaLocation as a 2D top-down rectangle.",
  radius:
    'Distance from the X/Z center to the perimeter. Vanilla TraderPlus defaults: 80 m for general traders, 40 m for tighter military traders. Drag the white edge handle on the map to resize visually.',
  countdown:
    'Grace period after a player crosses the perimeter — safe-zone protections (no PvP, blocked actions) keep applying until this counts down to zero. The Leaving zone message is concatenated with this live timer. Default 15.',
}

export default function SafeZonePopup({ zone, onClose }) {
  const [name, setName] = useState(zone.name)
  const [x, setX] = useState(zone.game_x.toFixed(2))
  const [z, setZ] = useState(zone.game_z.toFixed(2))
  const [radius, setRadius] = useState(String(zone.radius))
  const [countdown, setCountdown] = useState(String(zone.countdown))
  const [saving, setSaving] = useState(false)
  const pushToast = useToast()
  const confirm = useConfirm()

  async function save() {
    const gameX = keepUntouched(x, zone.game_x, 2)
    const gameZ = keepUntouched(z, zone.game_z, 2)
    // SafeZone reads Radius as a float and Countdown as an int
    // (SafeZoneLocation.c); a fraction in Countdown is refused by the server.
    const radiusVal = parseFloat(radius)
    if ([gameX, gameZ, radiusVal].some(Number.isNaN)) {
      pushToast('Invalid values — X, Z, and Radius must be numbers.', 'error')
      return
    }
    setSaving(true)
    try {
      await meApi.put(`/safezones/${zone.index}`, {
        name: name.trim(),
        game_x: gameX,
        game_z: gameZ,
        radius: radiusVal,
        countdown: Number(countdown),
      })
    } catch (err) {
      pushToast(`Save failed: ${err.message}`, 'error')
    } finally {
      setSaving(false)
    }
  }

  async function remove() {
    const ok = await confirm({
      title: 'Delete safe zone?',
      message: `Remove "${zone.name}"?`,
      confirmLabel: 'Delete',
      danger: true,
    })
    if (!ok) return
    try {
      await meApi.del(`/safezones/${zone.index}`)
      onClose()
    } catch (err) {
      pushToast(`Delete failed: ${err.message}`, 'error')
    }
  }

  return (
    <div className="spawn-popup safezone-popup">
      <div className="popup-title safezone-title">{zone.name}</div>
      <div className="popup-form">
        <div className="popup-field">
          <label
            className="popup-field-label"
            data-tooltip-id="app-tip"
            data-tooltip-content={HINTS.name}
          >
            Zone Name
          </label>
          <input
            className="popup-input"
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </div>

        <div className="popup-separator" />
        <div className="popup-field">
          <label
            className="popup-field-label"
            data-tooltip-id="app-tip"
            data-tooltip-content={HINTS.x}
          >
            X
          </label>
          <input
            className="popup-input"
            type="number"
            step="any"
            value={x}
            onChange={(e) => setX(e.target.value)}
          />
        </div>
        <div className="popup-field">
          <label
            className="popup-field-label"
            data-tooltip-id="app-tip"
            data-tooltip-content={HINTS.z}
          >
            Z (Y in config)
          </label>
          <input
            className="popup-input"
            type="number"
            step="any"
            value={z}
            onChange={(e) => setZ(e.target.value)}
          />
        </div>
        <div className="popup-field">
          <label
            className="popup-field-label"
            data-tooltip-id="app-tip"
            data-tooltip-content={HINTS.radius}
          >
            Radius (meters)
          </label>
          <input
            className="popup-input"
            type="number"
            step="any"
            min="1"
            value={radius}
            onChange={(e) => setRadius(e.target.value)}
          />
        </div>
        <div className="popup-field">
          <label
            className="popup-field-label"
            data-tooltip-id="app-tip"
            data-tooltip-content={HINTS.countdown}
          >
            Countdown (seconds)
          </label>
          <input
            className="popup-input"
            type="number"
            step="1"
            min="0"
            value={countdown}
            onChange={(e) => setCountdown(e.target.value)}
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
