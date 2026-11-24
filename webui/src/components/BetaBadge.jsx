import { BETA_FEATURES } from '../betaFeatures.js'
import { tooltipAttrs } from './Tooltip.jsx'

const FEATURE_TIP = 'Beta: tested in a test setup only, not yet on a real server.'
export const VERSION_BETA_TIP = 'Zedstead is in beta. A feature marked Beta is tested in a test setup only.'

// The pill itself, also used beside the version in the sidebar.
// `as` picks the element: a span by default, since the pill sits inside
// headings, where a div is not allowed; a div in a block layout (the
// sidebar's version line).
export function BetaPill({ tip = FEATURE_TIP, as: Tag = 'span' }) {
  return (
    <Tag className="status-pill status-pill-beta beta-badge" {...tooltipAttrs(tip)}>
      Beta
    </Tag>
  )
}

// Marks a feature that no person has run on a real server yet. It renders
// nothing for a key that is not in BETA_FEATURES (or no key at all), so a
// page can pass a lookup that misses.
export default function BetaBadge({ feature }) {
  if (!BETA_FEATURES.has(feature)) return null
  return <BetaPill />
}
