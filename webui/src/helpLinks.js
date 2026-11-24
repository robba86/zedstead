// Resolves the `zsm:<slug>` pseudo-scheme: a panel page named by slug, so
// text outside the router (the guide's steps, the Dashboard, Players) never
// hardcodes `/configs/events`, which would drift the moment a route moves.
// The slug resolves against the tables that already exist:
//
//   1. crossLinks.js LINKS   — the cross-link table (`mods`, `types`, `tp-price`…)
//   2. routes.js ROUTES      — any top-level route path (`doctor`, `backups`…)
//   3. pageHelp.js EDITORS   — any /configs/:id editor id (`serverdz`, `battleye`…)
//
// A slug that matches none of them resolves to null, so a typo or a deleted
// page never renders a dead link.
//
// The scheme began in app/data/HELP.md, which the panel rendered until the
// help moved to zedstead.com in 2026 Q4; the site shows those links as text.

import { LINKS } from './crossLinks.js'
import { ROUTES } from './routes.js'
import { EDITORS } from './pageHelp.js'

const ROUTE_PATHS = new Set(ROUTES.map((r) => r.path))

// Everything from the first `?` or `#` onward is carried through untouched, so
// a slug can deep-link: `mods?tab=Conflicts`, `cleanup#reset-traderplus`.
function splitSuffix(slug) {
  const i = slug.search(/[?#]/)
  return i === -1 ? [slug, ''] : [slug.slice(0, i), slug.slice(i)]
}

export function resolveHelpSlug(slug) {
  const [base, suffix] = splitSuffix(slug)
  if (!base) return null

  const entry = LINKS[base]
  if (entry) return entry.to + suffix

  const path = base.startsWith('/') ? base : `/${base}`
  if (ROUTE_PATHS.has(path)) return path + suffix

  if (Object.prototype.hasOwnProperty.call(EDITORS, base)) {
    return `/configs/${base}${suffix}`
  }

  return null
}
