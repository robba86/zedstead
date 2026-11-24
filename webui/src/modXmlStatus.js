// webui/src/modXmlStatus.js
//
// How a mod XML row's `status` (app/xml/xml_report.py's STATUS_* values) is
// worded and coloured. Shared by the Mod XML report and the per-mod page's
// file browser so one status never gets two names — keep the keys in step
// with xml_report.py.
//
// The labels say what the admin needs to know, not how zsm applied the file:
// "Active" for both linked and merged, because the page's "Goes into" column
// already names the file it landed in. `tip` carries the how.
export const XML_STATUS = {
  linked: {
    label: 'Active',
    short: 'Active',
    tone: 'ok',
    tip: 'Copied into the mission’s custom/ folder and listed in cfgeconomycore.xml.',
  },
  merged: {
    label: 'Active',
    short: 'Active',
    tone: 'ok',
    tip: 'Added to the mission file under “Goes into”, inside a marked block.',
  },
  disabled: {
    label: 'Mod is off',
    short: 'Mod is off',
    tone: 'muted',
    tip: 'The mod is disabled, so none of its XML is used.',
  },
  excluded: {
    label: 'Off',
    short: 'Off',
    tone: 'muted',
    tip: 'You turned this file off. Updates and installs leave it out.',
  },
  deleted: {
    label: 'Deleted',
    short: 'Deleted',
    tone: 'muted',
    tip: 'You deleted this file. Updates and installs won’t bring it back.',
  },
  other_map: {
    label: 'For another map',
    short: 'Other map',
    tone: 'muted',
    tip: 'Made for a different map than the one this mission uses, so it is skipped.',
  },
  no_target: {
    label: 'Not applied yet',
    short: 'Not applied',
    tone: 'warn',
    tip: 'Not in the mission yet. “Re-apply all” puts it there.',
  },
  // The merge skips a mod folder whose name has "--" or ends in "-": that
  // name cannot sit in the START/END MOD comments (xml_merge.marker_name_ok).
  bad_name: {
    label: 'Folder name blocks it',
    short: 'Bad name',
    tone: 'warn',
    tip: 'The mod folder name has “--” or ends in “-”, so Zedstead cannot mark its block in the mission file. Rename the folder, then “Re-apply all”.',
  },
  // messages is the one CE kind never linked off a mod dir: a mod's example
  // broadcasts would otherwise go out to real players. Opt in per file by
  // taking it over.
  not_auto_linked: {
    label: 'Not used — take over to use',
    short: 'Not used',
    tone: 'muted',
    tip: 'A mod’s messages.xml is never used on its own: its example messages would be sent to your players.',
  },
  invalid: {
    label: 'Broken XML',
    short: 'Broken XML',
    tone: 'error',
    tip: 'Zedstead could not read this file, so it is skipped.',
  },
  ignored: {
    label: 'Not loot or event XML',
    short: 'Not used',
    tone: 'muted',
    tip: 'Not a file the economy or a mission file can load, so it is skipped.',
  },
}

// How a file reaches the mission — the badge beside "Goes into". DayZ loads
// extra economy files only through cfgeconomycore.xml, so those are copied to
// custom/ and *linked*; every other mission file is read from one fixed place,
// so content for it has to be *merged* into that file.
export const XML_HOW = {
  linked: {
    label: 'Linked',
    tip:
      'Copied to the mission’s custom/ folder and listed in cfgeconomycore.xml. ' +
      'That is how DayZ loads extra loot, event and spawn files.',
  },
  merged: {
    label: 'Merged',
    tip:
      'Put inside this mission file, in a marked block. DayZ reads this file only ' +
      'from the mission, so it can’t be linked like a custom/ file.',
  },
}

export const XML_STATUS_ORDER = [
  'linked',
  'merged',
  'no_target',
  'bad_name',
  'not_auto_linked',
  'invalid',
  'excluded',
  'deleted',
  'disabled',
  'other_map',
  'ignored',
]

// What a file holds, in words. Keyed by the report's `kind` (a CE type or a
// merge config name); the raw kind stays available as the tooltip.
export const XML_KIND_LABEL = {
  types: 'Loot items',
  events: 'Dynamic events',
  spawnabletypes: 'Spawn attachments',
  globals: 'Globals',
  economy: 'Economy switches',
  messages: 'Server messages',
  cfgeventspawns: 'Event positions',
  cfgeventgroups: 'Event groups',
  cfgrandompresets: 'Random presets',
  cfglimitsdefinition: 'Usage & value flags',
  cfglimitsdefinitionuser: 'User flag groups',
}

export function pillClass(tone) {
  if (tone === 'ok') return 'status-pill status-pill-success'
  if (tone === 'warn') return 'status-pill status-pill-running'
  if (tone === 'error') return 'status-pill status-pill-failed'
  return 'status-pill status-pill-neutral'
}

// Does a mod name from one place name the same mod as another? `@` is
// optional and case does not matter, as in app/xml/xml_manual.py::mod_matches.
// The Mods page's XML link marks a mod's rows on Custom config with it.
export function sameMod(a, b) {
  const norm = (v) => String(v || '').trim().replace(/^@/, '').toLowerCase()
  return !!norm(a) && norm(a) === norm(b)
}
