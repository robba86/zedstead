import Select from '../components/Select.jsx'
import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { HiOutlineCheck, HiOutlinePlus, HiOutlineXMark } from 'react-icons/hi2'
import { api } from '../api'
import CodeArea from '../components/CodeArea.jsx'
import FileIcon from '../components/FileIcon.jsx'
import { Switch } from '../components/FlagSwitch.jsx'
import SideDrawer from '../components/SideDrawer.jsx'
import Tabs from '../components/Tabs.jsx'
import TableEditor from '../components/TableEditor.jsx'
import FormEditor from '../components/FormEditor.jsx'
import { useFitCards } from '../components/DataTable.jsx'
// Its own chunk, as on /configs/types (BespokeEditorDrawer).
const TypesEditor = lazy(() => import('./TypesEditor.jsx'))
import XmlTools from '../components/XmlTools.jsx'
import { tooltipAttrs } from '../components/Tooltip.jsx'
import { useToast } from '../components/Toast.jsx'
import { useConfirm } from '../components/useConfirm.jsx'
import {
  discardUnsavedChanges,
  useConfirmDiscard,
  useUnsavedChanges,
} from '../useUnsavedChanges.js'
import { MISSION_FILE_SLUGS, configLink } from '../crossLinks.js'
import { useJobs } from '../jobs.jsx'
import { XML_HOW, XML_KIND_LABEL, XML_STATUS, pillClass, sameMod } from '../modXmlStatus.js'
import { useCan } from '../role.jsx'
import PanelNote from '../components/PanelNote.jsx'
import LoadingState from '../components/LoadingState.jsx'

// Manual XML files: user-authored mission XML that the merge re-applies.
//
// Why the feature exists (app/xml/xml_manual.py has the long version): since
// 2026 Q3 a merge leaves the mission files alone apart from the mod blocks it
// owns, so this is no longer how you *keep* an edit. What it still buys: the
// content lives outside the mission, so it survives a map switch, a reinstall
// and the custom/ wipe every merge does — and it is applied after every mod, so
// it wins where DayZ takes the last definition.
//
// There are still exactly TWO ways a file is applied, and they are not
// interchangeable — but the form no longer asks the user to know them. One
// "Goes into" picker offers both, grouped:
//
//   custom/ folder — only the six kinds cfgeconomycore can reference (types,
//           events, spawnabletypes, globals, economy, messages). Result:
//           custom/user-<id>.xml plus a <ce folder="custom"> entry (the "link"
//           mode). The option list comes from the API's `ce_kinds`.
//   a mission file — any other mission XML. Result: the fragment spliced into
//           that file inside this file's own START/END MOD markers (the
//           "merge" mode). cfgeconomycore is never involved.
//
// A merge does NOT ask for a kind: the user picks the *file*, and the server
// (`xml_manual.normalize_kind_target`) upgrades it to the smarter merge config
// when one owns that file.
//
// Every write here is followed by the apply job, so what the table shows is
// what the mission has — there is no separate "Apply" step to forget.

const CE_TARGET = 'cfgeconomycore.xml'

// Display name for a stored map scope. `terrain_names` covers every spelling
// the server accepts (`livonia` as well as `enoch`); a map zsm doesn't know
// falls back to the raw value rather than rendering blank.
function terrainLabel(terrain, names) {
  if (!terrain) return ''
  return (names || {})[terrain] || terrain
}

// Shown on a file taken over from a mod whose own copy has since changed. The
// whole point of the badge: nothing else in the app looks at a mod file this
// mission no longer applies, so without it the drift is invisible forever.
// Exported: ModXmlPage shows the same badge on the mod-file row this manual file
// replaced, and one wording for one signal.
export const UPSTREAM_TIP =
  'The mod has changed its own copy of this file since you took it over. Your ' +
  'version is what the mission loads — see what changed and decide whether to port it.'

// "Linked" / "Merged" ahead of a "Goes into" file. Exported: the Mod files
// table on ModXmlPage shows the same badge, one wording for one fact.
export function HowBadge({ how }) {
  const info = XML_HOW[how]
  if (!info) return null
  return (
    <span className={`modxml-how modxml-how-${how}`} {...tooltipAttrs(info.tip)}>
      {info.label}
    </span>
  )
}

const kindLabel = (kind) => (XML_KIND_LABEL[kind] ? `${XML_KIND_LABEL[kind]} (${kind})` : kind)

