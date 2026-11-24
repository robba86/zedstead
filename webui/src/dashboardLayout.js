// Where the Dashboard's layout is saved.
//
// Per-browser localStorage, best-effort try/catch — same class of state as
// `zsm.theme` and `zsm.wizard.progress`: it describes the viewer's window,
// not the server, so it is deliberately NOT a DayzConfig field and has no
// endpoint. Two admins on one server keep their own arrangements.
//
// Stored shape (one key owns all four axes, so "Reset layout" is one delete):
//
//   { order:  [cardId, ...]        every known card, hidden ones included
//     hidden: [cardId, ...]        subset of `order` that is not rendered
//     spans:  { cardId: 1..4 }     grid columns; missing = the card's default
//     status: { order: [key, ...], hidden: [key, ...] } }
//
// `order` keeps hidden cards so unhiding restores a card's position rather than
// dumping it at the end. Unknown ids (a card removed since the layout was
// saved) are dropped and cards the saved layout never knew about are appended —
// visible unless the registry ships them hidden — so a newly shipped card is
// never invisible behind a stale entry.
import { useSyncExternalStore } from 'react'
import { DEFAULT_STATUS_ORDER } from './statusFields.js'

const LAYOUT_KEY = 'zsm.dashboard.layout'
// Pre-hide/resize layouts: a bare array of card ids. Read once, on migration.
const LEGACY_ORDER_KEY = 'zsm.dashboard.order'
// What that key held for a user who never dragged anything.
const LEGACY_DEFAULT_ORDER = ['doctor', 'wizard', 'mapeditor', 'runcommand', 'service']

export const MIN_SPAN = 1
export const MAX_SPAN = 4

function readRaw() {
  try {
    const raw = JSON.parse(localStorage.getItem(LAYOUT_KEY) || 'null')
    return raw && typeof raw === 'object' && !Array.isArray(raw) ? raw : null
  } catch {
    return null
  }
}

function readLegacyOrder() {
  try {
    const raw = JSON.parse(localStorage.getItem(LEGACY_ORDER_KEY) || 'null')
    return Array.isArray(raw) ? raw.filter((id) => typeof id === 'string') : null
  } catch {
    return null
  }
}

function strings(value) {
  return Array.isArray(value) ? value.filter((v) => typeof v === 'string') : []
}

function clampSpan(value, fallback) {
  const n = Math.round(Number(value))
  if (!Number.isFinite(n)) return fallback
  return Math.min(MAX_SPAN, Math.max(MIN_SPAN, n))
}

// The saved order, reconciled against the ids the app actually ships.
function mergeOrder(saved, defaults) {
  const known = strings(saved).filter((id) => defaults.includes(id))
  return [...new Set([...known, ...defaults])]
}

function normalizeStatus(saved, defaults) {
  const source = saved && typeof saved === 'object' ? saved : {}
  const order = mergeOrder(source.order, defaults.order)
  const known = new Set(order)
  return {
    order,
    hidden: [...new Set(strings(source.hidden).filter((key) => known.has(key)))],
  }
}

// `defaults` is the shipped layout:
//   { order, hidden, spans, status: { order, hidden } }
export function readLayout(defaults) {
  const saved = readRaw()
  if (saved === null) {
    const legacy = readLegacyOrder()
    // A saved order that is just the old shipped one means the user never
    // rearranged anything — take the new shipped layout whole, rather than
    // pinning the old five cards ahead of every new tile and lighting up
    // "Reset layout" for someone who has customized nothing.
    if (legacy && sameList(legacy, LEGACY_DEFAULT_ORDER)) return defaults
    // Otherwise keep the order the user arranged.
    if (legacy) {
      return {
        ...defaults,
        order: mergeOrder(legacy, defaults.order),
      }
    }
    return defaults
  }

  const order = mergeOrder(saved.order, defaults.order)
  const known = new Set(order)
  // Hidden ids the saved layout never mentioned fall back to the shipped
  // hidden set only for cards it did not know about at all — a card the user
  // has explicitly unhidden must stay visible.
  const savedKnown = new Set(strings(saved.order))
  const hidden = new Set(strings(saved.hidden).filter((id) => known.has(id)))
  for (const id of defaults.hidden) {
    if (!savedKnown.has(id) && known.has(id)) hidden.add(id)
  }

  const spans = {}
  const savedSpans = saved.spans && typeof saved.spans === 'object' ? saved.spans : {}
  for (const [id, value] of Object.entries(savedSpans)) {
    if (!known.has(id)) continue
    const span = clampSpan(value, null)
    if (span !== null) spans[id] = span
  }

  return {
    order,
    hidden: [...hidden],
    spans,
    status: normalizeStatus(saved.status, defaults.status),
  }
}

