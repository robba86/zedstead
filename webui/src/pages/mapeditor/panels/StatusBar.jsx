// Connection state + live cursor coordinates.
//
// "Connected" reflects the app's shared WebSocket, which is also what carries
// the editor's change pings — if it is down, the map is not live.

import { useEditor } from '../state/MapEditorProvider'

export default function StatusBar() {
  const { connected, ui, showBuildings, buildings, maps } = useEditor()
  const coords = ui.hoverCoords
  // The building map reads an installed mission for this terrain (plans/69);
  // without one it is an empty square, and that needs saying.
  const noBuildings = showBuildings && buildings && !buildings.mission
  const mapName = noBuildings
    ? maps.find((m) => m.terrain === buildings.terrain)?.name || buildings.terrain
    : ''

  return (
    <div className="me-status-bar">
      <span className={`me-conn ${connected ? 'connected' : 'disconnected'}`}>
        {connected ? 'Connected' : 'Disconnected'}
      </span>
      {noBuildings && (
        <span className="me-status-note">
          No installed mission for {mapName}, so no buildings to draw
        </span>
      )}
      <span className="me-coords">
        {coords ? `X: ${Math.round(coords.x)}  Z: ${Math.round(coords.z)}` : ''}
      </span>
    </div>
  )
}
