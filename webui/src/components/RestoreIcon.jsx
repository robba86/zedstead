// The glyph on every button that puts back an earlier state of a file or of
// the mods: a clock with an arrow running backwards. Heroicons has none, so
// it is Lucide's "history", drawn at Heroicons' 1.5 stroke to sit beside
// them. A Reset or Undo button keeps Heroicons' u-turn arrow.
import { LuHistory } from 'react-icons/lu'

export default function RestoreIcon(props) {
  return <LuHistory strokeWidth={1.5} aria-hidden="true" {...props} />
}
