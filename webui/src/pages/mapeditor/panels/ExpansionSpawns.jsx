// Expansion's spawn locations in the Players tab — and which of the two
// player-spawn systems the server is actually using.
//
// The two do not blend. Expansion's own documentation defines the switch
// (wiki, "[Server-Hosting] SpawnSettings"): with `EnableSpawnSelection` off,
// "Players will spawn randomly on the map like in vanilla"; with it on,
// "Players will be able to choose where to spawn on the map according the
// config done bellow". So one of the two lists is live and the other is
// inert, and which one is a single flag in SpawnSettings.json.
//
// That flag is what this file renders. The map drew both sets of markers with
// nothing saying which ones a player will ever see, which is the question
// anyone looking at the two layers together ends up asking.
//
// Positions are edited by dragging them on the map (the Expansion Spawn Points
// layer). There is no add/delete here because there is no endpoint for it —
// the panel lists what exists and flies to it.

import { useEditor } from '../state/MapEditorProvider'
import { EXPANSION_COLORS } from '../leaflet/useExpansionLayers'
import { editorLink } from '../../../crossLinks'
import useStoredOpen from '../state/useStoredOpen'
import ListSection from '../../../components/ListSection.jsx'
import ListRow, { RowDot, RowTag } from '../../../components/ListRow.jsx'
import CountBadge from '../../../components/CountBadge.jsx'
import PanelNote from '../../../components/PanelNote.jsx'
import StateNotice from '../../../components/StateNotice.jsx'

/** Which system is live — rendered above the vanilla sections. */
export function SpawnSystemNote() {
  const { expSpawnPoints } = useEditor()
  if (!expSpawnPoints) return null

  const on = Boolean(expSpawnPoints.enabled)
  const count = (expSpawnPoints.spawn_locations || []).length

  return (
    <StateNotice active={on}>
      {on ? (
        <>
          <strong>Expansion spawn selection is on.</strong> Players pick from the{' '}
          {count} Expansion location{count === 1 ? '' : 's'} below; the vanilla sections are
          not used while it is.
        </>
      ) : (
        <>
          <strong>Expansion spawn selection is off.</strong> Players spawn from the vanilla
          sections below, as on an unmodded server; Expansion's {count} location
          {count === 1 ? ' is' : 's are'} configured but unused.
        </>
      )}{' '}
      The switch is <code>EnableSpawnSelection</code> in SpawnSettings.json.
    </StateNotice>
  )
}

/** Expansion's own spawn locations — rendered below the vanilla sections. */
export default function ExpansionSpawnLocations() {
  const { expSpawnPoints, dispatch, url } = useEditor()
  const [open, setOpen] = useStoredOpen('zsm.mapeditor.player-spawns.expansion.open', false)
  const locations = expSpawnPoints?.spawn_locations || []
  if (!locations.length) return null

  const total = locations.reduce((sum, loc) => sum + (loc.positions?.length || 0), 0)

  function flyTo(location) {
    const pos = location.positions?.[0]
    if (!pos) return
    url.setLayer('expspawns', true)
    dispatch({ type: 'FLY_TO', payload: { lat: pos.lat, lng: pos.lng, zoom: 4 } })
  }

  return (
    <ListSection
      title="Expansion"
      lead={<RowDot color={EXPANSION_COLORS.spawn} />}
      count={[
        { value: locations.length, unit: 'locations' },
        { value: total, unit: 'pts' },
      ]}
      foldable
      open={open}
      onOpenChange={setOpen}
    >
      {locations.map((location) => (
        <ListRow
          key={location.index}
          name={location.name}
          nameTip={`${location.positions?.length || 0} positions${
            location.use_cooldown ? ' — respawn cooldown applies' : ''
          }`}
          meta={
            <>
              {location.use_cooldown && <RowTag>cooldown</RowTag>}{' '}
              <CountBadge value={location.positions?.length || 0} />
            </>
          }
          onClick={() => flyTo(location)}
        />
      ))}
      <PanelNote
        link={{
          to: editorLink('expansion-spawn'),
          label: 'Open SpawnSettings.json',
          tip: "Open Expansion's SpawnSettings.json editor",
        }}
      >
        Drag a point on the map to move it. The rest lives in SpawnSettings.json.
      </PanelNote>
    </ListSection>
  )
}
