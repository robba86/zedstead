// The Spawns tab's event list: search box, create button, and events grouped
// into collapsible categories, drawn with the shared list parts
// (webui/DESIGN.md). A category's open state is kept per browser through
// useStoredOpen. The per-event link to cfgeventspawns.xml lives in the event
// detail view (plan 89, Spawns).
//
// Categories come from a regex on the event name (icons/iconRules.js) — DayZ
// has no category field, and grouping ~200 raw cfgeventspawns entries by hand
// is unusable.

import { useMemo, useState } from 'react'
import SearchInput from '../../../components/SearchInput.jsx'
import ListSection from '../../../components/ListSection.jsx'
import ListRow, { RowDot } from '../../../components/ListRow.jsx'
import CountBadge from '../../../components/CountBadge.jsx'
import RowAction from '../../../components/RowAction.jsx'
import LinkOut from '../../../components/LinkOut.jsx'
import PanelNote from '../../../components/PanelNote.jsx'
import { useEditor } from '../state/MapEditorProvider'
import useStoredOpen from '../state/useStoredOpen'
import { getEventCategory, getEventColor } from '../icons/iconRules'
import CreateEventDialog from './CreateEventDialog.jsx'
import { LINKS, configLink } from '../../../crossLinks'

// Display order; anything the rules produce that is not listed lands at the end.
const CATEGORY_ORDER = [
  'Vehicles',
  'Animals',
  'Infected',
  'Crashes',
  'Static',
  'Contamination',
  'Loot',
  'Ambient',
  'Trader',
  'Other',
]

const FILTER_HELP =
  'Events from cfgeventspawns.xml. Each event spawns items at map positions. ' +
  'Tick an event to show it, click the pencil to edit timing and radii, ' +
  'click + to add spawn points.'

const EditIcon = () => (
  <svg
    width="12"
    height="12"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2.5"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" />
  </svg>
)

const PlusIcon = () => (
  <svg
    width="12"
    height="12"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="3"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M5 12h14" />
    <path d="M12 5v14" />
  </svg>
)

// One event in the list. The row ticks the event on the map; the pencil opens
// its detail view and + arms adding spawn points.
function EventRow({ event, onToggleAdd }) {
  const { ui, dispatch, url } = useEditor()
  const isEmpty = event.positions.length === 0
  const visible = !isEmpty && ui.visibleEvents.has(event.name)
  const adding = ui.addMode?.kind === 'event' && ui.addMode.event === event.name

  function toggleVisible() {
    if (isEmpty) return
    // Ticking one event with the master layer off would be a silent no-op,
    // so it switches the layer on, as the vanilla editor did.
    if (!visible && !url.layers.spawns) url.setLayer('spawns', true)
    dispatch({ type: 'TOGGLE_EVENT', payload: event.name })
  }

  return (
    <ListRow
      lead={
        <>
          <input
            type="checkbox"
            disabled={isEmpty}
            checked={visible}
            aria-label={`Show ${event.name} on the map`}
            onClick={(e) => e.stopPropagation()}
            onChange={toggleVisible}
          />
          <RowDot color={getEventColor(event.name)} />
        </>
      }
      name={event.name}
      nameTip={event.name}
      meta={<CountBadge value={event.positions.length} />}
      dimmed={isEmpty}
      disabled={event.active === false}
      onClick={isEmpty ? undefined : toggleVisible}
      aria-pressed={isEmpty ? undefined : visible}
      actions={
        <>
          <RowAction
            icon={EditIcon}
            label={`Edit ${event.name}`}
            data-tooltip-content="Edit event timing"
            onClick={() => dispatch({ type: 'SELECT_EVENT', payload: event.name })}
          />
          <RowAction
            icon={PlusIcon}
            label={`Add spawn points to ${event.name}`}
            data-tooltip-content="Click map to add spawn"
            active={adding}
            onClick={() => onToggleAdd(event.name)}
          />
        </>
      }
    />
  )
}

// One category. While the filter has text, it shows only the matches and is
// forced open; its stored open state comes back when the filter is cleared.
function EventCategory({ name, events, matches, filtering, onToggleAdd }) {
  const [open, setOpen] = useStoredOpen(`zsm.mapeditor.spawns.${name}.open`)
  const totalPositions = events.reduce((sum, e) => sum + e.positions.length, 0)
  return (
    <ListSection
      title={name}
      count={[
        { value: events.length, unit: 'events' },
        { value: totalPositions, unit: 'pts' },
      ]}
      foldable
      sticky
      open={filtering || open}
      onOpenChange={setOpen}
    >
      {matches.map((event) => (
        <EventRow key={event.name} event={event} onToggleAdd={onToggleAdd} />
      ))}
    </ListSection>
  )
}

export default function EventList() {
  const { spawns, ui, dispatch, url } = useEditor()
  const [query, setQuery] = useState('')
  const [creating, setCreating] = useState(false)

  const categories = useMemo(() => {
    const sorted = [...(spawns?.events || [])].sort((a, b) => a.name.localeCompare(b.name))
    const groups = new Map()
    for (const event of sorted) {
      const category = getEventCategory(event.name)
      if (!groups.has(category)) groups.set(category, [])
      groups.get(category).push(event)
    }
    const ordered = CATEGORY_ORDER.filter((c) => groups.has(c))
    for (const c of groups.keys()) if (!ordered.includes(c)) ordered.push(c)
    return ordered.map((name) => ({ name, events: groups.get(name) }))
  }, [spawns])

  const q = query.trim().toLowerCase()

  function toggleAddMode(name) {
    if (ui.addMode?.kind === 'event' && ui.addMode.event === name) {
      dispatch({ type: 'EXIT_ADD' })
      return
    }
    // Placing into a layer that is switched off would look like a no-op.
    url.setLayer('spawns', true)
    dispatch({ type: 'ENTER_EVENT_ADD', payload: { event: name } })
  }

  if (!spawns) return <PanelNote>Loading events…</PanelNote>

  return (
    <>
      <div className="me-event-filter">
        <SearchInput
          boxClassName="search-field-wide"
          className="me-event-search"
          type="text"
          placeholder="Filter events..."
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <RowAction
          icon={PlusIcon}
          label="Create event"
          data-tooltip-content="Create a new event (adds to events.xml and cfgeventspawns.xml)"
          onClick={() => setCreating(true)}
        />
        <LinkOut
          to={configLink('eventspawns')}
          label={`Open ${LINKS.eventspawns.label}`}
          tip={`Open ${LINKS.eventspawns.label} — ${LINKS.eventspawns.tip}`}
        />
      </div>
      <PanelNote>{FILTER_HELP}</PanelNote>

      <div className="me-event-list">
        {categories.map(({ name, events }) => {
          const matches = q ? events.filter((e) => e.name.toLowerCase().includes(q)) : events
          if (q && matches.length === 0) return null
          return (
            <EventCategory
              key={name}
              name={name}
              events={events}
              matches={matches}
              filtering={Boolean(q)}
              onToggleAdd={toggleAddMode}
            />
          )
        })}
      </div>

      {creating && (
        <CreateEventDialog
          onClose={() => setCreating(false)}
          onCreated={(name) => {
            setCreating(false)
            url.setLayer('spawns', true)
            dispatch({ type: 'ENTER_EVENT_ADD', payload: { event: name } })
          }}
        />
      )}
    </>
  )
}
