// The glyph a spawn event's map marker draws, in the event's colour, for the
// event's name in the side panel and the marker popup. Draws nothing for an
// event the map shows as a plain circle.

import { getEventColor, spawnIconComponent } from './iconRules'

export default function SpawnIcon({ name, size = 16 }) {
  const Icon = spawnIconComponent(name)
  if (!Icon) return null
  return <Icon className="me-title-icon" size={size} color={getEventColor(name)} aria-hidden />
}
