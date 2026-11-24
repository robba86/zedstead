import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { useSearchPrefill } from '../useSearchPrefill.js'
import {
  HiOutlineDocumentPlus,
  HiOutlineLink,
  HiOutlineMapPin,
} from 'react-icons/hi2'
import RestoreIcon from './RestoreIcon.jsx'
import { api } from '../api'
import { useToast } from './Toast.jsx'
import FormEditor from './FormEditor.jsx'
import TableEditor from './TableEditor.jsx'
import TextEditor from './TextEditor.jsx'
import RawEditor from './RawEditor.jsx'
import EditorVersions from './EditorVersions.jsx'
import Tabs from './Tabs.jsx'
import RunCommandCard from './RunCommandCard.jsx'
import WeatherPresets from './WeatherPresets.jsx'
import DynamicAiCard from './DynamicAiCard.jsx'
import ExpansionAiCard, { EXPANSION_AI_EDITORS } from './ExpansionAiCard.jsx'
import RadZoneCard from './RadZoneCard.jsx'
import ToxicZoneCard from './ToxicZoneCard.jsx'
import EditorDrawer, { fileTabs } from './EditorDrawer.jsx'
import Tooltip, { tooltipAttrs } from './Tooltip.jsx'
import { useConfirm } from './useConfirm.jsx'
import { discardUnsavedChanges } from '../useUnsavedChanges.js'
import { EDITORS } from '../pageHelp.js'
import {
  EDITOR_LINKS,
  EDITOR_READONLY_LINKS,
  LINKS,
  MAP_EDITOR_LINKS,
  editorLink,
  editorSiblings,
  mapEventLink,
} from '../crossLinks.js'
import Segmented from './Segmented.jsx'
import INTERACTIVE_VIEWS from '../interactiveViews.jsx'
import { useUrlTabs } from '../urlTabs.js'
import { useCan } from '../role.jsx'
import { EditorSyncContext, useConfigSync } from '../configSync.js'

