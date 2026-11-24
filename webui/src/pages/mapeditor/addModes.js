// "Click the map to place a thing" modes for the TraderPlus entities.
//
// Event spawns and player spawns place into an existing container, so they
// only need to know which one. These four create a brand new record, and three
// of them need a classname before they can — hence the extra text field in the
// add-mode bar, and this table describing it.
//
// Unlike the spawn add modes these are one-shot: the vanilla editor exited the
// mode as soon as one was placed, because you rarely want ten identical
// traders in a row and a stuck crosshair over the whole map is expensive here.

export const MAP_ADD_MODES = {
  safezone: {
    label: 'Safe Zone',
    endpoint: '/safezones',
  },
  'trader-npc': {
    label: 'Trader NPC',
    endpoint: '/traders',
    classField: 'name',
    classDefault: 'SurvivorM_Peter',
    classPlaceholder: 'NPC classname (e.g. SurvivorM_Jose, TraderPlusSign)',
  },
  'trader-object': {
    label: 'Trader Object',
    endpoint: '/trader-objects',
    classField: 'object_name',
    classDefault: 'Land_City_Stand_FastFood',
    classPlaceholder: 'Object classname (e.g. bldr_Misc_CrashBarrier)',
  },
  'garage-npc': {
    label: 'Garage NPC',
    endpoint: '/garage',
    classField: 'class_name',
    classDefault: 'SurvivorM_Peter',
    classPlaceholder: 'NPC classname (e.g. SurvivorM_Peter)',
  },
  // The admin's own place names (plans/87, panels/OwnPlaceNames.jsx). The
  // bar's text field is the name. The label file is not watched, so the
  // click handler reloads the locations itself (`reload`).
  location: {
    label: 'Place name',
    endpoint: '/locations/own',
    classField: 'name',
    classDefault: '',
    classPlaceholder: 'Place name (e.g. Old Mill)',
    fieldLabel: 'Place name',
    reload: 'locations',
  },
  // A lasting object in Zedstead's Object Spawner file (the Items tab). The
  // server puts it on the ground; the bar's Y box overrides that, and is how
  // a map without terrain heights places one at all.
  object: {
    label: 'Object',
    endpoint: '/object-spawner',
    classField: 'name',
    classDefault: '',
    classPlaceholder: 'Object classname',
    yField: true,
    reload: 'objects',
  },
  'location-move': {
    label: 'Move place name',
    endpoint: (addMode) => ownPlacePath(addMode),
    method: 'put',
    reload: 'locations',
  },
}

/**
 * The route for one own place name (`{index, name}`). A write goes by the
 * place's index, and the name it showed rides along (`expect`), so the server
 * answers 409 instead of changing another place when the list is stale.
 */
export function ownPlacePath(place) {
  return `/locations/own/${place.index}?expect=${encodeURIComponent(place.name)}`
}

/** The endpoint *addMode* writes to: a fixed path, or one built from the mode. */
export function addEndpoint(addMode) {
  const config = MAP_ADD_MODES[addMode.mode]
  return typeof config.endpoint === 'function' ? config.endpoint(addMode) : config.endpoint
}

/** Request body for placing *mode* at lat/lng. */
export function buildAddBody(addMode, lat, lng) {
  const config = MAP_ADD_MODES[addMode.mode]
  if (!config) return null

  if (addMode.mode === 'location') return { lat, lng, name: (addMode.className || '').trim() }
  if (addMode.mode === 'location-move') return { lat, lng }
  if (addMode.mode === 'object') {
    const y = String(addMode.y ?? '').trim()
    // Duplicate carries the copied object's yaw; a new placement faces north.
    return {
      lat,
      lng,
      name: (addMode.className || '').trim(),
      ...(y !== '' && { y: Number(y) }),
      ...(addMode.yaw != null && { yaw: addMode.yaw }),
    }
  }

  if (addMode.mode === 'safezone') {
    // TraderPlus' own defaults for a fresh zone.
    return { lat, lng, radius: 80, countdown: 15 }
  }

  const body = { lat, lng, game_y: 0 }
  const className = (addMode.className || '').trim()
  if (className) body[config.classField] = className

  // Cloning an NPC carries its stock (Id) and identity across; the position is
  // the only thing the user is choosing.
  const clone = addMode.clone
  if (clone) {
    if (clone.id != null) body.id = clone.id
    if (clone.given_name) body.given_name = clone.given_name
    if (clone.role) body.role = clone.role
    if (clone.clothes) body.clothes = clone.clothes
  }
  return body
}
