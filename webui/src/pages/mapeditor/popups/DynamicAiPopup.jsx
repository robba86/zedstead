// Leaflet popup for one Dynamic AI point, location or audio location
// (SpatialSettings.json, plans/47 D).
//
// Read-only facts plus the two map actions D3 allows: "Copy here…" (a new entry
// is only ever a copy of one that works) and, on a location, "Move here…" (the
// trigger, and — the dialog after the click asks — its spawn positions too;
// how an entry still at the mod's 0, 0 placeholder gets placed). Everything
// else about the entry is the Configs page's Dynamic AI table, one click away.

import { Link } from 'react-router-dom'
import { editorLink } from '../../../crossLinks.js'

const EDITORS = { points: 'dynai-points', locations: 'dynai-locations', audio: 'dynai-audio' }
const KINDS = { points: 'Point', locations: 'Location', audio: 'Audio location' }
const TABLES = { points: 'Points', locations: 'Locations', audio: 'Audio' }

function Fact({ label, hint, children }) {
  return (
    <div className="popup-field">
      <span
        className="popup-field-label"
        data-tooltip-id={hint ? 'app-tip' : undefined}
        data-tooltip-content={hint}
      >
        {label}
      </span>
      <span className="popup-value">{children}</span>
    </div>
  )
}

export default function DynamicAiPopup({ section, entry, modes, onCopy, onMove, onClose }) {
  const isPoint = section === 'points'
  const off = modes && !modes[section]
  const where = isPoint ? entry : entry.trigger
  return (
    <div className="spawn-popup safezone-popup">
      <div className="popup-title safezone-title">
        {KINDS[section]}: {entry.name}
      </div>
      <div className="popup-form">
        {isPoint && entry.safe ? (
          <Fact label="Safe" hint="Spatial_Safe: a no-spawn zone">
            nothing spawns here
          </Fact>
        ) : (
          <>
            <Fact label="Faction">{entry.faction || '—'}</Fact>
            <Fact label="AI" hint="Spatial_MinCount to Spatial_MaxCount">
              {entry.min_count}–{entry.max_count}
            </Fact>
          </>
        )}
        <Fact
          label="Radius"
          hint={isPoint ? 'Spatial_Radius — drag the edge handle to resize' : 'Spatial_TriggerRadius — drag the edge handle to resize'}
        >
          {Math.round(entry.radius)} m
        </Fact>
        <Fact label="At" hint={isPoint ? 'Spatial_Position, x and z' : 'Spatial_TriggerPosition, x and z'}>
          {Math.round(where.game_x)}, {Math.round(where.game_z)}
        </Fact>
        {!isPoint && (
          <Fact label="Spawns" hint="Spatial_SpawnPosition: where the AI appear when the trigger fires">
            {entry.spawns.length}
          </Fact>
        )}
      </div>
      <div className="popup-actions">
        {!isPoint && (
          <button type="button" className="btn btn-ghost btn-small" onClick={onMove}>
            Move here…
          </button>
        )}
        <button type="button" className="btn btn-ghost btn-small" onClick={onCopy}>
          Copy here…
        </button>
      </div>
      {entry.unplaced && (
        <div className="popup-note">
          Still at 0, 0, the mod&apos;s placeholder.{' '}
          {isPoint ? 'Drag it to where it belongs.' : 'Move here… places it and asks about its spawn positions.'}
        </div>
      )}
      {off && (
        <div className="popup-note">This list is switched off in the settings, so the mod skips it.</div>
      )}
      <div className="popup-note">
        Everything else about this entry lives in the{' '}
        <Link to={editorLink(EDITORS[section])} onClick={onClose}>
          Dynamic AI {TABLES[section]} table
        </Link>
        .
      </div>
    </div>
  )
}
