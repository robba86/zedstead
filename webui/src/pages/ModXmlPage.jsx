// The Mod XML page: which mod XML files reach the mission, how each one is
// merged, and the deleted and manual files.

import { Switch } from '../components/FlagSwitch.jsx'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { HiOutlineDocumentText, HiOutlineRocketLaunch } from 'react-icons/hi2'
import { api } from '../api'
import CodeArea from '../components/CodeArea.jsx'
import Tabs from '../components/Tabs.jsx'
import FileIcon from '../components/FileIcon.jsx'
import FilterBox from '../components/FilterBox.jsx'
import PageIntro from '../components/PageIntro.jsx'
import DiffView from '../components/DiffView.jsx'
import SideDrawer from '../components/SideDrawer.jsx'
import TableEditor from '../components/TableEditor.jsx'
import CountBadge from '../components/CountBadge.jsx'
import FoldCaret from '../components/FoldCaret.jsx'
import { tooltipAttrs } from '../components/Tooltip.jsx'
import ManualXmlFiles, { FORM_NAME as MANUAL_FORM, HowBadge, UPSTREAM_TIP } from './ManualXmlFiles.jsx'
import {
  MISSION_FILE_SLUGS,
  MODXML_KIND_SLUGS,
  configLink,
  editorLink,
  manualMarker,
  modLink,
} from '../crossLinks.js'
import { CONFIGS_MOD_XML_TAB } from '../pageTabs.js'
import { useConfirmDiscard } from '../useUnsavedChanges.js'
import { useHashTarget } from '../hashTarget.js'
import { useJobs } from '../jobs.jsx'
import { useToast } from '../components/Toast.jsx'
import { useConfirm } from '../components/useConfirm.jsx'
// Human wording + severity for each status the backend reports
// (app/xml/xml_report.py) — shared with the per-mod page's file browser.
import { XML_HOW, XML_KIND_LABEL, XML_STATUS, pillClass, sameMod } from '../modXmlStatus.js'
import { useCan } from '../role.jsx'
import RefreshButton from '../components/RefreshButton.jsx'
import PanelNote from '../components/PanelNote.jsx'

// Mission files a mod fragment can be merged into -> their editor route, so a
// row is one click from the file it landed in. Both maps live in crossLinks.js
// — this page owns no routes of its own.
const targetRoute = (target) => configLink(MISSION_FILE_SLUGS[target])
const kindRoute = (kind) => configLink(MODXML_KIND_SLUGS[kind])

const DRAWER_WIDTH_KEY = 'zsm.modxml.drawer.width'

const TAKE_OVER_TIP =
  'Make your own copy of this file that you can edit. Your copy is used instead of ' +
  'the mod’s, straight away and on every update after that.'

// The trade, stated once. Taking a file over is cheap to do and awkward to
// notice later, so the dialog spells out the one thing that actually changes:
// this file stops tracking the mod. Shared by the row and the drawer — two
// copies of this wording would drift.
const TAKE_OVER_CONFIRM = {
  title: 'Take this file over?',
  message:
    'You get your own copy of this file, and the mission uses it instead of the mod’s. ' +
    'If the mod later changes this file, the change won’t reach your server — Zedstead ' +
    'tells you when that happens. The rest of the mod keeps updating normally.',
  confirmLabel: 'Take over',
}

const REAPPLY_CONFIRM = {
  title: 'Re-apply all mod XML?',
  message:
    'Puts the XML of every enabled mod, and your manual XML files, into the mission ' +
    'again. The custom/ folder is rebuilt. Each mod’s and manual file’s block in the ' +
    'mission files is replaced, so edits made inside one (a spawn moved on the map, a ' +
    'value changed in an editor) are lost. Your edits outside those blocks, and the ' +
    'files you turned off or deleted here, are kept.',
  confirmLabel: 'Re-apply all',
}

// '' | 'excluded' | 'deleted' — one field, because the merge treats the last two
// identically and only the page tells them apart. Takes every record that can
// turn the file off, as the report's status does.
const stateOf = (...files) =>
  files.some((f) => f?.deleted) ? 'deleted' : files.some((f) => f?.excluded) ? 'excluded' : ''

// A custom/ tile with no mod row behind it (an orphan, or a record left by a
// file the user turned off) still needs a status of its own.
function tileStatus(tile) {
  if (tile.deleted) return 'deleted'
  if (tile.excluded) return 'excluded'
  if (tile.disabled) return 'disabled'
  return tile.linked ? 'linked' : 'no_target'
}

