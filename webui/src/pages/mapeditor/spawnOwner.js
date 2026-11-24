// Who owns a spawn position, and the URL that writes it.
//
// GET /spawns gives every event an `owner`: null for the mission's own, the
// `@user:<id>` marker of a manual XML file, or a mod's name (`@Bicycles`), plus
// `entry`, which numbers the events that share a name and an owner. A write
// names both, so it lands on the entry that was dragged even when the mission
// holds the same event name more than once. The server decides where the
// write goes (the mission, or the manual file then re-applied). A mod's
// position is written only with `copy`: the server first copies the mod's
// cfgeventspawns file into a manual XML file (the Mod XML page's Take over)
// and writes the change into the copy; without it the write is a 409. The
// page asks first (CopyIntoManualFile.jsx).

export const MANUAL_PREFIX = '@user:'

export function isManualOwner(owner) {
  return typeof owner === 'string' && owner.startsWith(MANUAL_PREFIX)
}

/** A mod's position: the next re-configure puts it back, so a change first copies the mod's file. */
export function isModOwner(owner) {
  return Boolean(owner) && !isManualOwner(owner)
}

/**
 * `/spawns/<event>[/<index>]?owner=…&entry=…` for one event entry. `copy`
 * (a mod file, or '' for the one the merge record names) asks the server to
 * copy the mod's file into a manual file before the write.
 */
export function spawnPath(event, posIndex, suffix = '', copy = null) {
  const params = new URLSearchParams({ owner: event.owner || '', entry: String(event.entry || 0) })
  if (copy != null) params.set('copy', copy)
  const index = posIndex == null ? '' : `/${posIndex}`
  return `/spawns/${encodeURIComponent(event.name)}${index}${suffix}?${params}`
}

/** The same event entry in a fresh payload, or undefined. */
export function findEventEntry(events, name, owner, entry) {
  return (events || []).find(
    (e) => e.name === name && (e.owner || null) === (owner || null) && (e.entry || 0) === (entry || 0)
  )
}

/** One sentence on where a change to this position is kept; '' for the mission's own. */
export function ownerNote(owner) {
  if (isManualOwner(owner)) {
    return `From your manual XML file "${owner.slice(MANUAL_PREFIX.length)}". A change here is saved into that file, so a re-configure keeps it.`
  }
  if (owner) {
    return `From ${owner}. A re-configure puts it back from the mod, so a change here first copies the mod's cfgeventspawns file into a manual XML file, and the change is saved into that copy.`
  }
  return ''
}
