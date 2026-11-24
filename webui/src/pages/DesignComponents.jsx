// The Components tab of the design reference (/design?tab=Components): every
// generic component in webui/src/components/ drawn once with sample data, and
// the old look-alikes beside the part that replaces them, so duplicates show
// side by side before they are merged (plan 89). A component that only makes
// sense with live server data is named in FEATURES instead of drawn.
// tests/test_design_parts.py checks that every components/*.jsx file is either
// imported here or named in FEATURES, so a new component has to pick one.

import { useState } from 'react'
import { HiOutlineArrowTopRightOnSquare, HiOutlineCog6Tooth, HiOutlinePlus, HiOutlineTrash } from 'react-icons/hi2'

import AutoRefreshIndicator from '../components/AutoRefreshIndicator.jsx'
import BarChart from '../components/BarChart.jsx'
import BetaBadge, { BetaPill } from '../components/BetaBadge.jsx'
import BranchBadge from '../components/BranchBadge.jsx'
import CodeArea from '../components/CodeArea.jsx'
import ColorField from '../components/ColorField.jsx'
import CommandBlock from '../components/CommandBlock.jsx'
import ConfirmModal from '../components/ConfirmModal.jsx'
import CountBadge from '../components/CountBadge.jsx'
import DataTable from '../components/DataTable.jsx'
import DefaultPill from '../components/DefaultPill.jsx'
import DetailText from '../components/DetailText.jsx'
import DiffView from '../components/DiffView.jsx'
import ErrorBanner from '../components/ErrorBanner.jsx'
import FileIcon from '../components/FileIcon.jsx'
import FilterBox from '../components/FilterBox.jsx'
import FoldCaret from '../components/FoldCaret.jsx'
import FormField from '../components/FormField.jsx'
import HelpButton from '../components/HelpButton.jsx'
import IconButton from '../components/IconButton.jsx'
import RestoreIcon from '../components/RestoreIcon.jsx'
import RowMenu from '../components/RowMenu.jsx'
import ItemIcon from '../components/ItemIcon.jsx'
import JsonTree from '../components/JsonTree.jsx'
import LoadingState from '../components/LoadingState.jsx'
import ListField from '../components/ListField.jsx'
import { RowTag } from '../components/ListRow.jsx'
import LogPane, { ProgressBar } from '../components/LogPane.jsx'
import ModNameText from '../components/ModNameText.jsx'
import MultiSelectList from '../components/MultiSelectList.jsx'
import Pager from '../components/Pager.jsx'
import PageIntro from '../components/PageIntro.jsx'
import PanelNote from '../components/PanelNote.jsx'
import PasswordField from '../components/PasswordField.jsx'
import PerformanceSparklines from '../components/PerformanceSparklines.jsx'
import RangeField, { sliderScale } from '../components/RangeField.jsx'
import RconCommandInput from '../components/RconCommandInput.jsx'
import RefreshButton from '../components/RefreshButton.jsx'
import ScrollTable from '../components/ScrollTable.jsx'
import SearchInput from '../components/SearchInput.jsx'
import Suggestions from '../components/Suggestions.jsx'
import UnitInput from '../components/UnitInput.jsx'
import Segmented from '../components/Segmented.jsx'
import FilterSelect, { Filters } from '../components/FilterSelect.jsx'
import Select from '../components/Select.jsx'
import { Switch } from '../components/FlagSwitch.jsx'
import SideDrawer from '../components/SideDrawer.jsx'
import { SkeletonLines } from '../components/Skeleton.jsx'
import Sparkline from '../components/Sparkline.jsx'
import Tabs from '../components/Tabs.jsx'
import TileStats from '../components/TileStats.jsx'
import { useToast } from '../components/Toast.jsx'
import Tooltip from '../components/Tooltip.jsx'
import { useConfirm } from '../components/useConfirm.jsx'
import WorkshopLink from '../components/WorkshopLink.jsx'

