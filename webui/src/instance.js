// Which instance this page is about (plans/62 §2.10, D1).
//
// One panel serves every instance: `/i/<id>/…` is that instance's page, and an
// unprefixed URL is the instance the panel started with (D8), which is all a
// single-instance install ever sees. The backend strips the prefix in one
// middleware (app/web/instance_prefix.py), so every API path, the socket and
// every page path below it are the same as before.
//
// Read once, at load: switching instance is a full page load (`instanceHref`),
// which resets every per-instance client state at once — caches, sockets,
// the guide — instead of each module having to notice.

const match = window.location.pathname.match(/^\/i\/([^/]+)/)

// The id, or '' for the unprefixed (startup) instance.
export const INSTANCE_ID = match ? decodeURIComponent(match[1]) : ''

// The URL prefix, or ''. The router's basename and every URL the page builds.
export const BASE = match ? `/i/${match[1]}` : ''

// `/api/…` → `/i/pvp/api/…` on pvp's page. Anything not starting with `/`
// (an absolute URL, a relative path) is left alone.
export function withBase(path) {
  return BASE && path.startsWith('/') ? BASE + path : path
}

// A storage key for something that belongs to one server, not to the browser:
// `zsm.serverStatus` → `zsm.serverStatus@pvp`. Unprefixed, the key is
// unchanged, so a single-instance install keeps what it stored.
export function instanceKey(key) {
  return INSTANCE_ID ? `${key}@${INSTANCE_ID}` : key
}

// Where instance *id*'s copy of *path* is. The startup instance's is
// unprefixed: its canonical address, and the one old bookmarks use.
export function instanceHref(id, startupId, path = '/') {
  return id === startupId ? path : `/i/${encodeURIComponent(id)}${path}`
}

// An instance's name in a picker's option, as text rather than a BranchBadge: the
// switcher, the sidebar's label and the sign-in picker all say "(Experimental)"
// in text (plans/66).
// The branch is added only when the label does not say it already: an
// instance named "Experimental" read "Experimental (Experimental)", which the
// sidebar squeezed to nothing.
export function instanceOptionLabel(row) {
  const says = /experimental/i.test(row.label || '')
  return row.branch === 'experimental' && !says ? `${row.label} (Experimental)` : row.label
}
