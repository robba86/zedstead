// One side of a reported collision. Ports collisions.js's buildPopup(), which
// built the same fields as an HTML string.

import { COLLISION_KIND_LABELS } from '../collisionModes.js'

const SEVERITY_COLORS = {
  error: '#ff3333',
  warning: '#ffaa00',
  info: '#6699cc',
}

export default function CollisionPopup({ collision, side }) {
  const c = collision
  const color = SEVERITY_COLORS[c.severity] || SEVERITY_COLORS.error
  const isA = side === 'A'
  const pos = isA ? c.pos_a : c.pos_b
  const otherPos = isA ? c.pos_b : c.pos_a
  const event = isA ? c.event_a : c.event_b
  const otherEvent = isA ? c.event_b : c.event_a

  return (
    <div className="collision-popup">
      <div style={{ color, fontWeight: 'bold', marginBottom: 4 }}>
        {COLLISION_KIND_LABELS[c.kind] || c.kind}
      </div>
      <div>
        <b>Event:</b> {event}
      </div>
      <div>
        <b>Position:</b> {pos.game_x.toFixed(1)}, {pos.game_z.toFixed(1)}
      </div>
      <div style={{ marginTop: 4, borderTop: '1px solid var(--border)', paddingTop: 4 }}>
        <b>Conflicts with:</b> {otherEvent}
        <br />
        {otherPos.game_x.toFixed(1)}, {otherPos.game_z.toFixed(1)}
      </div>
      {c.distance > 0 && (
        <div>
          <b>Distance:</b> {c.distance}m
        </div>
      )}
      {c.radius > 0 && (
        <div>
          <b>Radius limit:</b> {c.radius}m
        </div>
      )}
    </div>
  )
}