// Components that need live server data or one page's state. They are listed,
// not drawn: each is a feature, not a part to build with.
export const FEATURES = {
  'App frame': [
    'Layout', 'InfoBar', 'Breadcrumbs', 'CommandPalette', 'ActivityDrawer', 'GuideDrawer', 'RoleGate',
    'RoleLink', 'ErrorBoundary', 'Logo', 'Wordmark', 'ThemeToggle', 'StyleToggle', 'NeedsSteam',
    'SteamLoginGate', 'Toast', 'Skeleton', 'StickyActionBar',
  ],
  'Dashboard tiles and cards': [
    'StatusCard', 'ServerControls', 'PlayersCard', 'BansCard', 'BackupsCard', 'ModsCard', 'ScheduleCard',
    'EconomyCard', 'DoctorCard', 'SortableCard', 'QuickSetupStart', 'SelfUpdateCard', 'ServiceSetupCard',
    'BootCheckCard', 'InstallServerCard', 'SteamLoginCard', 'SpawnEditorCard', 'UsersCard', 'AccessLogCard',
    'OffsiteRemoteCard', 'RunCommandCard', 'GuideCard', 'StatusFieldEditor', 'PerformanceCard',
  ],
  Players: ['PlayerActionDialog'],
  'Config editors': [
    'ConfigEditorDrawer', 'EditorDrawer', 'BespokeEditorDrawer', 'FormEditor', 'TableEditor', 'TextEditor', 'RawEditor', 'XmlTools', 'NestedParts',
    'FixCell', 'FileMissing', 'FileVersions', 'EditorVersions', 'ChangeReport', 'ChangeTimeline',
    'PresetPreview', 'SettingQuestion', 'HeaderToggle', 'IgnoreListInteractive', 'MessagesInteractive',
    'WeatherPresets', 'DependencyEdges', 'ModReportCard', 'LogFindings',
    'SmartView', 'SmartType', 'SmartEvent',
  ],
  'Zone and map cards': ['DynamicAiCard', 'ExpansionAiCard', 'RadZoneCard', 'ToxicZoneCard', 'ZoneOfferCard', 'MapSwitchDialog'],
}

const CHOICES = [
  { value: 'chernarusplus', label: 'Chernarus' },
  { value: 'enoch', label: 'Livonia' },
  { value: 'sakhal', label: 'Sakhal' },
]

// Sample metrics for PerformanceSparklines, shaped like /api/metrics/summary and /recent.
const PERF_RECENT = [42, 45, 40, 38, 44, 47, 46].map((fps, i) => ({ fps, memory_mb: 3100 + i * 40, players: [3, 4, 4, 6, 5, 7, 6][i] }))
const PERF_SUMMARY = { samples: PERF_RECENT.length, fps_avg: 43.1, memory_avg: 3220, players_peak: 7 }

const TABLE_ROWS = [
  { name: 'AKM', nominal: 12, min: 6 },
  { name: 'Mosin9130', nominal: 20, min: 10 },
  { name: 'SKS', nominal: 15, min: 8 },
]
const TABLE_COLUMNS = [
  { key: 'name', label: 'Item', className: 'cell-name' },
  { key: 'nominal', label: 'Nominal', help: 'How many the economy keeps on the map.' },
  { key: 'min', label: 'Min' },
]

const DIFF = '@@ -1,3 +1,3 @@\n <type name="AKM">\n-  <nominal>10</nominal>\n+  <nominal>12</nominal>'
const LOG_LINES = ['Loading mission…', 'Mission read.', 'Player "Survivor" is connected', 'Server shutting down']

/** One labelled sample, the same frame the Parts tab uses. */
function Sample({ label, children }) {
  return (
    <div className="design-sample">
      <div className="design-sample-label">{label}</div>
      {children}
    </div>
  )
}

/** A card of samples with its H2. */
function Group({ title, children }) {
  return (
    <div className="card">
      <div className="card-header">
        <h2>{title}</h2>
      </div>
      {children}
    </div>
  )
}

