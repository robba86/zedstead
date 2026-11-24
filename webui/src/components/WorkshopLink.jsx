// Steam Workshop page link for a workshop item id. Shared by the Mods table and
// the per-mod page. The backend also returns a ready-made `workshop_url` on the
// mod detail payload (app/modmgr/mod_info.py::workshop_url) — use that where it
// is available and this where only the id is at hand.

import { HiOutlineArrowTopRightOnSquare } from 'react-icons/hi2'
import { tooltipAttrs } from './Tooltip.jsx'

export const steamWorkshopUrl = (id) => `https://steamcommunity.com/sharedfiles/filedetails/?id=${id}`

// Empty id → dash, so callers can render it unconditionally. The trailing
// glyph says the link leaves zsm for Steam, in a new tab. `small` is the
// id in small text beside a mod's name (the Installed mods rows), and
// renders nothing without an id.
export default function WorkshopLink({ id, stop = false, small = false, children }) {
  if (!id) return small ? null : <span className="dep-info">—</span>
  return (
    <a
      className={small ? 'mod-link workshop-link-small' : 'mod-link'}
      href={steamWorkshopUrl(id)}
      target="_blank"
      rel="noopener noreferrer"
      onClick={stop ? (e) => e.stopPropagation() : undefined}
      {...(small ? tooltipAttrs('Open the Workshop page') : {})}
    >
      {children || id}
      <HiOutlineArrowTopRightOnSquare className="ext-link-icon" aria-hidden="true" />
    </a>
  )
}
