import { Switch } from './FlagSwitch.jsx'
import { Fragment, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { HiOutlineDocumentDuplicate, HiOutlineTrash } from 'react-icons/hi2'
import ListField from './ListField.jsx'
import NestedParts from './NestedParts.jsx'
import Select, { withCurrent } from './Select.jsx'
import FileMissing from './FileMissing.jsx'
import StickyActionBar from './StickyActionBar.jsx'
import { useEditorSync } from '../configSync.js'
import Tooltip, { tooltipAttrs } from './Tooltip.jsx'
import FilterBox from './FilterBox.jsx'
import Pager from './Pager.jsx'
import { fuzzyScoreAny } from '../fuzzy.js'
import { inputAttrsForType } from '../format.js'
import { manualFileLink, manualIdOf, modXmlLink } from '../crossLinks.js'
import { fieldLabel, helpWithKey } from '../fieldLabel.js'
import { useCan } from '../role.jsx'
import { TrashContext, TrashTray, insertAt, useTrashState } from '../trash.jsx'
import FoldCaret from './FoldCaret.jsx'
import { RowTag } from './ListRow.jsx'
import { SummaryToggle, ViewSwitch, useEditorView } from './SmartView.jsx'
import SmartEventForm, { eventSummary } from './SmartEvent.jsx'
import UnitInput from './UnitInput.jsx'
import { ShareInput } from './RangeField.jsx'

// A column's unit (the backend's word, editors/_help.py) as the short form a
// table cell has room for. A unit not listed shows as sent.
const SHORT_UNITS = { seconds: 's', minutes: 'min', hours: 'h', meters: 'm' }

const ALL = '\u0000all'

// A `multi` column's cell (cfglimitsdefinitionuser's flags) as its values:
// the row keeps the comma-separated text the file reads back as.
function splitList(text) {
  return String(text ?? '')
    .split(',')
    .map((v) => v.trim())
    .filter(Boolean)
}

// *choices* plus every value the cell holds that is not one of them, so a
// hand-written unknown flag stays visible to be corrected (as withCurrent).
function withAll(choices, values) {
  return [...choices, ...values.filter((v) => !choices.includes(v))]
}

// A table's Smart view, by the payload's `smart` (ce_tables.events_load):
// the columns a row keeps, the one-line summary that opens the row, and the
// form drawn under it in place of the Raw tree.
const SMART_TABLES = {
  event: { columns: ['name', 'active'], summary: eventSummary, Form: SmartEventForm },
}
// Rows per page. A table at or under this has no pager.
const PAGE_SIZE = 50

// Renders {kind:"table", columns:[{key,label,type}], rows:[{id,values}]}.
// Edits are in-memory until Save; new rows can be appended with "Add row".
// Optional data.filter_key groups rows by that column behind a picker (files
// like cfgeventspawns.xml carry thousands of rows — one event at a time).
//
// The group picker is TWO-WAY URL state (`group` + `onGroupChange`, wired to
// ConfigEditorDrawer's ?group=): a deep link selects a group, and picking one by hand
// rewrites the param. Two consequences that are easy to get wrong:
//   - An incoming `group` is adopted whenever it changes, not just on the first
//     load. Following a second link (Mod XML → one event) while already on the
//     page has to move the picker; first-load-only meant it silently did nothing.
//   - `emittedRef` holds the last value this editor put in the URL, so the
//     round-trip back through the prop is not mistaken for a new inbound link
//     (the CodeArea seed/emitted pattern).
// With no `group` the picker defaults to the FIRST group in the list the select
// actually shows — i.e. sorted, not the first row's value, which landed on a
// seemingly random entry in the middle of the dropdown.
//
// `initialSearch` comes from a deep link's ?q= (see webui/src/crossLinks.js) and
// is applied to the FIRST load only: a later reload (the re-fetch after Save, a
// WS-driven reload) must keep whatever the user has since typed.
//
// A row may carry `readonly: true` — a row this editor renders but does not own
// (cfgeconomycore.xml's generated custom/ registrations). Its inputs are greyed
// out, Remove is replaced by `readonlyLink` ({to, label}, from crossLinks.js's
// EDITOR_READONLY_LINKS) pointing at the view that does own it, and
// `data.readonly_reason` explains it once above the table. A row with `manual`
// (a manual XML file's id, plans/92) links to that file instead, and one with `mod`
// to that mod's files on Custom config, where Take over makes it editable. The rows are still
// submitted on Save — the server re-reads them from the file, so round-tripping
// them unchanged is what keeps a save honest.
//
// A column's `choice_help` ({value: text}) describes its values in the open list.
// A row may also carry `choices: {<col key>: [...]}` — a per-ROW enum, rendered
// as a <select>. Per row and not per column because one column can be shared by
// rows of different shapes (cfgeconomycore's "Value / type" holds a number on a
// `defaults` row and a CE type on a `ce:` one). The current value is always
// offered even when it isn't in the list, so a hand-edited bad value stays
// visible instead of being silently coerced to the first option. The server
// validates as well — a stale tab can post anything.
//
// A column may carry `readonly: true` — a cell the save never reads (a summary
// of the row's tree, cfgspawnabletypes' Attachments/cargo). A row may carry
// `locked: [<col key>, ...]` — cells of that row the save refuses to change
// (cfgenvironment's territory and agent names, and the cells that do not apply
// to the row's kind). Both are shown and greyed out; the row stays editable.
// A read-only column with `opens_nested: true` is drawn as a button holding
// its text that opens and closes the row's tree, like the caret does.
// `data.remove_row === false` hides Remove, as `add_row === false` hides Add
// row, for a file whose entries the editor can change but not add or delete.
//
// A row may carry `help: {<col key>: text}`, shown instead of the column's
// help on that row's cell (each cfgeconomycore default explains itself).
//
// `data.warnings` is a list of strings about the file as a whole (a registration
// pointing at a folder the mission hasn't got), shown above the table. Unlike
// `readonly_reason` it belongs to no row.
//
// `readOnly` makes the WHOLE table a viewer: no Save bar, no Add/Remove, every
// input disabled. Distinct from the per-row `readonly` flag on purpose — that
// one is for a *mixed* table (cfgeconomycore.xml's generated rows sitting beside
// editable ones) and still renders a Save bar, which is exactly wrong for a file
// nothing may write (the generated custom/ files). Filtering, searching and the
// group picker all still work; only writing is gone.
//
// A row may carry `nested: {key: value}` (plans/76 step 2b): the entry's
// objects and lists of objects no column shows (a zone's particles, a slot's
// clothes). A caret in the first column opens a full-width row under it with
// one JsonTree per key. A row whose tree was edited sends `$nested` (the edited
// keys) and `$nested_base` (those keys as loaded) among its values, the form's
// shape (app/web/editors/_nested.py). A duplicate copies its source's tree as
// it stands, with the source's as-loaded base, since the server starts the
// copy from that entry.
//
// A row may carry `owner`, the mod or manual file (`@user:<id>`) whose START MOD
// block holds it. The `owner` prop (?owner=) shows only that owner's rows, with
// a banner that clears it: a manual file's "Goes into" link opens its rows.
//
// `data.nested_choices` ({key: [options]}) offers names inside those trees
// (an event's position, a spawnable group's kind; plans/76 step 3).
//
// The rows the group picker and the search leave are shown PAGE_SIZE at a time.
// The page is local state, back to 1 when the group or search changes, and is
// clamped to the last page, so a removal never leaves an empty one and Add row
// (which appends) jumps to the end where the new row is. Save still sends every
// row, on every page. The pager sits centred on the Save bar; where there is
// no Save bar (a read-only table, or below admin) it sits under the table.
//
// The first column (the row's name, with the caret that opens its tree) and
// the actions column stay in view while a wide table scrolls sideways; each
// cell carries `data-type` so the CSS sizes a number narrower than a name.
// A row added or duplicated and not saved yet is marked "new". Reset puts
// back the rows as loaded; a removed row, list item or tree entry can be put
// back from the trash (trash.jsx) until Save or Reset.

// A row as Save sends it: the tree state stays in the page.
function rowToSave({ nested, nestedBase, nestedDirty, ...row }) {
  const keys = Object.keys(nestedDirty || {})
  if (!keys.length) return row
  return {
    ...row,
    values: {
      ...row.values,
      $nested: Object.fromEntries(keys.map((k) => [k, nested[k]])),
      $nested_base: Object.fromEntries(keys.map((k) => [k, nestedBase[k]])),
    },
  }
}

// The enum's options, with whatever the file actually holds kept in the list —
// a value the server would reject has to stay visible so it can be corrected,
// not be silently swapped for the first option the moment the row renders.
// The rows as loaded, in the page's state shape.
function fromData(data) {
  // `choices` is carried into state: dropping it here is why cfgeconomycore's
  // per-row type select (9ccf1cb) rendered as a plain text box until 2026 Q3.
  return data.rows.map((r) => ({
    id: r.id,
    values: { ...r.values },
    readonly: !!r.readonly,
    manual: r.manual,
    mod: r.mod,
    owner: r.owner,
    choices: r.choices,
    help: r.help,
    locked: r.locked,
    ...(r.nested && {
      nested: structuredClone(r.nested),
      nestedBase: r.nested,
      nestedDirty: {},
    }),
  }))
}

const isNewRow = (row) => /^(new|dup)-/.test(String(row.id))

// What a cell holds, for its width (index.css, .data-table [data-type]).
function cellType(col) {
  return col.choices || col.multi ? 'choice' : col.type || 'str'
}

// The groups the picker offers, in the order it shows them.
function groupsOf(rows, key) {
  if (!key) return []
  return [...new Set(rows.map((r) => String(r.values[key] ?? '')))].sort()
}

export default function TableEditor({
  data,
  onSave,
  saving,
  group = '',
  onGroupChange = null,
  onGroupShown = null,
  initialSearch = '',
  readonlyLink = null,
  readOnly = false,
  editorId = null,
  owner = '',
}) {
  const [rows, setRows] = useState([])
  // The rows as loaded: Reset's target, and what a restore is measured against.
  const [loaded, setLoaded] = useState([])
  const [edited, setEdited] = useState(false)
  const [filter, setFilter] = useState(ALL)
  // The owner whose rows alone are shown ('' for every row). Seeded once from
  // the prop, when the file has rows of that owner.
  const [ownerOnly, setOwnerOnly] = useState('')
  const [search, setSearch] = useState('')
  // Row ids whose nested parts are open.
  const [open, setOpen] = useState(() => new Set())
  const [page, setPage] = useState(1)
  const isAdmin = useCan('admin')
  const appliedInitialRef = useRef(false)
  // ?q= as it was at mount, for the same reason as groupRef below: a new ?q=
  // remounts this editor (searchSeed in ConfigEditorDrawer) and must not re-run
  // the data effect, whose handleReset() dropped unsaved rows first.
  const initialSearchRef = useRef(initialSearch)
  // Latest inbound group, for the data effect — which must NOT re-run (and
  // reset every pending edit) just because the param moved.
  const groupRef = useRef(group)
  groupRef.current = group
  // What this editor last wrote to the URL; anything else is an inbound link.
  const emittedRef = useRef(group)

  const filterKey = data.filter_key || null
  const smartSpec = SMART_TABLES[data.smart] || null
  const [view, setView] = useEditorView()
  const smart = !!smartSpec && view === 'smart'
  const columns = smart ? data.columns.filter((c) => smartSpec.columns.includes(c.key)) : data.columns
  // Every edit replaces the row object it touches, so once the last removed
  // row is restored to its place each row is the loaded object again and the
  // table is not dirty. A reference check, stopping at the first difference;
  // an edit typed back to the old value stays dirty.
  const dirty = useMemo(
    () => edited && (rows.length !== loaded.length || rows.some((r, i) => r !== loaded[i])),
    [edited, rows, loaded]
  )
  // Reports up to ConfigEditorDrawer, which owns `data` and so owns the refresh. No-op
  // under ModXmlPage, which renders this editor with no shell around it.
  const sync = useEditorSync(dirty)
  const loadedAt = useMemo(() => new Map(loaded.map((r, i) => [r.id, i])), [loaded])

  // A loaded row goes back before the first row that came after it in the
  // file, so restoring in any order rebuilds the file's order; a row added
  // since uses the index it was removed at.
  function restoreAt(prev, row, index) {
    const at = loadedAt.get(row.id)
    if (at === undefined) return index
    const next = prev.findIndex((r) => (loadedAt.get(r.id) ?? -1) > at)
    if (next >= 0) return next
    const last = prev.findLastIndex((r) => loadedAt.has(r.id))
    return last >= 0 ? last + 1 : index
  }

  // Puts a removed row, list item or tree entry back (trash.jsx).
  const trash = useTrashState(({ path: [root, rowId, key, ...trail], index, item }) => {
    setRows((prev) => {
      if (root === 'rows') return insertAt(prev, [], restoreAt(prev, item, index), item)
      return prev.map((r) => {
        if (r.id !== rowId) return r
        if (root === 'cell') return { ...r, values: { ...r.values, [key]: insertAt(r.values[key], [], index, item) } }
        return {
          ...r,
          nested: { ...r.nested, [key]: insertAt(r.nested[key], trail, index, item) },
          nestedDirty: { ...r.nestedDirty, [key]: true },
        }
      })
    })
    setEdited(true)
  })

  function handleReset() {
    const fresh = fromData(data)
    setRows(fresh)
    setLoaded(fresh)
    setEdited(false)
    trash.clear()
  }

  useEffect(() => {
    handleReset()
    const key = data.filter_key
    const first = !appliedInitialRef.current
    appliedInitialRef.current = true
    // ?q= seeds the search on every table, with a group picker or without
    // (plans/64: a patrol finding opens the patrol table on that patrol).
    if (first) setSearch(initialSearchRef.current)
    const ownerWanted = first && owner && data.rows.some((r) => r.owner === owner)
    if (ownerWanted) setOwnerOnly(owner)
    if (!key) return
    // The owner's rows can sit in several groups, so the picker shows them all.
    if (ownerWanted) {
      emittedRef.current = groupRef.current
      setFilter(ALL)
      return
    }
    // Default to the first group in the picker — Save always submits every row,
    // filtered or not.
    const present = groupsOf(data.rows, key)
    const firstGroup = present.length ? present[0] : ALL
    const wanted = groupRef.current

    if (first) {
      emittedRef.current = wanted
      // A deep link can name a group the file doesn't have (an event with no
      // positions, a since-renamed row) — show everything rather than a
      // seemingly random group.
      setFilter(wanted ? (present.includes(wanted) ? wanted : ALL) : firstGroup)
      return
    }
    // An inbound group that arrived before the rows it names (the effect below
    // could not match it against the previous load's rows).
    if (wanted && wanted !== emittedRef.current && present.includes(wanted)) {
      emittedRef.current = wanted
      setFilter(wanted)
      return
    }
    // Reload: keep the user's group if it still exists, and leave their search
    // alone.
    setFilter((prev) => (prev !== ALL && present.includes(prev) ? prev : firstGroup))
  }, [data])

  // An inbound ?group= change (a second deep link followed while already here).
  // Only a group that could actually be applied is marked as consumed: one that
  // names rows this load doesn't have yet stays pending for the data effect
  // above, and one that names nothing at all simply never applies.
  useEffect(() => {
    if (!filterKey || group === emittedRef.current) return
    if (!group) {
      emittedRef.current = group // param cleared elsewhere — leave the picker be
      return
    }
    if (!groupsOf(rows, filterKey).includes(group)) return
    emittedRef.current = group
    setFilter(group)
  }, [group, filterKey, rows])

  // The rows the picker counts: only the owner's while `ownerOnly` is set, so
  // it offers no group that would show an empty table.
  const scopeRows = useMemo(
    () => (ownerOnly ? rows.filter((r) => r.owner === ownerOnly) : rows),
    [rows, ownerOnly]
  )
  const groups = useMemo(() => groupsOf(scopeRows, filterKey), [scopeRows, filterKey])

  // The group on screen, '' for All, told to the drawer so its View on map
  // shows that event even when the picker only defaulted to it.
  useEffect(() => {
    if (onGroupShown) onGroupShown(filterKey && filter !== ALL ? filter : '')
  }, [onGroupShown, filterKey, filter])

  function pickGroup(value) {
    setFilter(value)
    if (!onGroupChange) return
    const next = value === ALL ? '' : value
    emittedRef.current = next
    onGroupChange(next)
  }

  // Free-text search composes with the group picker. Used as a predicate rather
  // than fuzzyFilter's ranking so rows keep their file order — a table's row
  // order is meaningful, and reshuffling it mid-edit would be disorienting.
  const needle = search.trim()
  const visibleRows = useMemo(() => {
    const grouped =
      filterKey && filter !== ALL
        ? scopeRows.filter((r) => String(r.values[filterKey] ?? '') === filter)
        : scopeRows
    if (!needle) return grouped
    return grouped.filter(
      (r) =>
        fuzzyScoreAny(needle, ...data.columns.map((c) => String(r.values[c.key] ?? ''))) !== null
    )
  }, [scopeRows, filterKey, filter, needle, data.columns])

  useEffect(() => setPage(1), [filter, needle, ownerOnly])

  const totalPages = Math.max(1, Math.ceil(visibleRows.length / PAGE_SIZE))
  const currentPage = Math.min(page, totalPages)
  const pageRows = visibleRows.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE)

  function setCell(rowId, key, value) {
    setRows((prev) =>
      prev.map((r) => (r.id === rowId ? { ...r, values: { ...r.values, [key]: value } } : r))
    )
    setEdited(true)
  }

  function setNested(rowId, key, value) {
    setRows((prev) =>
      prev.map((r) =>
        r.id === rowId
          ? {
              ...r,
              nested: { ...r.nested, [key]: value },
              nestedDirty: { ...r.nestedDirty, [key]: true },
            }
          : r
      )
    )
    setEdited(true)
  }

  function toggleOpen(rowId) {
    setOpen((prev) => {
      const next = new Set(prev)
      if (next.has(rowId)) next.delete(rowId)
      else next.add(rowId)
      return next
    })
  }

  function addRow() {
    const blank = {}
    for (const col of data.columns) blank[col.key] = col.type === 'bool' ? false : col.type === 'list' ? [] : ''
    if (filterKey && filter !== ALL) blank[filterKey] = filter
    const row = { values: blank }
    if (data.new_row_nested) {
      // `data.new_row_nested`: the tree a new entry starts with, so it can be
      // filled in before the first save. Its base is that same empty tree.
      row.nested = structuredClone(data.new_row_nested)
      row.nestedBase = data.new_row_nested
      row.nestedDirty = {}
    }
    setRows((prev) => [...prev, { ...row, id: `new-${prev.length}-${Date.now()}` }])
    setPage(Infinity)
    setEdited(true)
  }

  // `data.duplicate`: copy a row, placed right after it. The id says where it
  // came from ("dup-<source id>-…") so the server can carry over what the table
  // does not show (Dynamic AI's positions).
  function duplicateRow(rowId) {
    setRows((prev) => {
      const at = prev.findIndex((r) => r.id === rowId)
      if (at < 0) return prev
      const src = prev[at]
      const origin = String(src.id).startsWith('dup-') ? String(src.id).split('-')[1] : src.id
      const copy = { id: `dup-${origin}-${Date.now()}`, values: { ...src.values }, choices: src.choices, locked: src.locked }
      if (src.nested) {
        // The server starts the copy from the source entry as the file has
        // it, so the base is the source's as loaded and the source's edits
        // so far travel as the copy's own.
        copy.nested = structuredClone(src.nested)
        copy.nestedBase = src.nestedBase
        copy.nestedDirty = { ...src.nestedDirty }
      }
      return [...prev.slice(0, at + 1), copy, ...prev.slice(at + 1)]
    })
    setEdited(true)
  }

  function removeRow(rowId) {
    const at = rows.findIndex((r) => r.id === rowId)
    if (at < 0) return
    const row = rows[at]
    const name = String(row.values[data.columns[0]?.key] ?? '') || `Row ${at + 1}`
    trash.ctx.add({ label: `Row ${name}`, path: ['rows'], index: at, item: row })
    setRows((prev) => prev.filter((r) => r.id !== rowId))
    setEdited(true)
  }

  if (!data.columns.length) {
    return <FileMissing editorId={editorId} />
  }

  const filterLabel = filterKey
    ? data.columns.find((c) => c.key === filterKey)?.label || filterKey
    : ''

  const hasReadonly = rows.some((r) => r.readonly)
  const nestedKeys = (row) => Object.keys(row.nested || {})
  // The Smart view opens a row from its summary cell, so it has no caret.
  const hasNested = !smart && rows.some((r) => nestedKeys(r).length > 0)
  const columnCount = columns.length + (smart ? 1 : 0) + (readOnly ? 0 : 1)
  // StickyActionBar renders nothing below admin, so the pager goes there only
  // when the bar is really shown.
  const saveBar = !readOnly && isAdmin
  const pager = totalPages > 1 && (
    <Pager
      page={currentPage}
      totalPages={totalPages}
      onPrev={() => setPage(currentPage - 1)}
      onNext={() => setPage(currentPage + 1)}
    />
  )
  // One flag for "this cell can't be typed in", whichever reason applies.
  const frozen = (row) => readOnly || row.readonly
  // A cell the save does not take: the column's or this row's.
  const cellFrozen = (row, col) => frozen(row) || !!col.readonly || !!row.locked?.includes(col.key)

  const caret = (row) =>
    nestedKeys(row).length > 0 && (
      <button
        type="button"
        className="btn btn-ghost btn-small table-nested-toggle"
        aria-expanded={open.has(row.id)}
        aria-label={`${open.has(row.id) ? 'Hide' : 'Show'} ${nestedKeys(row).join(', ')}`}
        {...tooltipAttrs(`${open.has(row.id) ? 'Hide' : 'Show'} ${nestedKeys(row).join(', ')}`)}
        onClick={() => toggleOpen(row.id)}
      >
        <FoldCaret open={open.has(row.id)} />
      </button>
    )

  // A row's own `help` ({<col key>: text}) wins over the column's: one
  // column can hold rows of different meanings (cfgeconomycore's defaults).
  const cellHelp = (row, col) => (row.readonly ? data.readonly_reason : row.help?.[col.key] || col.help)

  const cell = (row, col) =>
    col.opens_nested && nestedKeys(row).length > 0 ? (
      <button
        type="button"
        className="btn btn-ghost btn-small table-nested-summary"
        aria-expanded={open.has(row.id)}
        {...tooltipAttrs(col.help, { keep: true })}
        onClick={() => toggleOpen(row.id)}
      >
        <FoldCaret open={open.has(row.id)} />
        <span className="table-nested-summary-text">{row.values[col.key] || 'None'}</span>
      </button>
    ) : col.type === 'bool' ? (
      <Switch
        label={col.label}
        help={cellHelp(row, col)}
        checked={!!row.values[col.key]}
        disabled={cellFrozen(row, col)}
        onChange={(on) => setCell(row.id, col.key, on)}
      />
    ) : col.type === 'list' ? (
      <ListField
        value={row.values[col.key]}
        item={col.item}
        label={col.label}
        help={cellHelp(row, col)}
        disabled={cellFrozen(row, col)}
        trashPath={['cell', row.id, col.key]}
        onChange={(next) => setCell(row.id, col.key, next)}
      />
    ) : col.multi ? (
      <Select
        multi
        // Without a known list (the definitions file is missing) a flag is typed.
        creatable={!(row.choices?.[col.key] || col.choices)}
        aria-label={col.label}
        help={cellHelp(row, col)}
        value={splitList(row.values[col.key])}
        disabled={cellFrozen(row, col)}
        options={withAll(row.choices?.[col.key] || col.choices || [], splitList(row.values[col.key]))}
        onChange={(next) => setCell(row.id, col.key, next.join(', '))}
      />
    ) : (row.choices?.[col.key] || col.choices) ? (
      <Select
        aria-label={col.label}
        help={cellHelp(row, col)}
        value={row.values[col.key] ?? ''}
        disabled={cellFrozen(row, col)}
        options={withCurrent(row.choices?.[col.key] || col.choices, row.values[col.key])}
        optionHelp={col.choice_help}
        onChange={(v) => setCell(row.id, col.key, v)}
      />
    ) : col.share ? (
      <ShareInput
        className="text-input"
        {...tooltipAttrs(cellHelp(row, col), { keep: true })}
        aria-label={col.label}
        value={row.values[col.key]}
        disabled={cellFrozen(row, col)}
        onChange={(v) => setCell(row.id, col.key, v)}
      />
    ) : (
      <UnitInput
        unit={SHORT_UNITS[col.unit] ?? col.unit}
        className="text-input"
        {...inputAttrsForType(col.type)}
        {...tooltipAttrs(cellHelp(row, col), { keep: true })}
        aria-label={col.label}
        value={row.values[col.key] ?? ''}
        disabled={cellFrozen(row, col)}
        onChange={(e) => setCell(row.id, col.key, e.target.value)}
      />
    )

  return (
    <TrashContext.Provider value={trash.ctx}>
    <div className="table-editor">
      {hasReadonly && (data.readonly_reason || readonlyLink) && (
        <div className="banner banner-info">
          {data.readonly_reason}
          {readonlyLink && (
            <>
              {' '}
              <Link to={readonlyLink.to}>{readonlyLink.label} →</Link>
            </>
          )}
        </div>
      )}
      {ownerOnly && (
        <div className="banner banner-info">
          Showing the rows from{' '}
          {manualIdOf(ownerOnly) ? `the manual XML file ${manualIdOf(ownerOnly)}` : ownerOnly}.{' '}
          <button className="btn-link" type="button" onClick={() => setOwnerOnly('')}>
            Show every row
          </button>
        </div>
      )}
      {data.warnings?.length > 0 && (
        <div className="banner banner-warning">
          <ul className="banner-list">
            {data.warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        </div>
      )}
      {filterKey && (
        <div className="table-filter">
          <label>
            {filterLabel}
            <Select
              value={filter}
              onChange={pickGroup}
              options={[
                { value: ALL, label: `All (${scopeRows.length} rows)` },
                ...groups.map((g) => ({
                  value: g,
                  label: `${g} (${scopeRows.filter((r) => String(r.values[filterKey] ?? '') === g).length})`,
                })),
              ]}
            />
          </label>
        </div>
      )}
      <FilterBox
        value={search}
        onChange={setSearch}
        placeholder="Search rows…"
        shown={visibleRows.length}
        total={rows.length}
        label="rows"
      />
      <div className="inline-form">
        {smartSpec && <ViewSwitch view={view} onChange={setView} />}
        <p className="hint">
          Showing {visibleRows.length} of {rows.length} rows
          {readOnly ? '.' : ' — Save writes all of them.'}
        </p>
      </div>
      <div className="data-table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              {columns.map((col, i) => (
                <th key={col.key} data-type={cellType(col)} className={i === 0 ? 'col-sticky-start' : undefined}>
                  {/* A column named only by its key reads as words (fieldLabel.js). */}
                  <Tooltip keep text={helpWithKey(col.help, fieldLabel(col).raw)}>{fieldLabel(col).text}</Tooltip>
                </th>
              ))}
              {smart && <th>Summary</th>}
              {!readOnly && <th className="col-sticky-end" />}
            </tr>
          </thead>
          <tbody>
            {pageRows.map((row) => (
              <Fragment key={row.id}>
              <tr className={[row.readonly && 'row-readonly', isNewRow(row) && 'row-new'].filter(Boolean).join(' ') || undefined}>
                {columns.map((col, i) =>
                  i === 0 ? (
                    <td key={col.key} data-type={cellType(col)} data-label={fieldLabel(col).text} className="col-sticky-start">
                      <div className="table-first-cell">
                        {hasNested && <span className="table-nested-toggle-slot">{caret(row)}</span>}
                        {cell(row, col)}
                      </div>
                    </td>
                  ) : (
                    <td key={col.key} data-type={cellType(col)} data-label={fieldLabel(col).text}>
                      {cell(row, col)}
                    </td>
                  )
                )}
                {smart && (
                  <td data-type="summary" data-label="Summary">
                    <SummaryToggle summary={smartSpec.summary(row.values)} open={open.has(row.id)} onToggle={() => toggleOpen(row.id)} />
                  </td>
                )}
                {!readOnly && (
                  <td className="col-sticky-end">
                    <div className="table-row-actions">
                      {row.readonly ? (
                        row.manual ? (
                          <Link
                            className="btn btn-ghost btn-small"
                            to={manualFileLink(row.manual)}
                            {...tooltipAttrs(`Edit the manual XML file ${row.manual}`)}
                          >
                            Edit file
                          </Link>
                        ) : row.mod ? (
                          <Link
                            className="btn btn-ghost btn-small"
                            to={modXmlLink(row.mod)}
                            {...tooltipAttrs(`Every update puts ${row.mod}'s rows back. Take its file over on Custom config to edit them.`)}
                          >
                            Take over
                          </Link>
                        ) : (
                          readonlyLink && (
                            <Link className="btn btn-ghost btn-small" to={readonlyLink.to}>
                              {readonlyLink.label}
                            </Link>
                          )
                        )
                      ) : (
                        <>
                          {isNewRow(row) && <RowTag>new</RowTag>}
                          {data.duplicate && (
                            <button
                              type="button"
                              className="btn btn-icon"
                              aria-label="Duplicate row"
                              {...tooltipAttrs('Duplicate: a copy right below this row')}
                              onClick={() => duplicateRow(row.id)}
                            >
                              <HiOutlineDocumentDuplicate />
                            </button>
                          )}
                          {data.remove_row !== false && (
                            <button
                              type="button"
                              className="btn btn-icon btn-icon-danger"
                              aria-label="Remove row"
                              {...tooltipAttrs('Remove (restore it from Removed until you save)')}
                              onClick={() => removeRow(row.id)}
                            >
                              <HiOutlineTrash />
                            </button>
                          )}
                        </>
                      )}
                    </div>
                  </td>
                )}
              </tr>
              {smart && open.has(row.id) && (
                <tr className="table-nested-row">
                  <td colSpan={columnCount}>
                    <smartSpec.Form
                      values={row.values}
                      nested={row.nested}
                      choices={data.nested_choices}
                      templates={data.nested_templates}
                      help={data.nested_help}
                      trashPath={['nest', row.id]}
                      disabled={frozen(row)}
                      onCell={(key, value) => setCell(row.id, key, value)}
                      onTree={(key, next) => setNested(row.id, key, next)}
                    />
                  </td>
                </tr>
              )}
              {!smart && open.has(row.id) && nestedKeys(row).length > 0 && (
                <tr className="table-nested-row">
                  <td colSpan={columnCount}>
                    <NestedParts
                      nested={row.nested}
                      choices={data.nested_choices}
                      templates={data.nested_templates}
                      switches={data.nested_switches}
                      help={data.nested_help}
                      trashPath={['nest', row.id]}
                      disabled={frozen(row)}
                      onChange={(key, next) => setNested(row.id, key, next)}
                    />
                  </td>
                </tr>
              )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
      {!saveBar && pager}
      {saveBar && (
        <StickyActionBar
          dirty={dirty}
          saving={saving}
          onSave={() => onSave(rows.map(rowToSave))}
          onReset={handleReset}
          center={pager}
          {...sync}
        >
          {/* `add_row: false` — rows that need something a blank one cannot
              have (a position) are only ever duplicated. */}
          {data.add_row !== false && (
            <button className="btn btn-ghost btn-add" onClick={addRow}>
              + Add row
            </button>
          )}
          <TrashTray trash={trash} />
        </StickyActionBar>
      )}
    </div>
    </TrashContext.Provider>
  )
}
