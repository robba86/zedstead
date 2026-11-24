// The Loot layer's row in the layer list (plans/82): the switch that opens
// the Loot finder (LootDrawer.jsx) over the map's right edge. Open exactly
// when ?loot= or ?lootmod= is in the URL, so a view is linkable.

import { Switch } from '../../../components/FlagSwitch.jsx'
import { useEditor } from '../state/MapEditorProvider'
import { NO_MISSION } from './LootDrawer.jsx'

const fmt = (n) => Number(n || 0).toLocaleString()

export default function LootPanel() {
  const { url, hasBuildings, lootData } = useEditor()
  const { open, item, mod } = url.loot
  const enabled = hasBuildings
  const shown = open && enabled
  const count = (item || mod) && lootData ? lootData.count : null

  return (
    <li className={enabled ? undefined : 'disabled'}>
      <div
        className="me-layer-toggle"
        data-tooltip-id="app-tip"
        data-tooltip-content={
          enabled
            ? 'Open the Loot finder: where an item or a mod can spawn, and what a building can hold'
            : NO_MISSION
        }
      >
        <Switch checked={shown} disabled={!enabled} onChange={(next) => url.setLoot({ open: next })}>
          <svg
            className={`me-toggle-icon${shown ? ' me-toggle-icon-active' : ''}`}
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M21 8l-9-5-9 5v8l9 5 9-5z" />
            <path d="M3 8l9 5 9-5" />
            <line x1="12" y1="13" x2="12" y2="21" />
          </svg>
          {count != null ? `Loot (${fmt(count)} buildings)` : 'Loot'}
        </Switch>
      </div>
    </li>
  )
}
