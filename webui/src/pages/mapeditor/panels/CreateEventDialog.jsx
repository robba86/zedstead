// "Create New Event" dialog, with a classname typeahead.
//
// Creating an event writes to two files (events.xml for the metadata,
// cfgeventspawns.xml for the — initially empty — position list), which is why
// the name has to be a real classname: GET /type-names collects candidates
// from types.xml, mod XMLs and PBO CfgVehicles classes, filtered to exclude
// names that already have a cfgeventspawns.xml entry. Names that are defined but
// position-less — in events.xml, or in a mod's CE-linked custom/ file — are NOT
// excluded: they appear nowhere on the map, and this dialog is how they get a
// cfgeventspawns.xml entry (the POST then skips the events.xml half, so a mod's
// definition is never duplicated).
//
// Rendered into document.body so the overlay is not trapped by the editor
// shell's `overflow: hidden` or Leaflet's stacking contexts.

import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useFocusTrap } from '../../../components/useFocusTrap.js'
import { meApi } from '../api'
import { RowTag } from '../../../components/ListRow.jsx'
import Suggestions from '../../../components/Suggestions.jsx'
import { useEditor } from '../state/MapEditorProvider'

const MAX_SUGGESTIONS = 50

export default function CreateEventDialog({ onClose, onCreated }) {
  const { spawns, reload } = useEditor()
  const [name, setName] = useState('')
  const [sources, setSources] = useState(null)
  const [activeIndex, setActiveIndex] = useState(-1)
  const [suggestionsOpen, setSuggestionsOpen] = useState(false)
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const inputRef = useRef(null)
  const boxRef = useRef(null)
  useFocusTrap(boxRef)
  const blurTimer = useRef(null)

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  // Fetched in the background: on a big server this walks PBOs and can take a
  // second or two, and the dialog stays usable without it.
  useEffect(() => {
    let cancelled = false
    meApi
      .get('/type-names')
      .then((data) => {
        if (!cancelled) setSources(data?.sources || [])
      })
      .catch(() => {
        if (!cancelled) setSources([])
      })
    return () => {
      cancelled = true
      clearTimeout(blurTimer.current)
    }
  }, [])

  const existing = useMemo(
    () => new Set((spawns?.events || []).map((e) => e.name.toLowerCase())),
    [spawns]
  )

  const query = name.trim().toLowerCase()

  const { items, overflow } = useMemo(() => {
    if (!sources || query.length < 2) return { items: [], overflow: 0 }
    const picked = []
    let total = 0
    for (const source of sources) {
      const matches = source.names.filter((n) => n.name.toLowerCase().includes(query))
      total += matches.length
      if (!matches.length) continue
      for (const match of matches) {
        if (picked.length >= MAX_SUGGESTIONS) break
        picked.push({ ...match, source: source.label })
      }
    }
    return { items: picked, overflow: Math.max(0, total - picked.length) }
  }, [sources, query])

  const showSuggestions = suggestionsOpen && items.length > 0

  function choose(index) {
    const item = items[index]
    if (!item) return
    setName(item.name)
    setSuggestionsOpen(false)
    setActiveIndex(-1)
    inputRef.current?.focus()
  }

  function onKeyDown(e) {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      if (showSuggestions) setActiveIndex((i) => Math.min(i + 1, items.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      if (showSuggestions) setActiveIndex((i) => Math.max(i - 1, -1))
    } else if (e.key === 'Enter') {
      if (showSuggestions && activeIndex >= 0) {
        e.preventDefault()
        choose(activeIndex)
      } else {
        submit()
      }
    } else if (e.key === 'Escape') {
      // First Escape dismisses the dropdown, second closes the dialog.
      if (showSuggestions) {
        e.stopPropagation()
        setSuggestionsOpen(false)
        setActiveIndex(-1)
      } else {
        onClose()
      }
    }
  }

  async function submit() {
    const value = name.trim()
    if (!value) {
      setError('Event name is required.')
      return
    }
    setSubmitting(true)
    setError('')
    try {
      await meApi.post('/events', { name: value })
      // The WS ping refetches too, but not before the caller wants to enter
      // add mode for an event the list does not know about yet.
      await Promise.all([reload.spawns(), reload.events()])
      onCreated(value)
    } catch (err) {
      setError(err.message || 'Failed to create event.')
      setSubmitting(false)
    }
  }

  return createPortal(
    <div
      className="dialog-overlay"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div ref={boxRef} className="dialog-box">
        <div className="dialog-title">Create New Event</div>
        <div className="dialog-body">
          <label className="dialog-label" htmlFor="me-create-event-name">
            Event Name
          </label>
          <input
            id="me-create-event-name"
            ref={inputRef}
            className="dialog-input"
            type="text"
            placeholder="e.g. VehicleCarPack_Sedan"
            spellCheck={false}
            autoComplete="off"
            value={name}
            onChange={(e) => {
              setName(e.target.value)
              setSuggestionsOpen(true)
              setActiveIndex(-1)
            }}
            onFocus={() => setSuggestionsOpen(true)}
            onBlur={() => {
              // Delayed so a mousedown on a suggestion still lands.
              blurTimer.current = setTimeout(() => setSuggestionsOpen(false), 150)
            }}
            onKeyDown={onKeyDown}
          />

          {showSuggestions && (
            <Suggestions
              items={items.map((item) => {
                const exists = existing.has(item.name.toLowerCase())
                return {
                  key: `${item.source}:${item.name}`,
                  name: item.name,
                  group: item.source,
                  dimmed: exists,
                  meta: (
                    <>
                      {item.detail}
                      {exists && <> <RowTag>exists</RowTag></>}
                    </>
                  ),
                }
              })}
              activeIndex={activeIndex}
              onChoose={choose}
              more={overflow > 0 && `${overflow} more matches`}
            />
          )}

          <div className="dialog-hint">
            Type to search classnames from types.xml and mod files. Creates the event in
            both events.xml and cfgeventspawns.xml — for one that is already defined (in
            events.xml, or in a mod's custom/ economy file) it adds only the missing
            cfgeventspawns.xml entry, so it can hold positions.
          </div>
          {error && <div className="dialog-error">{error}</div>}
        </div>
        <div className="dialog-actions">
          <button type="button" className="dialog-btn dialog-button-cancel" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="dialog-btn dialog-button-submit"
            disabled={submitting}
            onClick={submit}
          >
            {submitting ? 'Creating...' : 'Create'}
          </button>
        </div>
      </div>
    </div>,
    document.body
  )
}
