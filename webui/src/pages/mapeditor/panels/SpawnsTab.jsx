// Spawns tab: the event list, or one event's detail editor.
//
// The vanilla editor kept both views mounted and toggled a `hidden` class; the
// detail view is a fetch away, so mounting one at a time is simpler and the
// detail form can seed itself on mount instead of being re-rendered into.
// Keyed by event, so another event never shows, or saves, the last one's form.

import { useEditor } from '../state/MapEditorProvider'
import EventList from './EventList.jsx'
import EventDetail from './EventDetail.jsx'

export default function SpawnsTab() {
  const { ui } = useEditor()
  return ui.selectedEvent ? <EventDetail key={ui.selectedEvent} name={ui.selectedEvent} /> : <EventList />
}
