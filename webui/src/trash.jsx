import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { HiOutlineTrash } from 'react-icons/hi2'
import { api } from './api'
import { formatWhen } from './format.js'
import { useWs } from './ws'
import FoldCaret from './components/FoldCaret.jsx'
import CountBadge from './components/CountBadge.jsx'

// Removed values an editor can put back until Save or Reset (frontend only),
// and, for a list editor the server keeps a removed list for, what earlier
// saves removed (useSavedRemovals) in the same tray.
// The editor that owns the state provides the context; anything inside it
// that removes something (ListField, JsonTree, a table row) calls
// useTrash()?.add({label, path, index, item}). `path` is [root, ...trail]:
// the root names the editor's own piece of state and the trail the keys and
// indexes down to the list the item was in. The owner restores with
// insertAt, at the old index or the end when the list got shorter. Without a
// provider useTrash() is null, so a ListField outside an editor just removes.
export const TrashContext = createContext(null)

export function useTrash() {
  return useContext(TrashContext)
}

// A copy of `value` with `item` inserted into the list at `trail`.
export function insertAt(value, trail, index, item) {
  if (!trail.length) {
    const list = Array.isArray(value) ? [...value] : []
    list.splice(Math.min(index, list.length), 0, item)
    return list
  }
  const [head, ...rest] = trail
  if (Array.isArray(value)) {
    return value.map((v, i) => (i === head ? insertAt(v, rest, index, item) : v))
  }
  return { ...(value || {}), [head]: insertAt(value?.[head], rest, index, item) }
}

// The owner's half: the list, add, and a restore that hands each entry to
// `put(entry)` (the owner's functional state update). Latest removal first,
// so restoring everything puts each item back where it was.
export function useTrashState(put) {
  const [items, setItems] = useState([])
  const api = useRef(null)
  api.current = {
    add: (entry) => setItems((prev) => [...prev, { ...entry, id: `${Date.now()}-${Math.random()}` }]),
  }
  const [ctx] = useState(() => ({ add: (e) => api.current.add(e) }))
  return {
    ctx,
    items,
    clear: () => setItems([]),
    restore: (id) => {
      const entry = items.find((e) => e.id === id)
      if (!entry) return
      put(entry)
      setItems((prev) => prev.filter((e) => e.id !== id))
    },
    restoreAll: () => {
      for (const entry of [...items].reverse()) put(entry)
      setItems([])
    },
  }
}

// What earlier saves removed from a list editor's file, from the versions the
// server keeps of it (GET /api/configs/{id}/removed, config_versions.
// removed_names): [{name, removed_at}], newest first. It lives on the server,
// so it is there after a reload and on another computer. Putting one back is
// the editor's own add, saved like any edit. It re-reads on `configs_changed`,
// so a restore, a Raw-tab save or another admin's save shows here too.
export function useSavedRemovals(editorId) {
  const [items, setItems] = useState([])
  const reload = useCallback(() => {
    api
      .get(`/api/configs/${editorId}/removed`)
      .then((d) => setItems(d.items || []))
      .catch(() => setItems([]))
  }, [editorId])
  useEffect(() => {
    reload()
  }, [reload])
  useWs(
    useCallback(
      (msg) => {
        if (msg.type === 'configs_changed') reload()
      },
      [reload]
    )
  )
  return { items, reload }
}

// A removed value in words: a scalar as itself, a table row by its cells, any
// other object by its first plain fields ("name: Wolf · min: 2"), with a count
// for what nests. `full` is the whole value, shown on request when the summary
// leaves something out (user test, 2026 Q4: a cut-off `{"lo…` chip told
// nobody what had been removed).
const SUMMARY_FIELDS = 4

function scalarText(v) {
  return v === null || v === undefined || v === '' ? '—' : String(v)
}

export function describeRemoved(item) {
  if (item === null || typeof item !== 'object') return { summary: scalarText(item), full: null }
  // A TableEditor row: its cells are the value, the rest is bookkeeping.
  const value = item.values && typeof item.values === 'object' && !Array.isArray(item.values) ? item.values : item
  if (Array.isArray(value)) {
    const parts = value.slice(0, SUMMARY_FIELDS).map((v) => (v !== null && typeof v === 'object' ? '{…}' : scalarText(v)))
    const more = value.length > SUMMARY_FIELDS ? ` · +${value.length - SUMMARY_FIELDS} more` : ''
    return { summary: `${value.length} item${value.length === 1 ? '' : 's'}: ${parts.join(', ')}${more}`, full: JSON.stringify(value, null, 2) }
  }
  const entries = Object.entries(value)
  const plain = entries.filter(([, v]) => v === null || typeof v !== 'object')
  const nested = entries.filter(([, v]) => v !== null && typeof v === 'object')
  const parts = plain.slice(0, SUMMARY_FIELDS).map(([k, v]) => `${k}: ${scalarText(v)}`)
  for (const [k, v] of nested.slice(0, Math.max(0, SUMMARY_FIELDS - parts.length))) {
    const n = Array.isArray(v) ? v.length : Object.keys(v).length
    parts.push(`${k}: ${n} ${Array.isArray(v) ? 'item' : 'field'}${n === 1 ? '' : 's'}`)
  }
  const shown = Math.min(entries.length, SUMMARY_FIELDS)
  const leftOut = entries.length > shown || nested.length > 0
  return {
    summary: parts.join(' · ') || '(empty)',
    full: leftOut ? JSON.stringify(value, null, 2) : null,
  }
}

