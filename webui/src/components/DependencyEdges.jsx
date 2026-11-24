// The mod dependency graph a conflict scan found, as a collapsed reference
// block. Rendered by both Conflicts surfaces — the Doctor page's card and the
// Mods page's tab — the same arrangement FixCell has with the two tables.
//
// **Every edge here is satisfied.** `@DayZ-Expansion-Core → @Dabs Framework`
// means Core declares it needs Dabs and Dabs is installed; a dependency that is
// actually missing is a finding in the table above, with a severity and an
// Install button. That distinction is the whole reason this is collapsed,
// labelled and greyed rather than listed open under a "Conflicts" heading,
// where it was read as a list of faults.
//
// The two payloads name the same data differently (the Doctor report flattens
// it to `dependencies`, /api/mods/conflicts sends the scan dataclass as
// `pbo_debug`), so each caller passes its own keys in and this renders.

import { Link } from 'react-router-dom'
import { modLink } from '../crossLinks.js'
import PanelNote from './PanelNote.jsx'
import FoldCaret from './FoldCaret.jsx'
import CountBadge from './CountBadge.jsx'

export default function DependencyEdges({ edges, pbosScanned }) {
  if (!edges || edges.length === 0) return null
  return (
    <details className="doctor-passing doctor-deps">
      <summary>
        <FoldCaret /> Detected dependencies <CountBadge value={edges.length} /> — reference, not problems
      </summary>
      <PanelNote>
        What each mod declares it needs, read from {pbosScanned} scanned PBOs. A dependency
        that is actually <em>missing</em> shows up as a finding above, and in the Doctor
        page's Mod dependencies health check.
      </PanelNote>
      <ul className="doctor-dep-list">
        {edges.map((e, idx) => (
          <li key={idx}>
            <Link className="mod-link" to={modLink(e.mod)}>
              <code>{e.mod}</code>
            </Link>{' '}
            →{' '}
            <Link className="mod-link" to={modLink(e.depends_on)}>
              <code>{e.depends_on}</code>
            </Link>{' '}
            <span className="doctor-detail">({e.source})</span>
          </li>
        ))}
      </ul>
    </details>
  )
}