// One list for every mod file, whichever way zsm applies it. The report keeps
// two shapes — `custom` (central-economy files, keyed by the generated name)
// and `files` (every mod XML) — and this is the only place they meet, so the
// page never has to explain the difference.
//
// A custom/ tile keeps its mod row through `custom_name`, which is set whatever
// the status — joining on `custom_file` (linked-only) is what once made a
// disabled file surface twice. Files generated FROM a manual XML file are
// left out: they are listed with the manual files, where they are edited.
function buildModRows(report) {
  const sourceFor = new Map(
    report.files.filter((f) => f.custom_name).map((f) => [f.custom_name, f])
  )
  // A manual file is paired with the file it generates AND the original it forked.
  const linkFor = new Map()
  for (const l of report.manual || []) {
    if (l.custom_file) linkFor.set(l.custom_file, l)
    if (l.forked_from && !linkFor.has(l.forked_from)) linkFor.set(l.forked_from, l)
  }

  const rows = []
  for (const c of report.custom) {
    const link = linkFor.get(c.name)
    if (link && link.custom_file === c.name) continue
    const enc = encodeURIComponent(c.name)
    const src = sourceFor.get(c.name)
    // A central-economy file is off by its tile's record (this page's switch)
    // or by its source's: Adopt's "Keep mine" writes only the source one
    // (`set_source_state`). On clears both, as the mod's Restore does.
    const sourceOff = Boolean(src?.excluded)
    const setOn = async () => {
      if (sourceOff) await api.put('/api/modxml/source/state', { path: src.source, state: '' })
      if (c.excluded || !sourceOff) return api.put(`/api/modxml/custom/${enc}/state`, { state: '' })
    }
    rows.push({
      key: `custom:${c.name}`,
      name: c.source ? c.source.split('/').pop() : c.name,
      mod: c.mod || '',
      kind: c.type || src?.kind || null,
      into: `custom/${c.name}`,
      how: 'linked',
      intoRoute: null,
      status: src?.status || tileStatus(c),
      state: stateOf(c, src),
      source: c.source || null,
      custom: c.missing ? null : c.name,
      link: link || null,
      // A forked original keeps its own on/off and delete: switching it back on
      // is how a take-over is undone.
      keepsSwitch: true,
      // Nothing to copy only when the generated file is gone AND no mod file
      // produces it (an orphan left behind by an uninstalled mod).
      canTakeOver: !c.missing || Boolean(c.source),
      label: `custom/${c.name}`,
      ops: {
        setState: (state) => (state ? api.put(`/api/modxml/custom/${enc}/state`, { state }) : setOn()),
        fork: () => api.post(`/api/modxml/custom/${enc}/fork`, { source: c.source || '' }),
        remove: () => api.del(`/api/modxml/custom/${enc}`),
      },
    })
  }
  for (const f of report.files) {
    if (f.group !== 'merged') continue
    const link = linkFor.get(f.source)
    rows.push({
      key: `source:${f.source}`,
      name: f.name,
      mod: f.mod,
      kind: f.kind,
      into: f.target || null,
      how: 'merged',
      intoRoute: f.target ? targetRoute(f.target) : null,
      status: f.status,
      state: stateOf(f),
      source: f.source,
      custom: null,
      link: link || null,
      // Its content lives in the manual file now — the source is already off,
      // and a switch here would put the mod's copy back beside yours.
      keepsSwitch: !link,
      canTakeOver: !f.excluded,
      label: `${f.mod}'s ${f.name}`,
      ops: {
        setState: (state) => api.put('/api/modxml/source/state', { path: f.source, state }),
        fork: () => api.post('/api/modxml/source/fork', { path: f.source }),
        remove: () => api.del(`/api/modxml/source?path=${encodeURIComponent(f.source)}`),
      },
    })
  }
  const low = (s) => (s || '').toLowerCase()
  rows.sort((a, b) => low(a.mod).localeCompare(low(b.mod)) || low(a.name).localeCompare(low(b.name)))
  return rows
}

const statusText = (row) => (row.link ? 'Replaced by your copy' : XML_STATUS[row.status]?.label || row.status)

function StatusPill({ status }) {
  const info = XML_STATUS[status] || { label: status, tone: 'muted' }
  return (
    <span className={pillClass(info.tone)} {...tooltipAttrs(info.tip)}>
      {info.label}
    </span>
  )
}

// The kind in words, linked to the editor that owns that content.
function KindCell({ kind }) {
  if (!kind) return <>—</>
  const label = XML_KIND_LABEL[kind] || kind
  const route = kindRoute(kind)
  if (!route) return <span {...tooltipAttrs(kind)}>{label}</span>
  return (
    <Link to={route} {...tooltipAttrs(`${kind} — open its editor`)}>
      <FileIcon action="edit" />
      {label}
    </Link>
  )
}

// The signal that a taken-over file has drifted from the mod, wherever the pair
// is shown. Same badge as the manual-file table, so "Mod updated this file"
// means one thing on the page.
function UpstreamBadge({ link, onViewUpstream }) {
  if (link?.upstream !== 'changed') return null
  return (
    <button
      className="status-pill status-pill-running"
      type="button"
      onClick={() => onViewUpstream(link.id)}
      {...tooltipAttrs(UPSTREAM_TIP)}
    >
      Mod updated this file
    </button>
  )
}

// busy flag + toast + reload around one write. Shared by the row switch and the
// drawer so the two can't report the same click differently.
function useFileAction(onChanged) {
  const [busy, setBusy] = useState(false)
  const pushToast = useToast()
  const run = useCallback(
    async (fn, done) => {
      setBusy(true)
      try {
        const result = await fn()
        if (done) pushToast(done, 'info')
        onChanged()
        return result
      } catch (err) {
        pushToast(err.message || 'Action failed', 'error')
        return null
      } finally {
        setBusy(false)
      }
    },
    [onChanged, pushToast]
  )
  return [busy, run]
}