export function writeLayout(layout) {
  try {
    localStorage.setItem(LAYOUT_KEY, JSON.stringify(layout))
  } catch {
    // storage full/unavailable — persistence is best-effort
  }
  publishStatus(layout.status)
}

// A change to the status slice alone: the status panel's edit mode, which sits
// outside the Dashboard page. The rest of the saved layout stays as it is.
export function writeStatusLayout(status) {
  writeLayout({ ...readRaw(), status })
}

export function clearLayout(defaults) {
  try {
    localStorage.removeItem(LAYOUT_KEY)
    localStorage.removeItem(LEGACY_ORDER_KEY)
  } catch {
    // ignore
  }
  if (defaults) publishStatus(defaults.status)
}

function sameList(a, b) {
  return a.length === b.length && a.every((v, i) => v === b[i])
}

function sameSet(a, b) {
  if (a.length !== b.length) return false
  const other = new Set(b)
  return a.every((v) => other.has(v))
}

// Whether the layout still matches what the app ships — drives whether the
// "Reset layout" button is offered. Compares all four axes: reset clears them
// all, so any one of them differing has to keep the button on screen.
export function isDefaultLayout(layout, defaults) {
  return (
    sameList(layout.order, defaults.order) &&
    sameSet(layout.hidden, defaults.hidden) &&
    Object.keys(layout.spans).length === 0 &&
    sameList(layout.status.order, defaults.status.order) &&
    sameSet(layout.status.hidden, defaults.status.hidden)
  )
}

// --- the status slice, as a subscribable store ---------------------------
//
// InfoBar renders in Layout, and the Dashboard page's Reset layout has to reach
// it, though it is not the page's descendant. Same shape as crumbTitle.js (useSyncExternalStore over a
// module-level snapshot) rather than a context wrapping the whole shell for one
// list.
//
// `snapshot` MUST return a cached object: re-parsing localStorage per call
// hands useSyncExternalStore a new identity every time and it re-renders
// forever.

// The shipped status sub-layout: every field, none hidden. Kept here as well
// as in the page's DEFAULTS so a read that happens before the dashboard has
// ever mounted (an InfoBar on any other page) still normalizes against the
// full key list instead of dropping every key.
export const DEFAULT_STATUS_LAYOUT = { order: DEFAULT_STATUS_ORDER, hidden: [] }

const statusDefaults = DEFAULT_STATUS_LAYOUT
let statusCurrent = null
const statusSubs = new Set()

function statusSubscribe(fn) {
  statusSubs.add(fn)
  return () => statusSubs.delete(fn)
}

function statusSnapshot() {
  if (statusCurrent === null) {
    statusCurrent = normalizeStatus(readRaw()?.status, statusDefaults)
  }
  return statusCurrent
}

function publishStatus(status) {
  const next = normalizeStatus(status, statusDefaults)
  const cur = statusSnapshot()
  if (sameList(cur.order, next.order) && sameSet(cur.hidden, next.hidden)) return
  statusCurrent = next
  for (const fn of statusSubs) fn()
}

// Another tab reordered the status block: re-read rather than drift apart.
if (typeof window !== 'undefined') {
  window.addEventListener('storage', (e) => {
    if (e.key !== null && e.key !== LAYOUT_KEY) return
    statusCurrent = null
    for (const fn of statusSubs) fn()
  })
}

// Read side (StatusFieldEditor, InfoBar, DashboardPage). Writes go through
// writeLayout or writeStatusLayout, which publish here.
export function useStatusLayout() {
  return useSyncExternalStore(statusSubscribe, statusSnapshot, statusSnapshot)
}
