// Leaflet popup for a TraderPlus trader NPC.
//
// The two dropdowns are the interesting part:
//
//   Name (class) — offered from the classnames already in use, because a typo
//   here spawns nothing at all and there is no validation server-side. "Custom…"
//   opens a free-text field for a genuinely new one.
//
//   Id — the trader's stock. Ids come from TraderPlusIDsConfig.json and are
//   shared: two NPCs with the same Id sell the same categories. The current Id
//   is added to the list even when the config does not list it, so an ATM
//   (Id -2) does not silently get reassigned by opening its popup.

import Select from '../../../components/Select.jsx'
import { useState } from 'react'
import { meApi } from '../api'
import { keepUntouched } from '../coords'
import { useToast } from '../../../components/Toast.jsx'
import { useConfirm } from '../../../components/useConfirm.jsx'

const CUSTOM = '__custom__'

const SPECIAL_IDS = { '-2': 'ATM / Bank', '-1': 'Special' }

const HINTS = {
  name: 'Survivor model OR static object classname (e.g. SurvivorM_Peter, Land_Survivors_BankATM). Static objects let you "place" an NPC as a prop — the ATM trader (Id -2) typically uses one. The dropdown lists classnames already in use; pick "Custom…" to type a new one.',
  x: 'Trader world X (west-east). Use izurvive.com to look up coordinates by clicking on the map.',
  y: 'NPC vertical position (height). Often left at 0 — DayZ snaps NPCs to terrain. Override only for traders on elevated platforms (e.g. building rooftops, ship decks).',
  z: 'Trader world Z (south-north). Use izurvive.com to look up coordinates by clicking on the map.',
  orientation:
    'NPC facing direction (yaw, in degrees). 0 = north, 90 = east, 180 = south, 270 = west. Point the trader so players approach from the front.',
}

export default function TraderNpcPopup({ trader, allTraders, traderIds, onClose }) {
  const [givenName, setGivenName] = useState(trader.given_name)
  const [role, setRole] = useState(trader.role)
  const [nameChoice, setNameChoice] = useState(trader.name)
  const [customName, setCustomName] = useState('')
  const [id, setId] = useState(String(trader.id))
  const [x, setX] = useState(trader.game_x.toFixed(2))
  const [y, setY] = useState(trader.game_y.toFixed(2))
  const [z, setZ] = useState(trader.game_z.toFixed(2))
  const [orientation, setOrientation] = useState(String(trader.orientation))
  const [saving, setSaving] = useState(false)
  const pushToast = useToast()
  const confirm = useConfirm()

  const classNames = [...new Set(allTraders.map((t) => t.name).filter(Boolean))].sort()
  if (trader.name && !classNames.includes(trader.name)) classNames.unshift(trader.name)

  const ids = traderIds || []
  const hasCurrentId = ids.some((entry) => String(entry.id) === String(trader.id))

  async function save() {
    const nums = {
      game_x: keepUntouched(x, trader.game_x, 2),
      game_y: keepUntouched(y, trader.game_y, 2),
      game_z: keepUntouched(z, trader.game_z, 2),
    }
    if (Object.values(nums).some(Number.isNaN)) {
      pushToast('Invalid coordinates — X, Y, Z must be numbers.', 'error')
      return
    }
    const name = nameChoice === CUSTOM ? customName.trim() : nameChoice
    setSaving(true)
    try {
      await meApi.put(`/traders/${trader.index}`, {
        ...nums,
        orientation: parseFloat(orientation),
        given_name: givenName.trim(),
        role: role.trim(),
        name,
        id: parseInt(id, 10),
      })
    } catch (err) {
      pushToast(`Save failed: ${err.message}`, 'error')
    } finally {
      setSaving(false)
    }
  }

  async function remove() {
    const ok = await confirm({
      title: 'Delete trader NPC?',
      message: `Remove "${trader.given_name}" (${trader.role})?`,
      confirmLabel: 'Delete',
      danger: true,
    })
    if (!ok) return
    try {
      await meApi.del(`/traders/${trader.index}`)
      onClose()
    } catch (err) {
      pushToast(`Delete failed: ${err.message}`, 'error')
    }
  }

  return (
    <div className="spawn-popup trader-popup">
      <div className="popup-title trader-npc-title">
        {trader.given_name} — {trader.role}
      </div>
      <div className="popup-form">
        <div className="popup-field">
          <label className="popup-field-label">Given Name</label>
          <input
            className="popup-input"
            type="text"
            value={givenName}
            onChange={(e) => setGivenName(e.target.value)}
          />
        </div>
        <div className="popup-field">
          <label className="popup-field-label">Role</label>
          <input
            className="popup-input"
            type="text"
            value={role}
            onChange={(e) => setRole(e.target.value)}
          />
        </div>
        <div className="popup-field">
          <label
            className="popup-field-label"
            data-tooltip-id="app-tip"
            data-tooltip-content={HINTS.name}
          >
            Name (class)
          </label>
          <Select
            className="popup-input"
            aria-label="Name (class)"
            value={nameChoice}
            onChange={setNameChoice}
            options={[...classNames, { value: CUSTOM, label: 'Custom…' }]}
          />
        </div>
        {nameChoice === CUSTOM && (
          <div className="popup-field">
            <label className="popup-field-label" />
            <input
              className="popup-input"
              type="text"
              placeholder="Enter classname…"
              value={customName}
              onChange={(e) => setCustomName(e.target.value)}
              autoFocus
            />
          </div>
        )}
        <div className="popup-field">
          <label className="popup-field-label">Id (stock/categories)</label>
          <Select
            className="popup-input"
            aria-label="Id (stock/categories)"
            value={id}
            onChange={setId}
            options={[
              ...(hasCurrentId ? [] : [{ value: trader.id, label: `${trader.id} — ${SPECIAL_IDS[String(trader.id)] || 'unlisted'}` }]),
              ...ids.map((entry) => {
                const categories =
                  entry.categories.length > 3
                    ? `${entry.categories.slice(0, 3).join(', ')}, …`
                    : entry.categories.join(', ')
                return { value: entry.id, label: `${entry.id} — ${categories || '(no categories)'}` }
              }),
            ]}
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
