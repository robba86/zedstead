// Location category checkboxes, one per type present in the database, and
// below them the admin's own place names (OwnPlaceNames).
//
// Every type starts visible; the Locations layer toggle is the master switch.

import { useEffect, useMemo } from 'react'
import ListRow, { RowDot } from '../../../components/ListRow.jsx'
import ListSection from '../../../components/ListSection.jsx'
import SectionHeightHandle from './SectionHeightHandle.jsx'
import { useEditor } from '../state/MapEditorProvider'
import useStoredOpen from '../state/useStoredOpen.js'
import { locationTypeColor } from '../icons/iconRules'
import { locationTypes } from '../leaflet/useLocationLayers'
import OwnPlaceNames from './OwnPlaceNames'

export default function LocationList() {
  const { locations, ui, dispatch, url } = useEditor()
  // Collapses like Layers, from its own title (state/useStoredOpen.js).
  const [open, setOpen] = useStoredOpen('zsm.mapeditor.locations.open')

  const types = useMemo(() => locationTypes(locations), [locations])
  const counts = useMemo(() => {
    const map = new Map()
    for (const loc of locations) {
      const type = loc.type || 'Other'
      map.set(type, (map.get(type) || 0) + 1)
    }
    return map
  }, [locations])

  // Default every type on when the set of types changes (map switch).
  useEffect(() => {
    dispatch({ type: 'SET_VISIBLE_LOCATION_TYPES', payload: new Set(types) })
  }, [types, dispatch])

  if (!url.layers.locations) return null
  // An admin with no names yet still gets the own-name tools (plans/87).
  if (types.length === 0) {
    return (
      <div className="me-location-list">
        <OwnPlaceNames locations={locations} />
      </div>
    )
  }

  const shown = types.filter((type) => ui.visibleLocationTypes.has(type)).length

  return (
    <>
    <ListSection
      className="me-location-list"
      title="Locations"
      count={open ? undefined : `${shown}/${types.length}`}
      foldable
      sticky
      open={open}
      onOpenChange={setOpen}
    >
      {types.map((type) => {
        const id = `me-location-type-${type}`
        return (
          <ListRow
            key={type}
            compact
            lead={
              <>
                <input
                  id={id}
                  type="checkbox"
                  checked={ui.visibleLocationTypes.has(type)}
                  onChange={() => dispatch({ type: 'TOGGLE_LOCATION_TYPE', payload: type })}
                />
                <RowDot color={locationTypeColor(type)} />
              </>
            }
            name={<label htmlFor={id}>{type}</label>}
            meta={counts.get(type)}
          />
        )
      })}
      <OwnPlaceNames locations={locations} />
    </ListSection>
    {open && <SectionHeightHandle storageKey="zsm.mapeditor.locations.height" label="Locations height" />}
    </>
  )
}
