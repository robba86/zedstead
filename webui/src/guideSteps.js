// The Guide's step list, its per-browser progress record, and the link
// resolver both guide surfaces use.
//
// Data only, so the guide drawer (components/GuideDrawer.jsx) and the
// Dashboard's compact Guide card (components/GuideCard.jsx) count the same
// steps and read the same progress without a second copy of either. Open/close
// and progress *state* live in guide.jsx; this module owns the content.
//
// Named "Wizard" until 2026 Q3, when the page became a drawer. The
// localStorage keys below deliberately kept their `zsm.wizard.*` names — a
// rename would silently reset every existing user's progress.
//
// COPY: nothing here is written for the wizard. The step order and wording
// mirror the `## Getting started` section of the web UI page on zedstead.com
// (`src/docs/web-ui.md` in the zedstead-site repo), and the per-page paragraphs are the same PAGE_HELP / EDITORS /
// crossLinks `tip` strings every other page shows. Change that getting-
// started list and the steps below should follow it.
//
// PROGRESS is per-browser (localStorage, `zsm.wizard.*`) and is never written
// server-side — but since 2026 Q3 it is no longer the whole answer. A step
// carrying a `detect` key is also answered by GET /api/setup-state, and
// mergeProgress() below decides which of the two wins. Reads/writes stay
// best-effort, like jobs.jsx's.

import { EDITORS, PAGE_HELP } from './pageHelp.js'
import { LINKS, MAP_EDITOR_LINKS } from './crossLinks.js'
import { ROUTES } from './routes.js'
import { resolveHelpSlug } from './helpLinks.js'
import { instanceKey } from './instance.js'

// Progress is a server's (plans/62): each instance is set up on its own.
export const PROGRESS_KEY = instanceKey('zsm.wizard.progress') // localStorage: { stepId: 'done'|'skipped' }
export const OPEN_KEY = 'zsm.wizard.open' // localStorage: id of the expanded step
export const DRAWER_KEY = 'zsm.guide.drawer' // localStorage: '1' open, '0' closed, absent = never chosen (opens)
export const WIDTH_KEY = 'zsm.guide.width' // localStorage: drawer width in px

// Mods tabs worth naming during setup, in the order a first-timer meets them.
// The copy comes from GET /api/mods/help (app/shared/mods_help.py) — the one
// source for mod-tab text; it is deliberately not duplicated into pageHelp.js.
export const MOD_TABS = ['Install', 'Installed', 'Conflicts', 'Updates']

// What a Mods tab is called on screen. The key stays the `?tab=` value, so
// every `/mods?tab=Conflicts` link (Doctor, log findings) still lands.
const MOD_TAB_LABELS = { Conflicts: 'Check mods' }
export const modTabLabel = (tab) => MOD_TAB_LABELS[tab] ?? tab

// --- steps ------------------------------------------------------------------
//
// body : paragraphs (plain text — no markdown parsing here).
// defs : term + explanation, each optionally linking to the page that owns it.
// note : a callout under the body; `level` picks the accent colour.
// links: slugs resolved through helpLinks.js (crossLinks LINKS -> routes ROUTES
//        -> pageHelp EDITORS), so no route is spelled out twice. `label` is only
//        needed for /configs/:id editor ids, which have no label table.
// detect: the key this step has in GET /api/setup-state's `steps` map. Present
//        only where the server can honestly answer "is this done?" — a step
//        without it is hand-ticked exactly as before.
// action: at most ONE button, hidden once `detect` says the step is done.
//        `job` goes through jobs.jsx::startJob (Activity drawer + toast),
//        `post` is a plain POST for an action that is not a job. Both name an
//        endpoint that already exists — the Guide adds no API of its own.
//
//        Only where **one button is the whole step**. `maps`, `mods`, `presets`
//        and `configs` deliberately have none: each is a choice, not a
//        command, and it needs the page's own UI. A button that merely
//        navigates is what the link beside it already is, and dressing it as a
//        button makes the step look like it does something it does not.
//        `minRole` is the role the endpoint needs (app/web/permissions.py);
//        absent means admin, the backend's own default for a write. Below it
//        the button is not drawn — its only outcome would be a 403.
// steamLogin: render components/SteamLoginCard.jsx inline (the `steam` step
//        only — a login is a form, not a button). Admin only, like the route.

