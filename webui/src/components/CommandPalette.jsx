// The Ctrl+K search palette (plans/75 P3). Lazy: Layout imports it with
// `lazy()`, so neither this file nor its CSS is in the entry chunk.
//
// Keyword search only. The entries are the client's own (searchSources.js:
// pages, tabs, links, settings sections, guide steps) merged with
// GET /api/search-index (settings, config editors and their fields, the map
// editor's guide, the Mods help), which the server filters by role. Each
// result is a link; picking one navigates, nothing here writes anything.

import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useFocusTrap } from './useFocusTrap.js'
import { useNavigate } from 'react-router-dom'
import { HiOutlineMagnifyingGlass, HiOutlineXMark } from 'react-icons/hi2'
import { api } from '../api'
import { fuzzyScore } from '../fuzzy.js'
import { instanceKey } from '../instance'
import { useGuide } from '../guide.jsx'
import { clientEntries, enrichEntries, mergeEntries } from '../searchSources.js'
import './CommandPalette.css'
import { ClearSearchButton } from './SearchInput.jsx'
import PanelNote from './PanelNote.jsx'

const MAX_RESULTS = 50
const MAX_RECENT = 8
const RECENT_KEY = instanceKey('zsm.search.recent')

// Result groups, in the order they are shown when nothing is typed. With a
// query the groups are ordered by their best result instead.
const GROUPS = [
  { id: 'page', label: 'Pages', kinds: ['page', 'tab'] },
  { id: 'editor', label: 'Config files', kinds: ['editor'] },
  { id: 'setting', label: 'Settings', kinds: ['setting', 'section'] },
  { id: 'field', label: 'Config fields', kinds: ['field'] },
  { id: 'guide', label: 'Guide', kinds: ['guide'] },
  { id: 'help', label: 'Help', kinds: ['help'] },
]
const GROUP_OF = Object.fromEntries(GROUPS.flatMap((g) => g.kinds.map((k) => [k, g.id])))

// The server's half, fetched once per page load and role. A failed fetch
// leaves the client's half, which is most of what a page search needs.
const serverCache = new Map()
function loadServerEntries(role) {
  if (!serverCache.has(role)) {
    serverCache.set(
      role,
      api.get('/api/search-index').catch(() => {
        serverCache.delete(role)
        return []
      })
    )
  }
  return serverCache.get(role)
}

function readRecent() {
  try {
    const list = JSON.parse(localStorage.getItem(RECENT_KEY) || '[]')
    return Array.isArray(list) ? list.filter((t) => typeof t === 'string') : []
  } catch {
    return []
  }
}

function writeRecent(to) {
  try {
    const list = [to, ...readRecent().filter((t) => t !== to)].slice(0, MAX_RECENT)
    localStorage.setItem(RECENT_KEY, JSON.stringify(list))
  } catch {
    // best effort, like every other per-browser preference
  }
}

const TEXT_HIT = 15

// Every word must match somewhere: the title (weighted most), the subtitle,
// or the help text (as a plain substring, since fuzzy matching a paragraph
// matches nearly anything).
function score(entry, words) {
  let total = 0
  for (const word of words) {
    const t = fuzzyScore(word, entry.title)
    const s = fuzzyScore(word, entry.sub)
    const x = entry._lower.includes(word.toLowerCase()) ? TEXT_HIT : null
    const best = Math.max(t === null ? -Infinity : t * 3, s === null ? -Infinity : s, x === null ? -Infinity : x)
    if (best === -Infinity) return null
    total += best
  }
  return total
}

function groupResults(entries, scored) {
  const groups = new Map()
  for (const { entry, value } of scored) {
    const id = GROUP_OF[entry.kind] || 'help'
    if (!groups.has(id)) groups.set(id, { ...GROUPS.find((g) => g.id === id), best: value, items: [] })
    groups.get(id).items.push(entry)
  }
  const list = [...groups.values()]
  if (scored.length && scored[0].value !== undefined) list.sort((a, b) => b.best - a.best)
  return list
}

