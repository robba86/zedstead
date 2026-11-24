// The two config editors that hold a spawn event, as ↗ links: events.xml (its
// timing, flags and nominal) and cfgeventspawns.xml (its positions), each
// opened on this event. Shared by the event detail panel and the spawn popup.

import EditorLink from './EditorLink.jsx'
import { LINKS, configLink } from '../../../crossLinks'

export default function EventFileLinks({ name }) {
  return (
    <>
      <EditorLink
        to={configLink('events', { q: name })}
        tip={`Open ${name} in ${LINKS.events.label} — ${LINKS.events.tip}`}
        label={`Open ${name} in ${LINKS.events.label}`}
      >
        {LINKS.events.label}
      </EditorLink>
      <EditorLink
        to={configLink('eventspawns', { group: name })}
        tip={`Open ${name} in ${LINKS.eventspawns.label} — ${LINKS.eventspawns.tip}`}
        label={`Open ${name} in ${LINKS.eventspawns.label}`}
      >
        {LINKS.eventspawns.label}
      </EditorLink>
    </>
  )
}
