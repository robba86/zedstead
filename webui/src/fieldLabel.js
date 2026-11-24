// Readable names for config keys: the one place a raw file key
// (CleanupLifetimeDeadInfected, meleeStaminaModifier, transfer_message) is
// turned into words for a label (Cleanup lifetime dead infected). A field with
// a hand-written label keeps it; a field whose label is just its key gets the
// words, and the key itself goes into the tooltip, so it can still be looked
// up in a mod's docs or a support thread. Display only: the key sent on save
// is never touched.

// Mixed-case names that splitting at capitals would break apart.
const KEEP = ['RCon', 'BattlEye', 'DayZ', 'TraderPlus', 'PvP', 'PvE', 'VoN', 'IDs', 'NPCs']
// Lower-case words (snake_case keys) that read as acronyms.
const ACRONYMS = {
  ai: 'AI', id: 'ID', ids: 'IDs', xml: 'XML', json: 'JSON', ui: 'UI', hud: 'HUD', url: 'URL',
  ip: 'IP', fps: 'FPS', cpu: 'CPU', npc: 'NPC', npcs: 'NPCs', pvp: 'PvP', pve: 'PvE', rcon: 'RCon', gps: 'GPS',
  api: 'API', tls: 'TLS', zsm: 'Zedstead', steamcmd: 'SteamCMD',
}
const KEEP_SET = new Set(KEEP)
// One word: a kept name, an acronym before a capitalised word (XMLFile), a
// capitalised or lower-case word, an acronym, an ordinal (3rd) or a number.
const WORD = new RegExp(
  `(?:${KEEP.join('|')})(?![a-z])|[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\\d+(?:st|nd|rd|th)(?![a-z])|\\d+(?:\\.\\d+)?`,
  'g'
)

// "CleanupLifetimeDeadInfected" → "Cleanup lifetime dead infected". Takes one
// key, never a dotted path (the caller passes the last part). Acronyms keep
// their capitals (AI, ID, XML, CE, BE, RCon, UI, PvP, HUD); a lone capital
// other than an axis letter is a word of its own and goes lower case.
export function humanizeKey(key) {
  const text = String(key ?? '').replace(/^m_/, '')
  const words = text.split(/[_\-\s]+/).flatMap((part) => part.match(WORD) || [])
  if (!words.length) return text
  return words
    .map((w, i) => {
      if (KEEP_SET.has(w)) return w
      if (ACRONYMS[w]) return ACRONYMS[w]
      if (/^[A-Z]{2,}s?$/.test(w) || /^[XYZ]$/.test(w)) return w
      const lower = w.toLowerCase()
      return i === 0 ? lower.charAt(0).toUpperCase() + lower.slice(1) : lower
    })
    .join(' ')
}

// The last part of a field key: after a "." (cfggameplay's dotted paths),
// "--" (Core settings' element--attr) or ":" (state:foo).
export function keyLeaf(key) {
  return String(key ?? '').split(/\.|--|:/).pop()
}

// Whether `label` is just the field's key (or its last part) as the backend
// fell back to it, rather than a label someone wrote.
function isRawLabel(label, key) {
  if (typeof label !== 'string' || !label || /\s/.test(label)) return false
  const k = String(key ?? '')
  return label === k || k.endsWith(`.${label}`) || k.endsWith(`--${label}`) || k.endsWith(`:${label}`)
}

// {text, raw} for a form field or table column: `text` is what the label
// shows, `raw` the file's own name when `text` was made from it (else null).
export function fieldLabel(field) {
  const label = field.label ?? field.key
  if (!isRawLabel(label, field.key)) return { text: label, raw: null }
  return { text: humanizeKey(keyLeaf(label)), raw: label }
}

// A field's tooltip: its help, then the key as the file spells it, when the
// label does not show it and the help does not already name it. `where` names
// the place the key is spelled that way (Settings: config.json and the CLI).
// The backend's help marks a command with backticks for the CLI; a tooltip is
// plain text, so they are dropped here (night test 2026-11-22).
export function helpWithKey(help, raw, where = 'In the file') {
  const text = (help || '').replace(/`([^`\n]+)`/g, '$1')
  if (!raw || text.includes(raw)) return text
  const line = `${where}: ${raw}`
  return text ? `${text}\n\n${line}` : line
}
