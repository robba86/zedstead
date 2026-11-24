// The design reference (plan 89 D2, webui/DESIGN.md): every shared part in
// every state, drawn by the real components with fixed sample data. It calls
// no API, so it shows the same in every theme and at every role. Switch the
// theme in the sidebar to compare. Reached by URL only (/design), not the nav.
// Two tabs: Parts (this file) and Components (DesignComponents.jsx).

import { useState } from 'react'
import { HiOutlineDocumentDuplicate, HiOutlinePencilSquare, HiOutlineTrash } from 'react-icons/hi2'

import CountBadge from '../components/CountBadge.jsx'
import { Switch } from '../components/FlagSwitch.jsx'
import LinkOut from '../components/LinkOut.jsx'
import ListRow, { RowDot, RowNumber, RowTag, SeverityDot } from '../components/ListRow.jsx'
import ListSection from '../components/ListSection.jsx'
import PageIntro from '../components/PageIntro.jsx'
import PanelNote from '../components/PanelNote.jsx'
import RowAction from '../components/RowAction.jsx'
import StateNotice from '../components/StateNotice.jsx'
import { DESIGN_TABS } from '../pageTabs.js'
import { useUrlTabs } from '../urlTabs.js'
import DesignComponents from './DesignComponents.jsx'
import Tabs from '../components/Tabs.jsx'

const INTRO =
  'Parts: every shared part in every state, as webui/DESIGN.md describes it. Components: every generic component, with the old look-alikes beside the part that replaces them. The page uses sample data and changes nothing. Switch the theme or the Slim style to compare.'

// Sample colours for the dots: theme tokens, so the page needs no raw hex.
const DOTS = ['var(--accent)', 'var(--good)', 'var(--warning)', 'var(--danger)']

const ACTIONS = (
  <>
    <RowAction icon={HiOutlinePencilSquare} label="Edit" />
    <RowAction icon={HiOutlineDocumentDuplicate} label="Duplicate" />
    <RowAction icon={HiOutlineTrash} label="Delete" danger />
  </>
)

/** One labelled sample, so each state on the page says what it is. */
function Sample({ label, children }) {
  return (
    <div className="design-sample">
      <div className="design-sample-label">{label}</div>
      {children}
    </div>
  )
}

export default function DesignPage() {
  const { tab, setTab } = useUrlTabs(DESIGN_TABS)
  return (
    <div className="page design-page">
      <div className="page-header">
        <h1>Design reference</h1>
      </div>
      <PageIntro text={INTRO} />
      <Tabs tabs={DESIGN_TABS} active={tab} onSelect={setTab} />
      {tab === 'Parts' ? <DesignParts /> : <DesignComponents />}
    </div>
  )
}

