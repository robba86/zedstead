// webui/src/components/Breadcrumbs.jsx
//
// Self-contained breadcrumb trail. Derives the trail from the current URL
// (useLocation) + the central ROUTES config — no per-page hardcoding. Uses
// matchPath (declarative-router safe), NOT useMatches (data-router only).
import { Link, matchPath, useLocation } from 'react-router-dom'
import { ROUTES, HOME } from '../routes'
import { useCrumbTitleFor } from '../crumbTitle'

const BY_PATH = Object.fromEntries(ROUTES.map((r) => [r.path, r]))

function resolveLabel(entry, params) {
  return typeof entry.label === 'function' ? entry.label(params) : entry.label
}

export default function Breadcrumbs() {
  const { pathname } = useLocation()
  // A page with a dynamic segment may publish a nicer name for itself than the
  // URL param (see crumbTitle.js) — e.g. the mod page, whose param is a
  // workshop id. Empty until (or unless) it does.
  const pageTitle = useCrumbTitleFor(pathname)

  let params = {}
  let matched = null
  for (const entry of ROUTES) {
    const m = matchPath(entry.path, pathname)
    if (m) {
      matched = entry
      params = m.params || {}
      break
    }
  }
  if (!matched || matched.breadcrumbs === false) return null

  // Walk parent chain up from the matched (current) entry.
  const chain = [matched]
  let cur = matched
  while (cur.parent && BY_PATH[cur.parent]) {
    cur = BY_PATH[cur.parent]
    chain.unshift(cur)
  }
  // Prepend Home unless we're already at the root.
  if (chain[0].path !== HOME.path) chain.unshift(HOME)

  if (chain.length <= 1) return null // don't render bare "Dashboard"

  return (
    <nav className="breadcrumbs" aria-label="Breadcrumb">
      <ol className="breadcrumb-list">
        {chain.map((entry, i) => {
          const isLast = i === chain.length - 1
          // Only the current (last) crumb needs the dynamic params — and only
          // it can be renamed by the page.
          const label = (isLast && pageTitle) || resolveLabel(entry, isLast ? params : {})
          return (
            <li key={entry.path} className="breadcrumb-item">
              {isLast ? (
                <span className="breadcrumb-current" aria-current="page">
                  {label}
                </span>
              ) : (
                <>
                  <Link className="breadcrumb-link" to={entry.path}>
                    {label}
                  </Link>
                  <span className="breadcrumb-sep" aria-hidden="true">
                    /
                  </span>
                </>
              )}
            </li>
          )
        })}
      </ol>
      {/* Optional per-route shortcut (routes.js `action`). */}
      {matched.action && (
        <Link className="breadcrumb-action" to={matched.action.to}>
          {matched.action.label}
        </Link>
      )}
    </nav>
  )
}
