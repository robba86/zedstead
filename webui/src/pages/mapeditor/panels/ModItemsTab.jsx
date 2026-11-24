// Mod items tab: set up an installed mod's items from the map editor, without
// opening the mod's page (ROADMAP "Add a mod's items from the Map Editor").
//
// Pick a mod, then one of its classes from the same list the mod page's Spawn
// test offers (GET /api/mods/{name}/spawn-test, useSpawnTest.js). A class
// then offers four things:
//   - Spawn points: an events.xml event that spawns the class, its points in
//     cfgeventspawns.xml, added with the Spawns tab's own click mode. The
//     Central Economy spawns it again after it is taken.
//   - Objects: Zedstead's Object Spawner file (app/spawn_editor/object_spawner.py),
//     an object at every start that is never removed.
//   - Test: the Spawn test's "at a location" mode, temporary, as the mod
//     page's "Pick in Map Editor" does.
//   - Its types.xml entry: the Types page's fields for that one entry, saved
//     through the same PUT /api/types/entry (typesEntry.js).
// The mod page's Spawn test card stays as it is.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../../api'
import { useWs } from '../../../ws'
import Select from '../../../components/Select.jsx'
import SearchInput from '../../../components/SearchInput.jsx'
import NestedParts from '../../../components/NestedParts.jsx'
import { Switch } from '../../../components/FlagSwitch.jsx'
import { ItemName } from '../../../components/ItemIcon.jsx'
import ListSection from '../../../components/ListSection.jsx'
import ListRow, { RowTag } from '../../../components/ListRow.jsx'
import CountBadge from '../../../components/CountBadge.jsx'
import RowAction from '../../../components/RowAction.jsx'
import PanelNote from '../../../components/PanelNote.jsx'
import { useToast } from '../../../components/Toast.jsx'
import { useConfirmDiscard, useUnsavedChanges } from '../../../useUnsavedChanges.js'
import { LINKS, configLink, editorLink, modLink } from '../../../crossLinks'
import { useCan } from '../../../role.jsx'
import { MAX_CLASS_SUGGESTIONS, filterClasses, useSpawnTest } from '../../../useSpawnTest.js'
import { NUMBER_CELLS, NUMBER_LABELS, shortPath, typesEntryBody } from '../../../typesEntry.js'
import { useEditor } from '../state/MapEditorProvider'
import { meApi } from '../api'
import { MAP_ADD_MODES } from '../addModes'
import { tooltipAttrs } from '../../../components/Tooltip.jsx'
import { useConfirm } from '../../../components/useConfirm.jsx'
import EditorLink from './EditorLink.jsx'
import useStoredOpen from '../state/useStoredOpen.js'
import { objectKey } from '../leaflet/useObjectSpawnerLayer'
import { gameToLatLng } from '../coords'
import {
  HiOutlineDocumentDuplicate,
  HiOutlineEye,
  HiOutlineEyeSlash,
  HiOutlinePencilSquare,
  HiOutlineTrash,
} from 'react-icons/hi2'

// The unsaved-changes key of the types.xml form, so Back and a tab switch ask.
const TYPES_FORM = 'mapeditor-mod-item-types'

// The spawn test's count when the bar opens. The bar's own box changes it.
const DEFAULT_COUNT = '10'

/**
 * The installed mods that add item classes, loaded again on `mods_changed`.
 *
 * GET /api/mods lists them at once. GET /api/mods/spawn-test/class-counts
 * then drops each mod that adds no class (the first call parses every mod's
 * PBOs, so it can take a while). A mod the counts do not name stays, and an
 * update that adds items to a mod brings it back on the next load. `hidden`
 * is how many were dropped, null while the counts load.
 */
function useInstalledMods() {
  const [mods, setMods] = useState(null)
  const [hidden, setHidden] = useState(null)
  const [error, setError] = useState('')
  const load = useCallback(async () => {
    let installed
    try {
      const data = await api.get('/api/mods')
      installed = (data.mods || []).filter((m) => m.installed)
      setMods(installed)
      setError('')
    } catch (err) {
      setError(err.message || 'Failed to load the mods')
      return
    }
    try {
      const { counts } = await api.get('/api/mods/spawn-test/class-counts')
      const withItems = installed.filter((m) => counts?.[m.name] !== 0)
      setMods(withItems)
      setHidden(installed.length - withItems.length)
    } catch {
      setHidden(0) // keep every mod: the class list says so per mod
    }
  }, [])
  useEffect(() => {
    load()
  }, [load])
  useWs(
    useCallback(
      (msg) => {
        if (msg.type === 'mods_changed') load()
      },
      [load]
    )
  )
  return { mods, hidden, error }
}

