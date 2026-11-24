// The Ctrl+K palette's client-side half (plans/75 P2).
//
// Everything here is already in the entry chunk (routes, crossLinks,
// pageHelp, guideSteps) or is the small pageTabs module, so building the list
// costs no download. The server-side half is GET /api/search-index
// (app/web/routers/search.py): settings, every config editor and its fields,
// the map editor's guide and the Mods help. CommandPalette merges the two.
//
// One entry shape for both halves:
//   { kind, title, sub, text, to, minRole }
// plus `stepId` (a Guide step: opens the drawer instead of navigating) or
// `href` (an outside page, opened in a new tab). `to` is a router path, so it
// stays on the instance (the router's basename adds the /i/<id>/ prefix).

import { ROUTES, minRoleFor } from './routes.js'
import { roleAtLeast } from './role.jsx'
import { LINKS, settingsSectionLink } from './crossLinks.js'
import { EDITORS, PAGE_HELP, SECTION_HELP } from './pageHelp.js'
import { STEPS, modTabLabel } from './guideSteps.js'
import {
  ANALYSIS_ECONOMY_SUBTABS,
  ANALYSIS_TABS,
  BACKUPS_TABS,
  CONFIGS_MODS_SUBTABS,
  CONFIGS_TABS,
  MODS_INSTALL_SUBTABS,
  MODS_TABS,
  PLAYERS_MODERATOR_TABS,
  PLAYERS_TABS,
  PRESETS_TABS,
  INSTANCES_TABS,
  SCHEDULER_TABS,
} from './pageTabs.js'

// The pages with a tab bar, and what each tab needs beyond the page's role.
const PAGE_TABS = [
  { path: '/backups', tabs: BACKUPS_TABS },
  { path: '/analysis', tabs: ANALYSIS_TABS, subtabs: { Economy: ANALYSIS_ECONOMY_SUBTABS } },
  {
    path: '/configs',
    tabs: CONFIGS_TABS,
    subtabs: { Mods: CONFIGS_MODS_SUBTABS },
  },
  {
    path: '/players',
    tabs: PLAYERS_TABS,
    minRole: (tab) => (PLAYERS_MODERATOR_TABS.includes(tab) ? 'moderator' : null),
  },
  { path: '/presets', tabs: PRESETS_TABS },
  { path: '/instances', tabs: INSTANCES_TABS },
  { path: '/scheduler', tabs: SCHEDULER_TABS },
  { path: '/mods', tabs: MODS_TABS, subtabs: { Install: MODS_INSTALL_SUBTABS }, label: modTabLabel },
]

// Where a scope of SECTION_HELP lives. Settings sections are entries of their
// own (the page's filter shows a whole section when the filter is its name);
// the two editors' section hints only add words to the editor's own entry.
const SETTINGS_SECTION = settingsSectionLink
const EDITOR_SECTION_SCOPES = ['coresettings', 'runcommand']

function query(path, params) {
  const qs = new URLSearchParams(params).toString()
  return qs ? `${path}?${qs}` : path
}

function pathOf(to) {
  return to.split(/[?#]/)[0]
}

function labelOf(path) {
  const route = ROUTES.find((r) => r.path === path)
  return typeof route?.label === 'string' ? route.label : path
}

function pageEntries() {
  return ROUTES.filter((r) => !r.path.includes(':') && typeof r.label === 'string' && r.path !== '/wizard').map(
    (r) => ({
      kind: 'page',
      title: r.label,
      sub: r.href ? 'zedstead.com' : 'Page',
      text: PAGE_HELP[r.path] || EDITORS[r.path.replace('/configs/', '')] || '',
      ...(r.href ? { href: r.href, to: r.href } : { to: r.path }),
      minRole: r.minRole,
    })
  )
}

function tabEntries() {
  const out = []
  for (const page of PAGE_TABS) {
    const pageLabel = labelOf(page.path)
    const pageRole = minRoleFor(page.path)
    const label = page.label || ((t) => t)
    for (const tab of page.tabs) {
      const role = page.minRole?.(tab) || pageRole
      out.push({
        kind: 'tab',
        title: label(tab),
        sub: `${pageLabel} tab`,
        text: '',
        to: query(page.path, { tab }),
        minRole: role,
      })
      for (const sub of page.subtabs?.[tab] || []) {
        out.push({
          kind: 'tab',
          title: sub,
          sub: `${pageLabel} · ${label(tab)}`,
          text: '',
          to: query(page.path, { tab, subtab: sub }),
          minRole: role,
        })
      }
    }
  }
  return out
}

function linkEntries() {
  return Object.values(LINKS).map((l) => ({
    kind: 'page',
    title: l.label,
    sub: 'Link',
    text: l.tip || '',
    to: l.to,
    minRole: minRoleFor(pathOf(l.to)),
  }))
}

function sectionEntries() {
  return Object.entries(SECTION_HELP.settings || {}).map(([title, text]) => ({
    kind: 'section',
    title,
    sub: 'Settings section',
    text,
    to: SETTINGS_SECTION(title),
    minRole: minRoleFor('/settings'),
  }))
}

// Text that only enriches an entry another source made: the editor intros in
// pageHelp.EDITORS and the editors' section hints. Never a result of its own.
export function enrichEntries() {
  const out = Object.entries(EDITORS).map(([id, text]) => ({ to: `/configs/${encodeURIComponent(id)}`, text }))
  for (const scope of EDITOR_SECTION_SCOPES) {
    for (const [title, text] of Object.entries(SECTION_HELP[scope] || {})) {
      out.push({ to: `/configs/${scope}`, text: `${title}: ${text}` })
    }
  }
  return out
}

function guideEntries() {
  return STEPS.map((s) => ({
    kind: 'guide',
    title: s.title,
    sub: 'Guide step',
    text: [...(s.body || []), s.note?.text || ''].filter((t) => typeof t === 'string').join('\n'),
    to: `#guide-${s.id}`,
    stepId: s.id,
    minRole: 'viewer',
  }))
}

// Every client-side entry the role may open. Settings sections address the
// filter box by title (`/settings?q=Discord`), which the page's filter matches
// against section names as well as keys.
export function clientEntries(role) {
  return [...pageEntries(), ...tabEntries(), ...linkEntries(), ...sectionEntries(), ...guideEntries()].filter((e) =>
    roleAtLeast(role, e.minRole)
  )
}

// Merge the server's entries into the client's, one entry per `to`. The first
// entry for a `to` keeps its title and kind; a later one only adds its text,
// so a page that the server also describes (the Mods tabs) is one result.
// `enrich` entries add text to an entry that exists and are dropped otherwise.
export function mergeEntries(client, server, enrich = []) {
  const byTo = new Map()
  const out = []
  const addText = (known, text) => {
    if (text && !known.text.includes(text)) known.text = known.text ? `${known.text}\n${text}` : text
  }
  for (const e of [...client, ...server]) {
    const known = byTo.get(e.to)
    if (known) {
      addText(known, e.text)
      continue
    }
    const copy = { ...e, minRole: e.minRole || e.min_role, text: e.text || '' }
    byTo.set(e.to, copy)
    out.push(copy)
  }
  for (const e of enrich) {
    const known = byTo.get(e.to)
    if (known) addText(known, e.text)
  }
  return out
}