export const STEPS = [
  {
    id: 'steam',
    detect: 'steam',
    title: 'Log in to Steam',
    body: [
      'SteamCMD fetches both the DayZ server files and every Workshop mod from your Steam account, so nothing below can be installed until it has cached credentials.',
      'Steam Guard sends a code to your email or to the Steam mobile app — that code is why this step needs a person and cannot be automated. Your password reaches SteamCMD in a private file that is deleted as soon as SteamCMD is done. Zedstead never stores it; only the username is saved.',
    ],
    // No `action`: the login is a form, not a button. The drawer renders the
    // existing components/SteamLoginCard.jsx inline here — it already handles
    // the two-phase Guard flow, the email/mobile distinction and the error
    // classification, and a second login form would have to repeat all three.
    steamLogin: true,
  },
  {
    id: 'install',
    detect: 'install',
    title: 'Install the server',
    body: [
      PAGE_HELP['/update'],
      'The button here runs only the first of those steps: SteamCMD installs the DayZ server files into your server directory. Mods come later, once you have picked them.',
    ],
    note: {
      level: 'tip',
      text: 'A first install is several GB and takes a while. It runs as a job, so you can navigate away — the log streams into the Activity drawer and a toast fires when it finishes.',
    },
    action: {
      label: 'Install server',
      busyLabel: 'Starting…',
      // Downloads through SteamCMD, so it is greyed out while Steam is
      // logged out (the step before this one logs in).
      needsSteam: true,
      // Not a new endpoint: `update_server` is an existing pipeline step key
      // and POST /api/update/run takes a subset of them.
      job: {
        endpoint: '/api/update/run',
        body: { steps: ['update_server'] },
        name: 'Install DayZ server',
        kind: 'update',
      },
    },
    links: [{ slug: 'update' }, { slug: 'quick-setup' }],
  },
  {
    id: 'maps',
    detect: 'maps',
    title: 'Pick the map',
    body: [
      PAGE_HELP['/maps'],
      'Pick the map you want to play (defaults to Chernarus). Required map mods and the mission folder are installed for you.',
    ],
    note: {
      level: 'tip',
      text: 'Tip: do one clean Start before any other edits, to confirm the server boots end to end. Always Stop the server before editing its files.',
    },
    links: [{ slug: 'maps' }],
  },
  {
    id: 'backup',
    detect: 'backup',
    title: 'Back up before you touch anything else',
    body: [
      "Every tool here edits your server's real files in place: mission XML, TraderPlus JSON, serverDZ.cfg, BattlEye. There is no staging area and no “save as” — an edit is on disk the moment you press Save.",
      'Once the server boots cleanly, take a Full backup, and keep taking Quick ones before risky changes.',
      PAGE_HELP['/backups'],
      'You get two safety nets, and they cover different things:',
    ],
    defs: [
      {
        term: 'Backups',
        slug: 'backups',
        text: 'The server directory and its persistence (players, vehicles, world). This is the one that survives a bad mod install, a wipe, or a broken mission. Nothing else replaces it.',
      },
      {
        term: 'Config versioning',
        slug: 'analysis',
        text: "Automatic, and only for single config files. Every save through the built-in editors snapshots the file first, so Analysis → Change history can show that file's history, diff any past version against the current one, and restore it. Restoring snapshots the current state first, so the rollback is itself reversible. Merged mod XML additionally leaves a .backup sibling per file.",
      },
    ],
    links: [{ slug: 'backups' }, { slug: 'analysis' }],
  },
  {
    id: 'mods',
    detect: 'mods',
    title: 'Install your mods',
    body: [
      'Install, update, enable/disable, and reorder your mods. Dependencies are resolved and pulled in for you.',
    ],
    modTabs: true,
    note: {
      level: 'tip',
      text: 'Already running this server with mods Zedstead did not install? Adopt it first, so the first update keeps them.',
    },
    links: [{ slug: 'adopt' }, { slug: 'mods' }, { slug: 'mod-xml' }],
  },
  {
    id: 'presets',
    title: 'Apply a preset',
    body: [
      PAGE_HELP['/presets'],
      'Apply an opinionated server setup, then use Quick tweaks to scale zombies, predators, wildlife, farm animals, vehicles, boats, food & drink, or gear.',
    ],
    links: [{ slug: 'presets' }],
  },
  {
    id: 'configs',
    title: 'Tune the config files',
    body: [
      PAGE_HELP['/configs'],
      'Gradual tweaks. Server files (serverDZ.cfg, Run Command, BattlEye), mission XML, TraderPlus JSON, and Mod XML.',
      'Every save is snapshotted first, so any edit is reversible from Analysis → Change history.',
    ],
    links: [
      { slug: 'configs' },
      { slug: 'serverdz', label: 'serverDZ.cfg' },
      { slug: 'runcommand', label: 'Run Command' },
      { slug: 'battleye', label: 'BattlEye' },
      { slug: 'types' },
      { slug: 'tp-price' },
      { slug: 'mod-xml' },
    ],
  },
  {
    id: 'map-editor',
    title: 'Place things on the map',
    body: [
      'See where things spawn, edit them, and add new spawns on a real map of your terrain. Includes TraderPlus.',
      'A Leaflet map of your terrain, with a collision overlay and three tabs: Spawns (event spawns and their metadata), Player Spawns, and Traders (TraderPlus NPCs, trader objects, safe zones, garages). Edits write to your mission as you go, and several admins can edit at once — changes appear live for everyone.',
      'Press ? in the editor for its own guide. Each section links back to the config editor for the file behind it, and the config editors link forward into the editor.',
    ],
    // The three tab deep-links come from crossLinks' MAP_EDITOR_LINKS table
    // (config editor id -> the editor set up to show that file's data), so the
    // ?tab=/?layers= combinations are not spelled out a second time here.
    links: [
      { slug: 'map-editor' },
      { to: MAP_EDITOR_LINKS.eventspawns, label: 'Event spawns' },
      { to: MAP_EDITOR_LINKS.playerspawnpoints, label: 'Player spawns' },
      { to: MAP_EDITOR_LINKS['tp-general'], label: 'Traders', traderplus: true },
    ],
  },
  {
    id: 'scheduler',
    detect: 'scheduler',
    title: 'Schedule restarts and backups',
    body: [
      PAGE_HELP['/scheduler'],
      'On Linux these install as real systemd .timer units, so they fire whether or not zsm web is running. Restart warnings (in-game RCON countdown, optional pre-restart backup, optional restart lock) are configured in Configs → Restart warnings.',
    ],
    note: {
      level: 'tip',
      text: 'A scheduled backup task is the cheapest insurance here.',
    },
    links: [{ slug: 'scheduler' }, { slug: 'settings' }],
  },
  {
    id: 'start',
    detect: 'start',
    title: 'Start the server',
    body: [
      PAGE_HELP['/'],
      'Start / Stop / Restart, live server status (version, uptime, FPS, CPU, memory, players, next restart), the generated launch command with links to everything that feeds it, and a preview of the active map.',
    ],
    action: {
      label: 'Start the server',
      busyLabel: 'Starting…',
      post: '/api/service/start',
      minRole: 'moderator',
    },
    links: [{ slug: '/', label: 'Dashboard' }, { slug: 'update' }],
  },
  {
    id: 'activity',
    title: 'Watch the Activity drawer',
    body: [
      'Watch the Activity drawer at the bottom of the window for the live server log. On Windows the DayZ server also runs in its own console window.',
      'Long-running work (updates, mod installs, backups, map switches) runs as a job: you can navigate away while it runs, the log streams into the Activity drawer at the bottom, a toast fires when it finishes, and a page refresh does not lose it.',
    ],
    note: {
      level: 'tip',
      text: 'The drawer is the strip along the bottom of every page — click its handle to open it.',
    },
  },
  {
    id: 'cleanup',
    title: 'Know what Cleanup does before you need it',
    body: [
      PAGE_HELP['/cleanup'],
      'Wipe persistence, clear logs, migrate the player database, remove workshop mods, reset the mission, reinstall the server or SteamCMD, reset TraderPlus.',
    ],
    note: {
      level: 'warning',
      text: 'Most of it is irreversible. Re-read the backup step before you use any of it.',
    },
    links: [{ slug: 'cleanup' }, { slug: 'backups' }],
  },
]

