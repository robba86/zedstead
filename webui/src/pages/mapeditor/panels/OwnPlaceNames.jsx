// The admin's own place names (plans/87), under the Locations type list.
//
// They live in locations_<map>.json in ~/.config/zsm/locations/ and show
// beside the names from the map's own world config. Add places one with a
// click on the map (the "location" add mode, addModes.js), and each row
// renames, retypes, moves, flies to or removes one. Export downloads the file,
// Import adds an exported file's places (or replaces the own names with them),
// so the names move to another Zedstead on another server.
//
// Admin-only like the routes (no MODERATOR_WRITES entry), and hidden for
// anyone else (role.jsx). Changes are locked to the mission's own map, as
// every map editor write is. Export and Import work on a previewed map too.

import { useRef, useState } from 'react'
import { HiOutlineCursorArrowRays, HiOutlineTrash, HiOutlineViewfinderCircle } from 'react-icons/hi2'
import { Switch } from '../../../components/FlagSwitch.jsx'
import ListRow, { RowDot } from '../../../components/ListRow.jsx'
import ListSection from '../../../components/ListSection.jsx'
import RowAction from '../../../components/RowAction.jsx'
import Select from '../../../components/Select.jsx'
import { useToast } from '../../../components/Toast.jsx'
import { useConfirm } from '../../../components/useConfirm.jsx'
import { useCan } from '../../../role.jsx'
import { useEditor } from '../state/MapEditorProvider'
import { MAP_ADD_MODES, ownPlacePath } from '../addModes'
import { LOCATION_TYPE_NAMES, locationTypeColor } from '../icons/iconRules'
import { withBase } from '../../../instance.js'
import { ME_BASE, meApi } from '../api'

/** One own place: its name saved on Enter or blur, its type on change. */
function OwnPlaceRow({ place, editable, onSave, onMove, onFly, onRemove }) {
  const [name, setName] = useState(place.name)
  const commit = () => {
    const next = name.trim()
    if (!next) setName(place.name)
    else if (next !== place.name) onSave({ name: next })
  }
  return (
    <ListRow
      className="me-own-place"
      lead={<RowDot color={locationTypeColor(place.type)} />}
      name={
        editable ? (
          <>
            <input
              className="field me-own-place-name"
              type="text"
              value={name}
              maxLength={80}
              aria-label="Place name"
              onChange={(e) => setName(e.target.value)}
              onBlur={commit}
              onKeyDown={(e) => {
                if (e.key === 'Enter') e.currentTarget.blur()
                if (e.key === 'Escape') setName(place.name)
              }}
            />
            <Select
              className="me-own-place-type"
              value={place.type}
              aria-label="Place type"
              options={LOCATION_TYPE_NAMES}
              onChange={(type) => type !== place.type && onSave({ type })}
            />
          </>
        ) : (
          place.name
        )
      }
      actions={
        <>
          <RowAction icon={HiOutlineViewfinderCircle} label="Show it on the map" onClick={onFly} />
          {editable && (
            <>
              <RowAction
                icon={HiOutlineCursorArrowRays}
                label="Move"
                data-tooltip-content="Move: then click the map where it belongs"
                onClick={onMove}
              />
              <RowAction icon={HiOutlineTrash} label="Remove this place name" danger onClick={onRemove} />
            </>
          )}
        </>
      }
    />
  )
}

/**
 * The "Add a place name…" toggle, shared by this list and the Locations layer
 * switch: `{ shown, adding, toggle }`. Shown to an admin on the mission's own
 * map, where a place can be written.
 */
export function useAddPlaceName() {
  const { terrain, isActiveTerrain, dispatch, ui } = useEditor()
  const isAdmin = useCan('admin')
  const adding = ui.addMode?.kind === 'map' && ui.addMode.mode === 'location'
  const toggle = () => {
    if (adding) {
      dispatch({ type: 'EXIT_ADD' })
      return
    }
    const config = MAP_ADD_MODES.location
    dispatch({ type: 'ENTER_MAP_ADD', payload: { mode: 'location', label: config.label, className: '' } })
  }
  return { shown: isAdmin && !!terrain && isActiveTerrain, adding, toggle }
}