/** The Parts tab: each part from webui/DESIGN.md in each state. */
function DesignParts() {
  const [picked, setPicked] = useState('Selected row')
  const [on, setOn] = useState(true)
  const [open, setOpen] = useState(true)
  const pick = (name) => () => setPicked(name)

  return (
    <div className="design-grid">
        <div className="card">
          <div className="card-header">
            <h2>Section header</h2>
          </div>
          <Sample label="Plain, with a count and a link out">
            <ListSection title="Trader NPCs" count={7} link={<LinkOut to="/design" label="Open the file" />}>
              <PanelNote>The section body.</PanelNote>
            </ListSection>
          </Sample>
          <Sample label="Foldable, with a lead dot and a count in parts">
            <ListSection
              title="Fresh spawns"
              lead={<RowDot color={DOTS[1]} />}
              count={[
                { value: 11, unit: 'groups' },
                { value: 49, unit: 'pts' },
              ]}
              foldable
              open={open}
              onOpenChange={setOpen}
              actions={<RowAction icon={HiOutlinePencilSquare} label="Edit the section" />}
            >
              <PanelNote>Fold me with the caret.</PanelNote>
            </ListSection>
          </Sample>
        </div>

        <div className="card">
          <div className="card-header">
            <h2>List row</h2>
          </div>
          <Sample label="Every state">
            {[
              ['Plain row', {}],
              ['Selected row', {}],
              ['Dimmed row', { dimmed: true }],
              ['Disabled row', { disabled: true }],
            ].map(([name, props], i) => (
              <ListRow
                key={name}
                lead={<RowDot color={DOTS[i]} />}
                name={name}
                sub="4353.0, 10481.5 · Y 338.1"
                meta={<CountBadge value={(i + 1) * 12} />}
                actions={ACTIONS}
                selected={picked === name}
                onClick={pick(name)}
                {...props}
              />
            ))}
          </Sample>
          <Sample label="Compact, with a number and a tag">
            <ListRow compact lead={<RowNumber>3</RowNumber>} name="Compact row" meta="12" />
            <ListRow compact lead={<RowNumber>4</RowNumber>} name="Compact row with a tag" meta={<RowTag>cooldown</RowTag>} />
          </Sample>
          <Sample label="Wrap, with a severity dot: a check or a fact with a long detail">
            {['error', 'warning', 'ok'].map((sev) => (
              <ListRow
                key={sev}
                wrap
                lead={<SeverityDot severity={sev} />}
                name={`A ${sev} check`}
                sub="A long detail wraps onto a second line instead of being cut, such as /home/zsm/servers/dayz/mpmissions/dayzOffline.chernarusplus/db/types.xml."
              />
            ))}
          </Sample>
          <Sample label="Mono: a name that is code, such as an RCon command">
            <ListRow compact wrap mono name="kick <player#> [reason]" sub="Kick a player by number." />
          </Sample>
        </div>

        <div className="card">
          <div className="card-header">
            <h2>Count, badge and row button</h2>
          </div>
          <Sample label="CountBadge">
            <span className="design-inline">
              <CountBadge value={3} />
              <CountBadge value={3796} />
              <CountBadge parts={[{ value: 12, unit: 'events' }, { value: 972, unit: 'pts' }]} />
              <CountBadge value="×20" />
            </span>
          </Sample>
          <Sample label="Status pill and RowTag">
            <span className="design-inline">
              <span className="status-pill status-pill-running">running</span>
              <span className="status-pill status-pill-success">success</span>
              <span className="status-pill status-pill-warning">warning</span>
              <span className="status-pill status-pill-failed">failed</span>
              <span className="status-pill status-pill-neutral">neutral</span>
              <RowTag>disabled</RowTag>
            </span>
          </Sample>
          <Sample label="RowAction: plain, active, danger, disabled, and LinkOut">
            <span className="design-inline">
              <RowAction icon={HiOutlinePencilSquare} label="Plain" />
              <RowAction icon={HiOutlinePencilSquare} label="Active" active />
              <RowAction icon={HiOutlineTrash} label="Danger" danger />
              <RowAction icon={HiOutlineTrash} label="Disabled" disabled />
              <LinkOut to="/design" label="Link out" />
            </span>
          </Sample>
        </div>

        <div className="card">
          <div className="card-header">
            <h2>Notes and switch</h2>
          </div>
          <Sample label="PanelNote: plain, with a link, warning">
            <PanelNote>A help line or an empty state.</PanelNote>
            <PanelNote link={{ to: '/design', label: 'Open the editor', tip: 'A link at the end of the line' }}>
              A help line with a link.
            </PanelNote>
            <PanelNote warn>A setting stops DayZ from reading this list.</PanelNote>
          </Sample>
          <Sample label="StateNotice: default state, then the other state in charge">
            <StateNotice>The server uses the vanilla player spawns.</StateNotice>
            <StateNotice active>Expansion spawn selection is on. DayZ ignores the vanilla spawns.</StateNotice>
          </Sample>
          <Sample label="Switch">
            <Switch checked={on} onChange={setOn}>
              A switch
            </Switch>
            <Switch checked={false} disabled onChange={() => {}}>
              A disabled switch
            </Switch>
          </Sample>
        </div>
    </div>
  )
}
