// webui/src/crumbTitle.js
//
// Lets the page that owns a dynamic route tell the breadcrumb what to call it.
// routes.js can only see the URL param — `/mods/1559212036` crumbs as
// "1559212036" — while the page knows the mod's real name once it has loaded.
//
// A tiny external store rather than a React context: <Breadcrumbs> renders
// ABOVE the page inside Layout, so a context would mean wrapping the whole
// shell to carry one string. Keyed by pathname so a title can never leak onto
// the next page while that page's own data is still loading.
import { useEffect, useSyncExternalStore } from 'react'

let current = { path: '', title: '' }
const subs = new Set()

function emit() {
  for (const fn of subs) fn()
}

function subscribe(fn) {
  subs.add(fn)
  return () => subs.delete(fn)
}

function snapshot() {
  return current
}

export function setCrumbTitle(path, title) {
  if (current.path === path && current.title === title) return
  current = { path, title }
  emit()
}

export function clearCrumbTitle(path) {
  if (current.path !== path) return
  current = { path: '', title: '' }
  emit()
}

// Page side: publish `title` for `path` while mounted. Pass an empty title
// while the data is still loading — the breadcrumb falls back to the route's
// own label rather than flashing a blank crumb.
export function useCrumbTitle(path, title) {
  useEffect(() => {
    setCrumbTitle(path, title || '')
    return () => clearCrumbTitle(path)
  }, [path, title])
}

// Breadcrumb side: the title published for `path`, or '' when there is none.
export function useCrumbTitleFor(path) {
  const state = useSyncExternalStore(subscribe, snapshot, snapshot)
  return state.path === path ? state.title : ''
}
