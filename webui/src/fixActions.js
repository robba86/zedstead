// Quick-fix actions attached to a finding by app/ops/fix_hints.py.
//
// The two Conflicts tables (Doctor's Mod Conflicts card, Mods → Conflicts) and
// the Doctor's health-check rows all render the same `{kind, label, ...}` action
// dicts, but they can't share one dispatcher: installing from the Mods page goes
// through its options modal (TraderPlus config / mod XML), while the Doctor page
// starts the install job directly. So the *shapes* live here and each page keeps
// its own funnel.
//
// Every action reuses an endpoint that already existed, bar `copy_bikeys`
// (POST /api/mods/copy-bikeys — the targeted repair for a missing .bikey).

import { api } from './api'

// Fix kinds that download through SteamCMD: NeedsSteam greys them out while
// Steam is logged out. The others (copy bikeys, regenerate the unit, reset
// TraderPlus XML) work without it.
export const STEAM_ACTION_KINDS = new Set(['install_mods', 'install_server'])

// Installs the server where it is missing: the update pipeline's own two
// steps, not /maintenance/reinstall-server, which wipes the server dir first
// and would delete mpmissions and profiles when only the binary is missing.
// The Doctor's fix and the Dashboard's install card both start this job.
export const INSTALL_SERVER_JOB = {
  endpoint: '/api/update/run',
  body: { steps: ['update_server', 'finalize'] },
  name: 'Install DayZ server',
  kind: 'update',
}

// `mods: [{name, id}]` → the /api/mods/install body. By workshop id when the
// finding carries one (the reliable key, and the endpoint caches id → name), by
// name otherwise.
export function installBodyFor(mods = []) {
  const ids = mods.filter((m) => m.id).map((m) => String(m.id))
  const id_names = {}
  for (const m of mods) if (m.id && m.name) id_names[String(m.id)] = m.name
  const names = mods.filter((m) => !m.id && m.name).map((m) => m.name)
  return { names, ids, id_names }
}

export async function copyBikeys() {
  const res = await api.post('/api/mods/copy-bikeys', {})
  const copied = res?.copied ?? 0
  return {
    ...res,
    // Keys are read at startup, so the fix isn't live until the server restarts
    // — saying so here keeps both pages' toasts honest about it.
    message: copied
      ? `Copied ${copied} bikey(s) into keys/ — restart the server to apply.`
      : 'No bikeys found in the enabled mods.',
  }
}
