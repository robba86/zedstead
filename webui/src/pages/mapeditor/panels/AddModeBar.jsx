// The "you are placing something" banner.
//
// One bar for every add mode, driven by the single `ui.addMode` slot — the
// vanilla editor had three independent globals and each entry point cleared
// the other two by hand.
//
// The TraderPlus modes also need a classname before they can create anything,
// so the bar grows a text field for those. It is kept in the reducer rather
// than local state because the map click handler, which lives on the other
// side of the tree, is what consumes it.

import { useEditor } from '../state/MapEditorProvider'
import { MAP_ADD_MODES } from '../addModes'
import { tooltipAttrs } from '../../../components/Tooltip.jsx'

export default function AddModeBar() {
  const { ui, dispatch } = useEditor()
  const mode = ui.addMode
  if (!mode) return null

  // Event add mode is identified by the event name; every other kind carries
  // its own display label (e.g. "fresh > NorthCoast", "Clone Trader NPC: …").
  const label = mode.kind === 'event' ? mode.event : mode.label || mode.kind
  const config = mode.kind === 'map' ? MAP_ADD_MODES[mode.mode] : null
  const spawnTest = mode.kind === 'spawn-test'

  return (
    // A bar with a box wraps, as the spawn test's does: in the 320px panel the
    // Trader NPC classname box was squeezed to 18px.
    <div className={`me-add-mode-bar${spawnTest || config?.classField ? ' me-add-mode-wrap' : ''}`}>
      <span>
        {mode.verb || 'Adding'}: <strong>{label}</strong>
      </span>
      {config?.classField && (
        <input
          className="me-add-mode-input"
          type="text"
          spellCheck={false}
          placeholder={config.classPlaceholder}
          value={mode.className || ''}
          onChange={(e) => dispatch({ type: 'SET_ADD_CLASSNAME', payload: e.target.value })}
          aria-label={config.fieldLabel || 'Classname'}
        />
      )}
      {config?.yField && (
        <input
          className="me-add-mode-input me-add-mode-count"
          type="number"
          step="0.1"
          placeholder="Y auto"
          value={mode.y ?? ''}
          onChange={(e) => dispatch({ type: 'SET_ADD_Y', payload: e.target.value })}
          aria-label="Height (Y), empty for the ground's height"
          {...tooltipAttrs("Height (Y) in metres. Leave it empty to put the object on the ground.")}
        />
      )}
      {spawnTest && (
        <>
          <input
            className="me-add-mode-input"
            type="text"
            spellCheck={false}
            placeholder="Class from this mod"
            value={mode.className || ''}
            onChange={(e) => dispatch({ type: 'SET_ADD_CLASSNAME', payload: e.target.value })}
            aria-label="Class"
          />
          <input
            className="me-add-mode-input me-add-mode-count"
            type="number"
            min="1"
            step="1"
            value={mode.count ?? ''}
            onChange={(e) => dispatch({ type: 'SET_ADD_COUNT', payload: e.target.value })}
            aria-label="Count"
          />
        </>
      )}
      <span className="me-add-mode-hint">
        {spawnTest ? 'click the map to start the test there' : 'click map to place'}
      </span>
      <button
        type="button"
        className="me-add-mode-cancel"
        onClick={() => dispatch({ type: 'EXIT_ADD' })}
      >
        Cancel
      </button>
    </div>
  )
}