// "Goes into" is one <select> value: `ce:<kind>` or `file:<target>`.
const ceDest = (kind) => `ce:${kind}`
const fileDest = (target) => `file:${target}`
function parseDest(dest) {
  const [how, ...rest] = (dest || '').split(':')
  const value = rest.join(':')
  return how === 'ce' ? { mode: 'link', kind: value, target: '' } : { mode: 'merge', kind: '', target: value }
}
function destLabel(dest) {
  const d = parseDest(dest)
  return d.mode === 'link' ? `the custom/ folder as ${kindLabel(d.kind)}` : d.target
}

const EMPTY = { id: '', dest: '', terrain: '', mod: '', note: '', content: '' }

// "Start from:" — a ready-to-edit skeleton for whatever the form is pointed at.
// Three sources, all served by GET /api/modxml/files/starters: shipped
// templates for the kind or file, one element sampled out of the user's own
// mission file (mod-merged blocks stripped), and an empty root built from that
// file's actual root tag — which is how a mission XML zsm ships no template
// for still gets a correct starting point.
function StarterRow({ starters, onInsert }) {
  if (!starters) return null
  const buttons = [
    ...(starters.templates || []).map((t) => ({
      key: t.id,
      label: t.label,
      title: t.summary,
      content: t.content,
    })),
    ...(starters.sample
      ? [
          {
            key: 'sample',
            label: `Copy an element from ${starters.sample.from}`,
            title:
              'Copies the first element in that file that no mod added, so you can edit a real one.',
            content: starters.sample.content,
          },
        ]
      : []),
    ...(starters.empty
      ? [
          {
            key: 'empty',
            label: starters.empty.label,
            title: 'Just the root element, ready for you to fill in.',
            content: starters.empty.content,
          },
        ]
      : []),
  ]
  if (!buttons.length && !starters.reason) return null

  return (
    <div className="manual-file-starters">
      <span className="manual-file-starters-label">Start from:</span>
      {buttons.map((b) => (
        <button
          key={b.key}
          className="btn btn-ghost btn-small"
          type="button"
          {...tooltipAttrs(b.title)}
          onClick={() => onInsert(b.content)}
        >
          {b.label}
        </button>
      ))}
      {starters.reason && <span className="field-hint">{starters.reason}</span>}
    </div>
  )
}

// The Form tab: a saved manual file in its kind's own editor, the one the
// mission file has (GET/PUT /api/modxml/files/<id>/form, plans/92). Its save
// writes the manual file, and `onSaved` then applies it, as every manual write.
// `beforeSave` resolves false to stop a save (unsaved XML on the other tab).
// A types file gets the Types editor itself, pinned to the file (`manual`).
function KindForm({ fileId, kind, beforeSave, onSaved }) {
  if (kind === 'types') {
    return (
      <Suspense fallback={<LoadingState />}>
        <TypesEditor manual={fileId} beforeWrite={beforeSave} onSaved={onSaved} />
      </Suspense>
    )
  }
  return <RowsForm fileId={fileId} beforeSave={beforeSave} onSaved={onSaved} />
}

// The table or form editor of a manual file's kind, on the file's own XML.
function RowsForm({ fileId, beforeSave, onSaved }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const pushToast = useToast()
  const path = `/api/modxml/files/${encodeURIComponent(fileId)}/form`

  const load = useCallback(async () => {
    try {
      setData(await api.get(path))
      setError('')
    } catch (err) {
      setError(err.message || 'Failed to load the form')
    }
  }, [path])

  useEffect(() => {
    load()
  }, [load])

  async function save(body) {
    if (!(await beforeSave())) return
    setSaving(true)
    try {
      await api.put(path, body)
    } catch (err) {
      pushToast(err.message || 'Save failed', 'error')
      setSaving(false)
      return
    }
    setSaving(false)
    await load()
    onSaved()
  }

  if (error) return <div className="banner banner-error">{error}</div>
  if (!data) return <LoadingState />
  return data.kind === 'form' ? (
    <FormEditor data={data} onSave={save} saving={saving} editorId={data.editor} />
  ) : (
    <TableEditor data={data} onSave={save} saving={saving} editorId={data.editor} />
  )
}

const DRAWER_TABS = [
  { id: 'form', label: 'Form' },
  { id: 'xml', label: 'XML' },
]