export const STEP_IDS = new Set(STEPS.map((s) => s.id))

// Always-available, not a step: the Help page carries it as an aside after the list.
export const DOCTOR = {
  text: 'Run Doctor at any time. It validates config, server install, SteamCMD, mods, mission, ports, and service state, and changes nothing. Findings link straight to the page that fixes them.',
  slug: 'doctor',
}

// --- link resolution --------------------------------------------------------

// Resolve one step link to {to, label, tip}. `slug` may carry a ?query/#hash
// suffix (resolveHelpSlug passes it through), e.g. `mods?tab=Conflicts`.
export function stepLink(slug, labelOverride) {
  const to = resolveHelpSlug(slug)
  if (!to) return null
  const base = slug.split(/[?#]/)[0]
  const entry = LINKS[base]
  const path = base.startsWith('/') ? base : `/${base}`
  const route = ROUTES.find((r) => r.path === path)
  const label =
    labelOverride || entry?.label || (typeof route?.label === 'string' ? route.label : base)
  // Tooltip copy, in order of specificity: the cross-link one-liner, the config
  // editor's intro, the page intro. All three already exist for other surfaces.
  const tip = entry?.tip || EDITORS[base] || PAGE_HELP[path] || ''
  return { to, label, tip, traderplus: !!entry?.traderplus }
}

// --- persistence ------------------------------------------------------------

export function readProgress() {
  try {
    const raw = localStorage.getItem(PROGRESS_KEY)
    const parsed = raw ? JSON.parse(raw) : null
    if (!parsed || typeof parsed !== 'object') return {}
    // Drop ids that no longer exist so a renamed step can't wedge the count.
    return Object.fromEntries(Object.entries(parsed).filter(([id]) => STEP_IDS.has(id)))
  } catch {
    return {}
  }
}

export function writeStored(key, value) {
  try {
    if (value === null || value === undefined) localStorage.removeItem(key)
    else localStorage.setItem(key, typeof value === 'string' ? value : JSON.stringify(value))
  } catch {
    // storage full/unavailable — persistence is best-effort
  }
}

// Server state decides when it can, the hand tick decides when it cannot.
//
// `detected[key].done === true` wins over anything stored: a step the server
// can see is finished is finished, and showing it as pending is the complaint
// this whole change answers. Everything else falls back to the stored value —
// a probe that says "not yet" must never UN-tick a step the user ticked off,
// and a failed fetch (`detected` null/undefined) must behave exactly like the
// build before any of this existed.
//
// `skipped` is only ever a stored value; nothing detected produces it. The
// stored record is returned unchanged when nothing is added, so a caller can
// use the result as a `useMemo` value without re-rendering on every poll.
export function mergeProgress(stored, detected) {
  if (!detected) return stored
  let merged = null
  for (const step of STEPS) {
    if (!step.detect || !detected[step.detect]?.done) continue
    if (stored[step.id] === 'done') continue
    merged = merged || { ...stored }
    merged[step.id] = 'done'
  }
  return merged || stored
}

// First step the user hasn't dealt with yet (falls back to the first step).
// Takes the MERGED record — there is deliberately no second code path for the
// detected half, here or in countProgress.
export function firstPending(progress) {
  return STEPS.find((s) => !progress[s.id]) || STEPS[0]
}

// {done, handled, total} — `handled` counts skipped steps too, which is what
// the progress bar fills on. Takes the merged record (see mergeProgress).
export function countProgress(progress) {
  return {
    done: STEPS.filter((s) => progress[s.id] === 'done').length,
    handled: STEPS.filter((s) => progress[s.id]).length,
    total: STEPS.length,
  }
}