/**
 * Every types.xml item name, lowercased, from GET /api/types/categories, so
 * the class list can mark the classes the economy knows. Loaded again on
 * `configs_changed`, which a types save broadcasts.
 */
function useTypeNames() {
  const [names, setNames] = useState(null)
  const load = useCallback(async () => {
    try {
      const data = await api.get('/api/types/categories')
      setNames(new Set(Object.keys(data.categories || {})))
    } catch {
      setNames(new Set())
    }
  }, [])
  useEffect(() => {
    load()
  }, [load])
  useWs(
    useCallback(
      (msg) => {
        if (msg.type === 'configs_changed') load()
      },
      [load]
    )
  )
  return names
}

export default function ModItemsTab() {
  const { mods, hidden, error: modsError } = useInstalledMods()
  const typeNames = useTypeNames()
  const [modName, setModName] = useState('')
  const [picked, setPicked] = useState('')
  // The list's filter lives here, so Back from a class finds it as it was.
  const [query, setQuery] = useState('')
  const [onlyTypes, setOnlyTypes] = useState(false)
  const spawnTest = useSpawnTest(modName || null)
  const { ui } = useEditor()
  const confirmDiscard = useConfirmDiscard()

  // A map click on a placed object shows the Placed objects list, so a class's
  // page closes first (asking when its types.xml form has edits).
  const selectSeq = ui.selectedObject?.seq
  useEffect(() => {
    if (!selectSeq) return
    let live = true
    confirmDiscard(TYPES_FORM).then((ok) => live && ok && setPicked(''))
    return () => {
      live = false
    }
    // confirmDiscard is not stable; only a new selection asks.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectSeq])

  if (modsError) return <PanelNote>{modsError}</PanelNote>
  if (!mods) return <PanelNote>Loading mods…</PanelNote>
  if (!mods.length) {
    return (
      <div className="me-mod-items">
        <PlacedObjects />
        <PanelNote link={{ to: configLink('mods'), label: 'Install one on the Mods page' }}>
          {hidden ? 'No installed mod adds item classes.' : 'No mod is installed.'}
        </PanelNote>
      </div>
    )
  }

  if (modName && picked) {
    return (
      <ClassDetail
        mod={modName}
        classname={picked}
        info={spawnTest.info}
        onBack={() => setPicked('')}
      />
    )
  }

  return (
    <div className="me-mod-items">
      <PlacedObjects />
      <div className="me-mod-items-pick">
        <Select
          className="field"
          value={modName}
          placeholder="Pick an installed mod"
          aria-label="Mod"
          onChange={(name) => {
            setModName(name)
            setPicked('')
            setQuery('')
          }}
          options={mods.map((m) => ({
            value: m.name,
            label: m.name,
            description: m.enabled ? undefined : 'disabled',
          }))}
        />
        {hidden === null && <PanelNote>Counting each mod&apos;s items…</PanelNote>}
        {hidden > 0 && (
          <PanelNote>
            {hidden} {hidden === 1 ? 'mod adds' : 'mods add'} no items, so the list leaves{' '}
            {hidden === 1 ? 'it' : 'them'} out.
          </PanelNote>
        )}
      </div>
      {modName && (
        <ClassList
          mod={modName}
          spawnTest={spawnTest}
          typeNames={typeNames}
          query={query}
          setQuery={setQuery}
          onlyTypes={onlyTypes}
          setOnlyTypes={setOnlyTypes}
          onPick={setPicked}
        />
      )}
    </div>
  )
}

/**
 * The end of a long list: calls *onVisible* once when it scrolls into view,
 * and is a button too, for a browser with no IntersectionObserver. The caller
 * gives it a new key per page, so each page arms a fresh observer.
 */
function LoadMore({ onVisible, children }) {
  const ref = useRef(null)
  // The latest callback, so a parent render never arms a second observer.
  const onVisibleRef = useRef(onVisible)
  onVisibleRef.current = onVisible
  useEffect(() => {
    const node = ref.current
    if (!node || typeof IntersectionObserver === 'undefined') return undefined
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) {
        observer.disconnect()
        onVisibleRef.current()
      }
    })
    observer.observe(node)
    return () => observer.disconnect()
  }, [])
  return (
    <button ref={ref} type="button" className="btn btn-ghost btn-small me-mod-item-more" onClick={onVisible}>
      {children}
    </button>
  )
}

