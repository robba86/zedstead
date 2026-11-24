// The server-telemetry fields, once.
//
// Two surfaces render them: the status panel on every page (InfoBar,
// `.info-bar-field`) and its edit mode (StatusFieldEditor, `.status-card`s).
// Only key/label/value/detail/tone live here; each surface keeps its own markup.
// `detail` is an optional second, smaller line under the value.
//
// `key` is a saved layout key (dashboardLayout.js) — renaming one drops that
// field out of every existing user's arrangement, so don't.
import { formatUptime, formatNextRestart, splitVersion } from './format'

// systemd's "deactivating": a Stop or Restart is still waiting for DayZ to
// exit (1.30 can take 90 s). `running` is false then, and the panel said
// "Stopped" and offered Start meanwhile.
export function isStopping(s) {
  return !s?.running && s?.restarts?.active_state === 'deactivating'
}

// The array order is the shipped field order (DEFAULT_STATUS_ORDER), set by
// the maintainer in 2026 Q4. It is also the order of the "add field" menus.
export const STATUS_FIELDS = [
  {
    key: 'status',
    label: 'Status',
    value: (s) => (s == null ? '—' : s.running ? 'Running' : isStopping(s) ? 'Stopping…' : 'Stopped'),
    tone: (s) => (s == null ? 'muted' : s.running ? 'good' : 'muted'),
  },
  { key: 'map', label: 'Map', value: (s) => s?.map ?? '—' },
  { key: 'max_players', label: 'Max Players', value: (s) => s?.max_players ?? '—' },
  {
    key: 'version',
    label: 'Version',
    value: (s) => splitVersion(s?.version).release,
    detail: (s) => {
      const { build } = splitVersion(s?.version)
      return build && `build ${build}`
    },
  },
  { key: 'uptime', label: 'Uptime', value: (s) => formatUptime(s?.uptime_seconds) },
  {
    key: 'next_restart',
    label: 'Next Restart',
    value: (s) => formatNextRestart(s?.next_restart_minutes),
  },
  {
    key: 'fps',
    label: 'FPS',
    value: (s) => s?.fps ?? '—',
    // DayZ logs FPS only with logAverageFps in serverDZ.cfg (status_poller.py).
    detail: (s) => s && s.fps == null && s.fps_logging === false && 'logAverageFps is off in serverDZ.cfg',
  },
  { key: 'cpu', label: 'CPU', value: (s) => s?.cpu ?? '—' },
  { key: 'memory', label: 'Memory', value: (s) => s?.memory ?? '—' },
  {
    // Null until the panel has measured it (right after it started).
    key: 'steam',
    label: 'Steam',
    value: (s) => (s?.steam_ok == null ? 'Not checked' : s.steam_ok ? 'Logged in' : 'Logged out'),
    tone: (s) => (s?.steam_ok ? 'good' : 'muted'),
  },
]

export const STATUS_FIELDS_BY_KEY = Object.fromEntries(STATUS_FIELDS.map((f) => [f.key, f]))
export const DEFAULT_STATUS_ORDER = STATUS_FIELDS.map((f) => f.key)

// The fields to render, in the viewer's saved order, hidden ones dropped.
// Unknown keys (a field removed since the layout was saved) fall out here.
export function resolveStatusFields(layout) {
  const hidden = new Set(layout?.hidden ?? [])
  const order = layout?.order?.length ? layout.order : DEFAULT_STATUS_ORDER
  return order
    .filter((key) => !hidden.has(key))
    .map((key) => STATUS_FIELDS_BY_KEY[key])
    .filter(Boolean)
}

// The hidden half, in registry order — what the "add field" menus list.
export function hiddenStatusFields(layout) {
  const hidden = new Set(layout?.hidden ?? [])
  return STATUS_FIELDS.filter((f) => hidden.has(f.key))
}