export default function DesignComponents() {
  const pushToast = useToast()
  const confirm = useConfirm()
  const [text, setText] = useState('')
  const [hintText, setHintText] = useState('')
  const [hintPick, setHintPick] = useState('Short')
  const [hintOn, setHintOn] = useState(true)
  const [map, setMap] = useState('chernarusplus')
  const [maps, setMaps] = useState(['enoch'])
  const [mode, setMode] = useState('auto')
  const [chip, setChip] = useState('')
  const [rangeValues, setRangeValues] = useState({ a: '3', b: '', c: '0.25' })
  const [color, setColor] = useState('0xFFCC3333')
  const [list, setList] = useState(['AKM', 'SKS'])
  const [rcon, setRcon] = useState('')
  const [json, setJson] = useState({ enabled: true, radius: 150, items: ['Apple', 'Rag'] })
  const [code, setCode] = useState('{\n  "radius": 150\n}')
  const [editorTab, setEditorTab] = useState('form')
  const [page, setPage] = useState(1)
  const [modal, setModal] = useState(false)
  const [drawer, setDrawer] = useState(false)
  const [open, setOpen] = useState(false)

  return (
    <div className="design-grid">
      <Group title="Buttons">
        <Sample label=".btn variants">
          <span className="design-inline">
            <button className="btn btn-primary">Primary</button>
            <button className="btn btn-ghost">Ghost</button>
            <button className="btn btn-danger">Danger</button>
            <button className="btn btn-good">Good</button>
            <button className="btn btn-link">Link</button>
            <button className="btn btn-primary" disabled>
              Disabled
            </button>
          </span>
        </Sample>
        <Sample label=".btn-small, .btn-icon, .btn-add">
          <span className="design-inline">
            <button className="btn btn-ghost btn-small">Small</button>
            <button className="btn btn-icon" aria-label="Settings">
              <HiOutlineCog6Tooth aria-hidden="true" />
            </button>
            <button className="btn btn-ghost btn-add">
              <HiOutlinePlus aria-hidden="true" /> Add row
            </button>
          </span>
        </Sample>
        <Sample label="RefreshButton, HelpButton">
          <span className="design-inline">
            <RefreshButton onRefresh={() => pushToast('Refreshed', 'success')} what="the sample" />
            <RefreshButton onRefresh={() => {}} what="the sample" icon />
            <HelpButton text="A longer help text that does not fit on one line." />
          </span>
        </Sample>
        <Sample label="IconButton: plain, danger, and a link (href)">
          <span className="design-inline">
            <IconButton icon={HiOutlineCog6Tooth} label="Settings" onClick={() => pushToast('Clicked', 'success')} />
            <IconButton icon={HiOutlineTrash} label="Remove the sample" danger onClick={() => {}} />
            <IconButton icon={HiOutlineArrowTopRightOnSquare} label="Open the design page" href="#" />
          </span>
        </Sample>
        <Sample label="RowMenu: a row's ⋯ menu, with a radio set under a heading and a danger item">
          <RowMenu
            label="Sample row: more actions"
            items={[
              { key: 'update', label: 'Update', onClick: () => pushToast('Update', 'success') },
              { key: 'off', label: 'Not now', disabled: true, tip: 'A disabled item says why in its tip.' },
              { key: 'a', group: 'Loads for', divider: true, label: 'Players + server', checked: true },
              { key: 'b', label: 'Server only', checked: false },
              { key: 'rm', label: 'Uninstall', divider: true, danger: true, onClick: () => {} },
            ]}
          />
        </Sample>
        <Sample label="RestoreIcon: on a button that puts back an earlier state (Reset and Undo keep the u-turn arrow)">
          <button type="button" className="btn btn-ghost">
            <RestoreIcon />
            Restore
          </button>
        </Sample>
      </Group>

      <Group title="Text inputs">
        <Sample label=".text-input, PasswordField">
          <span className="design-inline">
            <input className="text-input text-input-narrow" placeholder="A value" aria-label="A value" />
            <PasswordField placeholder="Password" aria-label="Password" />
          </span>
        </Sample>
        <Sample label="FormField: required with a hint, optional with a Clear button, and a wrong value">
          <FormField label="Host" hint="The SFTP server's name or address.">
            <input className="field" placeholder="backup.example.com" />
          </FormField>
          <FormField
            label="Password"
            optional
            action={
              <button type="button" className="btn btn-ghost btn-small">
                Clear
              </button>
            }
          >
            <PasswordField className="field" placeholder="Stored. Leave blank to keep it." />
          </FormField>
          <FormField label="Port" optional error="A number from 1 to 65535.">
            <input className="field" defaultValue="22x" aria-invalid />
          </FormField>
        </Sample>
        <Sample label="RangeField: whole steps, a key left out (dimmed knob on the default), and a 0.0-1.0 share as a percentage">
          {[
            { key: 'a', label: 'Speedhack detection', type: 'float', min: 1, max: 10 },
            { key: 'b', label: 'Voice quality', type: 'int', min: 0, max: 30, default: 20 },
            { key: 'c', label: 'Loot damage', type: 'float', min: 0, max: 1 },
          ].map((f) => (
            <RangeField
              key={f.key}
              field={f}
              scale={sliderScale(f)}
              value={rangeValues[f.key]}
              edited
              onChange={(v) => setRangeValues((prev) => ({ ...prev, [f.key]: v }))}
            />
          ))}
        </Sample>
        <Sample label="UnitInput: a number with its unit in the box (a field's `unit` from the backend)">
          <UnitInput unit="seconds" className="text-input text-input-narrow" type="number" defaultValue={5} aria-label="Respawn time" />
        </Sample>
        <Sample label="UnitInput with `defaultHint`: an empty box shows the server's default and a Default pill">
          <UnitInput unit="seconds" defaultHint={5} className="text-input text-input-narrow" type="number" value={hintText} onChange={(e) => setHintText(e.target.value)} aria-label="Log slow" />
          <UnitInput defaultHint={10} className="text-input text-input-narrow" type="number" value={hintText} onChange={(e) => setHintText(e.target.value)} aria-label="Send create" />
        </Sample>
        <Sample label="Select with `defaultHint` and Switch with a `badge`: the Default pill while on the server's default, and a Before 1.30 pill on a key a newer DayZ replaced">
          <Select className="text-input text-input-narrow" aria-label="Timestamp format" options={['Full', 'Short']} defaultHint="Short" value={hintPick} onChange={setHintPick} />
          <Switch checked={hintOn} badge={hintOn ? <DefaultPill className="flag-switch-default" /> : null} onChange={setHintOn}>Storage auto fix</Switch>
          <Switch checked={false} badge={<DefaultPill className="flag-switch-default">Before 1.30</DefaultPill>} onChange={() => {}}>Disable 3rd person</Switch>
        </Sample>
        <Sample label="DefaultPill: the pill itself, for a new control that can be on a server default (atDefault decides)">
          <DefaultPill />
        </Sample>
        <Sample label="SearchInput">
          <SearchInput value={text} onChange={(e) => setText(e.target.value)} placeholder="Search…" aria-label="Search" />
        </Sample>
        <Sample label="Suggestions: the dropdown under a text box, with a group, an active row and a dimmed row">
          <Suggestions
            items={[
              { key: 'a', name: 'VehicleCarPack_Sedan', group: 'events.xml', meta: 'vehicle' },
              { key: 'b', name: 'ItemCompass', group: 'types.xml', meta: 'tools' },
              { key: 'c', name: 'AnimalBear', group: 'types.xml', meta: <RowTag>exists</RowTag>, dimmed: true },
            ]}
            activeIndex={1}
            onChoose={() => {}}
            more="12 more matches"
          />
        </Sample>
        <Sample label="RconCommandInput: suggestions while the first word is typed, and the foldable command list (a pick fills the box, Send does nothing here)">
          <RconCommandInput value={rcon} onChange={setRcon} onSubmit={() => {}} busy={false} disabled={false} />
        </Sample>
        <Sample label='FilterBox, with "Showing X of Y" once you type'>
          <FilterBox value={text} onChange={setText} shown={text ? 2 : 3} total={3} label="items" />
        </Sample>
        <Sample label="ColorField">
          <ColorField value={color} format="0x-argb" onChange={setColor} label="Colour" />
        </Sample>
        <Sample label="CodeArea">
          <CodeArea value={code} onChange={setCode} language="json" />
        </Sample>
      </Group>

      <Group title="Choices">
        <Sample label="Select">
          <Select value={map} onChange={setMap} options={CHOICES} aria-label="Map" />
        </Sample>
        <Sample label="Select multi">
          <Select value={maps} onChange={setMaps} options={CHOICES} multi aria-label="Maps" />
        </Sample>
        <Sample label="Segmented">
          <Segmented
            value={mode}
            onChange={setMode}
            label="Mode"
            options={[
              { value: 'auto', label: 'Auto' },
              { value: 'manual', label: 'Manual' },
            ]}
          />
        </Sample>
        <Sample label="Filters + FilterSelect">
          <Filters>
            <FilterSelect label="Rarity" value={chip} onChange={setChip} available={new Set(['Rare', 'never'])} options={['Rare', 'Common', { value: 'never', label: 'Never spawns', description: 'Nominal 0' }]} />
          </Filters>
        </Sample>
        <Sample label="ListField">
          <ListField value={list} onChange={setList} label="Items" />
        </Sample>
        <Sample label="MultiSelectList">
          <MultiSelectList values={list} onChange={setList} label="Items" pool={['Apple', 'Rag', 'AKM']} />
        </Sample>
        <Sample label="JsonTree">
          <JsonTree value={json} onChange={setJson} name="sample" />
        </Sample>
      </Group>

      <Group title="Pills, badges and look-alikes">
        <Sample label="Shared: .status-pill, CountBadge, RowTag">
          <span className="design-inline">
            <span className="status-pill status-pill-pending">pending</span>
            <span className="status-pill status-pill-cancelling">cancelling</span>
            <span className="status-pill status-pill-cancelled">cancelled</span>
            <CountBadge value={12} />
            <RowTag>cooldown</RowTag>
          </span>
        </Sample>
        <Sample label="BetaPill, BetaBadge, BranchBadge">
          <span className="design-inline">
            <BetaPill />
            <BetaBadge feature="offsite" />
            <BranchBadge branch="experimental" />
          </span>
        </Sample>
        <Sample label=".status-pill-plain (a phrase), and a pill that is a button">
          <span className="design-inline">
            <span className="status-pill status-pill-warning status-pill-plain">2 warnings</span>
            <span className="status-pill status-pill-neutral status-pill-plain">3 mod quirks</span>
            <button type="button" className="status-pill status-pill-running status-pill-plain">
              4 not applied yet
            </button>
          </span>
        </Sample>
        <Sample label=".link-chip, after text and in a row">
          <span>
            No mods installed yet.
            <a className="link-chip" href="#design">
              Manage mods
            </a>
          </span>
        </Sample>
        <Sample label="Filter chip: .me-loot-chip, the Loot finder.s Found in buttons (one user, so no shared part yet)">
          <span className="me-loot-chip">Village</span>
        </Sample>
      </Group>

      <Group title="Help lines and messages">
        <Sample label="Shared: PanelNote">
          <PanelNote>A help line or an empty state.</PanelNote>
        </Sample>
        <Sample label=".text-muted (muted text inside a line or a row)">
          <span className="text-muted">No data</span>
        </Sample>
        <Sample label="PageIntro sub (a tab or section intro)">
          <PageIntro text="A tab intro, smaller than the page intro." sub />
        </Sample>
        <Sample label="ErrorBanner">
          <ErrorBanner>The server failed to start. Open the log.</ErrorBanner>
        </Sample>
        <Sample label="DetailText, ModNameText">
          <DetailText text="Could not read /home/zsm/serverDZ.cfg: the file is missing." />
          <DetailText text="Lingering is disabled. Run `sudo loginctl enable-linger zsm` or `zsm service enable-linger`." />
          <ModNameText text="@CF needs @Dabs Framework loaded first." known={new Set()} />
        </Sample>
        <Sample label="Tooltip">
          <Tooltip text="The tooltip text.">
            <span>Point at this text</span>
          </Tooltip>
        </Sample>
        <Sample label="Toast, ConfirmModal, useConfirm">
          <span className="design-inline">
            <button className="btn btn-ghost btn-small" onClick={() => pushToast('A success toast', 'success')}>
              Toast
            </button>
            <button className="btn btn-ghost btn-small" onClick={() => pushToast('An error toast', 'error')}>
              Error toast
            </button>
            <button className="btn btn-ghost btn-small" onClick={() => setModal(true)}>
              ConfirmModal
            </button>
            <button
              className="btn btn-ghost btn-small"
              onClick={async () => pushToast(`Answer: ${await confirm({ title: 'Delete the sample?', danger: true })}`)}
            >
              useConfirm
            </button>
          </span>
          <ConfirmModal
            open={modal}
            title="Confirm the sample?"
            message="Nothing changes, this is the design page."
            onConfirm={() => setModal(false)}
            onCancel={() => setModal(false)}
          />
        </Sample>
      </Group>

      <Group title="Progress and loading">
        <Sample label="ProgressBar">
          <ProgressBar progress={0.42} label="Download" />
        </Sample>
        <Sample label="AutoRefreshIndicator">
          <AutoRefreshIndicator paused={false} progress={0.6} secondsLeft={24} onToggle={() => {}} />
        </Sample>
        <Sample label="SkeletonLines">
          <SkeletonLines count={3} />
        </Sample>
        <Sample label="LoadingState: the one full-page loading state">
          <LoadingState label="Loading mission files…" />
        </Sample>
      </Group>

      <Group title="Data">
        <Sample label="TileStats">
          <TileStats
            items={[
              { label: 'online', value: 4, tone: 'good' },
              { label: 'bans', value: 1, tone: 'warning' },
              { label: 'slots', value: 60, tone: 'muted' },
            ]}
          />
        </Sample>
        <Sample label="BarChart">
          <BarChart
            title="Score per category"
            max={1}
            rows={[
              { key: 'weapons', label: 'Weapons', value: 0.8, text: '80%' },
              { key: 'food', label: 'Food', value: 0.45, text: '45%' },
            ]}
          />
        </Sample>
        <Sample label="Sparkline">
          <Sparkline values={[3, 5, 4, 8, 6, 9, 7]} />
        </Sample>
        <Sample label="PerformanceSparklines: the last hour of FPS, memory and players (Dashboard tile, Analysis)">
          <PerformanceSparklines summary={PERF_SUMMARY} recent={PERF_RECENT} />
        </Sample>
        <Sample label="DataTable, Pager">
          <DataTable columns={TABLE_COLUMNS} rows={TABLE_ROWS} rowKey={(r) => r.name} />
          <Pager page={page} totalPages={4} onPrev={() => setPage((p) => p - 1)} onNext={() => setPage((p) => p + 1)} />
        </Sample>
        <Sample label="ScrollTable">
          <ScrollTable>
            <table className="data-table">
              <tbody>
                {TABLE_ROWS.map((r) => (
                  <tr key={r.name}>
                    <td>{r.name}</td>
                    <td>{r.nominal}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </ScrollTable>
        </Sample>
      </Group>

      <Group title="Text and code">
        <Sample label="DiffView">
          <DiffView text={DIFF} />
        </Sample>
        <Sample label="CommandBlock">
          <CommandBlock text="zsm run --instance main" />
        </Sample>
        <Sample label="LogPane">
          <LogPane title="Server log" lines={LOG_LINES} expanded={false} onToggleExpand={() => {}} />
        </Sample>
      </Group>

      <Group title="Icons and links">
        <Sample label="ItemIcon, FileIcon, FoldCaret">
          <span className="design-inline">
            <ItemIcon name="AKM" />
            <ItemIcon name="Apple" />
            <FileIcon action="view" />
            <FileIcon action="edit" />
            <button className="btn btn-link" onClick={() => setOpen(!open)}>
              <FoldCaret open={open} /> Fold
            </button>
          </span>
        </Sample>
        <Sample label="Text link (index.css :where(a:link, a:visited), DESIGN.md Text link)">
          <p>
            A <a href="#design">link inside text</a> is accent blue and underlined, and a lighter blue on hover.
          </p>
        </Sample>
        <Sample label="WorkshopLink">
          <WorkshopLink id="1559212036">CF on the Workshop</WorkshopLink>
        </Sample>
      </Group>

      <Group title="Tabs and drawers">
        <Sample label="Tabs, with a disabled tab, and Tabs sub">
          <Tabs
            tabs={[
              { id: 'form', label: 'Form' },
              { id: 'raw', label: 'Raw' },
              { id: 'off', label: 'Disabled', disabled: true, tip: 'A disabled tab with a reason.' },
            ]}
            active={editorTab}
            onSelect={setEditorTab}
          />
          <Tabs tabs={['Vanilla items', 'Mod and custom items']} active="Vanilla items" onSelect={() => {}} sub />
        </Sample>
        <Sample label="SideDrawer">
          <button className="btn btn-ghost btn-small" onClick={() => setDrawer(true)}>
            Open the drawer
          </button>
          {drawer && (
            <SideDrawer title="Sample drawer" subtitle="A subtitle" storageKey="design-drawer" onClose={() => setDrawer(false)}>
              <PanelNote>The drawer body.</PanelNote>
            </SideDrawer>
          )}
        </Sample>
      </Group>

      <Group title="Not drawn: features">
        <PanelNote>These components need live server data or one page's state. Open their page to see them.</PanelNote>
        {Object.entries(FEATURES).map(([group, names]) => (
          <Sample key={group} label={group}>
            <span className="design-inline">
              {names.map((n) => (
                <code key={n}>{n}</code>
              ))}
            </span>
          </Sample>
        ))}
      </Group>
    </div>
  )
}
