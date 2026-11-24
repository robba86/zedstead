// Leaflet popup for an Expansion trader zone.
//
// One file per zone under <mission>/expansion/traderzones/, and the only place
// in zsm that edits one — the settings editors cover Expansion's 30 *settings*
// files, not its per-zone data.
//
// The zone's `Stock` table (up to ~1900 entries) is deliberately not here and
// never leaves the server: this edits position, radius and the two price
// percentages, and the save patches those into the file the backend read.

import { useState } from 'react'
import { meApi } from '../api'
import { keepUntouched } from '../coords'
import { useToast } from '../../../components/Toast.jsx'

const HINTS = {
  name: 'Display name shown in the trader menu (m_DisplayName in the zone file).',
  x: 'Game-world X (west-east) at the zone centre. Drag the coloured centre grip on the map to move it visually.',
  z: 'Game-world Z (south-north) at the zone centre. The height the zone was written with is kept as-is.',
  radius:
    'How far from the centre the zone reaches, in metres. Drag the white edge handle on the map to resize.',
  buy: 'Percentage applied to buy prices in this zone. 100 = the catalog price; -1 means "use the global setting".',
  sell: 'Percentage applied to sell prices in this zone. -1 means "use the global setting".',
  stock: 'Per-item stock the zone tracks. Edited by the server, not here — it is not loaded into the browser at all.',
}

export default function ExpansionTraderZonePopup({ zone, onClose }) {
  const [name, setName] = useState(zone.name)
  const [x, setX] = useState(zone.game_x.toFixed(2))
  const [z, setZ] = useState(zone.game_z.toFixed(2))
  const [radius, setRadius] = useState(String(zone.radius))
  const [buy, setBuy] = useState(String(zone.buy_percent))
  const [sell, setSell] = useState(String(zone.sell_percent))
  const [saving, setSaving] = useState(false)
  const pushToast = useToast()

  async function save() {
    const values = {
      game_x: keepUntouched(x, zone.game_x, 2),
      game_z: keepUntouched(z, zone.game_z, 2),
      radius: parseFloat(radius),
      buy_percent: parseFloat(buy),
      sell_percent: parseFloat(sell),
    }
    if (Object.values(values).some(Number.isNaN)) {
      pushToast('Invalid values — every field here is a number.', 'error')
      return
    }
    setSaving(true)
    try {
      await meApi.put(`/expansion/trader-zones/${encodeURIComponent(zone.id)}`, {
        ...values,
        name: name.trim(),
      })
      onClose()
    } catch (err) {
      pushToast(`Save failed: ${err.message}`, 'error')
    } finally {
      setSaving(false)
    }
  }

  const field = (label, hint, value, setValue, props = {}) => (
    <div className="popup-field">
      <label className="popup-field-label" data-tooltip-id="app-tip" data-tooltip-content={hint}>
        {label}
      </label>
      <input
        className="popup-input"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        {...props}
      />
    </div>
  )

  return (
    <div className="spawn-popup safezone-popup">
      <div className="popup-title safezone-title">{zone.name}</div>
      <div className="popup-form">
        {field('Zone name', HINTS.name, name, setName, { type: 'text' })}
        <div className="popup-separator" />
        {field('X', HINTS.x, x, setX, { type: 'number', step: 'any' })}
        {field('Z', HINTS.z, z, setZ, { type: 'number', step: 'any' })}
        {field('Radius (metres)', HINTS.radius, radius, setRadius, {
          type: 'number',
          step: '1',
          min: '1',
        })}
        <div className="popup-separator" />
        {field('Buy price %', HINTS.buy, buy, setBuy, { type: 'number', step: 'any' })}
        {field('Sell price %', HINTS.sell, sell, setSell, { type: 'number', step: 'any' })}
        <div className="popup-field">
          <label
            className="popup-field-label"
            data-tooltip-id="app-tip"
            data-tooltip-content={HINTS.stock}
          >
            Stock entries
          </label>
          <span className="popup-value">{zone.stock_items}</span>
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
