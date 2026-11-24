// Traders tab: four lists over the TraderPlus JSON configs — trader NPCs,
// trade objects, safe zones and garages.
//
// Every section has the same shape (title + count + an add button that arms
// map-add mode), so they share one <Section> here rather than four
// near-identical components. The lists are drawn with the shared parts in
// components/ (webui/DESIGN.md); only the map-data colours of the dots and the
// trader number stay in mapeditor.css.

import { useEditor } from '../state/MapEditorProvider'
import { MAP_ADD_MODES } from '../addModes'
import ListSection from '../../../components/ListSection.jsx'
import ListRow, { RowDot, RowNumber } from '../../../components/ListRow.jsx'
import RowAction from '../../../components/RowAction.jsx'
import LinkOut from '../../../components/LinkOut.jsx'
import PanelNote from '../../../components/PanelNote.jsx'
import { LINKS, configLink } from '../../../crossLinks'

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

const CloneIcon = () => (
  <svg
    width="12"
    height="12"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <rect x="9" y="9" width="13" height="13" rx="2" />
    <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
  </svg>
)

function Section({ title, count, addMode, addHint, editorKey, children }) {
  const { ui, dispatch, url } = useEditor()
  const armed = ui.addMode?.kind === 'map' && ui.addMode.mode === addMode

  function toggle() {
    if (armed) {
      dispatch({ type: 'EXIT_ADD' })
      return
    }
    url.setLayer('traders', true)
    dispatch({
      type: 'ENTER_MAP_ADD',
      payload: {
        mode: addMode,
        label: MAP_ADD_MODES[addMode].label,
        className: MAP_ADD_MODES[addMode].classDefault || '',
      },
    })
  }

  return (
    <ListSection
      title={title}
      count={count}
      actions={<RowAction icon={PlusIcon} label={addHint} active={armed} onClick={toggle} />}
      link={
        editorKey && (
          <LinkOut
            to={configLink(editorKey)}
            label={`Open ${LINKS[editorKey].label}`}
            tip={`Open ${LINKS[editorKey].label} — ${LINKS[editorKey].tip}`}
          />
        )
      }
    >
      {children}
    </ListSection>
  )
}

export default function TradersTab() {
  const { traders, safezones, garage, dispatch, url } = useEditor()

  const npcs = traders?.traders || []
  const objects = traders?.trader_objects || []
  const zones = safezones?.safezones || []
  const garages = garage?.garage_npcs || []

  // The map glows the marker it lands on (MapEditor.jsx's fly-to).
  function flyTo(lat, lng, zoom) {
    url.setLayer('traders', true)
    dispatch({ type: 'FLY_TO', payload: { lat, lng, zoom } })
  }

  function clone(mode, className, label, extra) {
    url.setLayer('traders', true)
    dispatch({
      type: 'ENTER_MAP_ADD',
      payload: {
        mode,
        label: `Clone ${MAP_ADD_MODES[mode].label}: ${label}`,
        className,
        clone: extra,
      },
    })
  }

  return (
    <>
      <PanelNote>Drag markers to reposition. Ctrl+drag to box-select and group-move.</PanelNote>

      <Section
        title="Trader NPCs"
        count={npcs.length}
        addMode="trader-npc"
        addHint="Add trader NPC (click map to place)"
        editorKey="tp-general"
      >
        {npcs.length === 0 ? (
          <PanelNote>No trader NPCs configured</PanelNote>
        ) : (
          npcs.map((t) => (
            <ListRow
              key={t.index}
              lead={
                <>
                  <RowNumber className="me-trader-id" tip={`Trader Id ${t.id} — NPCs with same Id share stock`}>
                    {t.id}
                  </RowNumber>
                  <RowDot className="me-trader-npc-dot" />
                </>
              }
              name={t.given_name}
              nameTip={t.name}
              meta={t.role}
              onClick={() => flyTo(t.lat, t.lng, 8)}
              actions={
                <RowAction
                  icon={CloneIcon}
                  label={`Clone ${t.given_name}`}
                  data-tooltip-content="Clone this NPC (same Id/stock, new position)"
                  onClick={() =>
                    clone('trader-npc', t.name, `${t.given_name} (Id ${t.id})`, {
                      id: t.id,
                      given_name: t.given_name,
                      role: t.role,
                      clothes: t.clothes || [],
                    })
                  }
                />
              }
            />
          ))
        )}
      </Section>

      <Section
        title="Trader Objects"
        count={objects.length}
        addMode="trader-object"
        addHint="Add trader object (click map to place)"
        editorKey="tp-general"
      >
        {objects.length === 0 ? (
          <PanelNote>No trader objects configured</PanelNote>
        ) : (
          objects.map((o) => (
            <ListRow
              key={o.index}
              lead={<RowDot className="me-trader-object-dot" />}
              name={o.object_name}
              onClick={() => flyTo(o.lat, o.lng, 8)}
              actions={
                <RowAction
                  icon={CloneIcon}
                  label={`Clone ${o.object_name}`}
                  data-tooltip-content="Clone this object at a new position"
                  onClick={() => clone('trader-object', o.object_name, o.object_name)}
                />
              }
            />
          ))
        )}
      </Section>

      <Section
        title="Safe Zones"
        count={zones.length}
        addMode="safezone"
        addHint="Add safe zone (click map to place)"
        editorKey="tp-safezone"
      >
        {zones.length === 0 ? (
          <PanelNote>No safe zones configured</PanelNote>
        ) : (
          zones.map((zone) => (
            <ListRow
              key={zone.index}
              lead={<RowDot className="me-safezone-dot" />}
              name={zone.name}
              meta={`r=${zone.radius}`}
              onClick={() => flyTo(zone.lat, zone.lng, 5)}
            />
          ))
        )}
      </Section>

      <Section
        title="Garage NPCs"
        count={garages.length}
        addMode="garage-npc"
        addHint="Add garage NPC (click map to place)"
        editorKey="tp-garage"
      >
        {garages.length === 0 ? (
          <PanelNote>No garage NPCs configured</PanelNote>
        ) : (
          garages.map((npc) => (
            <ListRow
              key={npc.index}
              lead={<RowDot className="me-garage-dot" />}
              name={npc.class_name}
              nameTip={npc.class_name}
              onClick={() => flyTo(npc.lat, npc.lng, 6)}
            />
          ))
        )}
      </Section>
    </>
  )
}
