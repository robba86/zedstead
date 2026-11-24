// Leaflet popup for one underground trigger (cfgundergroundtriggers.json).
//
// Two things this popup exists to say, because neither is visible on the map:
//
// * **The type is derived, not chosen.** Outer / transitional / inner follows
//   from the breadcrumbs and the eye accommodation, so it is shown as a read-
//   only line rather than a control. Changing the accommodation of a trigger
//   with no breadcrumbs is what moves it between outer and inner, and the line
//   updates when the save comes back.
// * **Height is not on the map.** The map is flat and Y is altitude, so the
//   height of the box and the height of its centre live here. A drag on the map
//   only ever writes X and Z.
//
// There is no delete and no add. Triggers pair with geometry built into the
// terrain — a box with no tunnel under it does nothing, and a tunnel with no
// box is pitch dark — so creating one from the map is a promise zsm cannot
// keep. Editing the ones the terrain already ships is the useful half.

import { useState } from 'react'
import { meApi } from '../api'
import { useToast } from '../../../components/Toast.jsx'

const HINTS = {
  comment: "The file's only human label. Bohemia fills it in on Sakhal and leaves it out elsewhere.",
  eye: 'How much a player inside can see without a flashlight: 1 is normal vision, 0 is full darkness.',
  size: 'The box, in metres along X (east-west), Y (height) and Z (north-south).',
  height: 'Altitude of the box centre. Dragging on the map never changes it — the map is flat.',
  yaw: 'Rotation in degrees around the vertical axis. Pitch and roll are shown but not editable here: they tilt the box out of the ground plane, which a top-down map cannot draw.',
}

const TYPE_EXPLAINED = {
  outer: 'Outer — just outside the entrance, where nothing has dimmed yet.',
  transitional: 'Transitional — its breadcrumbs fade the darkness in and out.',
  inner: 'Inner — deep underground, at a constant darkness.',
}

export default function UndergroundTriggerPopup({ trigger, onClose }) {
  const [comment, setComment] = useState(trigger.name === `Trigger ${trigger.index}` ? '' : trigger.name)
  const [eye, setEye] = useState(String(trigger.eye_accommodation))
  const [sizeX, setSizeX] = useState(String(trigger.size_x))
  const [sizeY, setSizeY] = useState(String(trigger.size_y))
  const [sizeZ, setSizeZ] = useState(String(trigger.size_z))
  const [height, setHeight] = useState(String(trigger.game_y))
  const [yaw, setYaw] = useState(String(trigger.yaw))
  const [saving, setSaving] = useState(false)
  const pushToast = useToast()

  async function save() {
    const numbers = { eye, sizeX, sizeY, sizeZ, height, yaw }
    for (const [field, raw] of Object.entries(numbers)) {
      // Infinity parses too, and is not JSON.
      if (!Number.isFinite(parseFloat(raw))) {
        pushToast(`${field} must be a number.`, 'error')
        return
      }
    }
    // Only what was changed. The fields show the file's values rounded to two
    // decimals, so sending them all back rewrote numbers nobody touched
    // (533.460144 became 533.46) and added an empty "Comment".
    const changed = (raw, shown) => parseFloat(raw) !== shown
    const body = {}
    const initialComment = trigger.name === `Trigger ${trigger.index}` ? '' : trigger.name
    if (comment.trim() !== initialComment) body.comment = comment.trim()
    if (changed(eye, trigger.eye_accommodation)) body.eye_accommodation = parseFloat(eye)
    if (changed(sizeX, trigger.size_x) || changed(sizeY, trigger.size_y) || changed(sizeZ, trigger.size_z)) {
      body.size = [parseFloat(sizeX), parseFloat(sizeY), parseFloat(sizeZ)]
    }
    if (changed(yaw, trigger.yaw)) body.orientation = [parseFloat(yaw), trigger.pitch, trigger.roll]
    if (changed(height, trigger.game_y)) body.game_y = parseFloat(height)
    if (Object.keys(body).length === 0) {
      onClose()
      return
    }
    setSaving(true)
    try {
      await meApi.put(`/underground-triggers/${trigger.index}`, body)
      onClose()
    } catch (err) {
      pushToast(`Save failed: ${err.message}`, 'error')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="spawn-popup safezone-popup">
      <div className="popup-title safezone-title">{trigger.name}</div>
      <div className="popup-note">{TYPE_EXPLAINED[trigger.type] || trigger.type}</div>
      <div className="popup-form">
        <div className="popup-field">
          <label
            className="popup-field-label"
            data-tooltip-id="app-tip"
            data-tooltip-content={HINTS.comment}
          >
            Comment
          </label>
          <input
            className="popup-input"
            value={comment}
            onChange={(e) => setComment(e.target.value)}
          />
        </div>
        <div className="popup-field">
          <label
            className="popup-field-label"
            data-tooltip-id="app-tip"
            data-tooltip-content={HINTS.eye}
          >
            Eye accommodation
          </label>
          <input
            className="popup-input"
            type="number"
            step="0.1"
            min="0"
            max="1"
            value={eye}
            onChange={(e) => setEye(e.target.value)}
          />
        </div>
        <div className="popup-field">
          <label
            className="popup-field-label"
            data-tooltip-id="app-tip"
            data-tooltip-content={HINTS.size}
          >
            Size X / Y / Z
          </label>
          <div className="popup-row">
            <input
              className="popup-input"
              type="number"
              step="0.1"
              value={sizeX}
              onChange={(e) => setSizeX(e.target.value)}
              aria-label="Size X"
            />
            <input
              className="popup-input"
              type="number"
              step="0.1"
              value={sizeY}
              onChange={(e) => setSizeY(e.target.value)}
              aria-label="Size Y"
            />
            <input
              className="popup-input"
              type="number"
              step="0.1"
              value={sizeZ}
              onChange={(e) => setSizeZ(e.target.value)}
              aria-label="Size Z"
            />
          </div>
        </div>
        <div className="popup-field">
          <label
            className="popup-field-label"
            data-tooltip-id="app-tip"
            data-tooltip-content={HINTS.height}
          >
            Height (Y)
          </label>
          <input
            className="popup-input"
            type="number"
            step="0.1"
            value={height}
            onChange={(e) => setHeight(e.target.value)}
          />
        </div>
        <div className="popup-field">
          <label
            className="popup-field-label"
            data-tooltip-id="app-tip"
            data-tooltip-content={HINTS.yaw}
          >
            Yaw
          </label>
          <input
            className="popup-input"
            type="number"
            step="1"
            value={yaw}
            onChange={(e) => setYaw(e.target.value)}
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
      </div>
    </div>
  )
}