// "Take over to edit". Two homes — the drawer's banner, and the row of a
// messages.xml that can only be used this way — so the confirm, the toast and
// the tooltip live here once.
function TakeOverButton({ row, onChanged, onEditLink, className, children }) {
  const isAdmin = useCan('admin')
  const [busy, run] = useFileAction(onChanged)
  const confirm = useConfirm()
  const pushToast = useToast()

  async function go() {
    if (!(await confirm(TAKE_OVER_CONFIRM))) return
    const created = await run(row.ops.fork)
    if (!created) return
    pushToast(
      created.applied === false
        ? `Your copy "${created.id}" is saved, but not in the mission yet (${created.reason}).`
        : `Your copy "${created.id}" is now used instead of the mod’s.`,
      'info'
    )
    if (created.id) onEditLink(created.id)
  }

  if (!isAdmin || !row.canTakeOver) return null
  return (
    <button
      className={className || 'btn btn-ghost btn-small'}
      type="button"
      onClick={go}
      disabled={busy}
      {...tooltipAttrs(TAKE_OVER_TIP)}
    >
      <FileIcon action="edit" />
      {children || 'Take over to edit'}
    </button>
  )
}

function DeleteButton({ row, onChanged }) {
  const isAdmin = useCan('admin')
  const [busy, run] = useFileAction(onChanged)
  const confirm = useConfirm()

  async function go() {
    const ok = await confirm({
      title: `Delete ${row.label}?`,
      message:
        'It stays deleted: updates and installs won’t bring it back. To undo, tick ' +
        '“Show deleted” under the list and press Restore.',
      confirmLabel: 'Delete',
      danger: true,
    })
    if (ok) run(row.ops.remove, `${row.label} deleted.`)
  }

  if (!isAdmin || row.state === 'deleted') return null
  return (
    <button className="btn btn-ghost btn-small" type="button" onClick={go} disabled={busy}>
      Delete
    </button>
  )
}

// The one control a row carries: On/Off, or Restore once deleted. A file that
// can only be used by taking it over gets that button instead; one replaced by
// your copy gets the way to it.
function RowControl({ row, onChanged, onEditLink }) {
  const isAdmin = useCan('admin')
  const [busy, run] = useFileAction(onChanged)
  if (!isAdmin) return null

  if (row.state === 'deleted') {
    return (
      <button
        className="btn btn-ghost btn-small"
        type="button"
        disabled={busy}
        onClick={() => run(() => row.ops.setState(''), `${row.label} restored.`)}
      >
        Restore
      </button>
    )
  }
  if (row.link && !row.keepsSwitch) {
    return (
      <button className="btn btn-ghost btn-small" type="button" onClick={() => onEditLink(row.link.id)}>
        <FileIcon action="edit" />
        Edit your copy
      </button>
    )
  }
  if (row.status === 'not_auto_linked' && !row.state) {
    return (
      <TakeOverButton row={row} onChanged={onChanged} onEditLink={onEditLink}>
        Take over
      </TakeOverButton>
    )
  }
  return (
    <Switch
      label={`${row.label} enabled`}
      checked={!row.state}
      disabled={busy}
      onChange={(on) =>
        run(
          () => row.ops.setState(on ? '' : 'excluded'),
          on ? `${row.label} turned on.` : `${row.label} turned off.`
        )
      }
    />
  )
}

// Read-only view of a mod's own XML file. Not editable on purpose: the mod dir
// belongs to SteamCMD and the next workshop update overwrites it — what the
// mission actually loads is the custom/ copy or the merged mission file.
function SourceFileViewer({ source }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    let stale = false
    setData(null)
    setError(null)
    api
      .get(`/api/modxml/source?path=${encodeURIComponent(source)}`)
      .then((d) => !stale && setData(d))
      .catch((err) => !stale && setError(err.message || 'Failed to load file'))
    return () => {
      stale = true
    }
  }, [source])

  return (
    <>
      {error && <p className="banner banner-warning">{error}</p>}
      {data && (
        <CodeArea value={data.content} onChange={() => {}} language={data.language} readOnly />
      )}
    </>
  )
}

// Raw text of one generated custom/ file — read-only. There is no save
// endpoint: custom/ is wiped and rebuilt from the mods on every merge, so this
// pane could write a file and lose it on the next install or mod toggle.
// Taking the file over is the way to edit it.
function CustomRawEditor({ name }) {
  const [content, setContent] = useState(null)
  const pushToast = useToast()

  useEffect(() => {
    let stale = false
    setContent(null)
    api
      .get(`/api/modxml/custom/${encodeURIComponent(name)}`)
      .then((d) => !stale && setContent(d.content))
      .catch((err) => !stale && pushToast(err.message || 'Failed to load file', 'error'))
    return () => {
      stale = true
    }
  }, [name, pushToast])

  if (content === null) return <div className="raw-editor" />

  return (
    <div className="raw-editor">
      <CodeArea language="xml" value={content} onChange={() => {}} readOnly />
    </div>
  )
}

