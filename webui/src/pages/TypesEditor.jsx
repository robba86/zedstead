import Select, { pathOption } from '../components/Select.jsx'
import SearchInput from '../components/SearchInput.jsx'
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useSearchPrefill } from '../useSearchPrefill.js'
import { api } from '../api'
import EditorDrawer from '../components/EditorDrawer.jsx'
import { PAGE_HELP } from '../pageHelp.js'
import { useToast } from '../components/Toast.jsx'
import ConfirmModal from '../components/ConfirmModal.jsx'
import ScrollTable from '../components/ScrollTable.jsx'
import Pager from '../components/Pager.jsx'
import NestedParts from '../components/NestedParts.jsx'
import { ItemName } from '../components/ItemIcon.jsx'
import FoldCaret from '../components/FoldCaret.jsx'
import { HiOutlineArrowUturnLeft } from 'react-icons/hi2'
import { tooltipAttrs } from '../components/Tooltip.jsx'
import RestoreIcon from '../components/RestoreIcon.jsx'
import { useCan } from '../role.jsx'
import { hiddenEditsNote, useRowEdits } from '../useRowEdits.js'
import { multiplierProblem } from '../multiplier.js'
import { NUMBER_CELLS, NUMBER_LABELS, shortPath, typesEntryBody } from '../typesEntry.js'
import PanelNote from '../components/PanelNote.jsx'
import { useConfirm } from '../components/useConfirm.jsx'
import { DurationField, SummaryToggle, durationText, ViewSwitch, useEditorView } from '../components/SmartView.jsx'
import FilterSelect, { Filters } from '../components/FilterSelect.jsx'
import useStoredOpen from './mapeditor/state/useStoredOpen.js'
import SmartTypeForm, { RARITY, typeSummary } from '../components/SmartType.jsx'

const PAGE_SIZE = 50

// The picker's options: each file by its name, with its short path's folder
// under it, so twins are told apart without a sideways-scrolling row.
function fileOptions(files) {
  return files.map((f) => pathOption(f, shortPath(f)))
}

// What a bulk multiplier does on the server, one line per value that changes
// (a factor of 1 is left out). The words follow the factor: x2 is more and
// longer, x0.5 fewer and shorter. Shown in the Apply confirm.
const MULTIPLIER_EFFECTS = {
  nominal: (up) => `The economy keeps ${up ? 'more' : 'fewer'} of each item on the map: this is the count it spawns up to.`,
  min: (up) => `The economy starts to spawn an item again at a ${up ? 'higher' : 'lower'} count, so it refills ${up ? 'before' : 'after'} the item runs low.`,
  lifetime: (up) => `An item that nobody touches stays on the ground ${up ? 'longer' : 'for a shorter time'} before cleanup removes it.`,
  restock: (up) => `The wait before the economy spawns an item again gets ${up ? 'longer, so refills come slower' : 'shorter, so refills come faster'}. An item with restock 0 stays at 0.`,
}

// The list's filters as the API takes them (routers/types.py `_matching`):
// the list and the bulk multiplier send the same set, so the multiplier
// writes exactly the rows the list counts. An empty filter is left out.
function filterParams({ category, query, rarity, usage, value }) {
  const band = RARITY.find((r) => r.value === rarity)
  const params = {
    category: category || null,
    q: query,
    usage: usage || null,
    value: value || null,
    nominal_min: band ? band.low : null,
    nominal_max: band && Number.isFinite(band.top) ? band.top : null,
  }
  return Object.fromEntries(Object.entries(params).filter(([, v]) => v !== null && v !== ''))
}

// The filters in words, for a bulk write's confirm: "category weapons, Rare".
function filterText({ category, query, rarity, usage, value }) {
  return [
    category && `category ${category}`,
    rarity && RARITY.find((r) => r.value === rarity)?.label,
    usage && `spawns at ${usage}`,
    value && `tier ${value}`,
    query && `name contains "${query}"`,
  ].filter(Boolean).join(', ')
}

