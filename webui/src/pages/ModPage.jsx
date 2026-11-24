import Select from '../components/Select.jsx'
import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import Suggestions from '../components/Suggestions.jsx'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import {
  HiOutlineArrowDownTray,
  HiOutlineArrowPath,
  HiOutlineArrowTopRightOnSquare,
  HiOutlineArrowUturnLeft,
  HiOutlineCog6Tooth,
  HiOutlineMap,
  HiOutlinePlay,
  HiOutlinePower,
  HiOutlineTrash,
} from 'react-icons/hi2'
import RestoreIcon from '../components/RestoreIcon.jsx'
import { api } from '../api'
import { useWs } from '../ws'
import { useJobs } from '../jobs'
import { useToast } from '../components/Toast.jsx'
import { useConfirm } from '../components/useConfirm.jsx'
import ConfirmModal from '../components/ConfirmModal.jsx'
import CodeArea from '../components/CodeArea.jsx'
import FilterBox from '../components/FilterBox.jsx'
import PageIntro from '../components/PageIntro.jsx'
import SteamLoginGate from '../components/SteamLoginGate.jsx'
import SideDrawer from '../components/SideDrawer.jsx'
import NeedsSteam from '../components/NeedsSteam.jsx'
import Tooltip, { tooltipAttrs } from '../components/Tooltip.jsx'
import WorkshopLink from '../components/WorkshopLink.jsx'
import { configLink, modLink, spawnTestMapLink } from '../crossLinks.js'
import { useCrumbTitle } from '../crumbTitle.js'
import { filterClasses, useSpawnTest } from '../useSpawnTest.js'
import { fuzzyFilter } from '../fuzzy.js'
import { XML_STATUS, pillClass } from '../modXmlStatus.js'
import { SkeletonLines } from '../components/Skeleton.jsx'
import InstallOptionsModal, { OrphanDepsField, wipeWarningFor } from '../installFlow.jsx'
import PanelNote from '../components/PanelNote.jsx'

// One mod, everything zsm knows about it, and the actions that apply right now.
//
// The route param is a mod name (`CF`, `@` optional) or a workshop id
// (`1559212036`) — GET /api/mods/{ref} resolves both to the same payload, so a
// caller links with whichever it holds (Doctor/Mod XML know names; the
// workshop cache only ids). The id is the canonical URL: once the payload
// names one, the page rewrites the address bar to it (no `%40`/`%20` for mod
// names that carry an `@` or a space).
//
// The buttons are driven by the payload's `actions` booleans rather than
// re-derived here: the rules live with the endpoints that enforce them (a
// scoped XML merge is refused for a disabled mod, uninstall needs the mod listed).
// Every action reuses an existing endpoint, except the Spawn test card's
// /api/mods/{name}/spawn-test and /api/mods/spawn-test/end (plans/37).

const DRAWER_WIDTH_KEY = 'zsm.mod.drawer.width'

function formatBytes(n) {
  if (!n) return '—'
  const units = ['B', 'KB', 'MB', 'GB']
  let value = n
  let i = 0
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024
    i += 1
  }
  return `${value < 10 && i > 0 ? value.toFixed(1) : Math.round(value)} ${units[i]}`
}

// Absolute local time + the same relative wording the backend's
// ModUpdateInfo.age_display uses, so "3d ago" means the same thing everywhere.
function relativeAge(epoch) {
  const s = Math.max(0, Math.floor(Date.now() / 1000) - epoch)
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}

function formatWhen(epoch) {
  return `${new Date(epoch * 1000).toLocaleString()} (${relativeAge(epoch)})`
}

// mod.cpp's `action` URL, when it points somewhere other than the workshop page
// the Workshop ID row already links. Mirrors app/modmgr/mod_info.py's tooltip
// formatting, which tolerates a scheme-less URL.
function homepageUrl(action) {
  if (!action || action.includes('steamcommunity.com')) return ''
  return action.startsWith('http') ? action : `https://${action}`
}

// .kv-grid is a <dl> whose rows are `display: contents` — the dt/dd markup is
// the contract (same shape as DoctorPage's KvGrid).
function Row({ label, children }) {
  return (
    <div className="kv-row">
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  )
}

// A `timestamps` entry — 0 means zsm has no such date for this mod (not
// downloaded, no meta.cpp, no workshop ACF entry), so the row is dropped.
function TimeRow({ label, tip, epoch }) {
  if (!epoch) return null
  return <Row label={<Tooltip text={tip}>{label}</Tooltip>}>{formatWhen(epoch)}</Row>
}

function ModList({ names, empty }) {
  if (!names.length) return <span className="dep-info">{empty}</span>
  return (
    <span className="mod-chip-list">
      {names.map((n) => (
        <Link key={n} className="mod-link" to={modLink(n)}>
          {n}
        </Link>
      ))}
    </span>
  )
}

// Read-only view of one file out of the mod's folder, shown in the page's
// right-hand drawer — the same surface (and component) the Mod XML page opens a
// mod's own file in, so file content never pushes the page it came from down.
// Read-only on purpose: the folder is SteamCMD's and the next workshop update
// overwrites it.
function ModFileViewer({ modRef, file }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let stale = false
    setData(null)
    setError('')
    api
      .get(`/api/mods/${encodeURIComponent(modRef)}/file?path=${encodeURIComponent(file)}`)
      .then((d) => !stale && setData(d))
      .catch((err) => !stale && setError(err.message || 'Failed to load file'))
    return () => {
      stale = true
    }
  }, [modRef, file])

  return (
    <>
      {error && <p className="banner banner-warning">{error}</p>}
      {data?.truncated && (
        <p className="banner banner-warning">
          Large file — showing the first {formatBytes(data.shown)} of {formatBytes(data.size)}.
        </p>
      )}
      {data && <CodeArea value={data.content} onChange={() => {}} language={data.language} readOnly />}
    </>
  )
}