// Structured pane — the same table the mission's own events.xml /
// cfgspawnabletypes.xml / types.xml editors use, served per file path. Read-only
// for the same reason as the XML pane; the whole table is frozen rather than
// every row flagged, so no Save bar renders over a file nothing may write.
function CustomTableEditor({ name, kind }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  const load = useCallback(async () => {
    setData(null)
    setError(null)
    try {
      const q = kind ? `?kind=${encodeURIComponent(kind)}` : ''
      setData(await api.get(`/api/modxml/custom/${encodeURIComponent(name)}/table${q}`))
    } catch (err) {
      setError(err.message || 'No table view for this file.')
    }
  }, [name, kind])

  useEffect(() => {
    load()
  }, [load])

  if (error) return <p className="banner banner-warning">{error} — use the XML tab.</p>
  if (!data) return <div />
  return <TableEditor data={data} onSave={() => {}} readOnly />
}

// What the mission loads (Table / XML of the generated file) and, where there
// is one, the mod's own original. One component for the mod-file drawer and the
// manual file's generated custom/ copy.
function FileTabs({ custom, kind, source }) {
  const tabs = useMemo(() => {
    const out = []
    if (custom) out.push({ id: 'table', label: 'Table' }, { id: 'xml', label: 'XML' })
    if (source) out.push({ id: 'source', label: custom ? 'Mod’s original' : 'Mod’s file' })
    return out
  }, [custom, source])
  const [tab, setTab] = useState(tabs[0]?.id)

  useEffect(() => {
    setTab(tabs[0]?.id)
  }, [tabs])

  if (!tabs.length) return <PanelNote>No file to show.</PanelNote>
  const active = tabs.some((t) => t.id === tab) ? tab : tabs[0].id
  return (
    <>
      <Tabs tabs={tabs} active={active} onSelect={setTab} hideSingle />
      {active === 'table' && <CustomTableEditor name={custom} kind={kind} />}
      {active === 'xml' && <CustomRawEditor name={custom} />}
      {active === 'source' && <SourceFileViewer source={source} />}
    </>
  )
}

// Everything about one mod file: why it can't be edited here, the actions that
// answer that, and its content. The row carries only its on/off switch.
function ModFileDrawer({ row, onChanged, onEditLink, onViewUpstream }) {
  const isAdmin = useCan('admin')
  const [busy, run] = useFileAction(onChanged)

  let text
  if (row.link) {
    text = (
      <>
        You replaced this file with your own copy, <strong>{row.link.id}</strong>. The mission
        uses your copy, so the mod’s later changes to this file don’t reach it.
      </>
    )
  } else if (row.state === 'deleted') {
    text = 'You deleted this file. Updates and installs won’t bring it back until you restore it.'
  } else if (row.status === 'not_auto_linked') {
    text =
      'A mod’s messages.xml is never used on its own: its example messages would be sent ' +
      'to your players. Take it over if you want to use it.'
  } else if (!row.source) {
    text = 'No installed mod makes this file any more. It can be deleted.'
  } else {
    text =
      'This file comes from the mod, and Zedstead rebuilds it on every update and install, so it ' +
      'can’t be edited here. Take it over to change it: you get your own copy, and the ' +
      'mission uses that instead.'
  }

  return (
    <>
      <div className="banner banner-info modxml-drawer-banner">
        <span>{text}</span>
        {isAdmin && (
          <span className="modxml-drawer-actions">
            {row.link ? (
              <>
                <button
                  className="btn btn-primary btn-small"
                  type="button"
                  onClick={() => onEditLink(row.link.id)}
                >
                  <FileIcon action="edit" />
                  Edit your copy
                </button>
                {row.link.upstream === 'changed' && (
                  <button
                    className="btn btn-ghost btn-small"
                    type="button"
                    onClick={() => onViewUpstream(row.link.id)}
                    {...tooltipAttrs(UPSTREAM_TIP)}
                  >
                    See what the mod changed
                  </button>
                )}
              </>
            ) : row.state === 'deleted' ? (
              <button
                className="btn btn-primary btn-small"
                type="button"
                disabled={busy}
                onClick={() => run(() => row.ops.setState(''), `${row.label} restored.`)}
              >
                Restore
              </button>
            ) : (
              <TakeOverButton
                row={row}
                onChanged={onChanged}
                onEditLink={onEditLink}
                className="btn btn-primary btn-small"
              />
            )}
            {(!row.link || row.keepsSwitch) && <DeleteButton row={row} onChanged={onChanged} />}
          </span>
        )}
      </div>
      <FileTabs custom={row.custom} kind={row.kind} source={row.source} />
    </>
  )
}