// The Filler's boxes, in the order the card shows them.
const FILL_FIELDS = ['nominal', 'min', 'lifetime', 'restock']

// What a fill does to a row that has 0, one line per filled value.
const FILL_EFFECTS = {
  nominal: 'An item at nominal 0 never spawns. Stock DayZ keeps many items at 0 on purpose, and this makes them spawn.',
  min: 'The economy starts to refill the item when fewer than this are left.',
  lifetime: 'An item with lifetime 0 is cleaned up at once. This gives it time on the map.',
  restock: 'Restock 0 refills at once. This adds a wait before a refill.',
}

function multiplierEffects(multipliers) {
  return multipliers
    .map(([name, raw]) => [name, Number(raw)])
    .filter(([, factor]) => factor !== 1)
    .map(([name, factor]) => ({ name, factor, text: MULTIPLIER_EFFECTS[name](factor > 1) }))
}

// The facets as FilterSelect's `available` sets: a rarity band counts when a
// nominal in the list falls inside it. Before the first list, nothing greys.
function availableChoices(facets) {
  if (!facets) return {}
  return {
    category: new Set(facets.categories),
    usage: new Set(facets.usages),
    value: new Set(facets.values),
    rarity: new Set(RARITY.filter((r) => facets.nominals.some((n) => n >= r.low && n <= r.top)).map((r) => r.value)),
  }
}

// The frame of the editor: the Configs page's drawer, or nothing when the
// editor sits in a manual XML file's own drawer (`manual`), which is the frame.
function TypesFrame({ manual, drawer, children }) {
  if (manual) return children
  return (
    <EditorDrawer {...drawer} help={PAGE_HELP['/configs/types']} helpPage="/configs/types">
      {children}
    </EditorDrawer>
  )
}

