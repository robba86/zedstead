// The editor's own sidebar: map selector, layer toggles, tabs, status.
//
// Kept as an editor-local panel rather than folded into the app nav — the tabs
// are per-map-view state, and the map needs the horizontal space.

import { useEffect, useRef, useState } from 'react'
import { MOBILE_QUERY, useMediaQuery } from '../../../useMediaQuery.js'
import CountBadge from '../../../components/CountBadge.jsx'
import FoldCaret from '../../../components/FoldCaret.jsx'
import { tooltipAttrs } from '../../../components/Tooltip.jsx'
import { useEditor } from '../state/MapEditorProvider'
import { TABS } from '../state/urlState'
import useStoredOpen from '../state/useStoredOpen.js'
import ExpandButton from './ExpandButton.jsx'
import HelpDialog from './HelpDialog.jsx'
import MapSelect from './MapSelect.jsx'
import LayerToggles, { BaseMapToggles } from './LayerToggles.jsx'
import TraderSystemsNote from './TraderSystemsNote.jsx'
import LocationList from './LocationList.jsx'
import RelatedLinks from './RelatedLinks.jsx'
import StatusBar from './StatusBar.jsx'
import AddModeBar from './AddModeBar.jsx'
import SelectionBar from './SelectionBar.jsx'
import SheetResizeHandle, { TOOLS_SHEET, useSheetHeight } from './SheetResizeHandle.jsx'
import SidebarWidthHandle from './SidebarWidthHandle.jsx'
import PanelNote from '../../../components/PanelNote.jsx'

const TAB_META = {
  spawns: {
    label: 'Spawns',
    icon: (
      <>
        <circle cx="12" cy="12" r="4" />
        <path d="M12 2v4" />
        <path d="M12 18v4" />
        <path d="M2 12h4" />
        <path d="M18 12h4" />
      </>
    ),
  },
  'player-spawns': {
    label: 'Players',
    icon: (
      <>
        <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />
        <circle cx="9" cy="7" r="4" />
      </>
    ),
  },
  traders: {
    label: 'Traders',
    traderplusOnly: true,
    icon: (
      <>
        <path d="M3 9l9-6 9 6" />
        <rect x="5" y="9" width="14" height="13" />
        <rect x="9" y="14" width="6" height="8" />
      </>
    ),
  },
  // A mod's classes: place one with a spawn test, edit its types.xml entry.
  'mod-items': {
    label: 'Items',
    icon: (
      <>
        <path d="M21 8l-9-5-9 5 9 5 9-5z" />
        <path d="M3 8v8l9 5 9-5V8" />
        <path d="M12 13v8" />
      </>
    ),
  },
}

// The panel's hide/show control: « while the panel shows, » while it is away.
// One control for both screens so they look alike: the wide screen's header
// and folded strip, and the phone's floating button over the map, which only
// adds `me-sheet-toggle` for its size and place (mapeditor.css mobile block).
function FoldButton({ open, onClick, className = '' }) {
  const label = open ? 'Hide the editor panel' : 'Show the editor panel'
  return (
    <button
      type="button"
      className={`me-fold-btn ${className}`.trim()}
      aria-label={label}
      aria-expanded={open}
      aria-controls="me-sidebar"
      {...tooltipAttrs(label)}
      onClick={onClick}
    >
      {open ? '«' : '»'}
    </button>
  )
}