// What the mod has changed in a file the admin took over. The pane exists
// because taking a file over is the one action here with a cost that only shows
// up later: that file stops tracking the mod, silently, for good.
//
// The diff is the mod's own before/after — NOT a comparison with the admin's
// edits. "What did the mod do, and do I want it too" is the question; diffing
// against their version would answer a different one and be mostly noise.
function UpstreamViewer({ fileId, onChanged }) {
  const isAdmin = useCan('admin')
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const confirm = useConfirm()
  const pushToast = useToast()

  const load = useCallback(async () => {
    setError(null)
    try {
      setData(await api.get(`/api/modxml/files/${encodeURIComponent(fileId)}/upstream`))
    } catch (err) {
      setError(err.message || 'Failed to load')
    }
  }, [fileId])

  useEffect(() => {
    setData(null)
    load()
  }, [load])

  async function act(what, done) {
    setBusy(true)
    try {
      setData(await api.post(`/api/modxml/files/${encodeURIComponent(fileId)}/upstream/${what}`))
      pushToast(done, 'info')
      onChanged()
    } catch (err) {
      pushToast(err.message || 'Action failed', 'error')
    } finally {
      setBusy(false)
    }
  }

  const adopt = async () => {
    const ok = await confirm({
      title: 'Take the mod’s version?',
      message:
        'Your copy of this file is replaced by the mod’s current one, and everything you ' +
        'changed in it goes. A version is saved first, so you can get it back from ' +
        'Analysis → Change history. It reaches the mission on the next merge.',
      confirmLabel: 'Take the mod’s version',
      danger: true,
    })
    if (ok) act('adopt', 'Replaced with the mod’s version.')
  }

  if (error) return <p className="banner banner-warning">{error}</p>
  if (!data) return <div />

  return (
    <div className="upstream-view">
      <PanelNote>
        {data.source ? <code>{data.source}</code> : 'Unknown source'}
        {data.captured_at && <> · taken over {new Date(data.captured_at).toLocaleString()}</>}
      </PanelNote>
      {data.status === 'changed' ? (
        <>
          {data.diff ? (
            <DiffView text={data.diff} />
          ) : (
            <PanelNote>
              The mod’s file changed on disk but its contents are the same — only formatting
              moved.
            </PanelNote>
          )}
          {/* Below the diff and sticky to the drawer floor, like every other
              action bar here — the answer comes after reading, and a long diff
              must not put the buttons out of reach. */}
          {isAdmin && (
            <div className="sticky-action-bar">
              <button
                className="btn btn-ghost"
                type="button"
                onClick={() => act('ack', 'Keeping yours.')}
                disabled={busy}
              >
                Keep mine
              </button>
              <button className="btn btn-danger" type="button" onClick={adopt} disabled={busy}>
                Take the mod’s version
              </button>
            </div>
          )}
        </>
      ) : (
        <PanelNote>{UPSTREAM_STATE[data.status] || data.status}</PanelNote>
      )}
    </div>
  )
}

const UPSTREAM_STATE = {
  current: 'Up to date — the mod has not changed this file since you took it over.',
  missing: 'The mod’s file is gone (uninstalled, renamed, or removed by an update). Yours is unaffected.',
  unknown:
    'This file was taken over before Zedstead started recording what it came from, so there is nothing to compare against.',
}

