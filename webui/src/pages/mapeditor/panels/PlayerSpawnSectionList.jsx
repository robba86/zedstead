// Player Spawns tab, list view: three collapsible sections (fresh / hop /
// travel), each with its groups, drawn with the shared list parts
// (webui/DESIGN.md). A section's open state is kept per browser through
// useStoredOpen and starts closed.
//
// Clicking a group name flies the map to its first position. That goes through
// the reducer (FLY_TO) rather than reaching for the Leaflet instance — the
// sidebar sits outside the map's subtree, and threading the map object into it
// would make every panel a potential holder of a stale, removed map.

import { useState } from 'react'
import { useEditor } from '../state/MapEditorProvider'
import { SECTION_COLORS, SECTION_LABELS } from '../leaflet/usePlayerSpawnLayer'
import { meApi } from '../api'
import { useToast } from '../../../components/Toast.jsx'
import { useConfirm } from '../../../components/useConfirm.jsx'
import PromptDialog from './PromptDialog.jsx'
import useStoredOpen from '../state/useStoredOpen'
import ListSection from '../../../components/ListSection.jsx'
import ListRow, { RowDot } from '../../../components/ListRow.jsx'
import CountBadge from '../../../components/CountBadge.jsx'
import RowAction from '../../../components/RowAction.jsx'
import PanelNote from '../../../components/PanelNote.jsx'
import { LINKS, configLink } from '../../../crossLinks'

const GearIcon = () => (
  <svg
    width="12"
    height="12"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2.5"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <circle cx="12" cy="12" r="3" />
    <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z" />
  </svg>
)

const PlusIcon = () => (
  <svg
    width="12"
    height="12"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="3"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M5 12h14" />
    <path d="M12 5v14" />
  </svg>
)

const TrashIcon = () => (
  <svg
    width="12"
    height="12"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2.5"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M3 6h18" />
    <path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6" />
    <path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2" />
  </svg>
)

// One section (fresh, hop or travel) and its groups.
function SpawnSection({ section, onAddGroup, onDeleteGroup }) {
  const { ui, dispatch, url } = useEditor()
  const name = section.section
  const [open, setOpen] = useStoredOpen(`zsm.mapeditor.player-spawns.${name}.open`, false)
  const totalPositions = section.groups.reduce((sum, g) => sum + g.positions.length, 0)

  function flyToGroup(group) {
    const pos = group.positions[0]
    if (!pos) return
    url.setLayer('playerspawns', true)
    dispatch({ type: 'FLY_TO', payload: { lat: pos.lat, lng: pos.lng, zoom: 4 } })
  }

  function startAdding(group) {
    url.setLayer('playerspawns', true)
    dispatch({
      type: 'ENTER_PS_ADD',
      payload: {
        section: name,
        groupIndex: group.index,
        label: `${name} > ${group.name}`,
      },
    })
  }

  return (
    <ListSection
      title={SECTION_LABELS[name] || name}
      lead={<RowDot color={SECTION_COLORS[name] || '#888'} />}
      count={[
        { value: section.groups.length, unit: 'groups' },
        { value: totalPositions, unit: 'pts' },
      ]}
      foldable
      open={open}
      onOpenChange={setOpen}
      actions={
        <RowAction
          icon={GearIcon}
          label={`Edit ${name} parameters`}
          data-tooltip-content="Edit section parameters"
          onClick={() => dispatch({ type: 'SELECT_PS_SECTION', payload: name })}
        />
      }
    >
      {section.groups.length === 0 && <PanelNote>No groups</PanelNote>}
      {section.groups.map((group) => {
        const adding =
          ui.addMode?.kind === 'player-spawn' &&
          ui.addMode.section === name &&
          ui.addMode.groupIndex === group.index
        return (
          <ListRow
            key={group.index}
            name={group.name}
            nameTip={`${group.positions.length} positions`}
            meta={<CountBadge value={group.positions.length} />}
            onClick={() => flyToGroup(group)}
            actions={
              <>
                <RowAction
                  icon={PlusIcon}
                  label={`Add positions to ${group.name}`}
                  data-tooltip-content="Add position (click map)"
                  active={adding}
                  onClick={() => (adding ? dispatch({ type: 'EXIT_ADD' }) : startAdding(group))}
                />
                <RowAction
                  icon={TrashIcon}
                  label={`Delete ${group.name}`}
                  data-tooltip-content="Delete group"
                  danger
                  onClick={() => onDeleteGroup(name, group)}
                />
              </>
            }
          />
        )
      })}
      <button type="button" className="btn btn-ghost btn-small me-ps-add-group" onClick={() => onAddGroup(name)}>
        + Add Group
      </button>
    </ListSection>
  )
}

export default function PlayerSpawnSectionList() {
  const { playerSpawns } = useEditor()
  const [addingGroupTo, setAddingGroupTo] = useState(null)
  const pushToast = useToast()
  const confirm = useConfirm()

  if (!playerSpawns.length) {
    return <PanelNote>No player spawn points loaded</PanelNote>
  }

  async function deleteGroup(section, group) {
    const ok = await confirm({
      title: 'Delete group?',
      message: `Delete group "${group.name}" and all its positions?`,
      confirmLabel: 'Delete',
      danger: true,
    })
    if (!ok) return
    try {
      await meApi.del(`/player-spawns/${encodeURIComponent(section)}/groups/${group.index}`)
    } catch (err) {
      pushToast(`Failed to delete group: ${err.message}`, 'error')
    }
  }

  return (
    <>
      {/* One link for all three sections, since they share a single file. */}
      <PanelNote
        link={{
          to: configLink('playerspawnpoints'),
          label: `Open ${LINKS.playerspawnpoints.label}`,
          tip: `Open ${LINKS.playerspawnpoints.label} — ${LINKS.playerspawnpoints.tip}`,
        }}
      >
        All three sections live in {LINKS.playerspawnpoints.label}.
      </PanelNote>
      {playerSpawns.map((section) => (
        <SpawnSection
          key={section.section}
          section={section}
          onAddGroup={setAddingGroupTo}
          onDeleteGroup={deleteGroup}
        />
      ))}

      {addingGroupTo && (
        <PromptDialog
          title={`New group in ${SECTION_LABELS[addingGroupTo] || addingGroupTo}`}
          label="Group name"
          placeholder="e.g. NorthCoast"
          hint="Groups partition a section's spawn positions; the server picks one at random."
          onSubmit={(groupName) =>
            meApi.post(`/player-spawns/${encodeURIComponent(addingGroupTo)}/groups`, {
              name: groupName,
            })
          }
          onClose={() => setAddingGroupTo(null)}
        />
      )}
    </>
  )
}
