// Collision scanner: the "Collisions" layer toggle plus its filter panel.
//
// Renders as <li> rows so it sits inside the layer list, where the vanilla
// editor had it. Panel-open state is not a separate flag: the panel is open
// exactly when ?collisions=<mode> is in the URL, which is also what makes the
// Analysis page's deep link work with no extra plumbing.

import { Switch } from '../../../components/FlagSwitch.jsx'
import SearchInput from '../../../components/SearchInput.jsx'
import Select from '../../../components/Select.jsx'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useToast } from '../../../components/Toast.jsx'
import { useEditor } from '../state/MapEditorProvider'
import { COLLISION_MODES } from '../state/urlState'
import {
  COLLISION_MODE_LABELS,
  buildScanPath,
  modeHasOptions,
  namesEndpoint,
} from '../collisionModes'
import { useResource } from '../state/useResource'
import PanelNote from '../../../components/PanelNote.jsx'
import CountBadge from '../../../components/CountBadge.jsx'

const DEFAULT_MODE = 'eventspawns'

export default function CollisionPanel() {
  const { url, collisions, collisionsLoading, runCollisionScan, isActiveTerrain, activeTerrain } =
    useEditor()
  const { mode, types, crossEventOverlaps, radiusConflicts } = url.collisions
  // A scan walks the running mission, so its coordinates only mean something on
  // that mission's terrain — the panel closes while another map is previewed
  // rather than offering a Scan whose rings would land on the wrong island.
  const open = Boolean(mode) && isActiveTerrain

  const pushToast = useToast()
  const onNamesError = useCallback(
    (err) => pushToast(err.message || 'Failed to load filter names', 'error'),
    [pushToast]
  )

  const [search, setSearch] = useState('')
  const names = useResource(open ? namesEndpoint(mode) : null, {
    initial: null,
    onError: onNamesError,
  })
  const availableTypes = names.data?.names || []

  const selected = useMemo(() => new Set(types), [types])
  const shown = useMemo(() => {
    const q = search.trim().toLowerCase()
    return q ? availableTypes.filter((n) => n.toLowerCase().includes(q)) : availableTypes
  }, [availableTypes, search])

  const allChecked = availableTypes.length > 0 && selected.size >= availableTypes.length
  const someChecked = selected.size > 0 && !allChecked

  // indeterminate is a DOM property, not an attribute — React cannot set it.
  const selectAllRef = useRef(null)
  useEffect(() => {
    if (selectAllRef.current) selectAllRef.current.indeterminate = someChecked
  }, [someChecked])

  // Deep link only: arriving with collision params already in the URL (the
  // Analysis page's "Open in Visual Map Editor") scans straight away. Opening
  // the panel by hand does not — a scan is a full mission walk, and the
  // vanilla editor made you press Scan too.
  const initialCollisionsRef = useRef(url.collisions)
  useEffect(() => {
    const initial = initialCollisionsRef.current
    if (!initial.mode) return
    // No availableTypes yet: whatever types the link carried are sent as-is.
    runCollisionScan(buildScanPath(initial, null))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function setOpen(next) {
    if (next) {
      url.setCollisions({ mode: DEFAULT_MODE })
    } else {
      url.setCollisions({ mode: '' })
      runCollisionScan(null)
    }
  }

  function changeMode(next) {
    // Type names are per mode, so the current selection means nothing in the
    // new one — and the results on screen even less.
    url.setCollisions({ mode: next, types: [] })
    runCollisionScan(null)
    setSearch('')
  }

  function toggleType(name, on) {
    const next = new Set(selected)
    if (on) next.add(name)
    else next.delete(name)
    url.setCollisions({ types: availableTypes.filter((n) => next.has(n)) })
  }

  const count = collisions?.collisions?.length || 0

  return (
    <>
      <li className={isActiveTerrain ? undefined : 'disabled'}>
        <div
          className="me-layer-toggle"
          data-tooltip-id="app-tip"
          data-tooltip-content={
            isActiveTerrain
              ? 'Highlight coordinate collisions — duplicate positions, radius conflicts, and cross-event overlaps'
              : `Mission data belongs to ${activeTerrain} — switch back to that map to scan it`
          }
        >
          <Switch checked={open} disabled={!isActiveTerrain} onChange={setOpen}>
            <svg
              className={`me-toggle-icon${open ? ' me-toggle-icon-active' : ''}`}
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
              <line x1="12" y1="9" x2="12" y2="13" />
              <line x1="12" y1="17" x2="12.01" y2="17" />
            </svg>
            Collisions{count > 0 && <> <CountBadge value={count} /></>}
          </Switch>
        </div>
      </li>

      {open && (
        <li className="sub">
          <div className="me-collision-filter">
            <label className="me-collision-select-all">
              <input
                ref={selectAllRef}
                type="checkbox"
                checked={allChecked}
                disabled={!availableTypes.length}
                onChange={(e) =>
                  url.setCollisions({ types: e.target.checked ? availableTypes : [] })
                }
              />
              <span>Select All</span>
            </label>

            <Select
              className="me-collision-mode"
              aria-label="Collision scan"
              value={mode}
              onChange={changeMode}
              options={COLLISION_MODES.map((m) => ({ value: m, label: COLLISION_MODE_LABELS[m] }))}
            />

            <SearchInput
              boxClassName="search-field-wide"
              className="me-collision-search"
              type="text"
              placeholder="Filter types..."
              value={search}
              spellCheck={false}
              onChange={(e) => setSearch(e.target.value)}
            />

            <div className="me-collision-type-list">
              {names.loading && !names.data && <PanelNote>Loading…</PanelNote>}
              {names.data && !availableTypes.length && (
                <PanelNote>Nothing to filter.</PanelNote>
              )}
              {shown.map((name) => (
                <label key={name}>
                  <input
                    type="checkbox"
                    checked={selected.has(name)}
                    onChange={(e) => toggleType(name, e.target.checked)}
                  />
                  {name}
                </label>
              ))}
            </div>

            {modeHasOptions(mode) && (
              <>
                <div className="me-collision-option">
                  <Switch checked={crossEventOverlaps} onChange={(on) => url.setCollisions({ crossEventOverlaps: on })}>
                    Include cross-event overlaps
                  </Switch>
                </div>
                <div className="me-collision-option">
                  <Switch checked={radiusConflicts} onChange={(on) => url.setCollisions({ radiusConflicts: on })}>
                    Include radius conflicts
                  </Switch>
                </div>
              </>
            )}

            <div className="me-collision-actions">
              <button
                type="button"
                className="me-collision-scan-btn"
                disabled={collisionsLoading}
                onClick={() => runCollisionScan(buildScanPath(url.collisions, availableTypes))}
              >
                {collisionsLoading ? 'Scanning…' : 'Scan'}
              </button>
            </div>

            {collisions && (
              <div className="me-collision-summary">
                {count === 0
                  ? 'No collisions found.'
                  : `${count} collision${count === 1 ? '' : 's'} in ${
                      collisions.scanned_positions
                    } positions`}
                {collisions.skipped?.length > 0 && (
                  <div>Not scanned (could not be read): {collisions.skipped.join('; ')}</div>
                )}
              </div>
            )}
          </div>
        </li>
      )}
    </>
  )
}