// The Types.xml editor, in the Configs page's drawer on /configs/types
// (plans/91; it was a page of its own until 2026 Q4). `drawer` is the frame's
// title, width key and close handler, from BespokeEditorDrawer.
//
// With `manual` (a manual XML file's id, plans/92) it is that file's Form tab
// on Custom config: every call carries `?manual=`, so the server lists and
// writes only that file, and there is no file picker and no restore.
// `beforeWrite` resolves false to stop a write (unsaved XML on the other tab),
// and `onSaved` runs after each write, which applies the file.
export default function TypesEditor({ drawer, manual = null, beforeWrite = null, onSaved = null }) {
  // Saves, the bulk multiplier and restore are admin writes (plans/43 §4).
  const isAdmin = useCan('admin')
  const [files, setFiles] = useState([])
  const [file, setFile] = useState('')
  const [meta, setMeta] = useState({ categories: [] })
  const [category, setCategory] = useState('')
  // The Smart filters: a rarity band (SmartType.jsx RARITY), a usage and a value tier.
  const [rarity, setRarity] = useState('')
  const [usage, setUsage] = useState('')
  const [value, setValue] = useState('')
  const [searchParams] = useSearchParams()
  // ?q= is the app-wide search prefill, read once: the box is the user's after.
  // The Custom config page's own ?q= is not this editor's.
  const [query, setQuery] = useState(() => (manual ? '' : searchParams.get('q') || ''))
  // A new ?q= while the page is open (the Ctrl+K palette) replaces the box.
  useSearchPrefill(manual ? () => {} : setQuery)
  // `?manual=<id>` on every call in a manual file's drawer, nothing otherwise.
  const pin = manual ? `?manual=${encodeURIComponent(manual)}` : ''
  // A write: asks `beforeWrite` first and calls `onSaved` after it.
  async function write(call) {
    if (beforeWrite && !(await beforeWrite())) return null
    const data = await call()
    onSaved?.()
    return data
  }
  const [offset, setOffset] = useState(0)
  const [entries, setEntries] = useState([])
  const [total, setTotal] = useState(0)
  // Per filter group, the choices that still list rows (routers/types.py `_facets`).
  const [facets, setFacets] = useState(null)
  // Per `entry.id` (file::name), kept across paging, the filters and another
  // row's save: the cells' overrides, and the keys of `entry.nested` (a row's
  // usage/value/tag/flags, plans/76 step 3) edited so far.
  const { edits, setEdit, dropRow: dropEdits } = useRowEdits()
  const { edits: trees, setEdit: setTree, dropRow: dropTree } = useRowEdits()
  const [openRows, setOpenRows] = useState(() => new Set())
  // Smart: a row is its summary and opens to sentences (SmartType.jsx).
  const [view, setView] = useEditorView()
  const smart = view === 'smart'

  const [multNominal, setMultNominal] = useState(1)
  const [multMin, setMultMin] = useState(1)
  const [multLifetime, setMultLifetime] = useState(1)
  const [multRestock, setMultRestock] = useState(1)
  const [confirmMultiplier, setConfirmMultiplier] = useState(false)
  // The Filler: a value per number, '' to leave it, written where a row has 0.
  const [fill, setFill] = useState({ nominal: '', min: '', lifetime: '', restock: '' })
  const [confirmFill, setConfirmFill] = useState(false)
  // The two bulk cards fold, remembered per browser.
  const [multiplierOpen, setMultiplierOpen] = useStoredOpen('zsm.types.multiplierOpen')
  const [fillerOpen, setFillerOpen] = useStoredOpen('zsm.types.fillerOpen')
  const confirm = useConfirm()

  const pushToast = useToast()

  const loadFiles = useCallback(async () => {
    try {
      const data = await api.get(`/api/types/files${pin}`)
      setFiles(data.files)
      // ?file= (a link from Analysis → Economy) picks the file once, when it is one of them.
      const asked = manual ? null : searchParams.get('file')
      if (!file && data.files.length) setFile(data.files.includes(asked) ? asked : data.files[0])
    } catch (err) {
      pushToast(err.message || 'Failed to load types files', 'error')
    }
  }, [file, pushToast, searchParams, pin, manual])

  const loadMeta = useCallback(async () => {
    try {
      setMeta(await api.get('/api/types/meta'))
    } catch {
      // ignore
    }
  }, [])

  const loadEntries = useCallback(async () => {
    if (!file) return
    try {
      const params = new URLSearchParams({
        file,
        offset: String(offset),
        limit: String(PAGE_SIZE),
        ...filterParams({ category, query, rarity, usage, value }),
        ...(manual && { manual }),
      })
      const data = await api.get(`/api/types?${params.toString()}`)
      setEntries(data.entries)
      setTotal(data.total)
      setFacets(data.facets)
    } catch (err) {
      pushToast(err.message || 'Failed to load type entries', 'error')
    }
  }, [file, category, query, rarity, usage, value, offset, pushToast, manual])

  useEffect(() => {
    loadFiles()
    loadMeta()
  }, [loadFiles, loadMeta])

  useEffect(() => {
    loadEntries()
  }, [loadEntries])

  // Back to the row as loaded: its cells and its tree.
  function resetRow(id) {
    dropEdits(id)
    dropTree(id)
  }

  function toggleRow(id) {
    setOpenRows((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  // Rows whose save is on its way. The ref answers a second click before the
  // re-render that disables the button; on Windows two saves of one file at
  // once gave "permission denied" (user test in 2026 Q4).
  const savingRef = useRef(new Set())
  const [savingIds, setSavingIds] = useState(() => new Set())
  const markSaving = (id, on) => {
    if (on) savingRef.current.add(id)
    else savingRef.current.delete(id)
    setSavingIds(new Set(savingRef.current))
  }

  async function saveRow(entry) {
    if (savingRef.current.has(entry.id)) return
    markSaving(entry.id, true)
    try {
      await saveRowNow(entry)
    } finally {
      markSaving(entry.id, false)
    }
  }

  async function saveRowNow(entry) {
    const { body, error } = typesEntryBody(entry, edits[entry.id], trees[entry.id])
    if (error) {
      pushToast(error, 'error')
      return
    }
    try {
      const saved = await write(() => api.put(`/api/types/entry${pin}`, body))
      if (!saved) return
      // A save into custom/ lasts only until the next merge: the server says so.
      if (saved?.warning) pushToast(saved.warning, 'warning', 10000)
      else pushToast(`${entry.name} saved.`, 'info')
      resetRow(entry.id)
      loadEntries()
    } catch (err) {
      pushToast(err.message || 'Save failed', 'error')
    }
  }

  // The backend refuses these too (app/web/multipliers.py); checking here
  // says which box is wrong before the confirm.
  const multipliers = [
    ['nominal', multNominal],
    ['min', multMin],
    ['lifetime', multLifetime],
    ['restock', multRestock],
  ]
  function askMultiplier() {
    const problem = multipliers.map(([name, raw]) => multiplierProblem(name, raw)).find(Boolean)
    if (problem) {
      pushToast(problem, 'error')
      return
    }
    setConfirmMultiplier(true)
  }

  async function applyMultiplier() {
    setConfirmMultiplier(false)
    try {
      // The rows the dialog counted: the list's file and filters, and the
      // count it showed, which the server checks.
      const data = await write(() => api.post(`/api/types/multiplier${pin}`, {
        file,
        ...filterParams({ category, query, rarity, usage, value }),
        expected: total,
        nominal: Number(multNominal),
        min: Number(multMin),
        lifetime: Number(multLifetime),
        restock: Number(multRestock),
      }))
      if (!data) return
      if (data.warning) pushToast(`Updated ${data.updated} entries. ${data.warning}`, 'warning', 10000)
      else pushToast(`Updated ${data.updated} entries.`, 'info')
      loadEntries()
    } catch (err) {
      pushToast(err.message || 'Multiplier failed', 'error')
    }
  }

  // The Filler's values as the API takes them: whole numbers, blanks left out.
  const fillValues = Object.fromEntries(FILL_FIELDS.filter((f) => String(fill[f]).trim() !== '').map((f) => [f, Number(fill[f])]))
  function askFill() {
    const values = Object.entries(fillValues)
    if (!values.length) {
      pushToast('Type at least one value to fill.', 'error')
      return
    }
    const bad = values.find(([, v]) => !Number.isInteger(v) || v < 0)
    if (bad) {
      pushToast(`${NUMBER_LABELS[bad[0]]} must be a whole number of 0 or more.`, 'error')
      return
    }
    setConfirmFill(true)
  }

  async function applyFill() {
    setConfirmFill(false)
    try {
      const data = await write(() => api.post(`/api/types/fill${pin}`, {
        file,
        ...filterParams({ category, query, rarity, usage, value }),
        expected: total,
        ...fillValues,
      }))
      if (!data) return
      if (data.warning) pushToast(`Filled ${data.updated} entries. ${data.warning}`, 'warning', 10000)
      else pushToast(`Filled ${data.updated} entries.`, 'info')
      loadEntries()
    } catch (err) {
      pushToast(err.message || 'Fill failed', 'error')
    }
  }

  async function restoreFile() {
    const ok = await confirm({
      title: 'Restore from backup?',
      // The server's restore_file: the installed version's file first (F10).
      message: `Restore ${file}? On a stock mission this puts back the file of the installed DayZ version, else its .backup copy. Your edits go, and a version is saved first.`,
      confirmLabel: 'Restore',
      danger: true,
    })
    if (!ok) return
    try {
      const data = await api.post('/api/types/restore', { file })
      pushToast(data.restored ? 'Restored from backup.' : 'No backup found.', 'info')
      loadEntries()
    } catch (err) {
      pushToast(err.message || 'Restore failed', 'error')
    }
  }

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const page = Math.floor(offset / PAGE_SIZE) + 1
  const hiddenNote = hiddenEditsNote([edits, trees], entries.map((entry) => entry.id))
  const available = useMemo(() => availableChoices(facets), [facets])

  return (
    <TypesFrame manual={manual} drawer={drawer}>
      {/* The same bar, glyph and words as a registry editor's Restore from
          backup (ConfigEditorDrawer). */}
      {isAdmin && !manual && (
        <div className="action-bar editor-drawer-actions">
          <button className="btn btn-ghost" disabled={!file} onClick={restoreFile}>
            <RestoreIcon />
            Restore from backup
          </button>
        </div>
      )}

      <div className="inline-form">
        {!manual && (
          <Select
            aria-label="File"
            value={file}
            onChange={(v) => { setFile(v); setOffset(0) }}
            options={fileOptions(files)}
            help={file}
          />
        )}
        <SearchInput
          placeholder="Search name…"
          value={query}
          onChange={(e) => { setQuery(e.target.value); setOffset(0) }}
        />
        <ViewSwitch view={view} onChange={setView} />
      </div>

      <Filters>
        <FilterSelect label="Category" value={category} options={meta.categories} available={available.category} onChange={(v) => { setCategory(v); setOffset(0) }} />
        <FilterSelect
          label="Rarity"
          value={rarity}
          options={RARITY.map(({ value, label, description }) => ({ value, label, description }))}
          available={available.rarity}
          onChange={(v) => { setRarity(v); setOffset(0) }}
        />
        <FilterSelect label="Spawns at" value={usage} options={meta.usages || []} available={available.usage} onChange={(v) => { setUsage(v); setOffset(0) }} />
        <FilterSelect label="Map tier" value={value} options={meta.values || []} available={available.value} onChange={(v) => { setValue(v); setOffset(0) }} />
      </Filters>

      {/* The two bulk tools sit side by side where both fit, stacked on a
          phone, a small tablet and in the Configs drawer (index.css .card-pair). */}
      {isAdmin && (
      <div className="card-pair">
      <details className="card card-fold" open={multiplierOpen} onToggle={(e) => setMultiplierOpen(e.currentTarget.open)}>
        <summary>
          <FoldCaret />
          <h2>Bulk multiplier ({total} matching entries)</h2>
        </summary>
        <div className="inline-form">
          <label className="modal-field">
            Nominal ×
            <input className="text-input text-input-narrow" type="number" step="0.1" min="0.1" max="10" value={multNominal} onChange={(e) => setMultNominal(e.target.value)} />
          </label>
          <label className="modal-field">
            Min ×
            <input className="text-input text-input-narrow" type="number" step="0.1" min="0.1" max="10" value={multMin} onChange={(e) => setMultMin(e.target.value)} />
          </label>
          <label className="modal-field">
            Lifetime ×
            <input className="text-input text-input-narrow" type="number" step="0.1" min="0.1" max="10" value={multLifetime} onChange={(e) => setMultLifetime(e.target.value)} />
          </label>
          <label className="modal-field">
            Restock ×
            <input className="text-input text-input-narrow" type="number" step="0.1" min="0.1" max="10" value={multRestock} onChange={(e) => setMultRestock(e.target.value)} />
          </label>
          <button className="btn btn-primary" onClick={askMultiplier}>
            Apply to filtered
          </button>
        </div>
      </details>

      <details className="card card-fold" open={fillerOpen} onToggle={(e) => setFillerOpen(e.currentTarget.open)}>
        <summary>
          <FoldCaret />
          <h2>Filler ({total} matching entries)</h2>
        </summary>
        <PanelNote>
          A multiplier cannot change a 0. The Filler writes a value only where a filtered row has 0, and leaves every other value alone. Leave a box empty to skip that value.
        </PanelNote>
        <div className="inline-form">
          <label className="modal-field">
            Nominal
            <input className="text-input text-input-narrow" inputMode="numeric" value={fill.nominal} onChange={(e) => setFill({ ...fill, nominal: e.target.value })} />
          </label>
          <label className="modal-field">
            Min
            <input className="text-input text-input-narrow" inputMode="numeric" value={fill.min} onChange={(e) => setFill({ ...fill, min: e.target.value })} />
          </label>
          <span className="modal-field">
            Lifetime
            <DurationField label="Lifetime" value={fill.lifetime} onChange={(v) => setFill((f) => ({ ...f, lifetime: v }))} />
          </span>
          <span className="modal-field">
            Restock
            <DurationField label="Restock" value={fill.restock} onChange={(v) => setFill((f) => ({ ...f, restock: v }))} />
          </span>
          <button className="btn btn-primary" onClick={askFill}>
            Fill filtered
          </button>
        </div>
      </details>
      </div>
      )}

      {hiddenNote && <PanelNote>{hiddenNote}</PanelNote>}

      {/* table-cards: a phone shows each row as a card (index.css), so an
          open row's usage, value and tag fields fit without a sideways scroll. */}
      <ScrollTable
        className="table-cards"
        footer={
          <Pager
            page={page}
            totalPages={totalPages}
            onPrev={() => setOffset(offset - PAGE_SIZE)}
            onNext={() => setOffset(offset + PAGE_SIZE)}
          />
        }
      >
        <table className="data-table">
          <thead>
            <tr>
              {!smart && <th className="table-nested-toggle-cell" />}
              <th>Name</th>
              <th>Category</th>
              {smart ? (
                <th>Summary</th>
              ) : (
                NUMBER_CELLS.map((f) => <th key={f}>{NUMBER_LABELS[f]}</th>)
              )}
              <th className="col-sticky-end" />
            </tr>
          </thead>
          <tbody>
            {entries.map((entry) => {
              const override = edits[entry.id] || {}
              // The row as shown: its loaded numbers with this page's edits on top.
              const values = Object.fromEntries(NUMBER_CELLS.map((f) => [f, override[f] ?? entry[f]]))
              const rowDirty = !!edits[entry.id] || !!trees[entry.id]
              const isOpen = openRows.has(entry.id)
              const toggleLabel = `${isOpen ? 'Hide' : 'Show'} usage, value, tag and flags`
              return (
                <Fragment key={entry.id}>
                <tr className={rowDirty ? 'row-dirty' : undefined}>
                  {!smart && (
                  <td className="table-nested-toggle-cell">
                    {entry.nested && (
                      <button
                        type="button"
                        className="btn btn-ghost btn-small table-nested-toggle"
                        aria-expanded={isOpen}
                        aria-label={toggleLabel}
                        {...tooltipAttrs(toggleLabel)}
                        onClick={() => toggleRow(entry.id)}
                      >
                        <FoldCaret open={isOpen} />
                      </button>
                    )}
                  </td>
                  )}
                  <td className="cell-name">
                    <ItemName name={entry.name} />
                  </td>
                  {/* data-type gives each cell the table editors' minimum
                      width (index.css): the inputs fill their cell, and on a
                      phone the cells were squeezed until a number was cut. */}
                  <td data-type="str" data-label="Category">
                    <input
                      className="text-input"
                      value={override.category ?? entry.category}
                      onChange={(e) => setEdit(entry.id, 'category', e.target.value)}
                    />
                  </td>
                  {smart ? (
                    <td data-type="summary" data-label="Summary">
                      <SummaryToggle summary={typeSummary(values)} open={isOpen} onToggle={() => toggleRow(entry.id)} />
                    </td>
                  ) : NUMBER_CELLS.map((f) => (
                    <td key={f} data-type="int" data-label={NUMBER_LABELS[f]}>
                      <input
                        className="text-input text-input-narrow"
                        value={override[f] ?? entry[f]}
                        onChange={(e) => setEdit(entry.id, f, e.target.value)}
                      />
                    </td>
                  ))}
                  <td className="col-sticky-end">
                    {isAdmin && (
                    <div className="table-row-actions">
                      {rowDirty && (
                        <button
                          type="button"
                          className="btn btn-icon"
                          aria-label={`Reset ${entry.name}`}
                          {...tooltipAttrs('Reset: back to the row as loaded')}
                          onClick={() => resetRow(entry.id)}
                        >
                          <HiOutlineArrowUturnLeft />
                        </button>
                      )}
                      <button
                        className={`btn btn-small ${rowDirty ? 'btn-primary' : 'btn-ghost'}`}
                        disabled={!rowDirty || savingIds.has(entry.id)}
                        onClick={() => saveRow(entry)}
                      >
                        {savingIds.has(entry.id) ? 'Saving…' : 'Save'}
                      </button>
                    </div>
                    )}
                  </td>
                </tr>
                {smart && isOpen && (
                  <tr className="table-nested-row">
                    <td colSpan={4}>
                      <SmartTypeForm
                        values={values}
                        nested={entry.nested && { ...entry.nested, ...trees[entry.id] }}
                        choices={{ usage: meta.usages, value: meta.values, tag: meta.tags }}
                        help={meta.nested_help}
                        disabled={!isAdmin}
                        onCell={(key, value) => setEdit(entry.id, key, value)}
                        onTree={(key, next) => setTree(entry.id, key, next)}
                      />
                    </td>
                  </tr>
                )}
                {!smart && isOpen && entry.nested && (
                  <tr className="table-nested-row">
                    <td colSpan={8}>
                      <NestedParts
                        nested={{ ...entry.nested, ...trees[entry.id] }}
                        choices={{ usage: meta.usages, value: meta.values, tag: meta.tags }}
                        switches={meta.nested_switches}
                        help={meta.nested_help}
                        disabled={!isAdmin}
                        onChange={(key, next) => setTree(entry.id, key, next)}
                      />
                    </td>
                  </tr>
                )}
                </Fragment>
              )
            })}
          </tbody>
        </table>
      </ScrollTable>

      <ConfirmModal
        open={confirmMultiplier}
        title="Apply multiplier?"
        message={[
          `This changes ${total} ${total === 1 ? 'item' : 'items'} in ${shortPath(file)}`,
          filterText({ category, query, rarity, usage, value }),
        ].filter(Boolean).join(', ') + '.'}
        confirmLabel="Apply"
        onConfirm={applyMultiplier}
        onCancel={() => setConfirmMultiplier(false)}
      >
        <MultiplierSummary effects={multiplierEffects(multipliers)} names={entries.map((e) => e.name)} total={total} />
      </ConfirmModal>

      <ConfirmModal
        open={confirmFill}
        title="Fill values?"
        message={[
          `This fills values on ${total} filtered ${total === 1 ? 'item' : 'items'} in ${shortPath(file)}`,
          filterText({ category, query, rarity, usage, value }),
        ].filter(Boolean).join(', ') + '. Only a value that is 0 changes.'}
        confirmLabel="Fill"
        onConfirm={applyFill}
        onCancel={() => setConfirmFill(false)}
      >
        <div className="multiplier-summary">
          <ul>
            {Object.entries(fillValues).map(([name, v]) => (
              <li key={name}>
                <strong>
                  {NUMBER_LABELS[name]} 0 → {name === 'lifetime' || name === 'restock' ? durationText(v) : v}:
                </strong>{' '}
                {FILL_EFFECTS[name]}
              </li>
            ))}
          </ul>
          <p>The server uses the new values after its next restart.</p>
        </div>
      </ConfirmModal>
    </TypesFrame>
  )
}

// The Apply confirm's body: what each changed value does on the server, the
// items it touches (the page's rows, then a count of the rest) and when it
// takes effect.
function MultiplierSummary({ effects, names, total }) {
  if (!effects.length) return <p className="modal-message">Every multiplier is 1, so no value changes.</p>
  const rest = total - names.length
  return (
    <div className="multiplier-summary">
      <ul>
        {effects.map(({ name, factor, text }) => (
          <li key={name}>
            <strong>
              {NUMBER_LABELS[name]} ×{factor}:
            </strong>{' '}
            {text}
          </li>
        ))}
      </ul>
      <p>Each new value is rounded down to a whole number. The server uses the new values after its next restart.</p>
      <p className="multiplier-summary-label">Items</p>
      <div className="multiplier-summary-items">
        {names.join(', ')}
        {rest > 0 && `, and ${rest} more`}
      </div>
    </div>
  )
}