/** The link itself: "Add a place name…", or "Cancel adding" while the mode is on. */
export function AddPlaceNameLink() {
  const { shown, adding, toggle } = useAddPlaceName()
  if (!shown) return null
  return (
    <button type="button" className="btn-link" onClick={toggle}>
      {adding ? 'Cancel adding' : 'Add a place name…'}
    </button>
  )
}

export default function OwnPlaceNames({ locations }) {
  const { terrain, isActiveTerrain, reload, dispatch } = useEditor()
  const isAdmin = useCan('admin')
  const pushToast = useToast()
  const confirm = useConfirm()
  const fileRef = useRef(null)
  const [replace, setReplace] = useState(false)

  if (!isAdmin || !terrain) return null
  const own = (locations || []).filter((l) => l.own)
  const query = `terrain=${encodeURIComponent(terrain)}`

  const write = async (fn, failure) => {
    try {
      await fn()
    } catch (err) {
      pushToast(`${failure}: ${err.message}`, 'error')
    } finally {
      reload.locations()
    }
  }

  const startMove = (place) =>
    dispatch({
      type: 'ENTER_MAP_ADD',
      payload: { mode: 'location-move', index: place.index, name: place.name, label: `Move ${place.name}` },
    })

  const fly = (place) => dispatch({ type: 'FLY_TO', payload: { lat: place.lat, lng: place.lng, zoom: 5 } })

  const remove = async (place) => {
    const yes = await confirm({
      title: `Remove ${place.name}?`,
      message: 'It is removed from your own label file for this map.',
      confirmLabel: 'Remove',
      danger: true,
    })
    if (yes) write(() => meApi.del(ownPlacePath(place)), 'Not removed')
  }

  const importFile = async (file) => {
    if (!file) return
    let places
    try {
      places = JSON.parse(await file.text())
    } catch {
      pushToast(`${file.name} is not a JSON file. Nothing was changed.`, 'error')
      return
    }
    if (replace && own.length) {
      const yes = await confirm({
        title: `Replace your ${own.length} own place names?`,
        message: `The names in ${file.name} take their place.`,
        confirmLabel: 'Replace',
        danger: true,
      })
      if (!yes) return
    }
    write(async () => {
      const { added, skipped } = await meApi.post(`/locations/import?${query}`, { places, replace })
      pushToast(`Imported ${added} place names${skipped ? `, ${skipped} already there` : ''}.`, 'info')
    }, 'Import failed')
  }

  return (
    <ListSection className="me-own-places" title="Your own place names" count={own.length}>
      {own.map((place) => (
        <OwnPlaceRow
          key={`${place.index}:${place.name}:${place.lat}:${place.lng}`}
          place={place}
          editable={isActiveTerrain}
          onSave={(fields) => write(() => meApi.put(ownPlacePath(place), fields), 'Not saved')}
          onMove={() => startMove(place)}
          onFly={() => fly(place)}
          onRemove={() => remove(place)}
        />
      ))}
      <div className="me-own-places-actions">
        <AddPlaceNameLink />
        {own.length > 0 && (
          // The route answers with the label file as an attachment, the file
          // another Zedstead imports.
          <a
            className="btn-link"
            href={withBase(`${ME_BASE}/locations/export?${query}`)}
            download={`locations_${terrain}.json`}
          >
            Export
          </a>
        )}
        <button type="button" className="btn-link" onClick={() => fileRef.current?.click()}>
          Import…
        </button>
        <Switch
          checked={replace}
          onChange={setReplace}
          className="me-own-places-replace"
          help={
            'On: an import deletes all your own place names on this map first, then adds the ones in the file. ' +
            'It asks before it deletes. Off: the file\'s names are added to yours, and a name at the same spot ' +
            'with the same name is skipped.'
          }
        >
          Import replaces my names
        </Switch>
        <input
          ref={fileRef}
          type="file"
          accept="application/json,.json"
          hidden
          onChange={(e) => {
            importFile(e.target.files?.[0])
            e.target.value = ''
          }}
        />
      </div>
    </ListSection>
  )
}
