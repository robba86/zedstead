// Collision scan modes: the four the vanilla editor offered, with the
// endpoints and query keys each one uses.
//
// The URL contract is fixed on both ends — app/ops/collision_ops.py's
// build_collision_query writes `collisions=<mode>&types=a,b`
// `&cross-event-overlaps=true&radius-conflicts=true`, the Analysis page links
// straight in with it, and app/web/routers/analysis.py reads the same names
// back. Renaming a key here breaks that deep link silently.

export const COLLISION_MODE_LABELS = {
  eventspawns: 'Event Spawns',
  playerspawns: 'Player Spawns',
  territories: 'Territories',
  all: 'All',
}

// Display names for Collision.kind (app/ops/collision_ops.py), shared by the
// map editor's popup and the Analysis page's Collisions tab.
export const COLLISION_KIND_LABELS = {
  exact_duplicate: 'DUPLICATE',
  radius_conflict: 'RADIUS CONFLICT',
  cross_event_overlap: 'CROSS-EVENT OVERLAP',
  playerspawn_duplicate: 'PLAYER SPAWN DUPLICATE',
  playerspawn_cluster: 'PLAYER SPAWN CLUSTER',
  playerspawn_cross_section: 'PLAYER SPAWN CROSS-SECTION',
  territory_overlap: 'ZONE OVERLAP',
  territory_contained: 'ZONE CONTAINED',
  effectarea_overlap: 'EFFECT AREA OVERLAP',
  effectarea_contained: 'EFFECT AREA CONTAINED',
  safezone_overlap: 'SAFEZONE OVERLAP',
  trader_npc_proximity: 'TRADER NPC',
  garage_parking_proximity: 'GARAGE PARKING',
  spawn_in_safezone: 'SPAWN IN SAFEZONE',
  spawn_in_territory: 'SPAWN IN TERRITORY',
  playerspawn_in_effectarea: 'PLAYER IN EFFECT AREA',
}

const MODE_NAMES_ENDPOINT = {
  eventspawns: '/collisions/eventspawn-names',
  playerspawns: '/collisions/playerspawn-section-names',
  territories: '/collisions/territory-file-names',
  all: '/collisions/eventspawn-names',
}

const MODE_SCAN_ENDPOINT = {
  eventspawns: '/collisions/eventspawns',
  playerspawns: '/collisions/playerspawns',
  territories: '/collisions/territories',
  all: '/collisions/all',
}

// The scan endpoints take the type filter under a different name per mode.
const MODE_TYPE_PARAM = {
  eventspawns: 'events',
  playerspawns: 'sections',
  territories: 'files',
  all: 'events',
}

/** The check_radius / check_overlap flags only apply to event-spawn scans. */
export function modeHasOptions(mode) {
  return mode === 'eventspawns' || mode === 'all'
}

export function namesEndpoint(mode) {
  return MODE_NAMES_ENDPOINT[mode] || null
}

/**
 * Editor path for a scan, or null when the mode is unset.
 *
 * `availableTypes` is the full list for the mode: when everything is selected
 * the filter is omitted entirely (the server then scans all of them), matching
 * the vanilla behaviour and keeping the URL short.
 */
export function buildScanPath({ mode, types, crossEventOverlaps, radiusConflicts }, availableTypes) {
  const base = MODE_SCAN_ENDPOINT[mode]
  if (!base) return null

  const query = new URLSearchParams()
  const typeParam = MODE_TYPE_PARAM[mode]
  const selected = types || []
  const total = availableTypes?.length || 0
  if (typeParam && selected.length && !(total && selected.length >= total)) {
    query.set(typeParam, selected.join(','))
  }
  if (modeHasOptions(mode)) {
    if (radiusConflicts) query.set('check_radius', 'true')
    if (crossEventOverlaps) query.set('check_overlap', 'true')
  }

  const suffix = query.toString()
  return suffix ? `${base}?${suffix}` : base
}