/** The picked mod's classes, filtered by a search box and a types.xml switch. */
function ClassList({ mod, spawnTest, typeNames, query, setQuery, onlyTypes, setOnlyTypes, onPick }) {
  const classes = spawnTest.info?.classes
  const inTypes = useCallback((c) => Boolean(typeNames?.has(c.toLowerCase())), [typeNames])
  // How many rows are drawn: one more page each time the end of the list
  // scrolls into view, back to one page when the filter changes.
  const [limit, setLimit] = useState(MAX_CLASS_SUGGESTIONS)
  useEffect(() => setLimit(MAX_CLASS_SUGGESTIONS), [mod, query, onlyTypes])
  const { items, overflow, typed } = useMemo(() => {
    const all = classes || []
    const shown = onlyTypes ? all.filter(inTypes) : all
    return { ...filterClasses(shown, query.trim().toLowerCase(), limit), typed: all.filter(inTypes).length }
  }, [classes, onlyTypes, inTypes, query, limit])

  if (spawnTest.error) return <PanelNote>{spawnTest.error}</PanelNote>
  if (!classes) return <PanelNote>Reading {mod}&apos;s classes…</PanelNote>
  if (!classes.length) {
    return (
      <PanelNote>
        {mod} adds no item classes. Its PBOs define no CfgVehicles, CfgWeapons or CfgMagazines class.
      </PanelNote>
    )
  }

  return (
    <>
      <div className="me-event-filter">
        <SearchInput
          boxClassName="search-field-wide"
          className="me-event-search"
          type="text"
          placeholder={`Filter ${classes.length} classes...`}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>
      <div className="me-mod-items-switch">
        <Switch
          checked={onlyTypes}
          onChange={setOnlyTypes}
          help="Show only the classes that have a types.xml entry, which the Central Economy can spawn."
        >
          Only in types.xml ({typed})
        </Switch>
      </div>
      <div className="me-event-list">
        {items.map((c) => (
          <ListRow
            key={c}
            compact
            name={<ItemName name={c} />}
            meta={inTypes(c) && <RowTag tip="This class has a types.xml entry.">types</RowTag>}
            onClick={() => onPick(c)}
          />
        ))}
        {!items.length && <PanelNote>No class matches.</PanelNote>}
        {overflow > 0 && (
          <LoadMore key={limit} onVisible={() => setLimit((n) => n + MAX_CLASS_SUGGESTIONS)}>
            {overflow} more
          </LoadMore>
        )}
      </div>
    </>
  )
}

/**
 * One class: place it for good (a Central Economy event, or an Object
 * Spawner object), try it with a temporary spawn test, and edit its
 * types.xml entry.
 */
function ClassDetail({ mod, classname, info, onBack }) {
  const confirmDiscard = useConfirmDiscard()
  return (
    <>
      <button
        type="button"
        className="me-back-btn"
        onClick={async () => {
          if (await confirmDiscard(TYPES_FORM)) onBack()
        }}
      >
        ← {mod}&apos;s classes
      </button>
      <div className="me-mod-item-detail">
        <div className="me-event-detail-header me-mod-item-head">
          <span className="me-event-detail-name">
            <ItemName name={classname} />
          </span>
          <Link className="me-mod-item-mod" to={modLink(mod)}>
            {mod}
          </Link>
        </div>
        <EventPlacement classname={classname} />
        <ObjectPlacement classname={classname} />
        <SpawnTestPlacement mod={mod} classname={classname} info={info} />
        <TypesEntryForm key={classname} classname={classname} />
      </div>
    </>
  )
}

/**
 * Lasting spawn points through the Central Economy: an events.xml event whose
 * child is the class, with its positions in cfgeventspawns.xml. An event that
 * already spawns the class gets more points; otherwise a new one is made
 * (POST /events with `child`). Points are then added with the Spawns tab's
 * own click mode, so they show, move and delete like every event's.
 */
