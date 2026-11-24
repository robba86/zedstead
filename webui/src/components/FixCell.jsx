import { Link } from 'react-router-dom'
import { NeedsSteamIf } from './NeedsSteam.jsx'
import Tooltip from './Tooltip.jsx'
import { STEAM_ACTION_KINDS } from '../fixActions.js'
import { useCan } from '../role.jsx'

// The "Fix" cell of a conflict finding — what to do about it, and the buttons
// and chips that do it. The payload is built server-side
// (app/ops/fix_hints.py::conflict_fix) and attached to the same items by both
// GET /api/doctor (`conflicts.issues[].fix`) and GET /api/mods/conflicts
// (`items[].fix`), so the Doctor page and the Mods page render one component and
// a hint added on the backend appears on both at once.
//
// Order is deliberate: do it → go there → why. Buttons first because that is the
// point of the cell; `why` is the hover text on the instruction rather than a
// fourth line, since the table is already wide.
export default function FixCell({ fix, onAction, busyAction }) {
  // Every action a fix carries is an admin write; below admin the advice and
  // its links stay and the buttons go (plans/43 §4).
  const isAdmin = useCan('admin')
  if (!fix) return <span className="dep-info">—</span>
  const actions = isAdmin ? fix.actions || [] : []
  const links = fix.links || []
  return (
    <div className="fix-cell">
      {(actions.length > 0 || links.length > 0) && (
        <div className="fix-actions">
          {actions.map((a, i) => (
            <NeedsSteamIf key={a.kind + a.label} when={STEAM_ACTION_KINDS.has(a.kind)}>
              <button
                className={`btn btn-small ${i === 0 ? 'btn-primary' : 'btn-ghost'}`}
                disabled={!!busyAction}
                onClick={() => onAction?.(a)}
              >
                {busyAction === a.kind + a.label ? 'Working…' : a.label}
              </button>
            </NeedsSteamIf>
          ))}
          {links.map((l) => (
            <Link key={l.to + l.label} className="link-chip" to={l.to}>
              {l.label}
            </Link>
          ))}
        </div>
      )}
      {fix.how && (
        // A <span>, not a <p>: Tooltip anchors its children inside a <span>.
        <Tooltip keep text={fix.why} className="fix-how-tip">
          <span className="fix-how">{fix.how}</span>
        </Tooltip>
      )}
    </div>
  )
}