function ModFilesTable({ rows, activeKey, markMod = '', onOpen, onChanged, onEditLink, onViewUpstream }) {
  const isAdmin = useCan('admin')
  return (
    <div className="data-table-wrap">
      <table className="data-table mod-files-table">
        <thead>
          <tr>
            <th>File</th>
            <th>Mod</th>
            <th>What</th>
            <th>Goes into</th>
            <th>Status</th>
            {isAdmin && <th>Enabled</th>}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={row.key}
              className={[
                row.state || row.link ? 'modxml-row-off' : '',
                // The open file, and the files of the mod a link named.
                row.key === activeKey || sameMod(row.mod, markMod) ? 'row-active' : '',
              ].join(' ')}
            >
              <td>
                <button
                  className="btn-link modxml-file-btn"
                  type="button"
                  onClick={() => onOpen(row.key)}
                  {...tooltipAttrs('View the file and what you can do with it')}
                >
                  <FileIcon />
                  {row.name}
                </button>
              </td>
              <td>
                {row.mod ? (
                  <Link className="mod-link" to={modLink(row.mod)}>
                    {row.mod}
                  </Link>
                ) : (
                  '—'
                )}
              </td>
              <td>
                <KindCell kind={row.kind} />
              </td>
              <td className="modxml-into">
                {row.into && <HowBadge how={row.how} />}
                {/* Pen: the mission file it merged into, in its editor. Eye: the
                    generated custom/ copy, read-only in the drawer. Neither:
                    nothing exists yet to open. */}
                {row.intoRoute ? (
                  <Link to={row.intoRoute} {...tooltipAttrs(`Edit ${row.into}`)}>
                    <FileIcon action="edit" />
                    {row.into}
                  </Link>
                ) : row.custom ? (
                  <button
                    className="btn-link modxml-file-btn"
                    type="button"
                    onClick={() => onOpen(row.key)}
                    {...tooltipAttrs('View the file the mission loads')}
                  >
                    <FileIcon />
                    {row.into}
                  </button>
                ) : (
                  <span>{row.into || '—'}</span>
                )}
              </td>
              <td>
                <span className="modxml-status">
                  {row.link ? (
                    <button
                      className="status-pill status-pill-success"
                      type="button"
                      onClick={() => onEditLink(row.link.id)}
                      {...tooltipAttrs(`Your copy: ${row.link.id}`)}
                    >
                      Replaced by your copy
                    </button>
                  ) : (
                    <StatusPill status={row.status} />
                  )}
                  <UpstreamBadge link={row.link} onViewUpstream={onViewUpstream} />
                </span>
              </td>
              {isAdmin && (
                <td className="modxml-row-control">
                  <RowControl row={row} onChanged={onChanged} onEditLink={onEditLink} />
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// Files zsm leaves alone, collapsed at the bottom: mod XML it skips, and the
// cfgeconomycore folders it doesn't write (read-only here — zsm preserves
// them on every rewrite but never writes one).
function OtherFiles({ skipped, ceFolders, openRef, onViewSource }) {
  const total = skipped.length + ceFolders.length
  if (!total) return null
  return (
    <details className="card modxml-more" ref={openRef}>
      <summary>
        <FoldCaret />
        <h2>Skipped and other files</h2>
        <CountBadge value={total} />
      </summary>

      {skipped.length > 0 && (
        <section>
          <h3>Skipped mod files</h3>
          <PanelNote>
            Zedstead never puts these in the mission: XML for another map, XML that isn’t loot or
            event data, and files it could not read.
          </PanelNote>
          <div className="data-table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>File</th>
                  <th>Mod</th>
                  <th>Why</th>
                </tr>
              </thead>
              <tbody>
                {skipped.map((f) => (
                  <tr key={f.source}>
                    <td>
                      <button
                        className="btn-link modxml-file-btn"
                        type="button"
                        onClick={() => onViewSource(f.source)}
                        {...tooltipAttrs(`View ${f.source}`)}
                      >
                        <FileIcon />
                        {f.name}
                      </button>
                    </td>
                    <td>
                      <Link className="mod-link" to={modLink(f.mod)}>
                        {f.mod}
                      </Link>
                    </td>
                    <td>
                      <StatusPill status={f.status} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {ceFolders.length > 0 && (
        <section>
          <h3>Other cfgeconomycore folders</h3>
          <PanelNote>
            The server loads these, but Zedstead doesn’t manage them: the mission’s own folders and
            anything added by hand.{' '}
            <Link to={configLink('economycore')}>
              <FileIcon action="edit" />
              Edit them in cfgeconomycore.xml
            </Link>
          </PanelNote>
          <div className="data-table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Folder</th>
                  <th>File</th>
                  <th>What</th>
                  <th>State</th>
                </tr>
              </thead>
              <tbody>
                {ceFolders.map((row) => (
                  <tr key={`${row.folder}/${row.name}`}>
                    <td>{row.folder}/</td>
                    <td className="mono">{row.name}</td>
                    <td>
                      <KindCell kind={row.type} />
                    </td>
                    <td>
                      {!row.known_type ? (
                        <span className={pillClass('error')}>Unknown type</span>
                      ) : !row.exists ? (
                        <span className={pillClass('warn')}>File missing</span>
                      ) : (
                        <span className={pillClass('ok')}>Loaded</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </details>
  )
}

// `embedded` is the Configs page's Custom config tab, which already
// carries the page heading and the way back.
// `editorOpen` is true while ConfigsPage shows a config editor's drawer over
// this tab, and `onCloseEditor` closes it: one drawer at a time.
export default function ModXmlPage({ embedded = false, editorOpen = false, onCloseEditor = null }) {
  const [report, setReport] = useState(null)
  // One drawer for everything: {kind: 'file'|'custom'|'source'|'upstream', name}.
  // The manual-file table lives in a child that fetches its own list, so its
  // buttons come back up here rather than opening a second drawer.
  const [drawer, setDrawerState] = useState(null)
  // The manual-file form has a drawer of its own in ManualXmlFiles: open
  // tracks it, and a bump of closeManual closes it.
  const [manualOpen, setManualOpen] = useState(false)
  const [closeManual, setCloseManual] = useState(0)
  const confirmDiscard = useConfirmDiscard()
  const navigate = useNavigate()
  const [query, setQuery] = useState('')
  const [showDeleted, setShowDeleted] = useState(false)
  // Bumped on every report load so ManualXmlFiles (which fetches its own list)
  // refreshes with it — taking a file over writes a manual file, and without
  // this the new row only showed up after a full page reload.
  const [linksVersion, setLinksVersion] = useState(0)
  const [focusLink, setFocusLink] = useState(null) // {id, at} — open its form
  const moreRef = useRef(null)
  const isAdmin = useCan('admin')
  const confirm = useConfirm()
  const pushToast = useToast()
  const { startJob } = useJobs()

  const load = useCallback(async () => {
    try {
      setReport(await api.get('/api/modxml'))
      setLinksVersion((n) => n + 1)
      return true
    } catch (err) {
      pushToast(err.message || 'Failed to load the mod XML report', 'error')
      return false
    }
  }, [pushToast])

  useEffect(() => {
    load()
  }, [load])

  // #manual-files is linked from the cfgeconomycore.xml editor; React Router
  // won't scroll to it, and the card only exists after the report loads.
  useHashTarget(!!report)
  // ?mod= (from the Mods page's XML count, crossLinks.modXmlLink) marks that
  // mod's rows in both file lists.
  const [searchParams] = useSearchParams()
  const markMod = searchParams.get('mod') || ''
  // ?file=<id> opens that manual XML file's form (crossLinks.manualFileLink,
  // the locked rows' Edit file in a mission file's editor).
  const fileParam = searchParams.get('file') || ''
  useEffect(() => {
    if (fileParam) setFocusLink({ id: fileParam, at: Date.now() })
  }, [fileParam])

  const rows = useMemo(() => (report ? buildModRows(report) : []), [report])

  // Opening one of the page's drawers replaces the form drawer, so a
  // half-written manual file asks first. Closing never needs to.
  const setDrawer = useCallback(
    async (next) => {
      if (next && manualOpen) {
        if (!(await confirmDiscard(MANUAL_FORM))) return
        setCloseManual((n) => n + 1)
      }
      if (next && editorOpen) onCloseEditor?.()
      setDrawerState(next)
    },
    [manualOpen, confirmDiscard, editorOpen, onCloseEditor]
  )
  const closeDrawer = useCallback(() => setDrawerState(null), [])
  // The form drawer opened: the page's drawer gets out of its way.
  // Stable on purpose: ManualXmlFiles calls it from an effect keyed on it, and
  // a new identity when the editor opened re-ran it and closed that editor.
  const editorRef = useRef({ editorOpen, onCloseEditor })
  editorRef.current = { editorOpen, onCloseEditor }
  const onManualOpen = useCallback((open) => {
    setManualOpen(open)
    if (!open) return
    setDrawerState(null)
    if (editorRef.current.editorOpen) editorRef.current.onCloseEditor?.()
  }, [])
  // A config editor opened over this tab (any link on it) closes this page's
  // drawers. The route guard or openEditor already asked about the form's
  // edits, so nothing asks again here.
  useEffect(() => {
    if (!editorOpen) return
    setDrawerState(null)
    setCloseManual((n) => n + 1)
  }, [editorOpen])
  // A mission file's config editor opens over this tab, which stays behind it
  // (ConfigsPage reads the state).
  // `fileId` filters the editor to that manual file's rows (?owner=).
  const openEditor = useCallback(
    (slug, fileId) =>
      navigate(editorLink(slug, { owner: fileId && manualMarker(fileId) }), {
        state: { gridTab: CONFIGS_MOD_XML_TAB },
      }),
    [navigate]
  )
  const viewSource = useCallback((name) => setDrawer({ kind: 'source', name }), [setDrawer])
  const viewUpstream = useCallback((name) => setDrawer({ kind: 'upstream', name }), [setDrawer])
  const openCustom = useCallback((name) => setDrawer({ kind: 'custom', name }), [setDrawer])
  // "Edit your copy": the form opens in the manual-file card below, so the
  // drawer gets out of its way.
  const editLink = useCallback((id) => {
    setDrawerState(null)
    setFocusLink({ id, at: Date.now() })
  }, [])

  async function reapplyAll() {
    if (!(await confirm(REAPPLY_CONFIRM))) return
    try {
      await startJob({
        endpoint: '/api/mods/xml/merge',
        name: 'Re-apply all mod XML',
        kind: 'mods_xml_merge',
        onStatusChange: (status) => {
          if (status === 'success') load()
        },
      })
    } catch (err) {
      pushToast(err.message || 'Failed to start', 'error')
    }
  }

  if (!report) return <div className="page" />

  const live = rows.filter((r) => r.state !== 'deleted')
  const deletedCount = rows.length - live.length
  const needle = query.trim().toLowerCase()
  const shown = (showDeleted ? rows : live).filter(
    (r) =>
      !needle ||
      [r.name, r.mod, r.kind, XML_KIND_LABEL[r.kind], XML_HOW[r.how]?.label, r.into, statusText(r)]
        .join(' ')
        .toLowerCase()
        .includes(needle)
  )

  const count = (pred) => live.filter(pred).length
  const active = count((r) => r.link || r.status === 'linked' || r.status === 'merged')
  const off = count((r) => !r.link && (r.state || r.status === 'disabled'))
  const pending = count((r) => !r.link && !r.state && r.status === 'no_target')
  const mods = new Set(live.map((r) => r.mod).filter(Boolean)).size

  const skipped = report.files.filter((f) => f.group === 'skipped')
  const ceFolders = report.ce_folders || []

  // The drawer is opened from a row this render may no longer show, so it
  // would otherwise sit open over nothing.
  let drawerRow = null
  let drawerOk = false
  if (drawer?.kind === 'file') {
    drawerRow = rows.find((r) => r.key === drawer.name) || null
    drawerOk = Boolean(drawerRow)
  } else if (drawer?.kind === 'custom') {
    drawerOk = report.custom.some((c) => c.name === drawer.name && !c.missing)
  } else if (drawer?.kind === 'source') {
    drawerOk = report.files.some((f) => f.source === drawer.name)
  } else if (drawer?.kind === 'upstream') {
    // Keyed on the manual file's id, which the report's `manual` list carries.
    drawerOk = (report.manual || []).some((f) => f.id === drawer.name)
  }

  const showSkipped = () => {
    const el = moreRef.current
    if (!el) return
    el.open = true
    el.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  return (
    <div className={`page${drawerOk || manualOpen ? ' has-side-drawer' : ''}`}>
      <div className="page-header">
        {!embedded && <h1>Custom config</h1>}
        <div className="action-bar">
          <RefreshButton className="btn btn-ghost" icon what="Mod XML report" onRefresh={load} />
          {isAdmin && report.mission && (
            <button
              className="btn btn-ghost"
              onClick={reapplyAll}
              type="button"
              {...tooltipAttrs('Put every mod’s XML and your own XML into the mission again.')}
            >
              <HiOutlineRocketLaunch aria-hidden="true" />
              Re-apply all
            </button>
          )}
          {!embedded && (
            <Link className="btn btn-ghost" to="/configs">
              <HiOutlineDocumentText aria-hidden="true" />
              Config Editors
            </Link>
          )}
        </div>
      </div>

      <PageIntro page="/configs/mod-xml" />

      {report.mission ? (
        <div className="modxml-summary">
          <span>
            Mission <strong>{report.mission}</strong> · {live.length} mod file
            {live.length === 1 ? '' : 's'} from {mods} mod{mods === 1 ? '' : 's'}
          </span>
          <span className="modxml-pills">
            <span className={pillClass('ok')}>{active} active</span>
            {off > 0 && <span className={pillClass('muted')}>{off} off</span>}
            {pending > 0 && (
              <button
                type="button"
                className={`${pillClass('warn')}`}
                onClick={() => setQuery(XML_STATUS.no_target.label)}
                {...tooltipAttrs('Show them. “Re-apply all” puts them in the mission.')}
              >
                {pending} not applied yet
              </button>
            )}
            {skipped.length > 0 && (
              <button
                type="button"
                className={`${pillClass('muted')}`}
                onClick={showSkipped}
              >
                {skipped.length} skipped
              </button>
            )}
          </span>
        </div>
      ) : (
        <div className="banner banner-warning">
          No mission found in serverDZ.cfg, so nothing can be put into one.
        </div>
      )}

      <div className="card" id="mod-files">
        <div className="card-header">
          <h2>Mod files</h2>
          <FilterBox
            value={query}
            onChange={setQuery}
            placeholder="Filter by file, mod or status…"
            shown={shown.length}
            total={showDeleted ? rows.length : live.length}
            label="files"
          />
        </div>
        <PanelNote>
          Click a file to see it, take it over to edit it, or delete it.
        </PanelNote>
        {shown.length === 0 ? (
          <PanelNote>
            {needle
              ? 'Nothing matches the filter.'
              : 'No mod XML yet. Install a mod, or run an update, to fill this list.'}
          </PanelNote>
        ) : (
          <ModFilesTable
            rows={shown}
            activeKey={drawer?.kind === 'file' ? drawer.name : null}
            markMod={markMod}
            onOpen={(key) =>
              drawer?.kind === 'file' && drawer.name === key
                ? closeDrawer()
                : setDrawer({ kind: 'file', name: key })
            }
            onChanged={load}
            onEditLink={editLink}
            onViewUpstream={viewUpstream}
          />
        )}
        {deletedCount > 0 && (
          <div className="modxml-show-deleted">
            <Switch checked={showDeleted} onChange={setShowDeleted}>
              Show deleted <CountBadge value={deletedCount} />
            </Switch>
          </div>
        )}
      </div>

      <ManualXmlFiles
        reportFiles={report.manual}
        onChanged={load}
        version={linksVersion}
        focus={focusLink}
        onViewUpstream={viewUpstream}
        onOpenCustom={openCustom}
        markMod={markMod}
        onOpenEditor={openEditor}
        onFormOpen={onManualOpen}
        closeForm={closeManual}
        drawerKey={DRAWER_WIDTH_KEY}
      />

      <OtherFiles
        skipped={skipped}
        ceFolders={ceFolders}
        openRef={moreRef}
        onViewSource={viewSource}
      />

      {drawerOk && drawer.kind === 'file' && (
        <SideDrawer
          title={drawerRow.name}
          badge={XML_KIND_LABEL[drawerRow.kind] || drawerRow.kind}
          subtitle={[drawerRow.mod, drawerRow.into && `goes into ${drawerRow.into}`]
            .filter(Boolean)
            .join(' · ')}
          storageKey={DRAWER_WIDTH_KEY}
          onClose={closeDrawer}
        >
          <ModFileDrawer
            row={drawerRow}
            onChanged={load}
            onEditLink={editLink}
            onViewUpstream={viewUpstream}
          />
        </SideDrawer>
      )}
      {drawerOk && drawer.kind === 'custom' && (
        <SideDrawer
          title={`custom/${drawer.name}`}
          badge="read-only"
          badgeTone="readonly"
          subtitle="Made from your manual XML file. Edit that file to change it."
          storageKey={DRAWER_WIDTH_KEY}
          onClose={closeDrawer}
        >
          <FileTabs
            custom={drawer.name}
            kind={report.custom.find((c) => c.name === drawer.name)?.type}
          />
        </SideDrawer>
      )}
      {drawerOk && drawer.kind === 'source' && (
        <SideDrawer
          title={drawer.name}
          badge="read-only"
          badgeTone="readonly"
          subtitle="The mod’s own file. Every workshop update overwrites it."
          storageKey={DRAWER_WIDTH_KEY}
          onClose={closeDrawer}
        >
          <SourceFileViewer source={drawer.name} />
        </SideDrawer>
      )}
      {drawerOk && drawer.kind === 'upstream' && (
        <SideDrawer
          title={`What the mod changed · ${drawer.name}`}
          badge="upstream"
          subtitle="The mod’s own before and after. Your copy stays as it is until you choose."
          storageKey={DRAWER_WIDTH_KEY}
          onClose={closeDrawer}
        >
          <UpstreamViewer fileId={drawer.name} onChanged={load} />
        </SideDrawer>
      )}
    </div>
  )
}
