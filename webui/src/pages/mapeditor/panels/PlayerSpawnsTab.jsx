// Player Spawns tab: the section list, or one section's parameter editor.
//
// Two systems can spawn players — the mission's cfgPlayerSpawnPoints.xml and
// Expansion's SpawnSettings.json — and only one of them is live at a time, so
// the tab shows both with a line saying which (see ExpansionSpawns.jsx). Both
// halves render nothing when Expansion has written no spawn data.

import { useEditor } from '../state/MapEditorProvider'
import PlayerSpawnSectionList from './PlayerSpawnSectionList.jsx'
import PlayerSpawnSectionDetail from './PlayerSpawnSectionDetail.jsx'
import ExpansionSpawnLocations, { SpawnSystemNote } from './ExpansionSpawns.jsx'

export default function PlayerSpawnsTab() {
  const { ui } = useEditor()
  if (ui.selectedPsSection) {
    return <PlayerSpawnSectionDetail section={ui.selectedPsSection} />
  }
  return (
    <>
      <SpawnSystemNote />
      <PlayerSpawnSectionList />
      <ExpansionSpawnLocations />
    </>
  )
}
