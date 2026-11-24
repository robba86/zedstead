// "Both trader mods are configured" — and where the two meet.
//
// TraderPlus and Expansion Market are separate trading systems with separate
// files, and the editor happily draws both (gold traders + green safe zones
// against violet trading zones). Nothing merges them, so a spot covered by
// both has two mods' rules on it at once. The overlap is measured server-side
// (`app/spawn_editor/trader_systems.py`); this panel only renders it, and
// deliberately does not say whether running both is a good idea — that is the
// mods' business, and neither one's documentation covers the other.
//
// Renders nothing at all unless both systems actually have data, which is the
// normal case. A plain message (the shared banner), not a fold: it is there to
// be read, and sits right under the panel header. Warning colour only when the
// two systems overlap. The × closes it for good in this browser (ErrorBanner's
// localStorage list); the key carries the overlap count, so a change shows it again.

import ErrorBanner from '../../../components/ErrorBanner.jsx'
import { useEditor } from '../state/MapEditorProvider'

function describe(overlap) {
  if (overlap.kind === 'trader') {
    return `${overlap.traderplus} (TraderPlus trader) stands ${overlap.distance} m from the centre of ${overlap.expansion_zone}`
  }
  return `${overlap.traderplus} (TraderPlus safe zone) reaches into ${overlap.expansion_zone}`
}

export default function TraderSystemsNote() {
  const { traderSystems } = useEditor()
  if (!traderSystems?.both) return null

  const overlaps = traderSystems.overlaps || []
  const count = traderSystems.overlap_count || 0

  return (
    <ErrorBanner
      dismissKey={`map-editor.trader-systems.${count}`}
      variant={count ? 'warning' : 'info'}
      className="me-trader-systems"
    >
      <p className="me-trader-systems-title">
        Two trader systems{count ? ` — ${count} overlap${count === 1 ? '' : 's'}` : ''}
      </p>
      <p>
        TraderPlus ({traderSystems.traderplus_traders} traders,{' '}
        {traderSystems.traderplus_safezones} safe zones) and Expansion Market (
        {traderSystems.expansion_zones} trading zones) both have data on this server. They
        are separate systems — their own zones, prices and NPCs, in their own files — and
        Zedstead edits each on its own; nothing here merges them.
      </p>
      {count === 0 ? (
        <p>Nowhere on this map does one system's zone reach into the other's.</p>
      ) : (
        <>
          <p>Both are live in the same place here:</p>
          <ul>
            {overlaps.map((overlap, i) => (
              <li key={i}>{describe(overlap)}</li>
            ))}
          </ul>
          {count > overlaps.length && <p>…and {count - overlaps.length} more.</p>}
        </>
      )}
    </ErrorBanner>
  )
}