export default function CommandPalette({ role, onClose }) {
  const navigate = useNavigate()
  const { openGuide } = useGuide()
  const [query, setQuery] = useState('')
  const [server, setServer] = useState([])
  const [active, setActive] = useState(0)
  const inputRef = useRef(null)
  const listRef = useRef(null)
  const boxRef = useRef(null)
  useFocusTrap(boxRef)

  useEffect(() => {
    let alive = true
    loadServerEntries(role).then((list) => {
      if (alive) setServer(Array.isArray(list) ? list : [])
    })
    return () => {
      alive = false
    }
  }, [role])

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  const entries = useMemo(() => {
    const merged = mergeEntries(clientEntries(role), server, enrichEntries())
    for (const e of merged) e._lower = `${e.text}`.toLowerCase()
    return merged
  }, [role, server])

  const groups = useMemo(() => {
    const words = query.trim().split(/\s+/).filter(Boolean)
    if (!words.length) {
      const recent = readRecent()
        .map((to) => entries.find((e) => e.to === to))
        .filter(Boolean)
      const pages = entries.filter((e) => e.kind === 'page' && e.sub === 'Page')
      const out = []
      if (recent.length) out.push({ id: 'recent', label: 'Recent', items: recent })
      out.push({ id: 'page', label: 'Pages', items: pages.filter((p) => !recent.includes(p)) })
      return out
    }
    const scored = []
    for (const entry of entries) {
      const value = score(entry, words)
      if (value !== null) scored.push({ entry, value })
    }
    scored.sort((a, b) => b.value - a.value)
    return groupResults(entries, scored.slice(0, MAX_RESULTS))
  }, [entries, query])

  const flat = useMemo(() => groups.flatMap((g) => g.items), [groups])

  useEffect(() => {
    setActive(0)
  }, [query])

  useEffect(() => {
    const el = listRef.current?.querySelector(`[data-index="${active}"]`)
    el?.scrollIntoView({ block: 'nearest' })
  }, [active])

  function pick(entry) {
    if (!entry) return
    writeRecent(entry.to)
    onClose()
    if (entry.stepId) {
      openGuide(entry.stepId)
    } else if (entry.href) {
      window.open(entry.href, '_blank', 'noopener,noreferrer')
    } else {
      // The stamp tells a page already showing this path that a new search
      // arrived, so it applies ?q= again (useSearchPrefill.js).
      navigate(entry.to, { state: { searchStamp: Date.now() } })
    }
  }

  function onKeyDown(e) {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActive((i) => (flat.length ? (i + 1) % flat.length : 0))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActive((i) => (flat.length ? (i - 1 + flat.length) % flat.length : 0))
    } else if (e.key === 'Home' && flat.length) {
      e.preventDefault()
      setActive(0)
    } else if (e.key === 'End' && flat.length) {
      e.preventDefault()
      setActive(flat.length - 1)
    } else if (e.key === 'Enter') {
      e.preventDefault()
      pick(flat[active])
    } else if (e.key === 'Escape') {
      e.preventDefault()
      e.stopPropagation()
      onClose()
    } else if (e.key === 'Tab') {
      // The input is the only stop: the list is reached with the arrows.
      e.preventDefault()
    }
  }

  let index = -1
  const activeId = flat.length ? `cmdk-opt-${active}` : undefined

  return createPortal(
    <div className="modal-backdrop command-palette-backdrop" onMouseDown={onClose}>
      <div
        ref={boxRef}
        className="modal command-palette"
        role="dialog"
        aria-modal="true"
        aria-label="Search the app"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="command-palette-input-row">
          <HiOutlineMagnifyingGlass className="command-palette-icon" aria-hidden="true" />
          <input
            ref={inputRef}
            autoFocus
            className="command-palette-input"
            type="text"
            role="combobox"
            aria-expanded="true"
            aria-controls="cmdk-list"
            aria-activedescendant={activeId}
            aria-autocomplete="list"
            placeholder="Search pages, settings and config fields…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onKeyDown}
            spellCheck={false}
            autoComplete="off"
          />
          {query && (
            <ClearSearchButton
              onClear={() => {
                setQuery('')
                inputRef.current?.focus()
              }}
            />
          )}
          {/* A button, not a hint: on a phone the palette covers the screen and
              there is no Esc key and no backdrop left to tap. A touch screen
              shows an X; a keyboard screen keeps the Esc label. */}
          <button type="button" className="command-palette-close" onClick={onClose} aria-label="Close search">
            <kbd className="command-palette-kbd">Esc</kbd>
            <HiOutlineXMark className="command-palette-x" aria-hidden="true" />
          </button>
        </div>
        <div id="cmdk-list" className="command-palette-list" role="listbox" ref={listRef} aria-label="Results">
          {flat.length === 0 && <PanelNote>No matches for “{query}”.</PanelNote>}
          {groups.map((g) =>
            g.items.length ? (
              <div key={g.id} role="group" aria-labelledby={`cmdk-group-${g.id}`}>
                <div id={`cmdk-group-${g.id}`} className="command-palette-group">
                  {g.label}
                </div>
                {g.items.map((entry) => {
                  index += 1
                  const i = index
                  return (
                    <div
                      key={`${g.id}:${entry.to}`}
                      id={`cmdk-opt-${i}`}
                      data-index={i}
                      role="option"
                      aria-selected={i === active}
                      className={`command-palette-item${i === active ? ' active' : ''}`}
                      onMouseMove={() => i !== active && setActive(i)}
                      onClick={() => pick(entry)}
                    >
                      <span className="command-palette-title">{entry.title}</span>
                      <span className="command-palette-sub">{entry.sub}</span>
                    </div>
                  )
                })}
              </div>
            ) : null
          )}
        </div>
        <div className="command-palette-foot" aria-hidden="true">
          <span>
            <kbd>↑</kbd> <kbd>↓</kbd> move
          </span>
          <span>
            <kbd>Enter</kbd> open
          </span>
          <span>
            <kbd>Ctrl</kbd> <kbd>P</kbd>, <kbd>Ctrl</kbd> <kbd>K</kbd> or <kbd>/</kbd> search
          </span>
        </div>
      </div>
    </div>,
    document.body
  )
}