// A file's mission-XML destination, when the Mod XML report knows one. Same
// wording as that report (modXmlStatus.js), and links to it.
function XmlCell({ info }) {
  if (!info) return <span className="text-muted">—</span>
  const status = XML_STATUS[info.status] || { label: info.status, tone: 'muted' }
  const tip = [info.kind && `Kind: ${info.kind}`, info.target && `Destination: ${info.target}`]
    .filter(Boolean)
    .join(' · ')
  return (
    <Link to={configLink('mod-xml')} {...tooltipAttrs(tip || 'Open Custom config')}>
      <span className={pillClass(status.tone)}>{status.label}</span>
    </Link>
  )
}

// What is actually in the mod's folder. Lazy (its own fetch) — the detail
// payload is the page's critical path and a mod dir can hold thousands of
// files. The backend serves the installed copy, falling back to the workshop
// cache, and only .xml/.json/.txt/.md are viewable; everything else is named.
//
// `viewing` lives on the page, not here: the drawer it opens is the page's
// (it publishes --side-drawer-width, which .page.has-side-drawer reserves).
function ModFilesCard({ modRef, viewing, onView }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')

  useEffect(() => {
    let stale = false
    setData(null)
    setError('')
    api
      .get(`/api/mods/${encodeURIComponent(modRef)}/files`)
      .then((d) => !stale && setData(d))
      .catch((err) => !stale && setError(err.message || 'Failed to list files'))
    return () => {
      stale = true
    }
  }, [modRef])

  const files = data?.files || []
  const shown = useMemo(() => fuzzyFilter(files, query, (f) => f.path), [files, query])

  if (error) {
    return (
      <div className="card">
        <div className="card-header">
          <h2>Files</h2>
        </div>
        <PanelNote>{error}</PanelNote>
      </div>
    )
  }
  if (!data) return null
  if (!files.length) {
    return (
      <div className="card">
        <div className="card-header">
          <h2>Files</h2>
        </div>
        <PanelNote>No files on this box — the mod is not downloaded.</PanelNote>
      </div>
    )
  }

  return (
    <div className="card">
      <div className="card-header">
        <h2>Files</h2>
        <span className="status-pill status-pill-neutral">
          {data.source === 'cache' ? 'workshop cache' : 'installed copy'}
        </span>
        <span className="hint">
          {files.length} file{files.length === 1 ? '' : 's'}
        </span>
      </div>
      <PanelNote>
        <code>{data.root}</code>
        {'\n'}Read-only: the folder belongs to SteamCMD and every workshop update overwrites it.
      </PanelNote>
      {files.length > 12 && (
        <FilterBox
          value={query}
          onChange={setQuery}
          placeholder="Filter files…"
          shown={shown.length}
          total={files.length}
          label="files"
        />
      )}
      <div className="data-table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>File</th>
              <th>Size</th>
              <th>Mission XML</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((f) => (
              <tr key={f.path} className={viewing === f.path ? 'row-active' : undefined}>
                <td>
                  {f.language ? (
                    <button
                      className="btn-link"
                      type="button"
                      {...tooltipAttrs(`View ${f.path}`)}
                      onClick={() => onView(viewing === f.path ? null : f.path)}
                    >
                      {f.path}
                    </button>
                  ) : (
                    <span className="dep-info">{f.path}</span>
                  )}
                </td>
                <td>{formatBytes(f.size)}</td>
                <td>
                  <XmlCell info={f.xml} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {data.truncated && (
        <p className="dep-info">Listing capped — this mod ships more files than are shown.</p>
      )}
    </div>
  )
}

// The spawn test's map: Leaflet and the map editor's layers, loaded only when
// a test at a location is being set up or shown.
const SpawnPointPicker = lazy(() => import('./mapeditor/SpawnPointPicker.jsx'))

function SpawnMap(props) {
  return (
    <Suspense fallback={<SkeletonLines count={3} />}>
      <SpawnPointPicker {...props} />
    </Suspense>
  )
}

// Same wording as app/ops/spawn_test_ops.py::count_warning_text — keep them in step.
function spawnCountWarning(count, warnAbove, reason) {
  if (warnAbove === null || warnAbove === undefined || count <= warnAbove) return null
  return `${count} is above the ${warnAbove} suggested for this server (${reason}). Spawning this many can lag or crash the server — you do this at your own risk.`
}

function sliderStart(warnAbove) {
  return warnAbove ? Math.max(100, 2 * warnAbove) : 100
}

// The "Only X" scopes, in the order they widen. Values match
// app/ops/spawn_test_ops.py::ONLY_X_SCOPES.
const ONLY_X_SCOPES = [
  ['loot', 'Loot only'],
  ['loot_vehicles', 'Loot + vehicles'],
  ['everything', 'Everything (also zombies and animals)'],
]
// The init.c modes plus "Only X". Values match the API's `mode`.
const SPAWN_MODES = [
  { value: 'near_player', label: 'Spawn next to me' },
  { value: 'at_location', label: 'Spawn at a location (moves me there)' },
  { value: 'only_x', label: 'Only X in the loot economy' },
]

// The location picker's entry for typing X and Z yourself.
const CUSTOM_PLACE = '__custom__'

// How the card names a test's place: "Adamovka (5340.7, 11380)" or "5340.7, 11380".
function placeText(location) {
  const where = `${location.x}, ${location.z}`
  return location.name ? `${location.name} (${where})` : where
}

const SCOPE_PHRASE = {
  loot: 'Sets every other item to 0',
  loot_vehicles: 'Sets every other item and vehicle event to 0',
  everything: 'Sets every other item and event to 0',
}

// Spawn test (plans/37): N of this mod's class next to the first player who
// joins, undone by "End test & restore" (init.c + the storage folder back as
// they were).
// Lazy like ModFilesCard — the first GET per mod parses its PBOs.
//
// The count has no maximum. A range input needs one, so the slider's end moves:
// letting go at the end doubles it, and the number box beside it takes any value.
// The RAM-based threshold only warns; nothing here or in the API refuses a count.
//
// The data lives in useSpawnTest, called by the page: it decides where the card
// goes (first while this mod's test runs) and the card shows what it loaded.
function SpawnTestCard({ modName, info, error, refresh }) {
  const pushToast = useToast()
  const confirm = useConfirm()
  const { trackJob } = useJobs()
  const [busy, setBusy] = useState(false)
  const [classname, setClassname] = useState('')
  const [countText, setCountText] = useState('10')
  const [mode, setMode] = useState('near_player')
  const [scope, setScope] = useState('loot')
  const [place, setPlace] = useState('')
  const [xText, setXText] = useState('')
  const [zText, setZText] = useState('')
  const [sliderMax, setSliderMax] = useState(() => sliderStart(info?.warn_above))
  const [suggestionsOpen, setSuggestionsOpen] = useState(false)
  const [activeIndex, setActiveIndex] = useState(-1)
  const blurTimer = useRef(null)

  useEffect(() => () => clearTimeout(blurTimer.current), [])

  const classes = info?.classes || []
  const query = classname.trim().toLowerCase()
  const { items, overflow } = useMemo(() => filterClasses(classes, query), [classes, query])

  if (error) {
    return (
      <div className="card">
        <div className="card-header">
          <h2>Spawn test</h2>
        </div>
        <PanelNote>{error}</PanelNote>
      </div>
    )
  }
  if (!info) return null

  const count = /^\d+$/.test(countText) ? Number(countText) : null
  const warning = count === null ? null : spawnCountWarning(count, info.warn_above, info.warn_reason)
  const active = info.active
  // storage_<instanceId>: the folder a running test wrote down, else the one
  // a new test would copy.
  const storage = active?.storage_name || info.storage_name
  const exactClass = classes.find((c) => c.toLowerCase() === query)
  // The picked place, or the typed position; null until it is a usable one.
  // The server checks the map's edges again.
  const location = (() => {
    if (mode !== 'at_location') return null
    if (place && place !== CUSTOM_PLACE) {
      const found = (info.locations || []).find((l) => l.name === place)
      return found ? { name: found.name, x: found.x, z: found.z } : null
    }
    if (place !== CUSTOM_PLACE || xText.trim() === '' || zText.trim() === '') return null
    const x = Number(xText)
    const z = Number(zText)
    const inside = (v) => Number.isFinite(v) && v >= 0 && (!info.map_size || v <= info.map_size)
    return inside(x) && inside(z) ? { name: null, x, z } : null
  })()
  const showSuggestions = suggestionsOpen && items.length > 0 && !(exactClass && items.length === 1)

  // A point picked on the map is a custom position.
  function pickOnMap({ x, z }) {
    setPlace(CUSTOM_PLACE)
    setXText(String(x))
    setZText(String(z))
  }

  function setCount(value) {
    setCountText(String(value))
    if (value > sliderMax) setSliderMax(value)
  }

  function growSlider(e) {
    if (Number(e.currentTarget.value) >= sliderMax) setSliderMax((m) => m * 2)
  }

  function choose(index) {
    const item = items[index]
    if (!item) return
    setClassname(item)
    setSuggestionsOpen(false)
    setActiveIndex(-1)
  }

  function onClassKeyDown(e) {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setSuggestionsOpen(true)
      setActiveIndex((i) => Math.min(i + 1, items.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActiveIndex((i) => Math.max(i - 1, -1))
    } else if (e.key === 'Enter' && showSuggestions && activeIndex >= 0) {
      e.preventDefault()
      choose(activeIndex)
    } else if (e.key === 'Escape' && showSuggestions) {
      e.stopPropagation()
      setSuggestionsOpen(false)
      setActiveIndex(-1)
    }
  }

  // Inline answers carry the result; a large storage folder comes back as a job.
  async function act(endpoint, body, jobName, kind, onDone) {
    setBusy(true)
    try {
      const data = await api.post(endpoint, body)
      if (data?.job_id) {
        trackJob(data.job_id, {
          name: jobName,
          kind,
          onStatusChange: (status) => status === 'success' && refresh(),
        })
      } else {
        onDone(data)
        refresh()
      }
    } catch (err) {
      pushToast(err.message || 'Spawn test failed', 'error')
    } finally {
      setBusy(false)
    }
  }

  async function start() {
    const onlyX = mode === 'only_x'
    const message = onlyX
      ? `${SCOPE_PHRASE[scope]}, sets ${exactClass} to ${count}, and empties ${storage} so the world starts fresh. Items spawn where the map allows them, not next to you. "End test & restore" puts every file and ${storage} back.`
      : location
        ? `Adds a spawn-test block to init.c and copies ${storage} aside. Start the server and join: ${count} x ${exactClass} appear at ${placeText(location)}, and the first player who joins is moved there. "End test & restore" puts init.c and ${storage} back.`
        : `Adds a spawn-test block to init.c and copies ${storage} aside. Start the server and join: ${count} x ${exactClass} appear next to the first player. "End test & restore" puts init.c and ${storage} back.`
    const ok = await confirm({
      title: `Start a spawn test for ${exactClass}?`,
      message: warning ? `${warning} ${message}` : message,
      confirmLabel: 'Start test',
      danger: onlyX,
    })
    if (!ok) return
    await act(
      `/api/mods/${encodeURIComponent(modName)}/spawn-test`,
      onlyX
        ? { mode, classname: exactClass, count, scope }
        : location
          ? { mode, classname: exactClass, count, x: location.x, z: location.z, location: location.name }
          : { mode, classname: exactClass, count },
      `Start spawn test for ${modName}`,
      'spawn_test_start',
      (data) => {
        pushToast(
          onlyX
            ? `Only X armed: ${data.classname} at ${data.count}. Start the server and give the economy time.`
            : data.location
              ? `Spawn test armed: ${data.count} x ${data.classname} at ${placeText(data.location)}. Start the server and join.`
              : `Spawn test armed: ${data.count} x ${data.classname}. Start the server and join.`,
          'success'
        )
        if (data.warning) pushToast(data.warning, 'info')
        // The spawn-position lines are the only ones that change what the
        // admin should expect to see ("you asked for 10, the map has 2 places
        // to put them"). Emptying the storage folder was already in the confirm.
        for (const line of data.log || []) {
          if (line.includes('spawn positions') || line.includes('no positions')) {
            pushToast(line, 'info')
          }
        }
      }
    )
  }

  async function end() {
    const onlyX = active.mode === 'only_x'
    const ok = await confirm({
      title: 'End the spawn test and restore?',
      message: onlyX
        ? `Puts every economy file back as it was and replaces ${storage} with the copy taken when the test started. Anything that happened in the world since — including the spawned ${active.classname} — is gone.`
        : `Puts init.c back and replaces ${storage} with the copy taken when the test started. Anything that happened in the world since — including the spawned ${active.classname} — is gone.`,
      confirmLabel: 'End test & restore',
      danger: true,
    })
    if (!ok) return
    await act('/api/mods/spawn-test/end', undefined, 'End spawn test', 'spawn_test_end', (data) => {
      pushToast(
        onlyX
          ? `Spawn test ended — ${(data.restored_files || []).length} files and ${storage} restored.`
          : `Spawn test ended — init.c and ${storage} restored.`,
        'success'
      )
      for (const line of data.log || []) {
        if (line.includes('during the test')) pushToast(line, 'info')
      }
    })
  }

  const endButton = active && (
    <div className="action-bar">
      <button className="btn btn-danger" disabled={busy} onClick={end}>
        <HiOutlineArrowUturnLeft aria-hidden="true" />
        End test &amp; restore
      </button>
    </div>
  )

  const activeHere = active && active.mod === modName

  return (
    <div className={`card spawn-test${activeHere ? ' card-attention' : ''}`}>
      <div className="card-header">
        <h2>Spawn test</h2>
        {active && <span className="status-pill status-pill-warning">Active</span>}
      </div>

      {activeHere ? (
        <>
          <dl className="kv-grid">
            <Row label="Mode">
              {(SPAWN_MODES.find((m) => m.value === active.mode) || SPAWN_MODES[0]).label}
            </Row>
            {active.location && <Row label="Location">{placeText(active.location)}</Row>}
            {active.mode === 'only_x' && (
              <Row label="Scope">
                {(ONLY_X_SCOPES.find(([value]) => value === active.scope) || [])[1] ||
                  active.scope}
              </Row>
            )}
            <Row label="Class">
              <code>{active.classname}</code>
            </Row>
            <Row label="Count">{active.count}</Row>
            <Row label="Mission">{active.mission}</Row>
            <Row label="Started">{new Date(active.started_at).toLocaleString()}</Row>
          </dl>
          {active.location && <SpawnMap point={active.location} />}
          <PanelNote>
            {active.mode === 'only_x'
              ? 'Start the server and give the central economy time to place them — they spawn where the map allows, not next to you. The server must be stopped to end the test.'
              : active.location
                ? 'Start the server and join: the first player who joins is moved to the location and finds them there, once. The server must be stopped to end the test.'
                : 'Start the server and join: the first player who joins gets them next to them, once. The server must be stopped to end the test.'}
          </PanelNote>
          {endButton}
        </>
      ) : active ? (
        <>
          <p className="dep-info">
            A spawn test is active for <Link to={modLink(active.mod)}>{active.mod}</Link> (
            <code>{active.classname}</code>). End it before starting another.
          </p>
          {endButton}
        </>
      ) : !classes.length ? (
        <PanelNote>No spawnable classes found in this mod&apos;s PBOs.</PanelNote>
      ) : (
        <>
          <PanelNote>
            Spawns a class from this mod next to the first player who joins, or at a place on
            the map you pick, so you can see it works. The server must be stopped to start or
            end a test.
          </PanelNote>
          <div className="spawn-test-grid">
            <label className="modal-field">
              Class
              <input
                className="text-input"
                type="text"
                placeholder={`e.g. ${classes[0]}`}
                spellCheck={false}
                autoComplete="off"
                value={classname}
                onChange={(e) => {
                  setClassname(e.target.value)
                  setSuggestionsOpen(true)
                  setActiveIndex(-1)
                }}
                onFocus={() => setSuggestionsOpen(true)}
                onBlur={() => {
                  // Delayed so a mousedown on a suggestion still lands.
                  blurTimer.current = setTimeout(() => setSuggestionsOpen(false), 150)
                }}
                onKeyDown={onClassKeyDown}
              />
              {showSuggestions && (
                <Suggestions
                  items={items.map((item) => ({ name: item }))}
                  activeIndex={activeIndex}
                  onChoose={choose}
                  more={overflow > 0 && `${overflow} more, keep typing`}
                />
              )}
            </label>

            <label className="modal-field">
              Mode
              <Select
                value={mode}
                onChange={setMode}
                options={SPAWN_MODES}
              />
            </label>

            {mode === 'at_location' && (
              <label className="modal-field spawn-test-scope">
                Location
                <Select
                  value={place}
                  onChange={setPlace}
                  placeholder="Pick a place"
                  options={[
                    { value: CUSTOM_PLACE, label: 'Custom position…' },
                    ...(info.locations || []).map((l) => ({
                      value: l.name,
                      label: l.name,
                      description: `${l.x}, ${l.z}`,
                    })),
                  ]}
                />
              </label>
            )}

            {mode === 'at_location' && place === CUSTOM_PLACE && (
              <div className="spawn-test-scope spawn-test-coords">
                <label className="modal-field">
                  X
                  <input
                    className="text-input text-input-narrow"
                    type="number"
                    min="0"
                    max={info.map_size || undefined}
                    step="0.1"
                    value={xText}
                    onChange={(e) => setXText(e.target.value)}
                  />
                </label>
                <label className="modal-field">
                  Z
                  <input
                    className="text-input text-input-narrow"
                    type="number"
                    min="0"
                    max={info.map_size || undefined}
                    step="0.1"
                    value={zText}
                    onChange={(e) => setZText(e.target.value)}
                  />
                </label>
              </div>
            )}

            {mode === 'at_location' && (
              <div className="spawn-test-scope">
                <SpawnMap point={location} onPick={pickOnMap} />
              </div>
            )}

            {mode === 'only_x' && (
              <label className="modal-field spawn-test-scope">
                Scope
                <Select
                  value={scope}
                  onChange={setScope}
                  options={ONLY_X_SCOPES.map(([value, label]) => ({ value, label }))}
                />
              </label>
            )}
          </div>

          {mode === 'at_location' && (
            <div className="action-bar">
              <Link
                className="btn btn-ghost"
                to={spawnTestMapLink({ mod: modName, classname: exactClass || classname.trim(), count })}
              >
                <HiOutlineMap aria-hidden="true" />
                Pick in Map Editor
              </Link>
            </div>
          )}

          {mode === 'at_location' && (
            <p className="dep-info">
              The first player who joins is moved to this place, and the items appear in a grid
              beside them.
              {' '}Click the map or drag the marker to put it anywhere.
              {place === CUSTOM_PLACE &&
                ` X and Z are map metres${info.map_size ? `, 0 to ${info.map_size}` : ''}.`}
            </p>
          )}

          {mode === 'only_x' && (
            <p className="dep-info">
              Rewrites the mission&apos;s economy files and empties{' '}
              <code>{storage}</code>, so the world starts fresh with only this class in it.
              An XML merge during the test — installing a mod, toggling one,{' '}
              <code>zsm run</code> — regenerates <code>custom/</code> and undoes it for mod
              items.
            </p>
          )}

          <div className="modal-field">
            <span>Count</span>
            {mode === 'only_x' && exactClass && (
              <span className="hint">
                Sets {exactClass}&apos;s nominal and min to this number.
              </span>
            )}
            <div className="spawn-test-count">
              <input
                type="range"
                min="0"
                max={sliderMax}
                step="1"
                aria-label="Count"
                value={Math.min(count ?? 0, sliderMax)}
                onChange={(e) => setCountText(e.target.value)}
                onPointerUp={growSlider}
                onKeyUp={growSlider}
              />
              <span className="hint">{sliderMax}</span>
              <input
                className="text-input text-input-narrow"
                type="number"
                min="0"
                step="1"
                aria-label="Count"
                value={countText}
                onChange={(e) => {
                  const text = e.target.value
                  if (/^\d+$/.test(text)) setCount(Number(text))
                  else setCountText(text)
                }}
              />
            </div>
          </div>

          {warning ? (
            <div className="banner banner-warning">⚠ {warning}</div>
          ) : info.warn_above === null ? (
            <p className="dep-info">
              Couldn&apos;t read this machine&apos;s RAM, so there&apos;s no size warning. Large
              counts are at your own risk.
            </p>
          ) : null}

          <div className="action-bar">
            <button
              className="btn btn-primary"
              disabled={
                busy || !exactClass || count === null || (mode === 'at_location' && !location)
              }
              onClick={start}
            >
              <HiOutlinePlay aria-hidden="true" />
              Start test
            </button>
          </div>
        </>
      )}
    </div>
  )
}

// Every URL segment the loaded payload is an answer to, so swapping a name URL
// for the canonical id URL doesn't refetch the same mod (or blank the page).
function refsFor(mod) {
  if (!mod) return new Set()
  const name = mod.name || ''
  return new Set([mod.id, name, name.replace(/^@/, '')].filter(Boolean))
}

export default function ModPage() {
  const { id } = useParams()
  const { pathname } = useLocation()
  const navigate = useNavigate()
  const pushToast = useToast()
  const confirm = useConfirm()
  const { startJob, trackJob } = useJobs()

  const [mod, setMod] = useState(null)
  const [error, setError] = useState('')
  const [help, setHelp] = useState({ actions: {} })
  const [busy, setBusy] = useState(false)
  // Both live in their confirm dialog — the orphan-deps choice is per
  // uninstall, and the install flags are asked by InstallOptionsModal.
  const [removeDeps, setRemoveDeps] = useState(false)
  const [confirmUninstall, setConfirmUninstall] = useState(false)
  const [installPrompt, setInstallPrompt] = useState(null)
  // Path of the mod file open in the right-hand drawer, or null.
  const [viewing, setViewing] = useState(null)
  const answersRef = useRef(new Set())
  const spawnTest = useSpawnTest(mod?.installed && mod?.listed ? mod.name : null)

  // The id in the URL now, so a slow answer for the mod just left is dropped.
  const idRef = useRef(id)
  idRef.current = id

  const load = useCallback(async () => {
    try {
      const data = await api.get(`/api/mods/${encodeURIComponent(id)}`)
      if (idRef.current !== id) return
      answersRef.current = refsFor(data)
      setMod(data)
      setError('')
    } catch (err) {
      if (idRef.current !== id) return
      answersRef.current = new Set()
      setMod(null)
      setError(err.message || 'Failed to load mod')
    }
  }, [id])

  useEffect(() => {
    // Already showing this mod under its other reference — the redirect below
    // just changed how the URL spells it.
    if (answersRef.current.has(id)) return
    setMod(null)
    setError('')
    setViewing(null)
    load()
  }, [id, load])

  // Canonical URL is the workshop id. Replace (not push) so Back still leaves
  // the page instead of bouncing through the name URL. Only once the mod on
  // screen answers this URL: right after a link to another mod, `mod` is still
  // the previous one, and redirecting then sent the URL back to it.
  useEffect(() => {
    if (mod?.id && mod.id !== id && answersRef.current.has(id)) {
      navigate(modLink(mod.id), { replace: true })
    }
  }, [mod, id, navigate])

  useEffect(() => {
    api.get('/api/mods/help').then(setHelp).catch(() => {})
  }, [])

  const handleWsMessage = useCallback(
    (msg) => {
      if (msg.type === 'mods_changed') load()
    },
    [load],
  )
  useWs(handleWsMessage)

  async function toggleEnabled() {
    setBusy(true)
    try {
      await api.post(`/api/mods/${encodeURIComponent(mod.name)}/${mod.enabled ? 'disable' : 'enable'}`)
      await load()
    } catch (err) {
      pushToast(err.message || 'Failed to toggle mod', 'error')
    } finally {
      setBusy(false)
    }
  }

  async function run(options) {
    try {
      await startJob({ ...options, onStatusChange: (status) => status === 'success' && load() })
    } catch (err) {
      pushToast(err.message || 'Failed to start job', 'error')
    }
  }

  // Scoped re-apply: rewrites this mod's XML only, but overwrites whatever is
  // there now — so confirm before running it.
  async function reconfigureXml() {
    const ok = await confirm({
      title: `Re-configure ${mod.name} XML?`,
      message: `Rewrites ${mod.name}'s mission XML: its custom/ economy files are regenerated and its merged blocks in the mission files are stripped and re-inserted from the mod's current XML. Manual edits to those are lost. Other mods are not touched.`,
      confirmLabel: 'Re-configure',
    })
    if (!ok) return
    run({
      endpoint: `/api/mods/${encodeURIComponent(mod.name)}/xml/merge`,
      name: `Re-configure ${mod.name} XML`,
      kind: 'mods_xml_merge',
    })
  }

  // The bulk undo for the Mod XML page's per-file Disable/Delete. Kept apart
  // from Re-configure on purpose: a merge that dropped those records would undo
  // them on every install and mod toggle, which is what they exist to survive.
  async function restoreXml() {
    const ok = await confirm({
      title: `Restore ${mod.name}'s turned-off XML?`,
      message: `Turns ${xml.off} XML file${xml.off === 1 ? '' : 's'} you disabled or deleted back on, then re-applies ${mod.name}'s mission XML. Manual XML files you forked from those files are left alone — disable or delete them yourself if you don't want both.`,
      confirmLabel: 'Restore',
    })
    if (!ok) return
    try {
      const result = await api.post(`/api/mods/${encodeURIComponent(mod.name)}/xml/restore`)
      pushToast(
        result.applied
          ? `Restored ${result.restored} file${result.restored === 1 ? '' : 's'}.`
          : `Restored ${result.restored} file${result.restored === 1 ? '' : 's'} — enable the mod to apply them.`,
        'info'
      )
      load()
    } catch (err) {
      pushToast(err.message || 'Restore failed', 'error')
    }
  }

  // Asks the install options first (same dialog the Mods page uses), then runs
  // the job with whatever it answered. `mode` 'reinstall' makes the dialog add
  // `reinstall: true`, which deletes the workshop cache copy before the download.
  function install(mode = 'install') {
    // By id when we have one (the endpoint caches id → name); by name otherwise.
    const request = mod.id
      ? { names: [], ids: [mod.id], id_names: mod.name ? { [mod.id]: mod.name } : {} }
      : { names: [mod.name], ids: [], id_names: {} }
    setInstallPrompt({ ...request, mode })
  }

  function startInstall(flags) {
    const { names, ids, id_names, mode } = installPrompt
    setInstallPrompt(null)
    run({
      endpoint: '/api/mods/install',
      body: { names, ids, id_names, ...flags },
      name: `${mode === 'reinstall' ? 'Reinstall' : 'Install'} ${mod.name || mod.id}`,
      kind: 'mods_install',
    })
  }

  // Rendered as a ConfirmModal rather than useConfirm(): the orphaned-deps
  // choice belongs inside the confirm, and useConfirm takes no children.
  function uninstall() {
    setRemoveDeps(false)
    setConfirmUninstall(true)
  }

  function startUninstall() {
    setConfirmUninstall(false)
    run({
      endpoint: '/api/mods/uninstall',
      body: { names: [mod.name], remove_deps: removeDeps },
      name: `Uninstall ${mod.name}`,
      kind: 'mods_uninstall',
    })
  }

  // Goes through the shared cleanup job — it also drops the item from SteamCMD's
  // workshop manifest, without which every later download fails.
  async function removeFromCache() {
    const ok = await confirm({
      title: 'Remove from workshop cache?',
      message: `Deletes the downloaded copy at ${mod.workshop_path}. Installed server files are not touched.`,
      confirmLabel: 'Remove',
      danger: true,
    })
    if (!ok) return
    try {
      const data = await api.post('/api/cleanup/workshop', { confirm: true, paths: [mod.workshop_path] })
      if (data?.job_id) {
        trackJob(data.job_id, {
          name: 'Remove workshop mods',
          kind: 'cleanup',
          onStatusChange: (status) => status === 'success' && load(),
        })
      }
    } catch (err) {
      pushToast(err.message || 'Failed to remove from cache', 'error')
    }
  }

  // The breadcrumb's own label can only see the URL param (a workshop id) —
  // hand it the heading instead. Empty while loading: it falls back to the param.
  const title = mod ? mod.meta?.name || mod.name || mod.id : ''
  useCrumbTitle(pathname, title)

  if (error) {
    return (
      <div className="page mod-page">
        <div className="page-header">
          <h1>{id}</h1>
        </div>
        <div className="card">
          <PanelNote>{error}</PanelNote>
          <Link className="btn btn-ghost" to="/mods">
            Back to Mods
          </Link>
        </div>
      </div>
    )
  }

  if (!mod) return <div className="page mod-page" />

  const a = mod.actions || {}
  const xml = mod.xml
  // Author/version/timestamp/overview are parsed out of the mod's own
  // mod.cpp/meta.cpp, so they exist only while the files are on this box (the
  // installed copy, else the downloaded workshop copy) — same source as the
  // hover tooltip on the Mods page.
  const homepage = homepageUrl(mod.meta?.action)
  const ts = mod.timestamps || {}
  const locallyAvailable = mod.installed || mod.cached
  // Shown inside the uninstall confirm — the moment the warning matters.
  const wipeWarning = wipeWarningFor({ names: [mod.name], ids: [mod.id] })
  // A test of this mod is the thing to see first, so its card moves to the top.
  const spawnCard =
    mod.installed && mod.listed && (spawnTest.info || spawnTest.error) ? (
      <SpawnTestCard key={mod.name} modName={mod.name} {...spawnTest} />
    ) : null
  const spawnFirst = spawnTest.info?.active?.mod === mod.name

  return (
    <div className={`page mod-page${viewing ? ' has-side-drawer' : ''}`}>
      <div className="page-header">
        <h1>{title}</h1>
        <div className="action-bar">
          {mod.workshop_url && (
            <a className="btn btn-ghost" href={mod.workshop_url} target="_blank" rel="noopener noreferrer">
              <HiOutlineArrowTopRightOnSquare aria-hidden="true" />
              Steam Workshop
            </a>
          )}
          {a.install && (
            <NeedsSteam tip={help.actions?.install} className="bare">
              <button className="btn btn-primary" onClick={() => install()}>
                <HiOutlineArrowDownTray aria-hidden="true" />
                Install
              </button>
            </NeedsSteam>
          )}
          {a.reinstall && (
            <NeedsSteam tip={help.actions?.reinstall} className="bare">
              <button className="btn btn-ghost" onClick={() => install('reinstall')}>
                <HiOutlineArrowPath aria-hidden="true" />
                Reinstall
              </button>
            </NeedsSteam>
          )}
          {(a.enable || a.disable) && (
            <Tooltip text={help.actions?.enable_disable} className="bare">
              <button className="btn btn-ghost" disabled={busy} onClick={toggleEnabled}>
                <HiOutlinePower aria-hidden="true" />
                {mod.enabled ? 'Disable' : 'Enable'}
              </button>
            </Tooltip>
          )}
          {a.reconfigure && (
            <Tooltip text={help.actions?.reconfigure} className="bare">
              <button className="btn btn-ghost" onClick={reconfigureXml}>
                <HiOutlineCog6Tooth aria-hidden="true" />
                Re-configure XML
              </button>
            </Tooltip>
          )}
          {a.uninstall && (
            <Tooltip text={help.actions?.uninstall} className="bare">
              <button className="btn btn-danger" onClick={uninstall}>
                <HiOutlineTrash aria-hidden="true" />
                Uninstall
              </button>
            </Tooltip>
          )}
        </div>
      </div>

      <PageIntro page="/mods/:id" />

      <SteamLoginGate />

      {/* The cached copy is what a download or a reinstall acts on, so it comes first. */}
      {mod.cached && (
        <div className="card">
          <div className="card-header">
            <h2>Workshop cache</h2>
          </div>
          <dl className="kv-grid">
            <Row label="Downloaded to">
              <code>{mod.workshop_path}</code>
            </Row>
          </dl>
          <PanelNote>{help.actions?.remove_cache}</PanelNote>
          <div className="action-bar">
            <button className="btn btn-danger" onClick={removeFromCache}>
              <HiOutlineTrash aria-hidden="true" />
              Remove from workshop cache
            </button>
          </div>
        </div>
      )}

      {spawnFirst && spawnCard}

      {/* Desktop: Overview and Dependencies side by side, 50/50 (.mod-page-pair, index.css). */}
      <div className={mod.listed ? 'mod-page-pair' : undefined}>
        <div className="card">
          <div className="card-header">
            <h2>Overview</h2>
            <span className="modxml-pills">
              <span className={`status-pill status-pill-${mod.installed ? 'success' : 'neutral'}`}>
                {mod.installed ? 'Installed' : mod.listed ? 'Listed, not installed' : 'Not installed'}
              </span>
              {mod.listed && (
                <span className={`status-pill status-pill-${mod.enabled ? 'success' : 'pending'}`}>
                  {mod.enabled ? 'Enabled' : 'Disabled'}
                </span>
              )}
              {mod.cached && <span className="status-pill status-pill-neutral">In workshop cache</span>}
              {mod.update && <span className="status-pill status-pill-running">Update available</span>}
            </span>
          </div>

          <dl className="kv-grid">
            <Row label="Name">{mod.name || <span className="dep-info">unknown</span>}</Row>
            <Row label="Workshop ID">
              <WorkshopLink id={mod.id} />
            </Row>
            {mod.meta?.author && <Row label="Author">{mod.meta.author}</Row>}
            {/* A credit line Zedstead keeps for a few mods (seed_data.MOD_CREDITS). */}
            {mod.credit && (
              <Row label="Credit">
                {mod.credit.text}{' '}
                <a className="mod-link" href={mod.credit.link_url} target="_blank" rel="noopener noreferrer">
                  {mod.credit.link_label} ↗
                </a>
              </Row>
            )}
            {mod.meta?.version && <Row label="Version">{mod.meta.version}</Row>}
            <TimeRow
              label="Built"
              tip={
                'When the mod author packed this build — meta.cpp’s own timestamp.\n' +
                'Read from the installed copy, else the workshop cache copy.'
              }
              epoch={ts.built}
            />
            <TimeRow
              label="Workshop revision"
              tip={
                'Publish date of the workshop revision SteamCMD downloaded, from the\n' +
                'workshop manifest (appworkshop_221100.acf). This is the mod version\n' +
                'you are running — compare it with “Workshop updated” below.'
              }
              epoch={ts.workshop_updated}
            />
            <TimeRow
              label="Downloaded"
              tip="When SteamCMD last wrote this mod into the workshop cache on this box."
              epoch={ts.downloaded}
            />
            <TimeRow
              label="Installed"
              tip="When the mod was last copied from the workshop cache into the server directory."
              epoch={ts.installed}
            />
            {homepage && (
              <Row label="Homepage">
                <a className="mod-link" href={homepage} target="_blank" rel="noopener noreferrer">
                  {homepage} ↗
                </a>
              </Row>
            )}
            {mod.installed && <Row label="Size on server">{formatBytes(mod.size_bytes)}</Row>}
            {mod.cached && <Row label="Size in cache">{formatBytes(mod.cached_size_bytes)}</Row>}
            {mod.order !== null && (
              <Row label="Load order">{`#${mod.order + 1} of ${mod.order_total}`}</Row>
            )}
            {mod.update && (
              <Row
                label={
                  <Tooltip text="Latest revision on the Steam Workshop, from the last update check. Newer than the copy on this box — re-install the mod to pick it up.">
                    Workshop updated
                  </Tooltip>
                }
              >
                {mod.update.remote_updated
                  ? `${formatWhen(mod.update.remote_updated)} — newer than the installed copy`
                  : 'newer than the installed copy'}
              </Row>
            )}
          </dl>

          {mod.meta?.overview && <p className="page-intro">{mod.meta.overview}</p>}
          {!mod.meta && !locallyAvailable && (
            <p className="dep-info">
              Author, version and description are read from the mod&apos;s own files — they appear once
              the mod is downloaded to this box.
            </p>
          )}
        </div>

        {mod.listed && (
          <div className="card">
            <div className="card-header">
              <h2>Dependencies</h2>
            </div>
            <dl className="kv-grid">
              <Row label="Requires">
                <ModList names={mod.requires} empty="none" />
              </Row>
              <Row label="Required by">
                <ModList names={mod.required_by} empty="none" />
              </Row>
            </dl>
          </div>
        )}
      </div>

      {xml && (xml.files > 0 || xml.manual > 0) && (
        <div className="card">
          <div className="card-header">
            <h2>Mission XML</h2>
            <Link className="card-link" to={configLink('mod-xml')}>
              Custom config →
            </Link>
          </div>
          <PanelNote>{help.actions?.xml}</PanelNote>
          <dl className="kv-grid">
            <Row label="Files">{xml.files}</Row>
            {xml.linked > 0 && <Row label="Linked">{xml.linked}</Row>}
            {xml.merged > 0 && <Row label="Merged">{xml.merged}</Row>}
            {xml.pending > 0 && <Row label="Not applied yet">{xml.pending}</Row>}
            {xml.disabled > 0 && <Row label="Disabled">{xml.disabled}</Row>}
            {xml.off > 0 && <Row label="Turned off by you">{xml.off}</Row>}
            {xml.manual > 0 && (
              <Row label="Custom config (Manual XML files)">
                <Link to={configLink('manual-xml-links')}>
                  {xml.manual} file{xml.manual === 1 ? '' : 's'}
                </Link>
              </Row>
            )}
            {xml.skipped > 0 && <Row label="Skipped">{xml.skipped}</Row>}
          </dl>
          {xml.off > 0 && (
            <p className="dep-info">
              Re-configure leaves the {xml.off} file{xml.off === 1 ? '' : 's'} you turned off
              alone. Restore brings them back.
            </p>
          )}
          {a.reconfigure || xml.off > 0 ? (
            <div className="action-bar">
              {a.reconfigure && (
                <button className="btn btn-ghost" onClick={reconfigureXml}>
                  <HiOutlineCog6Tooth aria-hidden="true" />
                  Re-configure XML
                </button>
              )}
              {xml.off > 0 && (
                <button className="btn btn-ghost" onClick={restoreXml}>
                  <RestoreIcon />
                  Restore {xml.off} file{xml.off === 1 ? '' : 's'}
                </button>
              )}
            </div>
          ) : xml.actionable > 0 && !mod.enabled ? (
            <p className="dep-info">Enable the mod to re-apply its XML.</p>
          ) : null}
        </div>
      )}

      {spawnCard && !spawnFirst && spawnCard}

      {locallyAvailable && <ModFilesCard modRef={id} viewing={viewing} onView={setViewing} />}

      <InstallOptionsModal
        request={installPrompt}
        onConfirm={startInstall}
        onCancel={() => setInstallPrompt(null)}
      />

      <ConfirmModal
        open={confirmUninstall}
        title={`Uninstall ${mod.name}?`}
        message="Removes the mod from the mod list, deletes its server folder and strips its mission XML."
        confirmLabel="Uninstall"
        danger
        onConfirm={startUninstall}
        onCancel={() => setConfirmUninstall(false)}
      >
        {wipeWarning && <p className="banner banner-warning">{wipeWarning}</p>}
        <OrphanDepsField checked={removeDeps} onChange={setRemoveDeps} />
      </ConfirmModal>

      {viewing && (
        <SideDrawer
          title={viewing}
          badge="read-only"
          badgeTone="readonly"
          subtitle="The mod's own file. Every workshop update overwrites it."
          storageKey={DRAWER_WIDTH_KEY}
          onClose={() => setViewing(null)}
        >
          <ModFileViewer modRef={id} file={viewing} />
        </SideDrawer>
      )}
    </div>
  )
}