// Config editor shell, in the Configs page's right-hand drawer (plans/79; it
// was its own page, EditorPage, until 2026 Q4). The page renders it on
// /configs/:id, so that address and every deep link into it are unchanged;
// closing the drawer is the page navigating back to /configs.
//
// Two modes per file, shown as tabs:
//   Edit — the bespoke view from INTERACTIVE_VIEWS where the file has one,
//          else the generic form/table renderer (GET/PUT /api/configs/{id}).
//   Raw  — direct file-text editing (RawEditor, its own endpoints).
//   Versions — the file's saved versions: diff and restore (EditorVersions).
// There were three until 2026 Q4: "Interactive" and "Basic" edited the
// same file twice. Every editor now has one Edit view, and what Basic did
// better was moved into it (the TraderPlus forms and weather went back to the
// generic form, see interactiveViews.jsx). Tab availability comes from /meta
// (cheap, so Raw stays reachable even when the structured load fails on a
// broken file). Default tab is the first one available — Edit, else Raw.
//
// URL params, all produced by crossLinks.js::editorLink/configLink:
//   ?tab=edit|raw|versions       which tab is open. Two-way, through the SAME
//                                useUrlTabs the other pages' tab bars use —
//                                clicking a tab rewrites it, so the URL is the
//                                address of what is on screen and an editor tab
//                                can be linked to or reloaded onto. `?view=`
//                                was this page's own spelling of it before
//                                `?tab=` was app-wide; it is still read (as
//                                useUrlTabs' `legacyKey`) and drops out of the
//                                URL on the first tab click. An old
//                                ?tab=basic or ?tab=interactive is a tab no
//                                file has, so it opens the first one, Edit.
//   ?group=<value>               select a TableEditor filter_key group. Also
//                                two-way — see the note on setGroup below.
//   ?q=<text>                    prefill the filter box (app-wide search key).
//                                Read-only: it seeds a box the user then owns.
// Props: `id` the editor id, `title` the listing's title (shown until /meta
// answers), `storageKey` the drawer's width key, `onClose` the page's navigate.
export default function ConfigEditorDrawer({ id, title, storageKey, onClose }) {
  const [params, setParams] = useSearchParams()
  const groupParam = params.get('group') || ''
  // The table's group on screen (TableEditor onGroupShown), which can be the
  // picker's default rather than ?group=.
  const [shownGroup, setShownGroup] = useState('')
  const searchParam = params.get('q') || ''
  // ?owner= shows only one START MOD block owner's rows (a manual XML file's
  // "Goes into" link, crossLinks.js withParams).
  const ownerParam = params.get('owner') || ''
  const pushToast = useToast()
  const isAdmin = useCan('admin')
  const confirm = useConfirm()
  const navigate = useNavigate()

  // The table's group picker is two-way: a deep link (Mod XML, the map editor's
  // per-event ↗) selects a group, and picking one by hand rewrites ?group= so
  // the URL stays the address of what is on screen. replace:true — a picker is
  // not a navigation step. TableEditor guards the loop: it only adopts a
  // groupParam it did not itself emit.
  const setGroup = useCallback(
    (value) => {
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev)
          if (value) next.set('group', value)
          else next.delete('group')
          return next
        },
        { replace: true }
      )
    },
    [setParams]
  )

  const [meta, setMeta] = useState(null) // { title, kind, raw, targets }

  // The generic Edit view's state (the payload is named `basic` from when its
  // tab was called Basic).
  const [basicData, setBasicData] = useState(null)
  const [basicError, setBasicError] = useState(null)
  // Reported up by whichever of Form/Table/TextEditor is mounted — the payload
  // is this shell's, the unsaved-edit answer is only theirs.
  const [basicDirty, setBasicDirty] = useState(false)
  // FormEditor/TableEditor read ?q= once, on mount. A new ?q= while this
  // editor is open (the Ctrl+K palette naming another field) remounts them
  // through their key, so the new filter applies. Not over unsaved edits: a
  // remount would drop them, and a ?q= change asks nothing first.
  const [searchSeed, setSearchSeed] = useState(0)
  const basicDirtyRef = useRef(basicDirty)
  basicDirtyRef.current = basicDirty
  const searchIdRef = useRef(id)
  useSearchPrefill(
    useCallback(() => {
      // Another editor: its key changes anyway, and the dirty flag is still
      // the file just left.
      if (searchIdRef.current !== id) {
        searchIdRef.current = id
        return
      }
      if (basicDirtyRef.current) {
        pushToast('Save or discard your changes first, then search again.', 'info')
        return
      }
      setSearchSeed((n) => n + 1)
    }, [id, pushToast])
  )
  const [saving, setSaving] = useState(false)
  const [capturing, setCapturing] = useState(false)
  // The manual XML file this page just created, so the success banner can link
  // straight to its row instead of leaving the user to find it.
  const [captured, setCaptured] = useState(null)

  const InteractiveView = INTERACTIVE_VIEWS[id]

  const tabs = useMemo(() => {
    if (!meta) return []
    const t = []
    // A text-kind editor (app/web/editors/text_fallback.py) has no structured
    // renderer, so its Edit view falls through to a plain code area — the same
    // file text Raw shows, minus XmlTools' formatter and its persistent error
    // line, and saving through the same save_raw(). Two tabs that differ only by
    // one being worse is not a choice, so Edit is dropped where Raw covers it.
    // Kept when there is no raw target (no mission folder yet), or the page
    // would offer no tab at all.
    //
    // Raw is the file itself, passwords included — GET .../raw is admin-only
    // (app/web/permissions.py), so below admin there is no Raw tab and a
    // text-kind file keeps its Edit view instead.
    const raw = meta.raw && isAdmin
    if (InteractiveView || meta.kind !== 'text' || !raw) t.push({ id: 'edit', label: 'Edit' })
    t.push(...fileTabs(meta, isAdmin))
    return t
  }, [meta, InteractiveView, isAdmin])

  // The tab is the URL, not state — the same rule (and the same hook) as every
  // other tab bar in the app. Which tabs exist depends on the file, so the list
  // is this page's memo rather than a module constant; empty until /meta lands,
  // where `tab` is undefined and the bar isn't rendered yet. An id the file has
  // no tab for (a ?tab=edit link to a text-kind editor) falls back to the first
  // available one, exactly as the hand-rolled version did.
  // A turned-off tab is no choice, so a ?tab=raw link to it lands on Edit.
  const tabIds = useMemo(() => tabs.filter((t) => !t.disabled).map((t) => t.id), [tabs])
  const { tab, setTab } = useUrlTabs(tabIds, null, { legacyKey: 'view' })

  const loadMeta = useCallback(async () => {
    try {
      setMeta(await api.get(`/api/configs/${id}/meta`))
    } catch (err) {
      pushToast(err.message || 'Failed to load config', 'error')
    }
  }, [id, pushToast])

  const basicPath = `/api/configs/${id}`

  // Split out so a background refresh can swap the payload in without the blank
  // frame `loadBasic`'s reset gives a fresh open. It clears the error too: a
  // file that failed to parse when the page opened and has since been fixed
  // (from Raw, or by a merge) should come back on its own.
  const applyBasic = useCallback((d) => {
    setBasicError(null)
    setBasicData(d)
  }, [])

  // Re-read without the blank frame. The re-fetch after Save uses it: a blank
  // unmounts the editor, which dropped the table's search, page and open rows.
  // An answer for the file just left is dropped: the shell is not keyed by id,
  // and a table Save would have sent that file's rows to this one.
  const basicPathRef = useRef(basicPath)
  basicPathRef.current = basicPath
  const refreshBasic = useCallback(async () => {
    try {
      const d = await api.get(basicPath)
      if (basicPathRef.current === basicPath) applyBasic(d)
    } catch (err) {
      if (basicPathRef.current === basicPath) setBasicError(err.message || 'Failed to load')
    }
  }, [applyBasic, basicPath])

  const loadBasic = useCallback(async () => {
    setBasicData(null)
    setBasicError(null)
    await refreshBasic()
  }, [refreshBasic])

  useEffect(() => {
    setMeta(null)
    setCaptured(null) // it names this file's manual copy; another file's is not it
    loadMeta()
  }, [loadMeta])

  // The generic view re-reads the file every time its tab becomes the active one, not once
  // when the page opens (2026 Q3). Interactive and Raw own their own load and
  // are unmounted while another tab is up, so they come back fresh; its data
  // lives in this shell and survives the switch, so a load-on-open kept showing
  // what the file held before an Interactive or Raw save wrote to it — the
  // values only appeared after a full page reload. The reverse direction already
  // worked, which is what made it look like this view alone was broken.
  //
  // Nothing unsaved is lost: Form/Table/TextEditor unmount with the tab, so
  // their edits are already gone by the time this refires. `loadBasic` is keyed
  // on `id`, so a cross-link to another editor while it is up reloads too.
  // The generic view is on screen: the Edit tab of a file with no bespoke view.
  const generic = tab === 'edit' && !InteractiveView

  useEffect(() => {
    if (generic) loadBasic()
  }, [generic, loadBasic])

  // …and re-checks it on `configs_changed` for as long as it is the tab on
  // screen, so a file rewritten by a job (or from another browser) doesn't sit
  // there stale. Parked on the other tabs: their views run their own sync, and
  // `basicData` is nobody's answer while it is off screen — the effect above
  // reloads it on the way back in.
  const { stale, onReload } = useConfigSync({
    path: basicPath,
    showing: basicData,
    dirty: basicDirty,
    busy: saving || (basicData === null && !basicError),
    apply: applyBasic,
    enabled: generic,
  })

  // Memoised on the two values, not on the hook's return object — that one is a
  // fresh literal every render, and a new context value re-renders the editor
  // under it (a TableEditor mid-edit is not worth re-rendering per keystroke).
  const syncValue = useMemo(
    () => ({ stale, onReload, reportDirty: setBasicDirty }),
    [stale, onReload]
  )

  async function handleSave(payload) {
    setSaving(true)
    try {
      const body =
        basicData.kind === 'form'
          ? { values: payload }
          : basicData.kind === 'table'
            ? { rows: payload }
            : { content: payload }
      await api.put(`/api/configs/${id}`, body)
      pushToast('Saved.', 'info')
      refreshBasic()
    } catch (err) {
      // A refusal whose change belongs on another page names it (`go`, a
      // LINKS key): ask whether to go there instead of only toasting.
      const there = err.body?.go && LINKS[err.body.go]
      if (there) {
        const go = await confirm({
          title: `Edit it in ${there.label} instead?`,
          message: `${err.message} Changes not saved in this table are not kept.`,
          confirmLabel: `Open ${there.label}`,
          cancelLabel: 'Stay here',
        })
        if (go) {
          // The dialog already said the unsaved edits go; don't ask again.
          discardUnsavedChanges()
          navigate(there.to)
        }
      } else {
        pushToast(err.message || 'Save failed', 'error')
      }
    } finally {
      setSaving(false)
    }
  }

  // Offered only where the server says a merge config owns this file
  // (`meta.capture_target`, from kind_for_target) — never a hardcoded list here,
  // or the page and the merge could disagree about which files a mod writes to.
  //
  // It is a MOVE, and since 2026 Q3 it keeps nothing that wasn't already
  // kept: a merge leaves this file alone. What it buys is the two things the
  // mission file can't give, which is what the dialog has to explain or the
  // button reads as pointless.
  async function handleCapture() {
    const target = meta.capture_target
    const ok = await confirm({
      title: `Move your entries out of ${target}?`,
      message:
        'Your own entries move into a manual XML file, kept outside the mission and ' +
        're-applied here on every merge. Entries that came from a mod stay exactly where ' +
        'they are. Two things change: your entries survive a map switch or a server ' +
        'reinstall, and they are applied after every mod, so they win when a mod defines ' +
        'the same name.',
      confirmLabel: 'Move them',
    })
    if (!ok) return
    setCapturing(true)
    try {
      const link = await api.post('/api/modxml/files/from-file', { target })
      pushToast(`Moved into manual XML file "${link.id}".`, 'info')
      setCaptured(link.id)
      loadBasic()
    } catch (err) {
      pushToast(err.message || 'Nothing to move', 'error')
    } finally {
      setCapturing(false)
    }
  }

  // Confirmed since 2026 Q3: a merge no longer restores these files, so an
  // admin's edits accumulate in the live file and this button is the one click
  // that throws all of them away. A version is taken first, so it is undoable
  // from this editor's Versions tab — say so rather than making it feel final.
  async function handleRestore() {
    const ok = await confirm({
      title: `Restore ${meta?.title || id} from backup?`,
      message:
        // On a stock mission the server puts back the installed DayZ version's
        // file, and the .backup only on a community mission (F10, 2026-11-17).
        'On a stock mission this puts back the file of the installed DayZ version. On a ' +
        'community mission it puts back the copy Zedstead made before its first merge into the ' +
        "file. Your own edits and every installed mod's merged blocks go. A version is " +
        'saved first, so you can get it back from the Versions tab of this editor, and the ' +
        'next merge re-applies the mods.',
      confirmLabel: 'Restore',
      danger: true,
    })
    if (!ok) return
    try {
      const result = await api.post(`/api/configs/${id}/restore`)
      if (result.restored) {
        pushToast('Restored from backup.', 'info')
        loadMeta()
        loadBasic()
      } else {
        pushToast('Nothing to restore.', 'info')
      }
    } catch (err) {
      pushToast(err.message || 'Restore failed', 'error')
    }
  }

  const drawerProps = { storageKey, onClose }
  if (!meta) return <EditorDrawer {...drawerProps} title={title || id} />


  // "View on map" for the files the map editor also edits. When this page was
  // itself reached from the editor (?group=/?q= naming one event), the link goes
  // back to that event rather than the whole layer — a clean round trip without
  // lifting the table's filter state up here.
  const eventName = id === 'eventspawns' ? shownGroup || groupParam : id === 'events' ? searchParam : ''
  const mapLink = eventName ? mapEventLink(eventName) : MAP_EDITOR_LINKS[id]

  // Raw is the only editor for this file (text kind — no structured renderer).
  // Where the Visual Map Editor owns the same file it IS the field-by-field
  // view, so say so: a lone Raw tab otherwise reads as "zsm can't really edit
  // this", and the header's "View on map" button doesn't announce itself as the
  // better way in.
  const rawOnly = tabs.length === 1 && tabs[0].id === 'raw'

  // The views that own part of this file (crossLinks.js) — for cfgeconomycore
  // that's the mod XML report and the manual XML files that write its <ce>
  // entries, which the table below deliberately can't edit.
  const relatedLinks = (EDITOR_LINKS[id] || []).map((slug) => LINKS[slug]).filter(Boolean)
  const readonlyLink = LINKS[EDITOR_READONLY_LINKS[id]] || null
  const siblings = editorSiblings(id)

  // "Restore a version", as an icon in the drawer's header: it opens the
  // Versions tab. Not for a file with a merge backup, which has "Restore from
  // backup" in the bar below. Disabled only with no file at all yet, or on
  // the Versions tab itself.
  const restoreVersion = isAdmin && !meta.restore && (
    <Tooltip
      text={
        meta.raw
          ? 'Restore a version: every save keeps the file as it was. Pick a saved version to compare or restore.'
          : 'There is no file to restore yet.'
      }
      className="bare"
    >
      <button
        type="button"
        className="btn btn-icon"
        aria-label="Restore a version"
        onClick={() => setTab('versions')}
        disabled={!meta.raw || tab === 'versions'}
      >
        <RestoreIcon />
      </button>
    </Tooltip>
  )

  return (
    <EditorDrawer
      {...drawerProps}
      title={meta.title || title || id}
      actions={restoreVersion}
      help={EDITORS[id]}
      helpPage="/configs/:id"
    >
      {/* Editors shown as one (crossLinks.js editorSiblings): a switch to
          the other. It is a navigation, so unsaved edits get the usual question. */}
      {siblings && (
        <div className="editor-siblings">
          <Segmented
            label="Editor"
            value={id}
            onChange={(next) => next !== id && navigate(editorLink(next))}
            options={siblings.map((e) => ({ value: e.id, label: e.label }))}
          />
        </div>
      )}
      {(relatedLinks.length > 0 || (isAdmin && (mapLink || meta.capture_target || meta.restore))) && (
        <div className="action-bar editor-drawer-actions">
          {relatedLinks.map((entry) => (
            <Link
              key={entry.to}
              className="btn btn-ghost"
              to={entry.to}
              {...tooltipAttrs(entry.tip)}
            >
              <HiOutlineLink aria-hidden="true" />
              {entry.label}
            </Link>
          ))}
          {mapLink && isAdmin && (
            <Link className="btn btn-ghost" to={mapLink}>
              <HiOutlineMapPin aria-hidden="true" />
              {eventName ? `View ${eventName} on map` : 'View on map'}
            </Link>
          )}
          {meta.capture_target && isAdmin && (
            <button className="btn btn-ghost" onClick={handleCapture} disabled={capturing}>
              <HiOutlineDocumentPlus aria-hidden="true" />
              {capturing ? 'Saving…' : 'Save as manual XML file'}
            </button>
          )}
          {/* A file a mod merges into has a merge backup to restore. Every
              other file still has its saved versions (every editor save keeps
              one), so there the drawer's header has `restoreVersion`, which
              opens the Versions tab (user test, 2026 Q4: a removed event
              looked gone for good). */}
          {isAdmin && meta.restore && (
            <button
              className="btn btn-ghost"
              onClick={handleRestore}
              {...tooltipAttrs('Put the file back the way it was before Zedstead first merged mod XML into it.')}
            >
              <RestoreIcon />
              Restore from backup
            </button>
          )}
        </div>
      )}

      {!isAdmin && (
        <p className="banner banner-info">
          Read-only — changing config files needs the admin role. Passwords are
          hidden for the same reason.
        </p>
      )}

      {captured && (
        <p className="banner banner-info">
          Your entries now live in manual XML file <strong>{captured}</strong>, applied to{' '}
          {meta.capture_target} after every mod.{' '}
          <Link to={LINKS['manual-xml-links'].to}>Edit it under Custom config →</Link>
        </p>
      )}

      {id === 'runcommand' && <RunCommandCard hideSelf />}
      {id.startsWith('dynai-') && <DynamicAiCard />}
      {EXPANSION_AI_EDITORS.has(id) && <ExpansionAiCard />}
      {id.startsWith('radzone-') && <RadZoneCard />}
      {id.startsWith('toxiczone-') && <ToxicZoneCard />}

      {rawOnly && mapLink && (
        <p className="banner banner-info">
          There is no field-by-field view here — this file nests its sections too deeply for a
          form or table. The <Link to={mapLink}>Map Editor</Link> is the guided way to
          edit it: every setting and position, on the map. The text below is the same file, for
          hand edits.
        </p>
      )}

      <Tabs tabs={tabs} active={tab} onSelect={setTab} hideSingle />

      {tab === 'edit' && InteractiveView && (
        <InteractiveView key={id} id={id} initialSearch={searchParam} />
      )}

      {generic && id === 'weather' && <WeatherPresets onApplied={loadBasic} />}

      {generic && (
        // The provider is the Basic half of configSync.js: the editor inside
        // reports its dirty flag up and reads {stale, onReload} back out.
        <EditorSyncContext.Provider value={syncValue}>
          {basicError ? (
            <p className="banner banner-warning">
              {basicError} — use the Raw tab to inspect or fix the file.
            </p>
          ) : basicData ? (
            basicData.kind === 'form' ? (
              // key={id} — this shell stays mounted across /configs/:id changes,
              // so without it the editor keeps the previous file's "already
              // applied the deep-link params" state and ignores the new link's
              // ?group=/?q=. searchSeed does the same for a new ?q= on the same
              // file (useSearchPrefill above).
              <FormEditor
                key={`${id}:${searchSeed}`}
                data={basicData}
                onSave={handleSave}
                saving={saving}
                editorId={id}
                initialSearch={searchParam}
              />
            ) : basicData.kind === 'table' ? (
              <TableEditor
                key={`${id}:${searchSeed}:${ownerParam}`}
                data={basicData}
                onSave={handleSave}
                saving={saving}
                group={groupParam}
                owner={ownerParam}
                onGroupChange={setGroup}
                onGroupShown={setShownGroup}
                initialSearch={searchParam}
                readonlyLink={readonlyLink}
                editorId={id}
              />
            ) : (
              <TextEditor data={basicData} onSave={handleSave} saving={saving} />
            )
          ) : (
            <div />
          )}
        </EditorSyncContext.Provider>
      )}

      {tab === 'raw' && <RawEditor id={id} targets={meta.targets} />}

      {tab === 'versions' && <EditorVersions key={id} id={id} targets={meta.targets} />}
    </EditorDrawer>
  )
}
