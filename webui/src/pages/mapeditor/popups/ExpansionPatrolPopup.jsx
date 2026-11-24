// Leaflet popup for one Expansion AI patrol (AIPatrolSettings.json).
//
// Read-only on everything except the route: a patrol carries ~47 fields —
// faction, loadout, formation, accuracy, respawn timing — and those belong to
// Expansion's own settings editor, not to a map bubble. What the map owns is
// the waypoint list, so this shows who the patrol is and lets you extend or
// remove its route.

import { meApi } from '../api'
import { useToast } from '../../../components/Toast.jsx'
import { useConfirm } from '../../../components/useConfirm.jsx'
import { editorLink } from '../../../crossLinks'
import { Link } from 'react-router-dom'

const HINTS = {
  faction: 'Which side the AI belongs to — Expansion decides who they shoot at from this.',
  behaviour: 'How the patrol walks its waypoints (LOOP, ONCE, HALT, ROAMING …).',
  chance: 'Probability this patrol spawns at all, 0 to 1.',
  waypoints: 'Positions walked in order. Drag one on the map to move it.',
}

export default function ExpansionPatrolPopup({ patrol, onAddWaypoint, onClose }) {
  const pushToast = useToast()
  const confirm = useConfirm()

  async function deletePatrol() {
    const ok = await confirm({
      title: 'Delete patrol?',
      message: `Delete "${patrol.name}" and all ${patrol.waypoints.length} of its waypoints?`,
      confirmLabel: 'Delete',
      danger: true,
    })
    if (!ok) return
    try {
      await meApi.del(`/expansion/ai/patrols/${patrol.index}`)
      onClose()
    } catch (err) {
      pushToast(`Delete failed: ${err.message}`, 'error')
    }
  }

  const row = (label, hint, value) => (
    <div className="popup-field">
      <label className="popup-field-label" data-tooltip-id="app-tip" data-tooltip-content={hint}>
        {label}
      </label>
      <span className="popup-value">{value}</span>
    </div>
  )

  return (
    <div className="spawn-popup safezone-popup">
      <div className="popup-title safezone-title">{patrol.name}</div>
      <div className="popup-form">
        {row('Faction', HINTS.faction, patrol.faction || '—')}
        {row('Behaviour', HINTS.behaviour, patrol.behaviour || '—')}
        {row('AI', 'How many bots this patrol spawns.', patrol.number_of_ai)}
        {row('Chance', HINTS.chance, patrol.chance)}
        {row('Waypoints', HINTS.waypoints, patrol.waypoints.length)}
      </div>

      <div className="popup-actions">
        <button type="button" className="btn btn-ghost btn-small" onClick={onAddWaypoint}>
          + Waypoint
        </button>
        <button type="button" className="btn btn-danger btn-small" onClick={deletePatrol}>
          Delete
        </button>
      </div>
      <div className="popup-note">
        Everything else about this patrol lives in{' '}
        <Link to={editorLink('expansion-ai-patrols')} onClick={onClose}>
          AIPatrolSettings.json
        </Link>
        .
      </div>
    </div>
  )
}