function EventPlacement({ classname }) {
  const { dispatch, url, ui } = useEditor()
  const pushToast = useToast()
  const [events, setEvents] = useState(null)
  const [name, setName] = useState(`Item${classname}`)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try {
      setEvents(await meApi.get(`/events-for-class/${encodeURIComponent(classname)}`))
    } catch {
      setEvents([])
    }
  }, [classname])
  useEffect(() => {
    load()
  }, [load])
  useWs(
    useCallback(
      (msg) => {
        if (
          msg.type === 'mapeditor_events_changed' ||
          msg.type === 'mapeditor_spawns_changed' ||
          msg.type === 'mapeditor_reloaded'
        ) {
          load()
        }
      },
      [load]
    )
  )

  const adding = ui.addMode?.kind === 'event' ? ui.addMode.event : null

  function addPoints(event) {
    if (adding === event) {
      dispatch({ type: 'EXIT_ADD' })
      return
    }
    // Placing into a layer that is switched off would look like a no-op.
    url.setLayer('spawns', true)
    dispatch({ type: 'ENTER_EVENT_ADD', payload: { event } })
  }

  async function create() {
    const event = name.trim()
    if (!event || busy) return
    setBusy(true)
    try {
      await meApi.post('/events', { name: event, child: classname })
      await load()
      addPoints(event)
      pushToast(`${event} created. Click the map to add its spawn points.`, 'success')
    } catch (err) {
      pushToast(err.message || 'Could not create the event', 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <ListSection title="Spawn points (Central Economy)" />
      <PanelNote>
        An event in events.xml spawns {classname} at its points in cfgeventspawns.xml, and spawns
        it again after it is taken or destroyed. A new event starts with nominal 1, so one point
        holds it at a time. Raise nominal, max and the child&apos;s max in the Spawns tab to fill
        more.
      </PanelNote>
      {events === null && <PanelNote>Loading…</PanelNote>}
      {events?.map((e) => (
        <ListRow
          key={e.name}
          name={e.name}
          meta={<CountBadge value={e.spawn_count} />}
          actions={
            <button type="button" className="btn btn-ghost btn-small" onClick={() => addPoints(e.name)}>
              {adding === e.name ? 'Stop adding' : 'Add points'}
            </button>
          }
        />
      ))}
      <div className="me-mod-item-row">
        <input
          className="field me-mod-item-input"
          type="text"
          spellCheck={false}
          value={name}
          onChange={(e) => setName(e.target.value)}
          aria-label="New event name"
          {...tooltipAttrs(
            'The new event\'s name. Bohemia names events by kind: Item…, Vehicle…, Animal…, Infected…, Static….'
          )}
        />
        <button
          type="button"
          className="btn btn-ghost btn-small"
          disabled={busy || !name.trim()}
          onClick={create}
        >
          New event
        </button>
      </div>
    </>
  )
}

/**
 * Lasting objects through DayZ's Object Spawner: Zedstead's own file, listed in
 * cfggameplay.json (app/spawn_editor/object_spawner.py). An object is always
 * there and is never respawned or cleaned up, which suits a building or a prop.
 * Placed with the Object click mode (MAP_ADD_MODES.object), on the ground
 * unless the bar's Y box says otherwise.
 */
/**
 * serverDZ.cfg's enableCfgGameplayFile as a switch, with a link to the same
 * field in the Server form. DayZ reads cfggameplay.json, and so the Object
 * Spawner files, only while it is 1. The write is the Server form's own save
 * (PUT /object-spawner/gameplay-file), so admins only.
 */
function GameplayFileSwitch({ objects, reload }) {
  const pushToast = useToast()
  const isAdmin = useCan('admin')
  const confirm = useConfirm()
  const [busy, setBusy] = useState(false)
  const on = objects?.gameplay_on
  if (on === null || on === undefined) return null

  async function toggle(next) {
    if (!next) {
      const yes = await confirm({
        title: 'Turn off cfggameplay.json?',
        message:
          'DayZ then ignores the whole file, not only these objects: stamina, base building, the map and compass, night lighting and spawn gear all fall back to the game defaults. To remove only an object, use Remove in the list below.',
        confirmLabel: 'Turn off',
        danger: true,
      })
      if (!yes) return
    }
    setBusy(true)
    try {
      await meApi.put('/object-spawner/gameplay-file', { on: next })
      pushToast(
        `enableCfgGameplayFile is ${next ? '1' : '0'}. DayZ reads it at the next server start.`,
        'success'
      )
    } catch (err) {
      pushToast(err.message || 'Save failed', 'error')
    }
    setBusy(false)
    reload.objects()
  }

  return (
    <div className="me-mod-item-row">
      <Switch
        checked={on}
        onChange={toggle}
        disabled={!isAdmin || busy}
        help={
          isAdmin
            ? 'serverDZ.cfg enableCfgGameplayFile. While it is off, DayZ ignores cfggameplay.json, its Object Spawner files and every other setting in it. The change takes effect at the next server start.'
            : 'Only an admin can change serverDZ.cfg.'
        }
      >
        DayZ reads cfggameplay.json
      </Switch>
      <EditorLink
        to={editorLink('serverdz', { q: 'enableCfgGameplayFile' })}
        tip="Open enableCfgGameplayFile in the Server config"
        label="Open enableCfgGameplayFile in the Server config"
      />
    </div>
  )
}

/** Remove one Object Spawner object, after a confirm; the Objects layer reloads. */
function useRemoveObject() {
  const { objects, reload } = useEditor()
  const pushToast = useToast()
  const confirm = useConfirm()
  return async function remove(obj) {
    const yes = await confirm({
      title: `Remove this ${obj.name}?`,
      message: `Removes the object at ${obj.x.toFixed(1)}, ${obj.z.toFixed(1)} from ${objects.file}. It is gone after the next server start.`,
      confirmLabel: 'Remove',
      danger: true,
    })
    if (!yes) return
    try {
      await meApi.del(objectPath(obj))
    } catch (err) {
      pushToast(err.message || 'Remove failed', 'error')
    }
    reload.objects()
  }
}

/** The route for one object: its index in its file, the name it showed, and which file. */
function objectPath(obj, action = '') {
  const query = `expect=${encodeURIComponent(obj.name)}${obj.disabled ? '&disabled=true' : ''}`
  return `/object-spawner/${obj.index}${action}?${query}`
}

/**
 * Disable a live object (it moves to a file DayZ never reads) or enable a
 * disabled one. The selection follows the object to its new place.
 */
function useToggleObject() {
  const { dispatch, reload, ui } = useEditor()
  const pushToast = useToast()
  return async function toggle(obj) {
    try {
      const row = await meApi.post(objectPath(obj, '/toggle'))
      const wasSelected = ui.selectedObject && objectKey(ui.selectedObject) === objectKey(obj)
      if (wasSelected) dispatch({ type: 'SELECT_OBJECT', payload: { disabled: row.disabled, index: row.index } })
      pushToast(`${obj.name} ${row.disabled ? 'disabled' : 'enabled'}. It takes effect at the next server start.`, 'success')
    } catch (err) {
      pushToast(err.message || 'Save failed', 'error')
    }
    reload.objects()
  }
}

/** Place a copy of *obj*: the same class and yaw, where the next map click lands. */
function useDuplicateObject() {
  const { dispatch, url } = useEditor()
  return function duplicate(obj) {
    if (!url.layers.objects) url.setLayer('objects', true)
    dispatch({
      type: 'ENTER_MAP_ADD',
      payload: { mode: 'object', label: `Copy of ${obj.name}`, className: obj.name, yaw: obj.yaw },
    })
  }
}

// The edit form's fields: the entry's pos and ypr, in the game's own units.
const OBJECT_FIELDS = [
  ['x', 'X'],
  ['y', 'Y'],
  ['z', 'Z'],
  ['yaw', 'Yaw'],
  ['pitch', 'Pitch'],
  ['roll', 'Roll'],
]

/**
 * A placed object's position and rotation as typed numbers. Each box starts
 * from the loaded value's full text, so an untouched box sends it back as it
 * was. PUT /object-spawner/{index} keeps every other key of the entry.
 */
function ObjectEditForm({ obj, onDone }) {
  const { reload, dispatch, ui, mapInfo } = useEditor()
  const pushToast = useToast()
  const [values, setValues] = useState(() =>
    Object.fromEntries(OBJECT_FIELDS.map(([key]) => [key, String(obj[key] ?? 0)]))
  )
  const [busy, setBusy] = useState(false)
  // Y typed by hand stays through a move; otherwise a move sets it to the ground.
  const yTypedRef = useRef(false)
  const key = objectKey(obj)

  // While the form is open its marker is draggable, and a map click moves it.
  useEffect(() => {
    dispatch({ type: 'OBJECT_EDIT_START', payload: { key, lat: obj.lat, lng: obj.lng } })
    dispatch({ type: 'FLY_TO', payload: { lat: obj.lat, lng: obj.lng, zoom: 6 } })
    return () => dispatch({ type: 'OBJECT_EDIT_END', payload: key })
    // Once per open form: the object's own later reloads do not restart it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  // A drop or a click on the map: the server converts the point (never the
  // client, coords.js) and reads the ground there.
  const drag = ui.objectEdit?.key === key ? ui.objectEdit.drag : null
  useEffect(() => {
    if (!drag) return
    let live = true
    meApi
      .get(`/object-spawner/ground?lat=${drag.lat}&lng=${drag.lng}`)
      .then((p) => {
        if (!live) return
        setValues((v) => ({
          ...v,
          x: String(p.x),
          z: String(p.z),
          ...(!yTypedRef.current && p.y != null && { y: String(Math.round(p.y * 1000) / 1000) }),
        }))
      })
      .catch((err) => pushToast(err.message || 'Could not read that point', 'error'))
    return () => {
      live = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [drag?.seq])

  // Typed X or Z moves the marker (display only, coords.js).
  function setField(name, text) {
    if (name === 'y') yTypedRef.current = true
    const next = { ...values, [name]: text }
    setValues(next)
    if (name === 'x' || name === 'z') {
      const at = gameToLatLng(Number(next.x), Number(next.z), mapInfo?.bounds, mapInfo?.scale)
      if (at && Number.isFinite(at.lat) && Number.isFinite(at.lng)) {
        dispatch({ type: 'OBJECT_EDIT_MOVE', payload: at })
      }
    }
  }
  const bad = OBJECT_FIELDS.find(([key]) => values[key].trim() === '' || !Number.isFinite(Number(values[key])))

  // Y from the terrain heights at the typed X and Z, as a map click places it.
  async function toGround() {
    try {
      const { y } = await meApi.get(`/object-spawner/ground?x=${Number(values.x)}&z=${Number(values.z)}`)
      yTypedRef.current = false
      setValues((v) => ({ ...v, y: String(Math.round(y * 1000) / 1000) }))
    } catch (err) {
      pushToast(err.message || 'No ground height here', 'error')
    }
  }

  async function save(e) {
    e.preventDefault()
    if (bad) return
    setBusy(true)
    try {
      const body = Object.fromEntries(OBJECT_FIELDS.map(([key]) => [key, Number(values[key])]))
      await meApi.put(objectPath(obj), body)
      pushToast(`${obj.name} saved. It moves at the next server start.`, 'success')
      onDone()
    } catch (err) {
      pushToast(err.message || 'Save failed', 'error')
    }
    setBusy(false)
    reload.objects()
  }

  return (
    <form className="me-mod-item-edit" onSubmit={save}>
      <PanelNote>
        Drag the marker, or click the map, to move it there. Y follows the ground unless you
        typed it.
      </PanelNote>
      <div className="me-mod-item-edit-grid">
        {OBJECT_FIELDS.map(([key, label]) => (
          <label key={key} className="me-mod-item-edit-field">
            <span>{label}</span>
            <input
              className="field me-mod-item-input"
              type="text"
              inputMode="decimal"
              value={values[key]}
              onChange={(e) => setField(key, e.target.value)}
            />
          </label>
        ))}
      </div>
      {bad && <PanelNote warn>{bad[1]} must be a number.</PanelNote>}
      <div className="me-mod-item-edit-actions">
        <button
          type="button"
          className="btn btn-ghost btn-small"
          disabled={busy || Boolean(bad)}
          onClick={toGround}
          {...tooltipAttrs('Set Y to the ground height at this X and Z. A changed X or Z keeps the old Y until you do.')}
        >
          Y to ground
        </button>
        <button type="submit" className="me-detail-save-btn" disabled={busy || Boolean(bad)}>
          Save
        </button>
        <button type="button" className="btn btn-ghost btn-small" onClick={onDone}>
          Cancel
        </button>
      </div>
    </form>
  )
}

/**
 * One placed object: its position flies the map there and selects it, then
 * Edit, Disable or Enable, Duplicate and Delete. *named* adds the class.
 */
function ObjectRow({ obj, named = false }) {
  const { dispatch, ui, url } = useEditor()
  const remove = useRemoveObject()
  const toggle = useToggleObject()
  const duplicate = useDuplicateObject()
  const [editing, setEditing] = useState(false)
  const ref = useRef(null)
  const selected = ui.selectedObject && objectKey(ui.selectedObject) === objectKey(obj)
  const seq = ui.selectedObject?.seq
  useEffect(() => {
    if (selected) ref.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [selected, seq])
  const where = `${obj.x.toFixed(1)}, ${obj.z.toFixed(1)} · Y ${obj.y.toFixed(1)}`
  return (
    <div ref={ref} className="me-object-row">
      <ListRow
        name={named ? <ItemName name={obj.name} /> : where}
        nameTip="Show it on the map"
        sub={named ? where : undefined}
        meta={obj.disabled && <RowTag>disabled</RowTag>}
        selected={selected}
        disabled={obj.disabled}
        onClick={() => {
          // The marker it flies to needs the Objects layer on.
          if (!url.layers.objects) url.setLayer('objects', true)
          dispatch({ type: 'SELECT_OBJECT', payload: { disabled: obj.disabled, index: obj.index } })
          dispatch({ type: 'FLY_TO', payload: { lat: obj.lat, lng: obj.lng, zoom: 6 } })
        }}
        actions={
          <>
            <RowAction
              icon={HiOutlinePencilSquare}
              label="Edit"
              data-tooltip-content="Edit: drag it on the map, click a new spot, or type its position and rotation."
              active={editing}
              aria-expanded={editing}
              onClick={() => {
                if (!editing && !url.layers.objects) url.setLayer('objects', true)
                setEditing((on) => !on)
              }}
            />
            <RowAction
              icon={obj.disabled ? HiOutlineEye : HiOutlineEyeSlash}
              label={obj.disabled ? 'Enable' : 'Disable'}
              data-tooltip-content={
                obj.disabled
                  ? 'Enable: DayZ spawns it again from the next server start.'
                  : 'Disable: DayZ stops spawning it from the next server start. Zedstead keeps it in a file DayZ does not read.'
              }
              onClick={() => toggle(obj)}
            />
            <RowAction
              icon={HiOutlineDocumentDuplicate}
              label="Duplicate"
              data-tooltip-content="Duplicate: click the map to place a copy with the same class and rotation."
              onClick={() => duplicate(obj)}
            />
            <RowAction icon={HiOutlineTrash} label="Delete" danger onClick={() => remove(obj)} />
          </>
        }
      />
      {editing && <ObjectEditForm obj={obj} onDone={() => setEditing(false)} />}
    </div>
  )
}

/**
 * Every object placed for good (Zedstead's Object Spawner file), whatever its mod,
 * apart from the class list. Its open state is kept per browser
 * (state/useStoredOpen.js).
 */
function PlacedObjects() {
  const { objects, ui } = useEditor()
  const [open, setOpen] = useStoredOpen('zsm.mapeditor.placed-objects.open')
  const list = objects?.objects || []
  // A map click on an object opens the list, so its row shows.
  const seq = ui.selectedObject?.seq
  useEffect(() => {
    if (seq) setOpen(true)
  }, [seq, setOpen])
  if (!list.length) return null
  return (
    <ListSection title="Placed objects" count={list.length} foldable open={open} onOpenChange={setOpen}>
      {objects?.gameplay_on === false && (
        <PanelNote warn>serverDZ.cfg has enableCfgGameplayFile = 0, so DayZ spawns none of these.</PanelNote>
      )}
      {list.map((obj) => (
        <ObjectRow key={objectKey(obj)} obj={obj} named />
      ))}
    </ListSection>
  )
}

function ObjectPlacement({ classname }) {
  const { dispatch, url, ui, objects, reload } = useEditor()
  const want = classname.toLowerCase()
  const mine = (objects?.objects || []).filter((o) => o.name.toLowerCase() === want)
  const placing = ui.addMode?.kind === 'map' && ui.addMode.mode === 'object'

  function place() {
    if (placing) {
      dispatch({ type: 'EXIT_ADD' })
      return
    }
    url.setLayer('objects', true)
    dispatch({
      type: 'ENTER_MAP_ADD',
      payload: { mode: 'object', label: MAP_ADD_MODES.object.label, className: classname },
    })
  }

  return (
    <>
      <ListSection title="Objects (Object Spawner)" />
      <PanelNote>
        DayZ places each object at every server start and never removes it, which suits a
        building or a prop. Zedstead keeps them in {objects?.file || 'its own file'}, listed in
        cfggameplay.json.
      </PanelNote>
      <GameplayFileSwitch objects={objects} reload={reload} />
      {objects?.gameplay_on === false && (
        <PanelNote warn>
          serverDZ.cfg has enableCfgGameplayFile = 0, so DayZ ignores cfggameplay.json and spawns
          none of these. Turn the switch above on.
        </PanelNote>
      )}
      {objects?.error && <PanelNote warn>{objects.error}</PanelNote>}
      {mine.map((obj) => (
        <ObjectRow key={objectKey(obj)} obj={obj} />
      ))}
      <button
        type="button"
        className="me-detail-save-btn"
        disabled={Boolean(objects?.error)}
        onClick={place}
      >
        {placing ? 'Stop placing' : 'Place object'}
      </button>
    </>
  )
}

/**
 * The temporary try-out: the mod page's spawn test at a clicked point. The
 * editor's spawn-test mode arms it (MapEditor's click handler); "End test &
 * restore" on the mod's page undoes it.
 */
function SpawnTestPlacement({ mod, classname, info }) {
  const { dispatch } = useEditor()
  const active = info?.active

  function place() {
    dispatch({
      type: 'ENTER_SPAWN_TEST',
      payload: { mod, label: mod, className: classname, count: DEFAULT_COUNT },
    })
  }

  return (
    <>
      <ListSection title="Test (temporary)" />
      <PanelNote>
        A spawn test: click the map, and the items appear there when the server starts. The first
        player who joins is moved to them. &quot;End test &amp; restore&quot; on{' '}
        <Link to={modLink(mod)}>{mod}&apos;s page</Link> undoes it.
      </PanelNote>
      {active ? (
        <PanelNote>
          A spawn test is active for <Link to={modLink(active.mod)}>{active.mod}</Link>. End it on
          that mod&apos;s page before you start another.
        </PanelNote>
      ) : (
        <button type="button" className="me-detail-save-btn me-mod-item-test" disabled={!info} onClick={place}>
          Test here
        </button>
      )}
    </>
  )
}

/**
 * The class's types.xml entry: the Types page's numbers, category and
 * usage/value/tag/flags for that one entry. A class in two files (a mod's
 * custom/ copy and the mission's own) offers each, the last one first, as
 * DayZ reads the later file.
 */
function TypesEntryForm({ classname }) {
  const pushToast = useToast()
  const confirmDiscard = useConfirmDiscard()
  const [entries, setEntries] = useState(null)
  const [meta, setMeta] = useState(null)
  const [fileChoice, setFileChoice] = useState('')
  const [override, setOverride] = useState({})
  const [tree, setTree] = useState(null)
  const [saving, setSaving] = useState(false)
  const dirty = Object.keys(override).length > 0 || Boolean(tree)
  useUnsavedChanges(dirty, { form: TYPES_FORM })

  const load = useCallback(async () => {
    try {
      const params = new URLSearchParams({ q: classname, limit: '100' })
      const data = await api.get(`/api/types?${params.toString()}`)
      const want = classname.toLowerCase()
      setEntries((data.entries || []).filter((e) => e.name.toLowerCase() === want))
    } catch (err) {
      pushToast(err.message || 'Failed to load the types.xml entry', 'error')
      setEntries([])
    }
  }, [classname, pushToast])

  useEffect(() => {
    setEntries(null)
    setOverride({})
    setTree(null)
    load()
  }, [load])

  useEffect(() => {
    api.get('/api/types/meta').then(setMeta, () => setMeta({}))
  }, [])

  // Another save of a types file: load again, but never under an edit.
  useWs(
    useCallback(
      (msg) => {
        if (msg.type === 'configs_changed' && !dirty) load()
      },
      [load, dirty]
    )
  )

  const entry = useMemo(() => {
    if (!entries?.length) return null
    return entries.find((e) => e.id === fileChoice) || entries[entries.length - 1]
  }, [entries, fileChoice])

  async function save() {
    if (!entry || saving) return
    const { body, error } = typesEntryBody(entry, override, tree)
    if (error) {
      pushToast(error, 'error')
      return
    }
    setSaving(true)
    try {
      const saved = await api.put('/api/types/entry', body)
      // A save into custom/ lasts only until the next merge: the server says so.
      if (saved?.warning) pushToast(saved.warning, 'warning', 10000)
      else pushToast(`${entry.name} saved.`, 'info')
      setOverride({})
      setTree(null)
      await load()
    } catch (err) {
      pushToast(err.message || 'Save failed', 'error')
    } finally {
      setSaving(false)
    }
  }

  const title = <ListSection title="types.xml entry" />
  if (!entries) {
    return (
      <>
        {title}
        <PanelNote>Loading…</PanelNote>
      </>
    )
  }
  if (!entry) {
    return (
      <>
        {title}
        <PanelNote>
          No types file in the mission names {classname}, so the Central Economy never spawns it. A
          spawn test still can. To add an entry, use the mod&apos;s XML or a{' '}
          <Link to={configLink('manual-xml-links')}>manual XML file</Link>.
        </PanelNote>
      </>
    )
  }

  return (
    <>
      {title}
      <div className="me-mod-item-file">
        {entries.length > 1 ? (
          <Select
            className="field"
            aria-label="Types file"
            value={entry.id}
            onChange={async (id) => {
              // Switching files drops the edits, so it asks first, as Back does.
              if (id === entry.id || !(await confirmDiscard(TYPES_FORM))) return
              setOverride({})
              setTree(null)
              setFileChoice(id)
            }}
            options={[...entries].reverse().map((e) => ({ value: e.id, label: shortPath(e.source_file) }))}
          />
        ) : (
          <code>{shortPath(entry.source_file)}</code>
        )}
        <EditorLink
          to={configLink('types', { q: classname, file: entry.source_file })}
          tip={`Open ${LINKS.types.label} — ${LINKS.types.tip}`}
          label={`Open ${LINKS.types.label}`}
        />
      </div>
      <div className="me-detail-fields">
        <div className="me-detail-field-row me-mod-item-wide">
          <label htmlFor="me-mod-item-category">Category</label>
          <input
            id="me-mod-item-category"
            type="text"
            value={override.category ?? entry.category ?? ''}
            onChange={(e) => setOverride((o) => ({ ...o, category: e.target.value }))}
          />
        </div>
        {NUMBER_CELLS.map((f) => (
          <div key={f} className="me-detail-field-row">
            <label htmlFor={`me-mod-item-${f}`}>{NUMBER_LABELS[f]}</label>
            <input
              id={`me-mod-item-${f}`}
              type="text"
              inputMode="numeric"
              value={override[f] ?? entry[f] ?? ''}
              onChange={(e) => setOverride((o) => ({ ...o, [f]: e.target.value }))}
            />
          </div>
        ))}
      </div>
      {entry.nested && meta && (
        <NestedParts
          nested={{ ...entry.nested, ...tree }}
          choices={{ usage: meta.usages, value: meta.values, tag: meta.tags }}
          switches={meta.nested_switches}
          help={meta.nested_help}
          onChange={(key, next) => setTree((t) => ({ ...t, [key]: next }))}
        />
      )}
      <button
        type="button"
        className="me-detail-save-btn"
        disabled={!dirty || saving}
        onClick={save}
      >
        {saving ? 'Saving…' : `Save ${entry.name}`}
      </button>
    </>
  )
}