// The unsaved-changes name of the one form below, which another file's Edit
// or Add XML would replace. Exported: the page asks with it before one of its
// own drawers takes the form drawer's place.
export const FORM_NAME = 'manual-xml-file'

// FileForm's key: a new one remounts the form and drops what it holds.
const formKey = (editing) => (editing ? (editing.existing ? editing.form.id : 'new') : null)

function FileForm({
  ceKinds,
  terrains,
  terrainNames,
  targets,
  kindTargets,
  mods,
  initial,
  editing,
  onCancel,
  onSubmit,
  saving,
  onSuggest,
  hidden = false,
}) {
  const [form, setForm] = useState(initial)
  const [suggestion, setSuggestion] = useState(null)
  const [starters, setStarters] = useState(null)
  const confirm = useConfirm()
  const dest = parseDest(form.dest)
  // Leaving the page asks first, as every config editor does: this box can
  // hold a whole hand-written types file, and a click on the sidebar dropped
  // it without a word. Cancel and Save unmount the
  // form, which clears it. Not pageWide: a tab switch unmounts it too.
  const dirty = useMemo(() => JSON.stringify(form) !== JSON.stringify(initial), [form, initial])
  useUnsavedChanges(dirty, { form: FORM_NAME })

  const setTo = (key) => (value) => setForm({ ...form, [key]: value })
  const set = (key) => (e) => setTo(key)(e.target.value)

  // target -> kind, for the files one of the five merge configs owns. Built
  // from the server's own map so the two can't drift.
  const smartTargets = useMemo(() => {
    const out = {}
    for (const [kind, target] of Object.entries(kindTargets || {})) {
      if (target && target !== CE_TARGET) out[target] = kind
    }
    return out
  }, [kindTargets])

  // Ask the server what the pasted XML looks like. Debounced, and purely a
  // proposal — nothing is applied until the user clicks it.
  useEffect(() => {
    const text = (form.content || '').trim()
    if (text.length < 12) {
      setSuggestion(null)
      return
    }
    let cancelled = false
    const timer = setTimeout(async () => {
      const guess = await onSuggest(text)
      if (!cancelled) setSuggestion(guess)
    }, 500)
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [form.content, onSuggest])

  // Only worth showing when it disagrees with what the form is set to.
  const proposal = useMemo(() => {
    if (!suggestion?.kind) return null
    const want = suggestion.mode === 'link' ? ceDest(suggestion.kind) : fileDest(suggestion.target)
    if (want === form.dest) return null
    return { dest: want, text: `This looks like ${kindLabel(suggestion.kind)}.` }
  }, [suggestion, form.dest])

  // Starter XML for the "Start from:" buttons. Not debounced — these change on
  // a select, not on typing. Silently absent on failure, like the classifier:
  // a missing convenience is not worth a toast.
  const starterKey =
    dest.mode === 'link'
      ? dest.kind && `kind=${dest.kind}`
      : dest.target && `target=${encodeURIComponent(dest.target)}`
  useEffect(() => {
    if (!starterKey) {
      setStarters(null)
      return
    }
    let cancelled = false
    api
      .get(`/api/modxml/files/starters?${starterKey}`)
      .then((d) => !cancelled && setStarters(d))
      .catch(() => !cancelled && setStarters(null))
    return () => {
      cancelled = true
    }
  }, [starterKey])

  async function insertStarter(content) {
    if (
      (form.content || '').trim() &&
      !(await confirm({
        title: 'Replace your XML?',
        message: 'The XML in the box is replaced with this starter.',
        confirmLabel: 'Replace',
      }))
    ) {
      return
    }
    // Replace, never append: two roots in one file is not a fragment the CE can
    // load, and detecting that here would need an XML parser the frontend
    // deliberately doesn't have.
    setForm({ ...form, content })
  }

  let explain = null
  if (dest.mode === 'link' && dest.kind) {
    explain = (
      <>
        Saved as <code>custom/user-{form.id || '<name>'}.xml</code> and listed in{' '}
        <code>{CE_TARGET}</code> — the same way mods add theirs.
      </>
    )
  } else if (dest.target && smartTargets[dest.target]) {
    explain = (
      <>
        Merged into the right sections of <code>{dest.target}</code>, inside a marked block so it
        can be updated or removed cleanly.
      </>
    )
  } else if (dest.target) {
    explain = (
      <>
        Everything inside your root element is added to the end of <code>{dest.target}</code>,
        inside a marked block so it can be updated or removed cleanly.
      </>
    )
  }

  return (
    <form
      className="manual-file-form"
      id="manual-file-form"
      // Kept mounted behind the Form tab, so its unsaved XML survives a switch.
      hidden={hidden}
      onSubmit={(e) => {
        e.preventDefault()
        onSubmit(form)
      }}
    >
      <div className="form-row">
        <label>
          Name
          <input
            className="text-input"
            value={form.id}
            onChange={set('id')}
            disabled={editing}
            placeholder="livonia-heli-spawns"
            required
          />
          <span className="field-hint">Lowercase letters, digits, - and _.</span>
        </label>

        <label>
          Goes into
          <Select
            value={form.dest}
            onChange={setTo('dest')}
            required
            placeholder="Choose where it goes…"
            options={[
              {
                label: 'The custom/ folder (listed in cfgeconomycore.xml)',
                options: ceKinds.map((k) => ({ value: ceDest(k), label: kindLabel(k) })),
              },
              {
                label: 'A mission file',
                options: [
                  ...targets.map((t) => ({ value: fileDest(t), label: t })),
                  // A saved target the mission no longer has stays selectable, or
                  // editing the file would silently blank where it goes.
                  ...(dest.mode === 'merge' && dest.target && !targets.includes(dest.target)
                    ? [{ value: form.dest, label: dest.target }]
                    : []),
                ],
              },
            ]}
          />
        </label>

        <label>
          Map
          {/* One row per map zsm knows about, named the way the Maps page
              names it — the server derives both from the same catalog. A scope
              saved as an accepted alias (or for a map zsm doesn't ship) is
              kept as its own option so editing a row can't silently move it to
              another map. */}
          <Select
            value={form.terrain}
            onChange={setTo('terrain')}
            options={[
              { value: '', label: 'Every map' },
              ...terrains.map((t) => ({ value: t.terrain, label: t.name })),
              ...(form.terrain && !terrains.some((t) => t.terrain === form.terrain)
                ? [{ value: form.terrain, label: terrainLabel(form.terrain, terrainNames) }]
                : []),
            ]}
          />
          <span className="field-hint">Only used while this map is loaded.</span>
        </label>

        <label>
          For mod (optional)
          {/* Creatable: the installed mods are suggestions, but the field has to
              stay free text so XML can be prepared for a mod that isn't
              installed yet. */}
          <Select
            creatable
            clearable
            value={form.mod}
            onChange={setTo('mod')}
            placeholder="@TraderPlus or 1590841260"
            options={mods.map((m) => ({ value: m.name, label: m.name, description: m.id || undefined }))}
          />
          <span className="field-hint">
            Name or workshop ID. Re-applied with that mod, and flagged if it isn&apos;t installed.
          </span>
        </label>
      </div>

      <label className="manual-file-note">
        Note
        <input
          className="text-input"
          value={form.note}
          onChange={set('note')}
          placeholder="What this adds and why"
        />
      </label>

      {explain && <p className="manual-file-explain">{explain}</p>}

      {proposal && (
        <div className="manual-file-suggest">
          <span>{proposal.text}</span>
          <button
            className="btn btn-ghost btn-small"
            type="button"
            onClick={() => setForm({ ...form, dest: proposal.dest })}
          >
            Put it in {destLabel(proposal.dest)}
          </button>
        </div>
      )}

      <StarterRow starters={starters} onInsert={insertStarter} />

      <XmlTools
        content={form.content}
        onChange={(content) => setForm({ ...form, content })}
        fragment
      >
        <CodeArea
          value={form.content}
          onChange={(content) => setForm({ ...form, content })}
          language="xml"
        />
      </XmlTools>
      <div className="action-bar">
        <button className="btn btn-primary" type="submit" disabled={saving}>
          <HiOutlineCheck aria-hidden="true" />
          {saving ? 'Saving…' : 'Save and apply'}
        </button>
        <button className="btn btn-ghost" type="button" onClick={onCancel}>
          <HiOutlineXMark aria-hidden="true" />
          Cancel
        </button>
      </div>
    </form>
  )
}

// The Manual XML files card: the table of the user's own files, and the add
// or edit form in a side drawer (one drawer at a time with the page's own).
export default function ManualXmlFiles({
  reportFiles,
  onChanged,
  version = 0,
  focus = null,
  onViewUpstream = null,
  onOpenCustom = null,
  // A mod whose files are marked (ModXmlPage's `?mod=`).
  markMod = '',
  // (slug, fileId) => open that mission file's config editor on this file's
  // rows. Without it the "Goes into" file is a plain link to the editor.
  onOpenEditor = null,
  // (open) => told when the form drawer opens or closes, so the page closes
  // its own drawer and reserves room for this one.
  onFormOpen = null,
  // Bumped by the page when one of its drawers takes this drawer's place.
  closeForm = 0,
  // The drawer width key, shared with the page's drawers.
  drawerKey = 'zsm.modxml.drawer.width',
}) {
  const [data, setData] = useState(null)
  const [editing, setEditing] = useState(null) // {form, existing}, or null
  const [saving, setSaving] = useState(false)
  // The drawer's tab, and a counter that remounts the XML tab after the Form
  // tab saved, so it shows the file as written.
  const [drawerTab, setDrawerTab] = useState('form')
  const [xmlRev, setXmlRev] = useState(0)
  const handledFocus = useRef(null)
  const pushToast = useToast()
  const confirm = useConfirm()
  const confirmDiscard = useConfirmDiscard()
  // Read by `open` without making `edit` change identity on every keystroke.
  const editingRef = useRef(null)
  // The table turns into cards up to 1024px, and wherever it does not fit
  // (DataTable's cardsBelow, the Mods page's Installed list).
  const tableRef = useRef(null)
  useFitCards(Boolean(data?.files?.length), tableRef, data?.files, 1024)
  editingRef.current = editing
  // Every write here — add, edit, toggle, delete — is admin.
  const isAdmin = useCan('admin')
  const { startJob } = useJobs()

  // Resolves to the list it loaded (null on failure).
  const load = useCallback(async () => {
    try {
      const next = await api.get('/api/modxml/files')
      setData(next)
      return next
    } catch (err) {
      pushToast(err.message || 'Failed to load manual XML files', 'error')
      return null
    }
  }, [pushToast])

  // `version` is bumped by the page whenever it reloads the report, so an action
  // taken elsewhere on the page (taking a mod file over writes a manual file)
  // shows up here without a page reload.
  useEffect(() => {
    load()
  }, [load, version])

  // The page opened a drawer of its own (it asked about unsaved edits first).
  useEffect(() => {
    if (closeForm) setEditing(null)
  }, [closeForm])

  const formOpen = Boolean(editing)
  useEffect(() => {
    onFormOpen?.(formOpen)
  }, [formOpen, onFormOpen])

// The drawer's close button, Escape and Cancel: ask before dropping edits.
  const cancelForm = useCallback(async () => {
    if (await confirmDiscard(FORM_NAME)) setEditing(null)
  }, [confirmDiscard])

  // A mission file's config editor replaces the form drawer. The user already
  // answered the discard question, so the route guard must not ask again.
  const openEditor = useCallback(
    async (slug, fileId) => {
      if (!(await confirmDiscard(FORM_NAME))) return
      if (editingRef.current) {
        discardUnsavedChanges()
        setEditing(null)
      }
      onOpenEditor(slug, fileId)
    },
    [confirmDiscard, onOpenEditor]
  )

  const ceKinds = useMemo(() => data?.ce_kinds || [], [data])
  const kindTargets = useMemo(() => data?.kind_targets || {}, [data])

  // Opening another file's form remounts it (its key), so a half-written
  // file asks first. The same file again keeps the form as it is.
  const open = useCallback(
    async (next) => {
      if (formKey(next) !== formKey(editingRef.current) && !(await confirmDiscard(FORM_NAME))) {
        return
      }
      if (formKey(next) !== formKey(editingRef.current)) setDrawerTab('form')
      setEditing(next)
    },
    [confirmDiscard]
  )

  // Editing an existing file: the stored kind decides where it goes, and a
  // known direct kind gets its implied file back into the picker.
  const edit = useCallback(
    (file) => {
      const isCe = ceKinds.includes(file.kind)
      return open({
        existing: true,
        form: {
          ...EMPTY,
          ...file,
          dest: isCe ? ceDest(file.kind) : fileDest(file.target || kindTargets[file.kind] || ''),
        },
      })
    },
    [ceKinds, kindTargets, open]
  )

  // Open the form for a file the page pointed at ("Edit your copy", or right
  // after taking a file over). The drawer is the view for an admin. Anyone
  // else gets the file's row brought into view.
  //
  // Consumed once per request: the effect has to wait for `data` (the file may
  // not exist yet when a take-over asks for it), and without the token every
  // later reload would reopen the same form.
  useEffect(() => {
    if (!focus || !data || handledFocus.current === focus.at) return
    const file = (data.files || []).find((f) => f.id === focus.id)
    if (!file) return
    handledFocus.current = focus.at
    if (isAdmin) {
      edit(file)
      return
    }
    // The row can mount after this render: wait for it a few frames rather
    // than scrolling to where it isn't yet.
    const targetId = `manual-file-row-${focus.id}`
    let frame = 0
    let raf = 0
    let flash = 0
    const reveal = () => {
      const el = document.getElementById(targetId)
      if (!el && frame++ < 30) {
        raf = requestAnimationFrame(reveal)
        return
      }
      const target = el || document.getElementById('manual-files')
      if (!target) return
      target.scrollIntoView({ behavior: 'smooth', block: 'start' })
      target.classList.add('hash-flash')
      flash = setTimeout(() => target.classList.remove('hash-flash'), 1600)
    }
    raf = requestAnimationFrame(reveal)
    return () => {
      cancelAnimationFrame(raf)
      clearTimeout(flash)
    }
  }, [focus, data, edit, isAdmin])

  // Status comes from the report (read off the mission), not from the stored
  // record — a file is "active" when its block is really in the mission file.
  const reportRow = (id) => (reportFiles || []).find((row) => row.id === id)

  // Classifier for the form's paste box. Failures are silent: a proposal that
  // can't be fetched is a missing nicety, not an error worth a toast.
  const suggest = useCallback(async (content) => {
    try {
      return await api.post('/api/modxml/files/classify', { content })
    } catch {
      return null
    }
  }, [])

  // Settles when the last apply job ends. A Form tab save waits for it: the
  // apply is an exclusive job, and a manual file write beside it is refused
  // (409), so a second row saved in quick succession failed.
  const applyDone = useRef(Promise.resolve())
  // At most a minute, so a missed job status never blocks the next save.
  const waitForApply = () =>
    Promise.race([applyDone.current, new Promise((resolve) => setTimeout(resolve, 60000))])

  // Every write is followed by this, so the mission matches the table. A
  // conflict (another merge running) leaves the saved file unapplied — say so,
  // and name the button that fixes it.
  async function applyNow(what) {
    let finish
    applyDone.current = new Promise((resolve) => { finish = resolve })
    try {
      await startJob({
        endpoint: '/api/modxml/files/apply',
        name: 'Apply manual XML files',
        kind: 'mods_xml_merge',
        onStatusChange: (status) => {
          if (status === 'success') onChanged?.()
          if (['success', 'failed', 'cancelled'].includes(status)) finish()
        },
      })
    } catch (err) {
      finish()
      pushToast(
        `${what}, but not applied yet (${err.message || 'could not start'}). ` +
          'Use “Re-apply all” at the top once the other job is done.',
        'error'
      )
    }
  }

  async function submit(form) {
    const { mode, kind, target } = parseDest(form.dest)
    const { id, dest: _dest, content, mod, terrain, note } = form
    // A merge posts the file and the generic kind; the server upgrades it to
    // the merge config that owns the file (or keeps it free-form).
    const body =
      mode === 'merge'
        ? { mod, terrain, note, content, kind: data.merge_kind || 'merge', target }
        : { mod, terrain, note, content, kind, target: '' }
    setSaving(true)
    try {
      if (editing?.existing) {
        await api.put(`/api/modxml/files/${encodeURIComponent(id)}`, body)
      } else {
        await api.post('/api/modxml/files', { ...body, id })
      }
    } catch (err) {
      pushToast(err.message || 'Save failed', 'error')
      setSaving(false)
      return
    }
    setSaving(false)
    setEditing(null)
    await load()
    onChanged?.()
    applyNow(`Saved "${id}"`)
  }

  async function toggle(file, on) {
    try {
      await api.put(`/api/modxml/files/${encodeURIComponent(file.id)}`, { enabled: on })
    } catch (err) {
      pushToast(err.message || 'Update failed', 'error')
      return
    }
    await load()
    onChanged?.()
    applyNow(`"${file.id}" turned ${on ? 'on' : 'off'}`)
  }

  async function remove(file) {
    const ok = await confirm({
      title: `Delete "${file.id}"?`,
      message: 'The file is deleted and what it added is taken out of the mission.',
      confirmLabel: 'Delete',
      danger: true,
    })
    if (!ok) return
    try {
      await api.del(`/api/modxml/files/${encodeURIComponent(file.id)}`)
      if (editing?.form.id === file.id) setEditing(null)
      await load()
      onChanged?.()
    } catch (err) {
      pushToast(err.message || 'Delete failed', 'error')
    }
  }

  // An existing file whose kind has an editor opens on the Form tab.
  const hasForm = Boolean(editing?.existing && (data?.form_kinds || []).includes(editing.form.kind))

  // The Form tab wrote the file: apply it, and show the new XML on the XML tab.
  async function formSaved() {
    const id = editingRef.current?.form.id
    const fresh = await load()
    const file = (fresh?.files || []).find((f) => f.id === id)
    if (file) {
      setEditing((current) =>
        current?.form.id === id ? { ...current, form: { ...current.form, content: file.content } } : current
      )
      setXmlRev((n) => n + 1)
    }
    onChanged?.()
    applyNow(`Saved "${id}"`)
  }

  if (!data) return null
  const files = data.files || []

  return (
    <>
      <div className="card" id="manual-files">
        <div className="card-header">
          <h2>Manual XML files</h2>
          <span className="text-muted">
            {files.length} file{files.length === 1 ? '' : 's'}
          </span>
          {isAdmin && (
            <div className="action-bar">
              <button
                className="btn btn-primary"
                type="button"
                onClick={() => open({ existing: false, form: { ...EMPTY } })}
              >
                <HiOutlinePlus aria-hidden="true" />
                Add XML
              </button>
            </div>
          )}
        </div>
        <PanelNote>
          Your own XML. It is kept outside the mission and put back after every mod on every update,
          so it wins when a mod defines the same thing — and it survives a map switch or a reinstall.
        </PanelNote>

        {files.length > 0 && (
          <div className="data-table-wrap table-cards">
            <table ref={tableRef} className="data-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Goes into</th>
                  <th>Map</th>
                  <th>For mod</th>
                  <th>Status</th>
                  {isAdmin && <th>Enabled</th>}
                  {isAdmin && <th />}
                </tr>
              </thead>
              <tbody>
                {files.map((file) => {
                  const row = reportRow(file.id)
                  // A disabled manual file is the user's own switch, not a mod's.
                  const status = row?.status === 'disabled' ? 'excluded' : row?.status
                  const info = XML_STATUS[status]
                  const target = file.target || kindTargets[file.kind] || ''
                  const slug = MISSION_FILE_SLUGS[target]
                  const targetTo = configLink(slug)
                  // The generated custom/ copy is rebuilt from this file on every
                  // merge, so an admin edits this file. Anyone else may look.
                  const openCustom = isAdmin
                    ? () => edit(file)
                    : row?.status === 'linked' && onOpenCustom
                      ? () => onOpenCustom(row.custom_file)
                      : null
                  return (
                    <tr
                      key={file.id}
                      id={`manual-file-row-${file.id}`}
                      className={
                        [
                          !file.enabled && 'modxml-row-off',
                          // The report's mod_name is resolved, so a file bound by
                          // workshop id matches too.
                          sameMod(row?.mod_name || file.mod, markMod) && 'row-active',
                        ]
                          .filter(Boolean)
                          .join(' ') || undefined
                      }
                    >
                      <td className="cell-name" {...tooltipAttrs(file.note)}>{file.id}</td>
                      <td className="modxml-into" data-label="Goes into">
                        <HowBadge how={ceKinds.includes(file.kind) ? 'linked' : 'merged'} />
                        {/* Name the file the user actually loads, not the
                            registry it is declared in. */}
                        {row?.custom_file ? (
                          openCustom ? (
                            <button
                              className="btn-link"
                              type="button"
                              onClick={openCustom}
                              {...tooltipAttrs(
                                isAdmin
                                  ? 'Edit the manual XML file this is made from'
                                  : 'View the file the mission loads'
                              )}
                            >
                              <FileIcon action={isAdmin ? 'edit' : undefined} />
                              custom/{row.custom_file}
                            </button>
                          ) : (
                            <>custom/{row.custom_file}</>
                          )
                        ) : targetTo && onOpenEditor ? (
                          <button
                            className="btn-link"
                            type="button"
                            onClick={() => openEditor(slug, file.id)}
                            {...tooltipAttrs(`Edit ${target}`)}
                          >
                            <FileIcon action="edit" />
                            {target}
                          </button>
                        ) : targetTo ? (
                          <Link to={targetTo} {...tooltipAttrs(`Edit ${target}`)}>
                            <FileIcon action="edit" />
                            {target}
                          </Link>
                        ) : (
                          target || '—'
                        )}
                      </td>
                      <td data-label="Map">{terrainLabel(file.terrain, data.terrain_names) || 'Every map'}</td>
                      <td className="cell-nowrap" data-label="For mod">{file.mod || '—'}</td>
                      <td data-label="Status">
                        <span className="modxml-status">
                          {info && (
                            <span className={pillClass(info.tone)} {...tooltipAttrs(info.tip)}>
                              {info.label}
                            </span>
                          )}
                          {row?.orphaned && (
                            <span
                              className={pillClass('warn')}
                              {...tooltipAttrs(
                                'That mod isn’t installed. The file is still applied, but the ' +
                                  'server may log errors for the classes it names.'
                              )}
                            >
                              Mod not installed
                            </span>
                          )}
                          {file.upstream === 'changed' &&
                            (onViewUpstream ? (
                              <button
                                className="status-pill status-pill-running"
                                type="button"
                                onClick={() => onViewUpstream(file.id)}
                                {...tooltipAttrs(UPSTREAM_TIP)}
                              >
                                Mod updated this file
                              </button>
                            ) : (
                              <span
                                className="status-pill status-pill-running"
                                {...tooltipAttrs(UPSTREAM_TIP)}
                              >
                                Mod updated this file
                              </span>
                            ))}
                        </span>
                      </td>
                      {isAdmin && (
                        <td data-label="Enabled">
                          <Switch
                            label={`${file.id} enabled`}
                            checked={file.enabled}
                            onChange={(on) => toggle(file, on)}
                          />
                        </td>
                      )}
                      {isAdmin && (
                        <td className="manual-file-actions cell-actions">
                          <button
                            className="btn btn-ghost btn-small"
                            type="button"
                            onClick={() => edit(file)}
                          >
                            <FileIcon action="edit" />
                            Edit
                          </button>
                          <button
                            className="btn btn-ghost btn-small"
                            type="button"
                            onClick={() => remove(file)}
                          >
                            Delete
                          </button>
                        </td>
                      )}
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
      {/* Outside the card: the drawer's layout rules skip anything inside one. */}
      {editing && (
        <SideDrawer
          title={editing.existing ? editing.form.id : 'New manual XML file'}
          badge="manual XML"
          subtitle={editing.existing ? editing.form.note : 'Your own XML, applied after every mod.'}
          storageKey={drawerKey}
          className="side-drawer-editor"
          maximizable
          onClose={cancelForm}
        >
          {hasForm && <Tabs tabs={DRAWER_TABS} active={drawerTab} onSelect={setDrawerTab} />}
          {hasForm && (
            <div className="manual-file-kind-form" hidden={drawerTab !== 'form'}>
              <KindForm
                key={editing.form.id}
                fileId={editing.form.id}
                kind={editing.form.kind}
                beforeSave={async () => { await waitForApply(); return confirmDiscard(FORM_NAME) }}
                onSaved={formSaved}
              />
            </div>
          )}
          <FileForm
            // A new key per file, so switching from one file's form to another's
            // starts from that file rather than keeping the last one's edits.
            key={`${formKey(editing)}:${xmlRev}`}
            ceKinds={ceKinds}
            terrains={data.terrains || []}
            terrainNames={data.terrain_names || {}}
            targets={data.targets || []}
            kindTargets={kindTargets}
            mods={data.mods || []}
            initial={editing.form}
            editing={editing.existing}
            saving={saving}
            onSuggest={suggest}
            onCancel={cancelForm}
            onSubmit={submit}
            hidden={hasForm && drawerTab !== 'xml'}
          />
        </SideDrawer>
      )}
    </>
  )
}
