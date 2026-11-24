// A name box on the map, at the spot just clicked in the "Add a place name"
// mode with no name typed in the add bar (MapEditor.jsx). Enter saves the
// place there, and Escape cancels. Leaving the box (a click on the map to pan)
// saves nothing: a half-typed name stays in the box, and an empty box goes. It follows the spot as the
// map pans and zooms, and sits over the map like the editor's other overlays,
// never in a Leaflet divIcon, whose drag and click handlers steal the input's
// keys.

import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { isMapAlive, useMapEvent } from '../leaflet/useLeafletMap'

export default function PlaceNameInput({ map, at, onSave, onCancel }) {
  const [name, setName] = useState('')
  const [point, setPoint] = useState(null)
  const inputRef = useRef(null)
  // Enter saves, and the box's removal then fires a blur: answer once.
  const doneRef = useRef(false)

  const place = () => {
    if (isMapAlive(map)) setPoint(map.latLngToContainerPoint([at.lat, at.lng]))
  }
  useLayoutEffect(place, [map, at])
  useMapEvent(map, 'move', place)
  useMapEvent(map, 'zoom', place)
  const shown = point != null
  useEffect(() => {
    if (shown) inputRef.current?.focus()
  }, [shown])

  if (!point) return null
  const save = () => {
    if (doneRef.current) return
    doneRef.current = true
    const text = name.trim()
    if (text) onSave(text)
    else onCancel()
  }
  return (
    <div className="me-place-name-input" style={{ left: point.x, top: point.y }}>
      <span className="me-place-name-dot" aria-hidden="true" />
      <input
        ref={inputRef}
        className="field"
        type="text"
        maxLength={80}
        placeholder="Name, then Enter"
        aria-label="Place name"
        value={name}
        onChange={(e) => setName(e.target.value)}
        onBlur={() => {
          if (!name.trim() && !doneRef.current) {
            doneRef.current = true
            onCancel()
          }
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter') save()
          if (e.key === 'Escape' && !doneRef.current) {
            doneRef.current = true
            onCancel()
          }
        }}
      />
    </div>
  )
}
