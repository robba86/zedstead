import { Link } from 'react-router-dom'
import { modLink } from '../crossLinks.js'

// Free text (a conflict message, an .RPT line) with the mod names inside it
// spliced into links to their mod page.
//
// `known` is the set of names allowed to become links: @Foo scraped out of an
// .RPT line may be anything at all, and an unresolvable ref 404s on /mods/:id —
// so a name nobody has heard of stays plain text.
export default function ModNameText({ text, known }) {
  const parts = String(text ?? '').split(/(@[A-Za-z0-9_.\-]+)/g)
  return parts.map((part, i) =>
    part.startsWith('@') && known.has(part) ? (
      <Link key={i} className="mod-link" to={modLink(part)}>
        {part}
      </Link>
    ) : (
      <span key={i}>{part}</span>
    ),
  )
}