function RemovedItem({ entry, onRestore }) {
  const { summary, full } = describeRemoved(entry.item)
  return (
    <li>
      <div className="trash-tray-head">
        <span className="trash-tray-label">{entry.label}</span>
        <button type="button" className="btn btn-ghost btn-small" onClick={onRestore}>
          Restore
        </button>
      </div>
      <span className="trash-tray-item">{summary}</span>
      {full && (
        <details className="trash-tray-full">
          <summary>
            <FoldCaret /> Show everything
          </summary>
          <pre>{full}</pre>
        </details>
      )}
    </li>
  )
}

// Shakes the element at `ref` each time `count` grows, so a removal shows
// where the value went. The Web Animations API restarts the shake on every
// removal, even mid-shake. Skipped when the viewer asks for reduced motion.
const SHAKE_FRAMES = [
  { transform: 'translateX(0) rotate(0deg)' },
  { transform: 'translateX(-4px) rotate(-4deg)' },
  { transform: 'translateX(4px) rotate(4deg)' },
  { transform: 'translateX(-3px) rotate(-3deg)' },
  { transform: 'translateX(3px) rotate(3deg)' },
  { transform: 'translateX(-1px) rotate(-1deg)' },
  { transform: 'translateX(0) rotate(0deg)' },
]

function useShakeOnGrow(ref, count) {
  const prev = useRef(count)
  useEffect(() => {
    const grew = count > prev.current
    prev.current = count
    const el = ref.current
    if (!grew || !el?.animate) return
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return
    el.animate(SHAKE_FRAMES, { duration: 450, easing: 'ease-in-out' })
  }, [ref, count])
}

// "Removed (N)" in the sticky bar; opens a list with Restore per item. The
// list is placed against the bar (.trash-tray is not positioned), so it spans
// the bar's width on a phone instead of running off the screen's edge.
// `saved` ([{name, removed_at}], from useSavedRemovals, less what is back in
// the list) adds what earlier saves removed, put back by `onRestoreSaved(name)`.
export function TrashTray({ trash, saved = [], onRestoreSaved }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)
  const buttonRef = useRef(null)
  useEffect(() => {
    if (!open) return undefined
    const close = (e) => ref.current && !ref.current.contains(e.target) && setOpen(false)
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [open])
  const count = trash.items.length + saved.length
  useEffect(() => {
    if (!count) setOpen(false)
  }, [count])
  useShakeOnGrow(buttonRef, trash.items.length)
  if (!count) return null
  const both = trash.items.length > 0 && saved.length > 0
  return (
    <div className="trash-tray" ref={ref}>
      <button
        ref={buttonRef}
        type="button"
        className="btn btn-ghost btn-small"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        <HiOutlineTrash aria-hidden="true" />
        Removed <CountBadge value={count} />
      </button>
      {open && (
        <div className="trash-tray-panel" role="dialog" aria-label="Removed values">
          {both && <p className="trash-tray-heading">Not saved yet</p>}
          {trash.items.length > 0 && (
            <ul>
              {[...trash.items].reverse().map((e) => (
                <RemovedItem key={e.id} entry={e} onRestore={() => trash.restore(e.id)} />
              ))}
            </ul>
          )}
          {saved.length > 0 && (
            <>
              <p className="trash-tray-heading">Removed by an earlier save</p>
              <ul>
                {saved.map((e) => (
                  <RemovedItem
                    key={`saved:${e.name}`}
                    entry={{ label: `Saved ${formatWhen(e.removed_at)}`, item: e.name }}
                    onRestore={() => onRestoreSaved?.(e.name)}
                  />
                ))}
              </ul>
            </>
          )}
          {trash.items.length > 1 && (
            <button type="button" className="btn btn-primary btn-small" onClick={trash.restoreAll}>
              Restore all
            </button>
          )}
        </div>
      )}
    </div>
  )
}
