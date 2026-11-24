// Leaflet popups for the three Expansion position lists that live inside
// settings files (TODO H4, 2026 Q3): a market vehicle spawn, a server map
// marker and a base-building zone.
//
// Each edits what the map cannot show (a heading, a height, a text) and offers
// "Copy here…"; there is no Delete, by design — removing an
// entry stays in the Configs editor's Raw tab, which the popup links to.
//
// The height notes are the point of these popups. zsm has no terrain heights,
// so a drag keeps the height the entry had, and what a wrong one does differs
// per list (read from Expansion's scripts; app/spawn_editor/
// expansion_position_state.py has the references).

import { useState } from 'react'
import UnitInput from '../../../components/UnitInput.jsx'
import { Link } from 'react-router-dom'
import { meApi } from '../api'
import { keepUntouched } from '../coords'
import { useToast } from '../../../components/Toast.jsx'
import { tooltipAttrs } from '../../../components/Tooltip.jsx'
import { editorLink } from '../../../crossLinks.js'
import { MARKET_KINDS } from '../leaflet/useExpansionLayers'

function Field({ label, hint, children }) {
  return (
    <div className="popup-field">
      <label className="popup-field-label" {...tooltipAttrs(hint)}>
        {label}
      </label>
      {children}
    </div>
  )
}

// A number box, with its unit ("deg", "m") inside the box (UnitInput).
function NumberInput({ value, onChange, step = '1', unit }) {
  return (
    <UnitInput
      unit={unit}
      className="popup-input"
      type="number"
      step={step}
      value={value}
      onChange={(e) => onChange(e.target.value)}
    />
  )
}

/** Save and Copy here…, plus the pointer to where deleting lives. */
function Actions({ saving, onSave, onCopy, editor, what }) {
  return (
    <>
      <div className="popup-actions">
        <button
          type="button"
          className="btn btn-primary btn-small"
          disabled={saving}
          onClick={onSave}
        >
          {saving ? 'Saving...' : 'Save'}
        </button>
        <button type="button" className="btn btn-ghost btn-small" onClick={onCopy}>
          Copy here…
        </button>
      </div>
      <div className="popup-note">
        To delete {what}, use the <Link to={editorLink(editor, { tab: 'Raw' })}>Raw tab</Link> of
        its settings.
      </div>
    </>
  )
}

/** PUT *body* to *path*; toasts a failure and closes on success. */
function useSave(path, onClose) {
  const [saving, setSaving] = useState(false)
  const pushToast = useToast()
  const save = async (body) => {
    if (Object.values(body).some((v) => typeof v === 'number' && Number.isNaN(v))) {
      pushToast('Every number field needs a number.', 'error')
      return
    }
    setSaving(true)
    try {
      await meApi.put(path, body)
      onClose()
    } catch (err) {
      pushToast(`Save failed: ${err.message}`, 'error')
    } finally {
      setSaving(false)
    }
  }
  return [saving, save]
}

const HEIGHT_HINT = {
  land: 'Land vehicles are dropped onto the ground below this height, so it must be at or above the ground here; below it, they spawn underground. Dragging keeps the height, because Zedstead cannot see the ground.',
  air: 'Aircraft are dropped onto the ground below this height, so it must be at or above the ground here; below it, they spawn underground. Dragging keeps the height, because Zedstead cannot see the ground.',
  water: 'A boat is placed at exactly this height, so keep it at the water line (sea level is 0).',
  train: 'Where a train is placed, height included. Dragging keeps the height.',
}

export function ExpansionMarketSpawnPopup({ list, spawn, data, onCopy, onClose }) {
  const [heading, setHeading] = useState(spawn.heading.toFixed(1))
  const [height, setHeight] = useState(spawn.game_y.toFixed(2))
  const [saving, save] = useSave(`/expansion/market-spawns/${list}/${spawn.index}`, onClose)
  const range =
    data?.max_distance != null
      ? `A trader uses this position when it is within ${Math.round(data.max_distance)} m${
          data.max_large_distance != null
            ? ` (${Math.round(data.max_large_distance)} m for large vehicles)`
            : ''
        }.`
      : null

  return (
    <div className="spawn-popup safezone-popup">
      <div className="popup-title safezone-title">
        {MARKET_KINDS[list]} vehicle spawn {spawn.index + 1}
      </div>
      <div className="popup-form">
        <Field label="Heading" hint="Which way the vehicle faces, clockwise from north.">
          <NumberInput value={heading} onChange={setHeading} unit="deg" />
        </Field>
        <Field label="Height" hint={HEIGHT_HINT[list]}>
          <NumberInput value={height} onChange={setHeight} step="0.1" unit="m" />
        </Field>
      </div>
      {range && <div className="popup-note">{range}</div>}
      {data && !data.enabled && (
        <div className="popup-note">The market is switched off in MarketSettings.json.</div>
      )}
      <Actions
        saving={saving}
        onSave={() =>
          save({
            orientation: keepUntouched(heading, spawn.heading, 1),
            game_y: keepUntouched(height, spawn.game_y, 2),
          })
        }
        onCopy={onCopy}
        editor="expansion-market"
        what="a spawn position"
      />
    </div>
  )
}

