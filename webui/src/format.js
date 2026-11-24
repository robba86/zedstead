// Shared display formatters for server-status fields, used by both the
// persistent InfoBar and its edit mode (DRY).

export function formatUptime(seconds) {
  if (seconds == null) return '—'
  const total = Math.floor(seconds)
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  return `${hours}h ${String(minutes).padStart(2, '0')}m`
}

// The RPT header's "1.30.164078.38": the release admins talk about, then the
// build. Shown as one string it overflows a status tile, so the tiles show the
// release large and the build under it. Anything else passes through whole.
export function splitVersion(version) {
  const m = /^(\d+\.\d+)\.(.+)$/.exec(version ?? '')
  return m ? { release: m[1], build: m[2] } : { release: version ?? '—', build: null }
}

// Minutes until the next restart (GET /api/status `next_restart_minutes`:
// the soonest scheduled restart or update task, else messages.xml's
// countdown), in the steps the Scheduled tasks tile writes a next run.
export function formatNextRestart(minutes) {
  if (minutes == null) return '—'
  if (minutes < 1) return 'now'
  if (minutes < 60) return `in ${minutes}m`
  if (minutes < 1440) return `in ${Math.floor(minutes / 60)}h ${minutes % 60}m`
  return `in ${Math.floor(minutes / 1440)}d ${Math.floor((minutes % 1440) / 60)}h`
}

export function formatBytes(n) {
  if (!n && n !== 0) return '—'
  const units = ['B', 'KB', 'MB', 'GB']
  let value = n
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${unit === 0 ? value : value.toFixed(1)} ${units[unit]}`
}

// An ISO timestamp as a short local date+time ("12 Mar, 14:05"). Anything
// unparseable comes back verbatim rather than as "Invalid Date".
export function formatWhen(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return String(iso)
  return d.toLocaleString(undefined, {
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  })
}

// A full local date and time ("10/13/2026, 7:29:20 PM"), for a list where
// the year matters (backups, bundles). Takes what `new Date` takes: an ISO
// string with its offset, or epoch milliseconds.
export function formatDateTime(value) {
  if (value == null || value === '') return '—'
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? String(value) : d.toLocaleString()
}

// Maps a backend field-type string ("int"/"float"/"str"/…) to the extra HTML
// attributes a text <input> should carry, so numeric fields render as
// type="number" with a spinner + mobile numeric keypad instead of plain text.
// Bool fields are handled separately (checkbox) by each editor. (DRY: shared by
// FormEditor, SettingsPage, TableEditor.)
export function inputAttrsForType(type) {
  if (type === 'int') return { type: 'number', inputMode: 'numeric', step: 1 }
  if (type === 'float') return { type: 'number', inputMode: 'decimal', step: 'any' }
  if (type === 'url') return { type: 'url', inputMode: 'url' }
  return { type: 'text' }
}

// A 0/1 int whose key reads like an on/off setting (EnableX, CanX,
// XEnabled…) is drawn as a switch (FlagSwitch.jsx) and still saved as 0 or 1.
// The name decides, never the value alone: a count of 1 stays a number box.
// Read once per field by the caller, so switching to 0 keeps the switch.
const FLAG_KEY =
  /^(Enable|Disable|Can|Use|Allow|Is|Has|Log|Require|Show|Prevent|Override)[A-Z_]|Enabled$|^Vehicle.*(Require|Locked)/

// Names that match but hold more than two values, measured on the test
// container's 21-mod install: Expansion's EnableLamps (0-3),
// EnableFlankingOutsideCombat (-1/0/1), VehicleRequireKeyToStart; and any
// name that says it is a level, mode, type or count (LogLevel, ShowMode).
// A field with `choices` (Dynamic AI's *_Enabled modes, 0/1/2) never gets
// here: FormEditor gives choices the select first.
const NOT_FLAG = /^(EnableLamps|EnableFlankingOutsideCombat|VehicleRequireKeyToStart)$|(Level|Mode|Type|Count)$/

export function isFlagKey(key, value) {
  const v = typeof value === 'string' && value !== '' ? Number(value) : value
  const name = String(key || '')
  return (v === 0 || v === 1) && FLAG_KEY.test(name) && !NOT_FLAG.test(name)
}

// Mods write 32-bit floats out as doubles, so the file holds
// 0.05000000074505806 where the admin typed 0.05. Shown at 7 significant
// digits while untouched; the caller sends the original back unless edited.
export function displayFloat(v) {
  if (typeof v !== 'number' || Number.isInteger(v) || !Number.isFinite(v)) return v
  return Number(v.toPrecision(7))
}

// A 0.0-1.0 score (the economy report) as a whole percentage.
export function formatScore(score) {
  return score == null ? '—' : `${Math.round(score * 100)}%`
}
