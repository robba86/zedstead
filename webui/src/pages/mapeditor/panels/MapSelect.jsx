// Terrain selector. Picking a map other than the mission's own shows that
// map's image and locations read-only — spawn/trader data belongs to the
// running mission and would be meaningless overlaid on another terrain.

import Select from '../../../components/Select.jsx'
import { Link } from 'react-router-dom'
import { HiOutlineArrowsRightLeft } from 'react-icons/hi2'
import { useEditor } from '../state/MapEditorProvider'
import { tooltipAttrs } from '../../../components/Tooltip.jsx'

export default function MapSelect() {
  const { maps, terrain, activeTerrain, url } = useEditor()

  // The link to the Maps page lived in the breadcrumb row, which this page
  // does not render; it sits beside the picker instead, since it is the other
  // half of the same question (preview a map here, switch the server there).
  const changeMap = (
    <Link
      to="/maps"
      className="me-link-btn"
      aria-label="Change map"
      {...tooltipAttrs('Change map: switch the server to another map on the Maps page')}
    >
      <HiOutlineArrowsRightLeft aria-hidden="true" />
    </Link>
  )

  if (!maps.length) return changeMap

  return (
    <>
    <Select
      className="me-map-select"
      value={terrain}
      onChange={(v) => url.setTerrain(v === activeTerrain ? '' : v)}
      aria-label="Map"
      // The description under each map says what picking it does, in place
      // of a tooltip on the picker.
      options={maps.map((m) => ({
        value: m.terrain,
        label: `${m.name}${m.current ? ' (current)' : ''}`,
        description: m.current ? "The mission's own map: you can edit it" : 'Preview only: editing works on the current map',
      }))}
    />
    {changeMap}
    </>
  )
}