export function ExpansionMapMarkerPopup({ marker, enabled, onCopy, onClose }) {
  const [text, setText] = useState(marker.text)
  const [icon, setIcon] = useState(marker.icon)
  const [color, setColor] = useState(marker.color)
  const [height, setHeight] = useState(marker.game_y.toFixed(2))
  const [saving, save] = useSave(`/expansion/map-markers/${marker.index}`, onClose)
  const shown = [marker.on_map && 'on the map', marker.in_world && 'in the world'].filter(Boolean)

  return (
    <div className="spawn-popup safezone-popup">
      <div className="popup-title safezone-title">{marker.text || marker.uid}</div>
      <div className="popup-form">
        <Field label="Text" hint="What players read beside the marker.">
          <input className="popup-input" value={text} onChange={(e) => setText(e.target.value)} />
        </Field>
        <Field
          label="Icon"
          hint="Expansion's own icon name (Trader, Boat, Helicopter, …). Zedstead does not check it."
        >
          <input className="popup-input" value={icon} onChange={(e) => setIcon(e.target.value)} />
        </Field>
        <Field label="Colour" hint="The marker's colour; its transparency is kept.">
          <input
            className="popup-input"
            type="color"
            value={color}
            onChange={(e) => setColor(e.target.value)}
          />
        </Field>
        <Field
          label="Height (m)"
          hint="A marker shown in the world is drawn at this height. Dragging keeps it, because Zedstead cannot see the ground."
        >
          <NumberInput value={height} onChange={setHeight} step="0.1" />
        </Field>
      </div>
      <div className="popup-note">
        Shown {shown.length ? shown.join(' and ') : 'nowhere (visibility 0)'}.
      </div>
      {!enabled && (
        <div className="popup-note">Server markers are switched off in MapSettings.json.</div>
      )}
      <Actions
        saving={saving}
        onSave={() =>
          save({
            text: text.trim(),
            icon: icon.trim(),
            color,
            game_y: keepUntouched(height, marker.game_y, 2),
          })
        }
        onCopy={onCopy}
        editor="expansion-map"
        what="a marker"
      />
    </div>
  )
}

export function ExpansionBuildZonePopup({ zone, noBuildZones, onCopy, onClose }) {
  const [name, setName] = useState(zone.name)
  const [radius, setRadius] = useState(zone.radius.toFixed(2))
  const [height, setHeight] = useState(zone.game_y.toFixed(2))
  const [saving, save] = useSave(`/expansion/build-zones/${zone.index}`, onClose)
  const count = zone.items.length

  return (
    <div className="spawn-popup safezone-popup">
      <div className="popup-title safezone-title">{zone.name}</div>
      <div className="popup-form">
        <Field label="Name" hint="Shown to a player whose placement the zone refuses.">
          <input className="popup-input" value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label="Radius (m)" hint="Drag the white edge handle to resize.">
          <NumberInput value={radius} onChange={setRadius} />
        </Field>
        <Field
          label="Height (m)"
          hint="Expansion measures the distance to the centre in 3D, so this should be the ground height here; far off, the zone shrinks or misses. Dragging keeps it."
        >
          <NumberInput value={height} onChange={setHeight} step="0.1" />
        </Field>
      </div>
      <div className="popup-note">
        {zone.whitelist
          ? `Inside, only ${count === 1 ? 'this item' : `these ${count} items`} can be placed`
          : `Inside, ${count === 1 ? 'this item' : `these ${count} items`} can't be placed`}
        {count ? `: ${zone.items.join(', ')}.` : ' (the list is empty).'}
      </div>
      {noBuildZones === false && (
        <div className="popup-note">
          ZonesAreNoBuildZones is 0, so outside every zone nothing can be placed.
        </div>
      )}
      <Actions
        saving={saving}
        onSave={() =>
          save({
            name: name.trim(),
            radius: keepUntouched(radius, zone.radius, 2),
            game_y: keepUntouched(height, zone.game_y, 2),
          })
        }
        onCopy={onCopy}
        editor="expansion-basebuilding"
        what="a zone"
      />
    </div>
  )
}