export default function LeftPanel({ children }) {
  const { url, traderplus, isActiveTerrain, terrain, activeTerrain, maps } = useEditor()
  // The names the map picker shows, not the terrain ids ("enoch" read where
  // the picker says Livonia).
  const mapName = (id) => (maps || []).find((m) => m.terrain === id)?.name || id
  const [helpOpen, setHelpOpen] = useState(false)
  // On a phone the 320px panel IS the screen, so it stops being a column beside
  // the map and becomes a bottom sheet over it (see the mobile block in
  // mapeditor.css). Closed by default: the map is the point of the page, and
  // the sheet is how you act on what you can see.
  const isMobile = useMediaQuery(MOBILE_QUERY)
  const [sheetOpen, setSheetOpen] = useState(false)
  const sheetRef = useRef(null)
  const [sheetHeight, setSheetHeight] = useSheetHeight(sheetRef, TOOLS_SHEET)
  // One sheet at a time: the Loot finder opening shuts the Tools sheet (its
  // Buildings switch is one way in), and closing it brings Tools back if it
  // was open. Tools opened over the finder hides it (mapeditor.css).
  const lootOpen = url.loot.open
  const sheetOpenRef = useRef(sheetOpen)
  sheetOpenRef.current = sheetOpen
  const reopenRef = useRef(false)
  useEffect(() => {
    if (!isMobile) return
    if (lootOpen) {
      reopenRef.current = sheetOpenRef.current
      setSheetOpen(false)
    } else if (reopenRef.current) {
      reopenRef.current = false
      setSheetOpen(true)
    }
  }, [lootOpen, isMobile])
  // The tabs and their content collapse like Layers, under a title of their
  // own drawn the same way; closed, the title names the tab that is active.
  const [tabsOpen, setTabsOpen] = useStoredOpen('zsm.mapeditor.tabs.open')
  // On a wide screen the whole panel folds to a thin strip, giving its width
  // to the map. The content stays mounted (only hidden), so a half-filled form
  // in a tab survives a fold. A phone has the sheet instead.
  const [panelOpen, setPanelOpen] = useStoredOpen('zsm.mapeditor.sidebar.open')
  const folded = !isMobile && !panelOpen

  // "?" opens the guide, as in the vanilla editor — but not while the user is
  // typing one into an event name or a classname field.
  useEffect(() => {
    const onKey = (e) => {
      if (e.key !== '?' || e.ctrlKey || e.altKey || e.metaKey) return
      const t = e.target
      if (t && (/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) || t.isContentEditable)) return
      e.preventDefault()
      setHelpOpen(true)
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [])

  return (
    <>
      {/* Outside the panel, so "?" still opens the guide while it is folded. */}
      {helpOpen && <HelpDialog traderplus={traderplus} onClose={() => setHelpOpen(false)} />}
      {/* Mobile only. Floats over the map's top-left corner, where the wide
          screen's panel sits, and is the only way to the sheet — deliberately
          not a peek row on the sheet's own header, which already holds the map
          picker and two buttons and would make "tap to expand" ambiguous with
          them. The wide screen's « / » button, at a thumb's size. */}
      {isMobile && (
        <FoldButton
          open={sheetOpen}
          className="me-sheet-toggle"
          onClick={() => setSheetOpen((o) => !o)}
        />
      )}
      <aside
        id="me-sidebar"
        ref={sheetRef}
        style={isMobile && sheetHeight != null ? { height: sheetHeight } : undefined}
        className={`me-sidebar${isMobile && sheetOpen ? ' me-sidebar-open' : ''}${folded ? ' me-sidebar-folded' : ''}`}
      >
        {isMobile && (
          <SheetResizeHandle
            sheetRef={sheetRef}
            setHeight={setSheetHeight}
            sheet={TOOLS_SHEET}
            label="Drag or tap to resize the tools panel"
          />
        )}
        {!isMobile && !folded && <SidebarWidthHandle panelRef={sheetRef} />}
        {folded && (
          // Expand rides along, so full-window mode can be left without
          // unfolding the panel first.
          <div className="me-sidebar-strip">
            <FoldButton open={false} onClick={() => setPanelOpen(true)} />
            <ExpandButton />
          </div>
        )}
        {/* The sheet's one scroller on a phone, so the tab body (the event
            list) gets the whole sheet instead of a nested box a few rows
            tall. `display: contents` on a wide screen, where every section
            keeps its own height and scroll. mapeditor.css */}
        <div className="me-sheet-body me-scroll">
          <div className="me-sidebar-header">
            {/* Buttons on the left, the map picker parked at the right edge by
                margin-left:auto on its wrapper. */}
            <div className="me-header-actions">
              {!isMobile && <FoldButton open onClick={() => setPanelOpen(false)} />}
              <ExpandButton />
              <button
                type="button"
                className="me-help-btn"
                aria-label="Help"
                data-tooltip-id="app-tip"
                data-tooltip-content="Open editor guide (press ?)"
                onClick={() => setHelpOpen(true)}
              >
                ?
              </button>
            </div>
            <div className="me-header-map">
              <MapSelect />
            </div>
          </div>

          <TraderSystemsNote />
          <BaseMapToggles />
          <LayerToggles />
          <LocationList />

          {/* The ListSection header look, written out: the tabs and the tab
              body it folds are two siblings in the sidebar's flex column. */}
          <div className="list-section-head me-tabs-head">
            <button
              type="button"
              className="list-section-title list-section-toggle"
              aria-expanded={tabsOpen}
              aria-controls="me-tabs me-tab-content"
              onClick={() => setTabsOpen((o) => !o)}
            >
              <FoldCaret open={tabsOpen} />
              <span>Edit</span>
              {!tabsOpen && TAB_META[url.tab] && <CountBadge value={TAB_META[url.tab].label} />}
            </button>
          </div>

          <div id="me-tabs" className="me-tabs" hidden={!tabsOpen}>
            {TABS.map((tab) => {
              const meta = TAB_META[tab]
              const disabled = (meta.traderplusOnly && !traderplus) || !isActiveTerrain
              const hint = meta.traderplusOnly && !traderplus
                ? 'TraderPlus mod is not installed'
                : !isActiveTerrain
                  ? `Editing is only available on the mission's map (${activeTerrain})`
                  : undefined
              return (
                <button
                  key={tab}
                  type="button"
                  className={`me-tab${url.tab === tab ? ' active' : ''}`}
                  disabled={disabled}
                  onClick={() => url.setTab(tab)}
                  aria-label={meta.label}
                  // The name as a tip too: a narrow panel shows only the icons.
                  data-tooltip-id="app-tip"
                  data-tooltip-content={hint || meta.label}
                >
                  <svg
                    className="me-tab-icon"
                    width="14"
                    height="14"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    {meta.icon}
                  </svg>
                  <span className="me-tab-label">{meta.label}</span>
                </button>
              )
            })}
          </div>

          <AddModeBar />
          <SelectionBar />

          <div id="me-tab-content" className="me-tab-content" hidden={!tabsOpen}>
            {isActiveTerrain ? (
              children
            ) : (
              <PanelNote>
                Showing {mapName(terrain)} for reference. Switch back to {mapName(activeTerrain)} to
                edit mission data.
              </PanelNote>
            )}
          </div>

          <RelatedLinks />
        </div>
        <StatusBar />
      </aside>
    </>
  )
}
