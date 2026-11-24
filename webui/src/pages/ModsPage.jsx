// The Mods page: the installed list and its load order, the Install and
// Updates tabs, and the batch actions on the selected mods.

import { Switch } from '../components/FlagSwitch.jsx'
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { DndContext, PointerSensor, closestCenter, useSensor, useSensors } from '@dnd-kit/core'
import { SortableContext, useSortable, verticalListSortingStrategy } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import {
  HiOutlineArrowDownTray,
  HiOutlineArrowPath,
  HiOutlineArrowUpCircle,
  HiOutlineArrowUturnLeft,
  HiOutlineCheck,
  HiOutlineCodeBracket,
  HiOutlineCog6Tooth,
  HiOutlineLockClosed,
  HiOutlineLockOpen,
  HiOutlineMagnifyingGlass,
  HiOutlinePower,
  HiOutlineTrash,
  HiOutlineXMark,
  HiOutlineSparkles,
} from 'react-icons/hi2'
import RestoreIcon from '../components/RestoreIcon.jsx'
import { api } from '../api'
import SearchInput from '../components/SearchInput.jsx'
import { useWs } from '../ws'
import { useToast } from '../components/Toast.jsx'
import ConfirmModal from '../components/ConfirmModal.jsx'
import { useConfirm } from '../components/useConfirm.jsx'
import DataTable from '../components/DataTable.jsx'
import Tooltip, { tooltipAttrs } from '../components/Tooltip.jsx'
import BetaBadge from '../components/BetaBadge.jsx'
import PageIntro from '../components/PageIntro.jsx'
import Segmented from '../components/Segmented.jsx'
import NeedsSteam, { NEEDS_STEAM_TIP, useSteamLoggedOut } from '../components/NeedsSteam.jsx'
import RowMenu from '../components/RowMenu.jsx'
import { rowActionClass } from '../components/RowAction.jsx'
import SteamLoginGate from '../components/SteamLoginGate.jsx'
import WorkshopLink from '../components/WorkshopLink.jsx'
import ModNameText from '../components/ModNameText.jsx'
import FixCell from '../components/FixCell.jsx'
import DependencyEdges from '../components/DependencyEdges.jsx'
import { copyBikeys, installBodyFor } from '../fixActions.js'
import { LINKS, configLink, manualFileLink, modLink, modXmlLink } from '../crossLinks.js'
import { formatWhen } from '../format.js'
import { useJobs } from '../jobs'
import { useHashTarget } from '../hashTarget.js'
import { useUrlTabs } from '../urlTabs.js'
import { MODS_INSTALL_SUBTABS as INSTALL_SUBTABS, MODS_TABS as TABS } from '../pageTabs.js'
import { useUnsavedChanges } from '../useUnsavedChanges.js'
import { modTabLabel } from '../guideSteps.js'
import { useMediaQuery } from '../useMediaQuery.js'
import useRangeTick from '../useRangeTick.js'
import InstallOptionsModal, { OrphanDepsField, wipeWarningFor } from '../installFlow.jsx'
import ExpansionMission from './ExpansionMission.jsx'
import PanelNote from '../components/PanelNote.jsx'
import { RowTag } from '../components/ListRow.jsx'
import Tabs from '../components/Tabs.jsx'
import FoldCaret from '../components/FoldCaret.jsx'
import CountBadge from '../components/CountBadge.jsx'
import CodeArea from '../components/CodeArea.jsx'

// The Auto configure dialog's three switches (mod_autoconfig.AutoConfigOptions),
// all on by default: what each does to the entries built from the PBO.
const AUTO_CONFIG_OPTIONS = [
  {
    key: 'split_nominal',
    label: 'Share nominal between variants',
    tip: 'Variants that copy the same vanilla item share its nominal and min, so 10 colours of one gun do not spawn 10 times as many.',
  },
  {
    key: 'guess_category',
    label: 'Guess the category',
    tip: 'An item with no known vanilla parent gets a category: weapons for a weapon or magazine class, or one from a whole word of its class name (TunaCan is food).',
  },
  {
    key: 'starter_nominal',
    label: 'Starter nominal',
    tip: 'An item with no known vanilla parent spawns at once: nominal 2 for a weapon, 4 for a magazine, 3 for any other item. Off: nominal 0, nothing spawns until you set it.',
  },
]
const AUTO_CONFIG_DEFAULTS = Object.fromEntries(AUTO_CONFIG_OPTIONS.map((o) => [o.key, true]))

// The Auto configure dialog's summary line: how many entries, from where.
function autoConfigMessage(p) {
  if (!p) return ''
  if (!p.count) return `Nothing to add for ${p.mod}: every class is skipped or already in the mission's types.`
  const from = []
  if (p.from_workshop?.length) from.push(`${p.from_workshop.length} from the author's Workshop page`)
  if (p.from_parent?.length) from.push(`${p.from_parent.length} copied from the vanilla item they extend`)
  if (p.from_defaults?.length) from.push(`${p.from_defaults.length} with no known parent`)
  return `Adds ${p.count} types.xml entries (${from.join(', ')}) as the manual XML file ${p.file_id}. Custom config then opens it in the Types form, where you change the values.`
}

// Install is the only tab with a nested bar, so ?subtab= means nothing elsewhere.
const SUBTABS_FOR = (tab) => (tab === 'Install' ? INSTALL_SUBTABS : null)

// A short tip, then the longer help text below it when the help has loaded.
const withHelp = (tip, more) => (more ? `${tip}\n\n${more}` : tip)

// The window width up to which the Installed list shows compact cards
// (index.css .installed-compact), not the table.
const COMPACT_CARDS_PX = 1024

const NO_API_KEY_TIP = 'Set steam_api_key in Settings first — Steam answers update checks only with a Web API key.'

// The Updates table's empty state says what was actually checked. Before
// plans/50 B it said "No updates detected." for a check that never ran.
function updatesEmptyMessage(check) {
  if (!check?.checked_at) return 'Not checked yet.'
  const when = formatWhen(check.checked_at * 1000)
  const missing = check.total - check.checked
  const unanswered = missing > 0 ? ` Steam gave no details for ${missing} (deleted, hidden or private).` : ''
  return `Checked ${check.checked} of ${check.total} mods at ${when} — none has an update.${unanswered}`
}

// "requires" or "required by", then one link chip per mod, each to its own
// mod page. Chips wrap as a whole, so a phone never breaks a name.
function DepChips({ verb, names }) {
  if (!names.length) return null
  return (
    <span className="dep-info dep-chips">
      {verb}
      {names.map((n) => (
        <Link key={n} className="link-chip" to={modLink(n)}>
          {n}
        </Link>
      ))}
    </span>
  )
}

// A count with its noun: "1 mod file", "2 manual files".
const fileCount = (n, kind) => n && `${n} ${kind} ${n === 1 ? 'file' : 'files'}`

// The Installed table's XML cell: the mod's own XML files ("mod"), the file
// Auto configuration wrote ("auto"), the admin's other manual files bound to
// it, and the files they turned off. The backend counts the Auto
// configuration file among `manual` and names it in `auto_file`, so it is
// taken out of the manual count here. Nothing at all when there is none, so
// only the mods with XML show a value.
function xmlSummary(s) {
  if (!s) return ''
  const auto = s.auto_file ? 1 : 0
  return [
    fileCount(s.files, 'mod'),
    fileCount(auto, 'auto'),
    fileCount((s.manual || 0) - auto, 'manual'),
    s.off && `${s.off} off`,
  ]
    .filter(Boolean)
    .join(' · ')
}

// Moves every mod in `names` one place up (delta -1) or down (+1) as a block.
// A mod stops where a dependency would end up on the wrong side of it, and the
// selected mods behind it stop too, so a selection never reorders itself.
// deps[name] = mods that must load BEFORE name. Returns the new order and the
// first reason a mod could not move.
function stepMods(order, names, delta, deps) {
  const next = [...order]
  let blocked = null
  const indices = next.map((_, k) => (delta < 0 ? k : next.length - 1 - k))
  for (const i of indices) {
    const j = i + delta
    if (!names.has(next[i]) || j < 0 || j >= next.length || names.has(next[j])) continue
    // After the swap `before` loads first, so it may not require `after`.
    const [before, after] = delta < 0 ? [next[i], next[j]] : [next[j], next[i]]
    if ((deps[before] || []).includes(after)) {
      blocked ??= `Cannot move: ${before} requires ${after}`
      continue
    }
    ;[next[i], next[j]] = [next[j], next[i]]
  }
  return { next, blocked }
}

// An unsaved order carried over to a mod list that changed meanwhile: the
// mods still listed keep the places the admin gave them, a mod that left is
// dropped, and a new one goes behind the mod it follows in the saved order.
function mergeUnsavedOrder(unsaved, saved) {
  const listed = new Set(saved)
  const next = unsaved.filter((name) => listed.has(name))
  const placed = new Set(next)
  saved.forEach((name, i) => {
    if (placed.has(name)) return
    // Every mod before `name` in `saved` is placed by now.
    const after = i === 0 ? -1 : next.indexOf(saved[i - 1])
    next.splice(after + 1, 0, name)
    placed.add(name)
  })
  return next
}

// A drag only ever changes the order, so the row never slides sideways.
const verticalOnly = ({ transform }) => ({ ...transform, x: 0 })

// Installed-table rows. A mod in the load order is a sortable row that drags
// from anywhere on it except a control (`startsDrag`), so its links, buttons and
// switches stay clickable. The ⠿ grip marks it, and on a touch screen it is the
// one place a drag starts, since the rest of the row scrolls the page. A
// disabled mod has no place in the order and is a plain row.
const InstalledRowsContext = createContext(null)
const RowGripContext = createContext(null)

function InstalledRow({ row, children }) {
  const { inOrder, selected } = useContext(InstalledRowsContext)
  const className = selected[row.name] ? 'row-selected' : undefined
  if (!inOrder.has(row.name)) return <tr className={className}>{children}</tr>
  return (
    <SortableRow id={row.name} className={className}>
      {children}
    </SortableRow>
  )
}

// True when a pointer press may start a row drag: the main button, and not on
// a control, whose own click must still work.
const ROW_CONTROLS = 'a, button, input, label, select, textarea, [role="button"], [role="radio"], [role="switch"], .rs'
function startsDrag({ nativeEvent: e }) {
  return e.isPrimary && e.button === 0 && !e.target.closest(ROW_CONTROLS)
}

// PointerSensor with the press filtered by `startsDrag`.
class RowPointerSensor extends PointerSensor {
  static activators = [{ eventName: 'onPointerDown', handler: startsDrag }]
}

// The Installed table's drag-and-drop frame. `selected` rides along so a
// ticked row is highlighted — that is the set ↑/↓ moves.
function LoadOrderDnd({ order, inOrder, selected, onDragEnd, children }) {
  const sensors = useSensors(
    // A few pixels before a drag starts, so a click on the row does nothing.
    useSensor(RowPointerSensor, { activationConstraint: { distance: 4 } })
  )
  return (
    <InstalledRowsContext.Provider value={{ inOrder, selected }}>
      <DndContext
        sensors={sensors}
        collisionDetection={closestCenter}
        modifiers={[verticalOnly]}
        onDragEnd={onDragEnd}
      >
        <SortableContext items={order} strategy={verticalListSortingStrategy}>
          {children}
        </SortableContext>
      </DndContext>
    </InstalledRowsContext.Provider>
  )
}

function SortableRow({ id, className, children }) {
  const { listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id })
  return (
    <tr
      ref={setNodeRef}
      data-mod={id}
      className={['row-sortable', className, isDragging && 'row-dragging'].filter(Boolean).join(' ')}
      style={{ transform: CSS.Translate.toString(transform), transition }}
      {...listeners}
    >
      <RowGripContext.Provider value={true}>{children}</RowGripContext.Provider>
    </tr>
  )
}

// How rows move. The ↑/↓ arrow keys are a keyboard's, so a touch screen
// (no hover) is told only to drag.
function useMoveHint() {
  const touch = useMediaQuery('(hover: none)')
  return touch
    ? 'drag a row by its ⠿ grip'
    : 'drag a row (anywhere but its buttons and links), or select rows and press the ↑/↓ arrow keys to move them together'
}

// The header box of a checkbox column: ticks or unticks every row. `mixed`
// (some rows ticked) shows the browser's indeterminate dash, which only a ref can set.
function TickAllBox({ checked, mixed, onChange, label }) {
  const ref = useRef(null)
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = mixed
  }, [mixed])
  return <input ref={ref} type="checkbox" checked={checked} onChange={onChange} aria-label={label} {...tooltipAttrs(label)} />
}

// Hidden from assistive tech on purpose: the keyboard path is the row's
// checkbox plus the ↑/↓ arrow keys, which move any number of rows at once.
// The drag listeners sit on the row (SortableRow), so a press here starts it too.
function RowGrip() {
  const sortable = useContext(RowGripContext)
  const hint = useMoveHint()
  if (!sortable) return null
  return (
    <span
      className="row-handle"
      aria-hidden="true"
      {...tooltipAttrs(`To change the load order, ${hint}.`)}
    >
      ⠿
    </span>
  )
}

// The padlock beside a mod that dependencies hold in place; `open` when
// manual load order no longer holds it (plans/49). The glyph is decorative;
// the span carries the name a screen reader gets.
function LockIcon({ open = false, ...props }) {
  const Glyph = open ? HiOutlineLockOpen : HiOutlineLockClosed
  return (
    <span className="lock-icon" role="img" aria-label={open ? 'Unlocked' : 'Locked'} {...props}>
      <Glyph aria-hidden="true" />
    </span>
  )
}

// Filterable multi-select list of installable mods, shared by the Cached and
// Search tabs.
// `linkById` mirrors installPicked's `byId`: workshop-folder mods are id-native
// and may have no resolvable name (an unknown one shows as the placeholder
// `@[123456]`), so their mod page is addressed by id.
const LARGE_SELECTION = 25

// The tick-and-act list shared by Install → Workshop, Install → Search and the
// Workshop Cache tab: a filter, a grid of tiles, one action for the ticked ones.
// `keyOf` names a row in `selected` (the cache keys by folder path, the install
// lists by mod name); the default action is Install.
function ModPicker({
  items,
  filter,
  setFilter,
  selected,
  setSelected,
  onAction,
  emptyMessage,
  linkById = false,
  keyOf = (m) => m.name,
  actionLabel = 'Install selected',
  ActionIcon = HiOutlineArrowDownTray,
  danger = false,
  needsSteam = false,
  tip,
}) {
  const confirm = useConfirm()
  const q = filter.trim().toLowerCase()
  // Alphabetical, ignoring the `@` and case, so a long list can be scanned.
  const sorted = useMemo(
    () => [...items].sort((a, b) => a.name.replace(/^@/, '').localeCompare(b.name.replace(/^@/, ''), undefined, { sensitivity: 'base' })),
    [items],
  )
  const visible = q
    ? sorted.filter((m) => m.name.toLowerCase().includes(q) || String(m.id).includes(q))
    : sorted
  const count = Object.values(selected).filter(Boolean).length
  const action = (
    <button className={danger ? 'btn btn-danger' : 'btn btn-primary'} disabled={!count} onClick={onAction}>
      <ActionIcon aria-hidden="true" />
      {actionLabel} <CountBadge value={count} />
    </button>
  )

  // A shift-click ticks every shown tile between it and the last one clicked.
  const toggle = useRangeTick(useMemo(() => visible.map(keyOf), [visible, keyOf]), setSelected)

  // Search lists every mod zsm knows (942 on the test box), so one click on
  // an unfiltered list ticks hundreds. Past this many it asks first.
  async function selectShown() {
    if (visible.length > LARGE_SELECTION) {
      const ok = await confirm({
        title: `Select all ${visible.length} mods?`,
        message: `"${actionLabel}" then acts on every one of them. Filter the list first to select only the mods you want.`,
        confirmLabel: `Tick ${visible.length}`,
        danger: true,
      })
      if (!ok) return
    }
    setSelected((prev) => {
      const next = { ...prev }
      for (const m of visible) next[keyOf(m)] = true
      return next
    })
  }

  return (
    <>
      <div className="mod-picker-toolbar">
        <SearchInput
          boxClassName="mod-picker-search"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Filter by name or Workshop ID…"
          aria-label="Filter mods"
        />
        <span className="dep-info">
          {q ? `${visible.length} of ${items.length}` : items.length} mod{items.length === 1 ? '' : 's'}
          {count ? ` · ${count} selected` : ''}
        </span>
        <span className="mod-picker-bulk">
          <button className="btn btn-small btn-ghost" disabled={!visible.length} onClick={selectShown}>
            Select shown
          </button>
          <button className="btn btn-small btn-ghost" disabled={!count} onClick={() => setSelected({})}>
            Clear
          </button>
        </span>
      </div>
      {visible.length === 0 ? (
        <PanelNote>{q ? 'No mods match the filter.' : emptyMessage}</PanelNote>
      ) : (
        <ul className="mod-picker">
          {visible.map((m) => (
            // The tick has its own strip, so the links in the body never toggle
            // it; the mod's info is the whole tile's tooltip.
            <li
              key={keyOf(m)}
              className={selected[keyOf(m)] ? 'mod-tile selected' : 'mod-tile'}
              {...tooltipAttrs(m.info, { keep: true })}
            >
              <label className="mod-tile-check" aria-label={`Select ${m.name}`}>
                <input type="checkbox" checked={!!selected[keyOf(m)]} onChange={(e) => toggle(keyOf(m), e)} />
              </label>
              <span className="mod-tile-body">
                <Link className="mod-link mod-tile-name" to={modLink(linkById && m.id ? m.id : m.name)}>
                  {m.name}
                </Link>
                <span className="dep-info">{m.id ? <WorkshopLink id={m.id} /> : 'No Workshop ID'}</span>
              </span>
            </li>
          ))}
        </ul>
      )}
      <div className="action-bar">
        {needsSteam ? (
          <NeedsSteam keep tip={tip}>{action}</NeedsSteam>
        ) : (
          <Tooltip keep text={tip} className="bare">{action}</Tooltip>
        )}
      </div>
    </>
  )
}

// The DayZ-Expansion module picker (Install → Expansion). Expansion ships as 19
// separate Workshop items, so picking is the point — selective mode plus one
// Select all.
//
// The dependency graph is NOT duplicated here: the API hands every module its
// full closure in `requires`, so ticking one only unions lists, and
// app/modmgr/expansion.py stays the single place that knows Vehicles needs
// Licensed. A required module is ticked and locked, with the reason on its padlock
// (the Load Order tab's pattern) — through `tooltipAttrs`, since nothing in
// webui/src uses a native title= (webui/CLAUDE.md).
function ExpansionPicker({ data, picked, setPicked, onInstall, tip }) {
  const entries = useMemo(() => [...data.frameworks, ...data.modules], [data])

  // {id: [labels of ticked modules that pull it in]} — the lock and its reason.
  const reasons = useMemo(() => {
    const out = {}
    for (const m of data.modules) {
      if (!picked[m.id]) continue
      for (const dep of m.requires) (out[dep] = out[dep] || []).push(m.label)
    }
    return out
  }, [picked, data])

  // What Install would actually download: ticked ∪ required, minus what is
  // already in the mod list.
  const toInstall = useMemo(
    () => entries.filter((m) => (picked[m.id] || reasons[m.id]) && !m.installed),
    [entries, picked, reasons]
  )
  const added = toInstall.filter((m) => !picked[m.id])
  const anyPicked = Object.values(picked).some(Boolean)

  // A shift-click ticks the range in the shown order (modules, then frameworks).
  // An installed or required module keeps its box, as its disabled checkbox says.
  const shownIds = useMemo(() => [...data.modules, ...data.frameworks].map((m) => m.id), [data])
  const isLocked = useCallback(
    (id) => {
      const m = entries.find((e) => e.id === id)
      return !m || m.installed || !!reasons[id]
    },
    [entries, reasons],
  )
  const toggle = useRangeTick(shownIds, setPicked, isLocked)

  function selectAll() {
    const next = {}
    for (const m of data.modules) if (!m.installed) next[m.id] = true
    setPicked(next)
  }

  // Everything a module is ticked, locked or already installed for — used to
  // hide a pairing hint that has nothing left to suggest.
  const covered = useMemo(() => {
    const out = new Set()
    for (const m of entries) {
      if (m.installed || picked[m.id] || reasons[m.id]) out.add(m.id)
    }
    return out
  }, [entries, picked, reasons])

  function row(m) {
    const required = !!reasons[m.id] && !m.installed
    const reason = required ? `Required by ${reasons[m.id].join(', ')}.` : ''
    // Expansion's own installation cheatsheet packages some modules together.
    // A hint, deliberately: none of these is in the mod's requiredAddons, so
    // the server boots without them and nothing here is ticked for you.
    const pairing = (m.paired_with || []).filter((dep) => !covered.has(dep.id))
    const ticked = m.installed || required || !!picked[m.id]
    return (
      <li key={m.id} className={`mod-tile${ticked ? ' selected' : ''}${m.installed ? ' installed' : ''}`}>
        <label className="mod-tile-check" aria-label={`Select ${m.label}`}>
          <input
            type="checkbox"
            checked={ticked}
            disabled={m.installed || required}
            onChange={(e) => toggle(m.id, e)}
          />
        </label>
        <div className="mod-tile-body">
          <span className="load-order-item">
            {required && <LockIcon {...tooltipAttrs(reason, { keep: true })} />}
            <Link className="mod-link mod-tile-name" to={modLink(m.name)}>
              {m.label}
            </Link>
          </span>
          <span className="dep-info">
            {m.name} · <WorkshopLink id={m.id} />
            {m.classes > 0 && ` · ${m.classes} classes`}
            {m.installed && ' · installed'}
            {required && ` · ${reason}`}
          </span>
          <p className="expansion-blurb">{m.blurb}</p>
          {pairing.length > 0 && (
            <p className="expansion-pairing">
              <RowTag
                keep
                tip={
                  "From Expansion's own installation cheatsheet, not from the mod's " +
                  'required addons — the server loads without it; the feature is what suffers.'
                }
              >
                Packaged with
              </RowTag>{' '}
              {pairing.map((dep) => dep.label).join(' + ')} — {m.pairing_note}
            </p>
          )}
        </div>
      </li>
    )
  }

  // Select all / Clear, above the list and again beside Install. `small` is
  // for the toolbar; the action bar sizes its own buttons.
  function bulkButtons(small) {
    const cls = small ? 'btn btn-small btn-ghost' : 'btn btn-ghost'
    return (
      <>
        <button className={cls} disabled={data.modules.every((m) => m.installed)} onClick={selectAll}>
          Select all
        </button>
        <button className={cls} disabled={!anyPicked} onClick={() => setPicked({})}>
          Clear
        </button>
      </>
    )
  }

  // Select all / Clear sit above the list, as on the other pickers: below it
  // they were under a scrolling list and easy to miss.
  return (
    <>
      <div className="mod-picker-toolbar">
        <h3>Modules</h3>
        <span className="dep-info">
          {data.modules.length} modules
          {toInstall.length ? ` · ${toInstall.length} to install` : ''}
        </span>
        <span className="mod-picker-bulk">{bulkButtons(true)}</span>
      </div>
      <ul className="mod-picker mod-picker-wide">{data.modules.map(row)}</ul>
      <h3>Frameworks</h3>
      <PanelNote>
        Not Expansion modules — Core needs both, and so does most of the rest of the workshop.
      </PanelNote>
      <ul className="mod-picker mod-picker-wide">{data.frameworks.map(row)}</ul>
      {added.length > 0 && (
        <PanelNote>
          Dependencies added automatically: {added.map((m) => m.label).join(', ')}.
        </PanelNote>
      )}
      <div className="action-bar">
        {bulkButtons(false)}
        <NeedsSteam keep tip={tip}>
          <button
            className="btn btn-primary"
            disabled={!toInstall.length}
            onClick={() => onInstall(toInstall)}
          >
            <HiOutlineArrowDownTray aria-hidden="true" />
            Install selected <CountBadge value={toInstall.length} />
          </button>
        </NeedsSteam>
      </div>
    </>
  )
}

// Install → Supported: one card per mod with its own Configs → Mods tab. The
// list, the editor counts and "installed" come from GET /api/mods/supported
// (app/ops/supported_mods.py); the card only links, installs and uninstalls.
// An installed picker mod (Expansion) keeps its button as "Manage modules…",
// so the picker stays one click away after the first install.
function SupportedMods({ mods, onInstall, onPick, onUninstall, tip }) {
  return (
    <div className="config-card-grid">
      {mods.map((m) => (
        <div key={m.group} className="card config-card">
          <div className="config-card-head">
            <h3>
              <Link className="mod-link" to={modLink(m.id)}>
                {m.group}
              </Link>
            </h3>
            {m.installed && <span className="status-pill status-pill-success">Installed</span>}
          </div>
          <p className="config-card-help">{m.blurb}</p>
          <p className="config-card-path">
            <Link to={`/configs?tab=Mods&subtab=${encodeURIComponent(m.group)}`}>
              {m.editors} config editor{m.editors === 1 ? '' : 's'}
            </Link>
            {m.layers.length > 0 && (
              <>
                {' · '}
                <Link to={`/map-editor?layers=${m.layers.join(',')}`}>
                  {m.layers.length} map layer{m.layers.length === 1 ? '' : 's'}
                </Link>
              </>
            )}
          </p>
          <div className="config-card-actions">
            {m.picker ? (
              <button className={`btn btn-small ${m.installed ? 'btn-ghost' : 'btn-primary'}`} onClick={onPick}>
                {m.installed ? 'Manage modules…' : 'Pick modules…'}
              </button>
            ) : (
              !m.installed && (
                <NeedsSteam keep tip={tip}>
                  <button className="btn btn-primary btn-small" onClick={() => onInstall(m)}>
                    <HiOutlineArrowDownTray aria-hidden="true" />
                    Install
                  </button>
                </NeedsSteam>
              )
            )}
            {m.installed && (
              <button className="btn btn-danger btn-small" onClick={() => onUninstall(m)}>
                <HiOutlineTrash aria-hidden="true" />
                Uninstall
              </button>
            )}
          </div>
        </div>
      ))}
    </div>
  )
}

export default function ModsPage() {
  // Tab and install sub-tab live in the URL (`/mods?tab=Install&subtab=Workshop`)
  // so other pages can deep-link into either — the Doctor page links findings
  // straight to the tab that shows them. See urlTabs.js.
  const { tab, setTab, subtab: installTab, setSubtab: setInstallTab } = useUrlTabs(
    TABS,
    SUBTABS_FOR,
  )
  const [mods, setMods] = useState([])
  const [selected, setSelected] = useState({})
  const [removeDeps, setRemoveDeps] = useState(false)
  // Names the uninstall confirm is asking about; null = closed. A row's own
  // Uninstall asks about that row alone, the header button about the ticks.
  const [uninstallNames, setUninstallNames] = useState(null)
  // How many applied Expansion mission records the open uninstall also removes
  // (the Expansion card's Uninstall only); 0 = none.
  const [uninstallMission, setUninstallMission] = useState(0)
  // The confirm's subject when one card stands for several mods ("Expansion").
  const [uninstallLabel, setUninstallLabel] = useState(null)
  // Auto configure's preview (`POST …/xml/auto-config/preview`); null = closed.
  const [autoConfig, setAutoConfig] = useState(null)
  const navigate = useNavigate()

  // Install options (shared by every install tab). The TraderPlus-config and
  // mod-XML options are asked per install instead — see installFlow.jsx.
  const [installDeps, setInstallDeps] = useState(true)
  // The install/update about to run, pending its options dialog. Payload plus
  // a `mode` ('install' | 'update'); null when no dialog is open.
  const [installPrompt, setInstallPrompt] = useState(null)

  // Install by name.
  const [installNames, setInstallNames] = useState('')
  // Install by id.
  const [installId, setInstallId] = useState('')
  const [installIdName, setInstallIdName] = useState('')
  // Cached / Search pickers.
  const [cachedMods, setCachedMods] = useState([])
  const [cachedFilter, setCachedFilter] = useState('')
  const [cachedSelected, setCachedSelected] = useState({})
  const [knownMods, setKnownMods] = useState([])
  const [knownFilter, setKnownFilter] = useState('')
  const [knownSelected, setKnownSelected] = useState({})
  // Expansion module picker: the catalog from the API, and what is ticked.
  const [expansionData, setExpansionData] = useState(null)
  const [expansionPicked, setExpansionPicked] = useState({})
  // Install -> Supported: the mods with Config/Map editors (GET /api/mods/supported).
  const [supportedMods, setSupportedMods] = useState(null)

  // {mod: {files, linked, merged, disabled, skipped, actionable}}
  const [xmlStatus, setXmlStatus] = useState({})

  const [order, setOrder] = useState([])
  const [orderDeps, setOrderDeps] = useState({})
  // The load order resolved over every listed mod, disabled ones included —
  // only for placing a disabled row (F78).
  const [rowOrder, setRowOrder] = useState([])
  const [orderDirty, setOrderDirty] = useState(false)
  const moveHint = useMoveHint()
  // Asks before the page is left with an unsaved order (F19). The order is
  // this page's state, so the other tabs keep it and do not ask.
  useUnsavedChanges(orderDirty, { pageWide: true })
  // What a reload that finishes later has to see: the order as it is then.
  const unsavedOrderRef = useRef(null)
  unsavedOrderRef.current = orderDirty ? order : null
  // Manual load order (plans/49): the saved order is used as it is, so moves
  // are no longer stopped at a dependency.
  const [manualOrder, setManualOrder] = useState(false)

  const [conflicts, setConflicts] = useState(null)
  const [scanning, setScanning] = useState(false)
  // Which Fix-column action is mid-flight, keyed `kind + label` — one at a time.
  const [fixBusy, setFixBusy] = useState(null)

  const [updates, setUpdates] = useState([])
  const [checkingUpdates, setCheckingUpdates] = useState(false)
  // When the list was last checked, how many mods Steam answered for, and
  // whether a key is set at all (plans/50 B). `null` until the first load.
  const [updateCheck, setUpdateCheck] = useState(null)

  // Workshop cache (downloaded-but-not-installed dirs) — remove to free disk.
  const [wsCache, setWsCache] = useState(null) // null = dir not found yet / unknown
  const [wsCacheSelected, setWsCacheSelected] = useState({})
  const [wsCacheFilter, setWsCacheFilter] = useState('')
  // ModPicker's row shape: a name to show and link, the Workshop id beside it.
  const wsCacheItems = useMemo(
    () => (wsCache || []).map((m) => ({ ...m, name: m.mod_name || m.workshop_id, id: m.workshop_id })),
    [wsCache],
  )

  // Tab/action hints (GET /api/mods/help, from app/shared/mods_help.py).
  const [help, setHelp] = useState({ tabs: {}, actions: {} })
  // What the last update set aside, and whether there is a reason to want it
  // back (TODO C5). Null while unknown / unreachable — no banner then.
  const [rollback, setRollback] = useState(null)

  const pushToast = useToast()
  const confirm = useConfirm()
  const { startJob, trackJob } = useJobs()
  const steamLoggedOut = useSteamLoggedOut()
  // Up to 1024px the Installed rows are the compact two-part cards.
  const compactCards = useMediaQuery(`(max-width: ${COMPACT_CARDS_PX}px)`)

  // Names a conflict message may link: installed mods plus the mods each finding
  // itself names (a missing dep is by definition not installed).
  const knownModNames = useMemo(() => {
    const names = new Set(mods.map((m) => m.name))
    for (const item of conflicts?.items || []) {
      if (item.mod_name) names.add(item.mod_name)
      if (item.dep_name) names.add(item.dep_name)
    }
    return names
  }, [mods, conflicts])

  const loadMods = useCallback(async () => {
    try {
      const data = await api.get('/api/mods')
      setMods(data.mods)
    } catch (err) {
      pushToast(err.message || 'Failed to load mods', 'error')
    }
  }, [pushToast])

  // Silent on failure: a missing rollback answer means no banner, which is
  // the same as nothing to offer.
  const loadRollback = useCallback(() => {
    api
      .get('/api/mods/rollback')
      .then(setRollback)
      .catch(() => setRollback(null))
  }, [])

  // Per-mod XML tallies — drive the XML column and whether a row gets a
  // Re-configure button at all.
  const loadXmlStatus = useCallback(async () => {
    try {
      const data = await api.get('/api/mods/xml-status')
      setXmlStatus(data.mods || {})
    } catch {
      // A report failure must not break the table — rows just show no XML info.
    }
  }, [])

  // `keepUnsaved` is for a reload nobody on this page asked for (the mod list
  // changed: a toggle, a finished job, another admin). It used to replace an
  // order the admin was still arranging with the saved one, without a word.
  const loadOrder = useCallback(async ({ keepUnsaved = false } = {}) => {
    try {
      const data = await api.get('/api/mods/load-order')
      const unsaved = keepUnsaved ? unsavedOrderRef.current : null
      const next = unsaved ? mergeUnsavedOrder(unsaved, data.order) : data.order
      setOrder(next)
      setOrderDeps(data.deps || {})
      setRowOrder(data.rows || [])
      setManualOrder(!!data.manual)
      setOrderDirty(next.some((name, i) => name !== data.order[i]))
    } catch (err) {
      pushToast(err.message || 'Failed to load mod order', 'error')
    }
  }, [pushToast])

  const loadCached = useCallback(async () => {
    try {
      const data = await api.get('/api/mods/cached')
      setCachedMods(data.mods)
    } catch (err) {
      pushToast(err.message || 'Failed to load cached mods', 'error')
    }
  }, [pushToast])

  const loadKnown = useCallback(async () => {
    try {
      const data = await api.get('/api/mods/known')
      setKnownMods(data.mods)
    } catch (err) {
      pushToast(err.message || 'Failed to load known mods', 'error')
    }
  }, [pushToast])

  const loadExpansion = useCallback(async () => {
    try {
      setExpansionData(await api.get('/api/mods/expansion'))
    } catch (err) {
      pushToast(err.message || 'Failed to load Expansion modules', 'error')
    }
  }, [pushToast])

  const loadSupported = useCallback(async () => {
    try {
      setSupportedMods((await api.get('/api/mods/supported')).mods)
    } catch (err) {
      pushToast(err.message || 'Failed to load supported mods', 'error')
    }
  }, [pushToast])

  const loadUpdates = useCallback(async () => {
    try {
      const data = await api.get('/api/mods/updates')
      setUpdates(data.updates)
      setUpdateCheck(data)
    } catch {
      // ignore
    }
  }, [])

  // Downloaded workshop mod dirs live under the cleanup overview.
  const loadWsCache = useCallback(async () => {
    try {
      const data = await api.get('/api/cleanup/overview')
      setWsCache(data.workshop_mods)
    } catch (err) {
      pushToast(err.message || 'Failed to load workshop cache', 'error')
    }
  }, [pushToast])

  useEffect(() => {
    loadMods()
    loadOrder()
    loadUpdates()
    loadXmlStatus()
    loadRollback()
    // The Installed table also needs it: a supported mod's Auto config opens its Configs tab.
    loadSupported()
  }, [loadMods, loadOrder, loadUpdates, loadXmlStatus, loadRollback, loadSupported])

  // Lazy-load the picker sub-tabs / workshop cache when first opened.
  useEffect(() => {
    if (tab === 'Install' && installTab === 'Workshop') loadCached()
    if (tab === 'Install' && installTab === 'Search') loadKnown()
    if (tab === 'Install' && installTab === 'Expansion') loadExpansion()
    if (tab === 'Install' && installTab === 'Supported') loadSupported()
    if (tab === 'Workshop Cache') loadWsCache()
  }, [tab, installTab, loadCached, loadKnown, loadExpansion, loadSupported, loadWsCache])

  useEffect(() => {
    api.get('/api/mods/help').then(setHelp).catch(() => {})
  }, [])

  const handleWsMessage = useCallback(
    (msg) => {
      if (msg.type === 'mods_changed') {
        loadMods()
        loadOrder({ keepUnsaved: true })
        loadCached()
        loadKnown()
        loadXmlStatus()
        if (expansionData) loadExpansion()
        if (supportedMods) loadSupported()
      }
    },
    [loadMods, loadOrder, loadCached, loadKnown, loadXmlStatus, loadExpansion, expansionData, loadSupported, supportedMods]
  )
  useWs(handleWsMessage)

  async function restoreRollback() {
    const ok = await confirm({
      title: 'Restore the previous mod versions?',
      message:
        'The mods currently in the server directory are replaced by the ones the last update set aside, and the kept copies are consumed. Their keys are copied into keys/. Stop the server first. Afterwards re-apply the mod XML with Re-configure all XML on the Installed tab; an update would replace them again.',
      confirmLabel: 'Restore',
    })
    if (!ok) return
    try {
      const res = await api.post('/api/mods/rollback/restore')
      pushToast(res.message || 'Restored.', 'info')
      loadRollback()
      loadMods()
    } catch (err) {
      pushToast(err.message || 'Restore failed', 'error')
    }
  }

  async function discardRollback() {
    const ok = await confirm({
      title: 'Discard the kept mod versions?',
      message:
        'Frees the disk they use. There is then nothing to roll back to until the next update sets a copy aside.',
      confirmLabel: 'Discard',
      danger: true,
    })
    if (!ok) return
    try {
      await api.post('/api/mods/rollback/discard')
      loadRollback()
    } catch (err) {
      pushToast(err.message || 'Discard failed', 'error')
    }
  }

  // -mod= vs -serverMod= (plans/47 A). Moving a mod to server-only is the
  // risky direction: one with items, models or UI players see stops loading
  // for them. So that direction confirms, and the way back does not.
  async function toggleServerSide(mod) {
    const toServer = !mod.server_side
    if (toServer) {
      const ok = await confirm({
        title: `Load ${mod.name} on the server only?`,
        message: `Moves it from -mod= to -serverMod=: the server loads it and players are not asked to have it. Only do this for a mod whose Workshop page says it is server-side. A mod with items, models or UI that players see breaks for them. The service file is rebuilt; the next server start uses it.`,
        confirmLabel: 'Server only',
        danger: true,
      })
      if (!ok) return
    }
    try {
      await api.post(`/api/mods/${encodeURIComponent(mod.name)}/server-side`, { server_side: toServer })
      loadMods()
    } catch (err) {
      pushToast(err.message || 'Failed to change where the mod loads', 'error')
    }
  }

  // Turns each mod in `list` on or off, one at a time: every call rewrites
  // the mod list and the unit, so two at once would race. A row's switch and
  // the bulk bar's Turn on and Turn off share it. It stops at the first failure.
  async function setEnabledMany(list, on) {
    try {
      for (const mod of list.filter((m) => m.enabled !== on)) {
        await api.post(`/api/mods/${encodeURIComponent(mod.name)}/${on ? 'enable' : 'disable'}`)
      }
    } catch (err) {
      pushToast(err.message || 'Failed to toggle mod', 'error')
    } finally {
      loadMods()
    }
  }

  function toggleEnabled(mod) {
    return setEnabledMany([mod], !mod.enabled)
  }

  // Re-apply mission XML. mod === null re-runs the whole step (the update
  // pipeline's "Apply mod XML"); a mod name scopes it to that mod.
  // Both rewrite mission files, so both confirm first. Neither restores from
  // .backup any more (2026 Q3): edits outside the mod blocks survive.
  async function reconfigureXml(mod) {
    const ok = await confirm(
      mod
        ? {
            title: `Re-configure ${mod} XML?`,
            message: `Rewrites ${mod}'s mission XML: its custom/ economy files are regenerated and its merged blocks in the mission files are stripped and re-inserted from the mod's current XML. Manual edits to those are lost. Other mods are not touched.`,
            confirmLabel: 'Re-configure',
          }
        : {
            title: 'Re-configure all mod XML?',
            message:
              'Re-applies the XML of every enabled mod, and your manual XML files, to the mission. The custom/ folder is rebuilt. Each mod’s and manual file’s block in the mission files is replaced, so edits made inside one (a spawn moved on the map, a value changed in an editor) are lost. Your edits outside those blocks, and the files you turned off or deleted on the Custom config tab, are kept.',
            confirmLabel: 'Re-configure all',
          },
    )
    if (!ok) return
    try {
      await startJob({
        endpoint: mod ? `/api/mods/${encodeURIComponent(mod)}/xml/merge` : '/api/mods/xml/merge',
        name: mod ? `Re-configure ${mod} XML` : 'Re-configure mod XML',
        kind: 'mods_xml_merge',
        onStatusChange: (status) => {
          if (status === 'success') loadXmlStatus()
        },
      })
    } catch (err) {
      pushToast(err.message || 'Failed to start re-configure', 'error')
    }
  }

  // The bulk undo for the Mod XML page's per-file Disable/Delete. Separate from
  // Re-configure on purpose: a merge that dropped those records would undo them
  // on every install and mod toggle, which is what they exist to survive.
  // Auto configure (app/ops/mod_autoconfig.py): show what would be written for
  // a mod that ships no XML, then run it as a job. The job builds the entries
  // again on the server, so the preview is only for reading. On success the
  // file opens in the Types form on Custom config, where the user changes the
  // suggested values.
  // `options` are the dialog's switches; a switch builds the preview again.
  async function previewAutoConfig(mod, options = AUTO_CONFIG_DEFAULTS) {
    try {
      setAutoConfig(await api.post(`/api/mods/${encodeURIComponent(mod)}/xml/auto-config/preview`, options))
    } catch (err) {
      pushToast(err.message || 'Auto configure failed', 'error')
    }
  }

  async function applyAutoConfig() {
    const mod = autoConfig?.mod
    const fileId = autoConfig?.file_id
    const options = autoConfig?.options
    setAutoConfig(null)
    if (!mod) return
    try {
      await startJob({
        endpoint: `/api/mods/${encodeURIComponent(mod)}/xml/auto-config`,
        body: options,
        name: `Auto configure ${mod}`,
        kind: 'mods_xml_merge',
        onStatusChange: (status) => {
          if (status !== 'success') return
          loadXmlStatus()
          navigate(manualFileLink(fileId))
        },
      })
    } catch (err) {
      pushToast(err.message || 'Failed to start Auto configure', 'error')
    }
  }

  async function restoreXml(mod, count) {
    const ok = await confirm({
      title: `Restore ${mod}'s turned-off XML?`,
      message: `Turns ${count} XML file${count === 1 ? '' : 's'} you disabled or deleted back on, then re-applies ${mod}'s mission XML. Manual XML files you forked from those files are left alone.`,
      confirmLabel: 'Restore',
    })
    if (!ok) return
    try {
      const result = await api.post(`/api/mods/${encodeURIComponent(mod)}/xml/restore`)
      pushToast(
        result.applied
          ? `Restored ${result.restored} file${result.restored === 1 ? '' : 's'}.`
          : `Restored ${result.restored} file${result.restored === 1 ? '' : 's'} — enable the mod to apply them.`,
        'info'
      )
      loadXmlStatus()
    } catch (err) {
      pushToast(err.message || 'Restore failed', 'error')
    }
  }

  // Single funnel for every install surface. Asks the per-install options
  // first (TraderPlus configs / mod XML) — startInstall runs the job.
  // `onStarted` runs only once the job is posted, so Cancel in the dialog or a
  // refused POST keeps a picker's ticks; they were cleared before the dialog
  // was answered.
  function runInstall({ names = [], ids = [], id_names = {}, expansion = false }, mode = 'install', onStarted) {
    if (!names.length && !ids.length) return
    setInstallPrompt({ names, ids, id_names, expansion, mode, onStarted })
  }

  // Updating a mod IS re-installing it: the install job always re-downloads via
  // SteamCMD and re-copies, and mod-list/load-order entries are idempotent.
  async function startInstall({ names, ids, id_names, mode, onStarted }, { apply_expansion_mission, ...flags }) {
    setInstallPrompt(null)
    // A reinstall downloads again too, so it clears the update flags like an update.
    const update = mode === 'update' || mode === 'reinstall'
    const verb = { update: 'Update', reinstall: 'Reinstall' }[mode] || 'Install'
    try {
      await startJob({
        endpoint: '/api/mods/install',
        body: {
          names,
          ids,
          id_names,
          install_deps: installDeps,
          ...flags,
        },
        name: `${verb} mods`,
        kind: 'mods_install',
        onStatusChange: (status, job) => {
          if (status !== 'success') return
          if (update) clearUpdateFlags(ids)
          if (apply_expansion_mission) applyExpansionMission()
          const missing = job.result?.missing_addons || []
          if (missing.length) {
            pushToast(
              `${verb === "Install" ? "Installed" : verb + "d"}, but ${missing.length} required addon(s) missing — see the Check mods tab.`,
              'warning',
              8000
            )
          }
        },
      })
      onStarted?.()
    } catch (err) {
      pushToast(err.message || `Failed to start ${verb.toLowerCase()}`, 'error')
    }
  }

  // The install dialog's "Apply Expansion mission files" step, run after the
  // install job succeeds: the filter reads the installed modules, so it cannot
  // run earlier. Downloads the templates first when none are unpacked. An empty
  // template name makes the API pick the one for this mission's map, and it
  // answers 404 when no template matches (app/web/routers/expansion.py).
  async function applyExpansionMission() {
    const apply = () =>
      startJob({
        endpoint: '/api/expansion/mission/apply',
        body: { template: '' },
        name: 'Apply Expansion mission files',
        kind: 'expansion_mission',
        onStatusChange: (status) => {
          if (status === 'success') loadXmlStatus()
        },
      })
    try {
      const templates = await api.get('/api/expansion/templates')
      if (templates.downloaded) {
        await apply()
        return
      }
      await startJob({
        endpoint: '/api/expansion/templates/download',
        name: 'Download Expansion templates',
        kind: 'expansion_templates',
        onStatusChange: (status) => {
          if (status === 'success')
            apply().catch((err) => pushToast(err.message || 'Failed to apply the Expansion mission files', 'error'))
        },
      })
    } catch (err) {
      pushToast(err.message || 'Failed to apply the Expansion mission files', 'error')
    }
  }

  // The detected-updates list is a cache file, so a mod that was just
  // re-downloaded still sits in it. Re-run the check (it reads the workshop
  // ACF's fresh timestamps); if that fails — no Steam API key, no network —
  // drop the ids we just updated so the badges at least agree with what ran.
  async function clearUpdateFlags(ids) {
    try {
      const data = await api.post('/api/mods/check-updates')
      setUpdates(data.updates || [])
      setUpdateCheck((prev) => ({ ...prev, ...data }))
    } catch {
      const done = new Set(ids.map(String))
      setUpdates((prev) => prev.filter((u) => !done.has(String(u.mod_id))))
    }
  }

  // Quick fixes on the Conflicts tab. The actions are built server-side
  // (app/ops/fix_hints.py) and the Doctor page renders the very same ones, but
  // installing is dispatched through *this* page's funnel so a reinstall still
  // asks the per-install options (installFlow.jsx) rather than starting blind.
  //
  // Which is also why an install skips `action.confirm`: the options modal names
  // the mod and is itself the confirmation, and two dialogs for one click is the
  // thing installFlow deliberately avoids. Everything else asks first — those
  // actions delete or overwrite files.
  async function runFixAction(action) {
    if (action.kind === 'install_mods') {
      const body = installBodyFor(action.mods)
      if (body.names.length || body.ids.length) runInstall(body)
      return
    }
    if (
      action.confirm &&
      !(await confirm({
        title: `${action.label}?`,
        message: action.confirm,
        confirmLabel: action.label,
        danger: true,
      }))
    )
      return
    if (action.kind === 'copy_bikeys') {
      setFixBusy(action.kind + action.label)
      try {
        const res = await copyBikeys()
        pushToast(res.message, 'success')
        scanConflicts()
      } catch (err) {
        pushToast(err.message || 'Copying bikeys failed', 'error')
      } finally {
        setFixBusy(null)
      }
    }
  }

  function handleInstallByName() {
    const names = installNames
      .split(',')
      .map((n) => n.trim())
      .filter(Boolean)
    runInstall({ names })
  }

  function handleInstallById() {
    const id = installId.trim()
    if (!/^\d+$/.test(id)) {
      pushToast('Workshop ID must be numeric.', 'error')
      return
    }
    const name = installIdName.trim()
    runInstall({ ids: [id], id_names: name ? { [id]: name } : {} }, 'install', () => {
      setInstallId('')
      setInstallIdName('')
    })
  }

  function installPicked(picked, setPicked, sourceItems, byId = false) {
    const chosen = sourceItems.filter((m) => picked[m.name])
    const clear = () => setPicked({})
    if (byId) {
      // Workshop-folder mods may have no resolvable name, so they install by
      // id. The job still runs SteamCMD for each one before it copies.
      const ids = chosen.filter((m) => m.id).map((m) => String(m.id))
      const id_names = {}
      for (const m of chosen) if (m.id) id_names[String(m.id)] = m.name
      const names = chosen.filter((m) => !m.id).map((m) => m.name)
      runInstall({ names, ids, id_names }, 'install', clear)
    } else {
      runInstall({ names: chosen.map((m) => m.name) }, 'install', clear)
    }
  }

  // The picker hands over the resolved install set (ticks + their
  // dependencies, already-installed modules dropped). Names come from the
  // catalog so the folders are named properly instead of @<id>.
  function installExpansion(entries) {
    const id_names = {}
    for (const m of entries) if (m.name) id_names[m.id] = m.name
    runInstall({ ids: entries.map((m) => m.id), id_names, expansion: true }, 'install', () => setExpansionPicked({}))
  }

  // A Supported card's Uninstall. The name comes from the mod list, matched by
  // id, since the folder can differ from the catalog name. Expansion's card
  // removes every installed Expansion module and then the applied mission
  // files; the frameworks (@CF, Dabs) go only through the confirm's
  // orphaned-dependencies switch.
  async function uninstallSupported(m) {
    const nameFor = (id, fallback) => mods.find((x) => String(x.id) === String(id))?.name || fallback
    if (!m.picker) {
      askUninstall([nameFor(m.id, m.name)])
      return
    }
    try {
      const [data, templates] = await Promise.all([api.get('/api/mods/expansion'), api.get('/api/expansion/templates')])
      const names = data.modules.filter((x) => x.installed).map((x) => nameFor(x.id, x.name))
      if (names.length) askUninstall(names, (templates.applied || []).length, m.group)
    } catch (err) {
      pushToast(err.message || 'Failed to load Expansion modules', 'error')
    }
  }

  // Opens the uninstall confirm. `mission` is the count of applied Expansion
  // mission records to remove after the uninstall succeeds, and `label` names
  // a group of mods in the title.
  function askUninstall(names, mission = 0, label = null) {
    // Per-uninstall choice, not sticky page state.
    setRemoveDeps(false)
    setUninstallMission(mission)
    setUninstallLabel(label)
    setUninstallNames(names)
  }

  async function handleUninstall() {
    const names = uninstallNames || []
    const mission = uninstallMission
    setUninstallNames(null)
    if (!names.length) return
    try {
      await startJob({
        endpoint: '/api/mods/uninstall',
        body: { names, remove_deps: removeDeps },
        name: 'Uninstall mods',
        kind: 'mods_uninstall',
        onStatusChange: (status) => {
          if (status === 'success' && mission) removeExpansionMission()
        },
      })
      // Only what was uninstalled leaves the selection: a row's own button
      // must not drop the ticks on the other rows.
      setSelected((prev) => {
        const next = { ...prev }
        for (const n of names) delete next[n]
        return next
      })
    } catch (err) {
      pushToast(err.message || 'Failed to start uninstall', 'error')
    }
  }

  // The Expansion card's uninstall also takes out the applied mission files,
  // which name the removed modules' classes. Same call as the mission card's
  // Remove: the copied .map files stay (expansion_ops.remove_applied).
  async function removeExpansionMission() {
    try {
      const res = await api.post('/api/expansion/mission/remove')
      pushToast(`Removed ${res.removed.length} Expansion mission file(s) from the mission.`, 'success')
      loadXmlStatus()
    } catch (err) {
      pushToast(err.message || 'Failed to remove the Expansion mission files', 'error')
    }
  }

  // Remove downloaded workshop-cache dirs. paths=null → remove all. Goes
  // through the shared /api/cleanup/workshop job (same as the Cleanup page).
  async function removeWorkshopCache(paths, label) {
    const ok = await confirm({ title: 'Remove workshop mods?', message: label, confirmLabel: 'Remove', danger: true })
    if (!ok) return
    try {
      const data = await api.post('/api/cleanup/workshop', { confirm: true, paths })
      if (data?.job_id) {
        trackJob(data.job_id, {
          name: 'Remove workshop mods',
          kind: 'cleanup',
          onStatusChange: (status) => {
            if (status === 'success') {
              loadWsCache()
              loadCached()
            }
          },
        })
      }
      setWsCacheSelected({})
    } catch (err) {
      pushToast(err.message || 'Failed to remove workshop mods', 'error')
    }
  }

  // Load-order dependency constraints.
  // orderDeps[name] = mods that must load BEFORE name (i.e. name requires them).
  const inOrder = new Set(order)
  function requiresOf(name) {
    return (orderDeps[name] || []).filter((d) => inOrder.has(d))
  }
  function requiredByOf(name) {
    return order.filter((m) => (orderDeps[m] || []).includes(name))
  }
  function hasConstraints(name) {
    return requiresOf(name).length > 0 || requiredByOf(name).length > 0
  }

  // `steps` places up (delta -1) or down (+1), stopping at the first
  // dependency in the way and saying which. Returns the new order, or null
  // when nothing moved.
  function moveMods(names, delta, steps = 1) {
    let next = order
    let blocked = null
    for (let s = 0; s < steps && !blocked; s++) {
      ;({ next, blocked } = stepMods(next, names, delta, manualOrder ? {} : orderDeps))
    }
    if (blocked) pushToast(blocked, 'warning')
    if (next.every((n, i) => n === order[i])) return null
    setOrder(next)
    setOrderDirty(true)
    return next
  }

  // A drop past a dependency lands just short of it, with the toast saying why.
  function onOrderDragEnd({ active, over }) {
    if (!over || active.id === over.id) return
    const from = order.indexOf(active.id)
    const to = order.indexOf(over.id)
    if (from < 0 || to < 0) return
    moveMods(new Set([active.id]), Math.sign(to - from), Math.abs(to - from))
  }

  // ↑/↓ move the ticked rows. On the window rather than the table because
  // reordering the rows can take focus off the checkbox that was clicked; a
  // ref so the listener always sees this render's order and selection.
  const orderKeyRef = useRef(null)
  orderKeyRef.current = (e) => {
    const delta = e.key === 'ArrowUp' ? -1 : e.key === 'ArrowDown' ? 1 : 0
    if (!delta || e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return
    // A text box, a select or an open dialog owns the arrow keys.
    if (e.target.closest?.('input:not([type=checkbox]), textarea, select, [contenteditable]')) return
    if (document.querySelector('.modal-backdrop')) return
    const names = new Set(order.filter((n) => selected[n]))
    if (!names.size) return
    e.preventDefault()
    const next = moveMods(names, delta)
    if (!next) return
    // Keep the leading edge of the selection on screen.
    const edge = (delta < 0 ? next : [...next].reverse()).find((n) => names.has(n))
    requestAnimationFrame(() => {
      const rows = document.querySelectorAll('.mods-page tr[data-mod]')
      ;[...rows].find((tr) => tr.dataset.mod === edge)?.scrollIntoView({ block: 'nearest' })
    })
  }
  const installedTab = tab === TABS[0]
  useEffect(() => {
    if (!installedTab) return
    const onKey = (e) => orderKeyRef.current(e)
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [installedTab])

  // Automatic -> Manual is the risky direction: nothing stops a mod loading
  // before one it requires any more. An unsaved order is saved with the switch.
  async function setOrderMode(manual) {
    const ok = await confirm(
      manual
        ? {
            title: 'Use a manual load order?',
            message:
              'Mods load in exactly the order you save, and you can move a mod before one it requires. The server may fail to start, or mods may error, if one loads before a mod it requires. Zedstead will no longer stop you. The current order is kept.',
            confirmLabel: 'Use manual order',
            danger: true,
          }
        : {
            title: 'Sort the load order automatically?',
            message:
              'Mods are sorted by their dependencies again, with your order breaking ties. A mod placed before one it requires moves after it.',
            confirmLabel: 'Sort automatically',
          }
    )
    if (!ok) return
    try {
      await api.put('/api/mods/load-order', orderDirty ? { manual, order } : { manual })
      pushToast(manual ? 'Manual load order on.' : 'Load order sorted automatically.', 'info')
      loadOrder()
    } catch (err) {
      pushToast(err.message || 'Failed to change the load order mode', 'error')
    }
  }

  async function saveOrder() {
    try {
      await api.put('/api/mods/load-order', { order })
      setOrderDirty(false)
      pushToast('Load order saved.', 'info')
      // The saved order is the server's: sorted by dependencies again unless
      // the order is manual.
      loadOrder()
    } catch (err) {
      pushToast(err.message || 'Failed to save load order', 'error')
    }
  }

  // Doctor deep-links here with #reconfigure-xml to point at the button.
  useHashTarget(tab === TABS[0])

  const scanConflicts = useCallback(async () => {
    setScanning(true)
    try {
      setConflicts(await api.get('/api/mods/conflicts'))
    } catch (err) {
      pushToast(err.message || 'Conflict scan failed', 'error')
    } finally {
      setScanning(false)
    }
  }, [pushToast])

  // A deep link (Doctor → /mods?tab=Conflicts) would otherwise land on an empty
  // tab — scan once the first time the tab is opened, however it was reached.
  const autoScannedRef = useRef(false)
  useEffect(() => {
    if (tab === 'Conflicts' && !autoScannedRef.current) {
      autoScannedRef.current = true
      scanConflicts()
    }
  }, [tab, scanConflicts])

  async function checkForUpdates() {
    setCheckingUpdates(true)
    try {
      const data = await api.post('/api/mods/check-updates')
      setUpdates(data.updates)
      setUpdateCheck((prev) => ({ ...prev, ...data }))
    } catch (err) {
      pushToast(err.message || 'Update check failed', 'error')
    } finally {
      setCheckingUpdates(false)
    }
  }

  // Installed mods sorted by the resolved load order. Disabled mods are not
  // part of the load order (get_active_mods skips them), so they trail the
  // list and get no reorder controls.
  const orderIndex = new Map(order.map((n, i) => [n, i]))
  // A disabled mod is not in the load order. It used to sort last, so turning
  // one Off made it jump to the bottom and the next click hit another mod
  // (F78). It goes just after the nearest enabled mod above it in `rowOrder`,
  // which is where it would load if it were on; several in a row keep that
  // order. The list order was not enough: the resolved order can differ from
  // the list's so much that the row still jumped (container, 2026 Q3).
  // Enabled rows follow `order`, so an unsaved drag still shows.
  const rowIndex = new Map(rowOrder.map((n, i) => [n, i]))
  const byRow = mods
    .map((m, i) => [m, rowIndex.get(m.name) ?? rowOrder.length + i])
    .sort((a, b) => a[1] - b[1])
  const sortKeys = new Map()
  let enabledBefore = -1
  byRow.forEach(([m], i) => {
    if (orderIndex.has(m.name)) enabledBefore = orderIndex.get(m.name)
    sortKeys.set(m.name, [orderIndex.has(m.name) ? enabledBefore : enabledBefore + 0.5, i])
  })
  const orderedMods = [...mods].sort((a, b) => {
    const [ak, ai] = sortKeys.get(a.name)
    const [bk, bi] = sortKeys.get(b.name)
    return ak - bk || ai - bi
  })

  const selectedNames = Object.keys(selected).filter((n) => selected[n])
  const selectedCount = selectedNames.length
  // The Installed table's ticks: shift-click for a range, the header box for all.
  const installedNames = useMemo(() => orderedMods.map((m) => m.name), [orderedMods])
  const toggleSelected = useRangeTick(installedNames, setSelected)
  const allTicked = installedNames.length > 0 && installedNames.every((n) => selected[n])
  const someTicked = !allTicked && installedNames.some((n) => selected[n])
  function tickAllInstalled() {
    setSelected(allTicked ? {} : Object.fromEntries(installedNames.map((n) => [n, true])))
  }

  // The ticked mods in row order, for the bulk bar above the table.
  const selectedMods = orderedMods.filter((m) => selected[m.name])
  const selectedWithId = selectedMods.filter((m) => m.id)

  // A row's ⋯ menu: what a row needs now and then. The row itself shows its
  // state (On/Off, chips) and an Update button only when one is waiting.
  // Update and Reinstall download through SteamCMD, so a logged-out Steam
  // disables them, with NeedsSteam's reason as the tip.
  // Supported mods by Workshop ID (GET /api/mods/supported), for autoConfigItems.
  const supportedById = useMemo(
    () => Object.fromEntries((supportedMods || []).map((sm) => [sm.id, sm])),
    [supportedMods]
  )

  // The Auto config items of a row's ⋯ menu. A supported mod gets Config files,
  // Zedstead's own setup: it opens the mod's Configs -> Mods tab, whatever XML
  // the mod has. Any other mod that ships no XML of its own gets Auto config,
  // which writes a manual types file for the mod. Once that file exists, Edit
  // auto config opens its form on Custom config. A mod with another manual
  // file of the admin's gets neither. A supported mod that already has an auto
  // config file keeps Edit auto config beside Config files.
  function autoConfigItems(m, s) {
    const supported = m.id && supportedById[m.id]
    const edit = s?.auto_file
      ? [
          {
            key: 'edit-auto-config',
            label: 'Edit auto config',
            tip: 'Opens the Auto configure file of this mod in the Types form on Custom config.',
            onClick: () => navigate(manualFileLink(s.auto_file)),
          },
        ]
      : []
    if (supported) {
      return [
        {
          key: 'config-files',
          label: 'Go to config files',
          tip: `Zedstead sets up ${supported.group} itself. Opens its config editors on Configs → Mods.`,
          onClick: () => navigate(`/configs?tab=Mods&subtab=${encodeURIComponent(supported.group)}`),
        },
        ...edit,
      ]
    }
    if (edit.length) return edit
    // A mod with no XML at all has no status row, so a missing row counts as none.
    if (s?.files > 0 || s?.manual > 0) return []
    // A framework mod (@CF) defines no item class, so there is nothing to write.
    const noItems = s?.no_items
    return [
      {
        key: 'auto-config',
        label: 'Auto configuration',
        icon: HiOutlineSparkles,
        disabled: !m.enabled || noItems,
        tip: noItems
          ? `${m.name} defines no item classes in its PBOs, so there is nothing to configure.`
          : m.enabled
          ? 'Writes types.xml entries for this mod: the author’s entries from the Workshop page, or values copied from the vanilla item each class extends. You see them before anything is written, then change the values in the Types form.'
          : 'Enable the mod first to auto configure it',
        onClick: () => previewAutoConfig(m.name),
      },
    ]
  }

  // The Re-configure and Restore items of a row's ⋯ menu: re-apply this mod's
  // mission XML, or turn back on the XML files the admin turned off.
  function reconfigureItems(m, s) {
    const items = []
    if (s?.actionable > 0) {
      items.push({
        key: 'reconfigure',
        label: 'Re-configure XML',
        disabled: !m.enabled,
        tip: m.enabled
          ? help.actions?.reconfigure || 'Re-apply just this mod’s mission XML, leaving other mods’ merged XML alone.'
          : 'Enable the mod first to re-apply its XML',
        onClick: () => reconfigureXml(m.name),
      })
    }
    if (s?.off > 0) {
      items.push({
        key: 'restore-xml',
        label: `Restore ${s.off} XML file${s.off === 1 ? '' : 's'}`,
        tip: `Turn the ${s.off} XML file(s) you disabled or deleted back on. Re-configure leaves them alone.`,
        onClick: () => restoreXml(m.name, s.off),
      })
    }
    return items
  }

  function rowMenuItems(m) {
    const steamTip = (tip) => (steamLoggedOut ? NEEDS_STEAM_TIP : tip)
    const s = xmlStatus[m.name]
    const xmlItems = [...autoConfigItems(m, s), ...reconfigureItems(m, s)]
    if (xmlItems.length) xmlItems[0] = { ...xmlItems[0], group: 'XML', divider: true }
    return [
      {
        key: 'update',
        label: 'Update',
        disabled: steamLoggedOut,
        tip: steamTip(help.actions?.update || 'Re-download this mod from the Workshop and re-copy it to the server.'),
        onClick: () => updateMods([m]),
      },
      {
        key: 'reinstall',
        label: 'Reinstall',
        disabled: steamLoggedOut || !m.id,
        tip: m.id ? steamTip(help.actions?.reinstall) : 'No Workshop ID, so there is nothing to download again.',
        onClick: () => updateMods([m], 'reinstall'),
      },
      ...xmlItems,
      {
        key: 'players',
        group: 'Loads for',
        divider: true,
        label: 'Players + server',
        checked: !m.server_side,
        tip: 'In -mod=: players download it too.',
        onClick: () => m.server_side && toggleServerSide(m),
      },
      {
        key: 'server',
        label: 'Server only',
        checked: !!m.server_side,
        tip: 'In -serverMod=: players never download it.',
        onClick: () => !m.server_side && toggleServerSide(m),
      },
      {
        key: 'uninstall',
        label: 'Uninstall',
        divider: true,
        danger: true,
        tip: help.actions?.uninstall,
        onClick: () => askUninstall([m.name]),
      },
    ]
  }
  // Named in the uninstall confirm rather than in the row: it is a warning
  // about the action, and it must be read at the moment it is confirmed.
  const uninstallWipeWarning = useMemo(
    () => (uninstallNames ? wipeWarningFor({ names: uninstallNames }) : null),
    [uninstallNames]
  )
  const uninstallOne = uninstallNames?.length === 1 ? uninstallNames[0] : null

  // Detected updates, keyed by workshop id — joins the Updates tab's data onto
  // the installed rows so the Update button says whether one is waiting.
  const updateById = useMemo(
    () => new Map(updates.map((u) => [String(u.mod_id), u])),
    [updates]
  )
  const outdatedMods = orderedMods.filter((m) => m.id && updateById.has(String(m.id)))
  const noApiKey = updateCheck?.api_key_set === false
  // The Updates tab knows mods by id only — this gets the name back for the
  // install body's id_names map (used for the folder / mod-list entry).
  const modsById = useMemo(
    () => new Map(mods.filter((m) => m.id).map((m) => [String(m.id), m])),
    [mods]
  )

  // Re-install one or more installed mods (see startInstall — an update IS an
  // install). By id where there is one, so the download skips name resolution.
  // `mode` 'reinstall' also deletes each mod's workshop cache copy first.
  function updateMods(list, mode = 'update') {
    const ids = list.filter((m) => m.id).map((m) => String(m.id))
    const id_names = {}
    for (const m of list) if (m.id && m.name) id_names[String(m.id)] = m.name
    const names = list.filter((m) => !m.id).map((m) => m.name)
    runInstall({ names, ids, id_names }, mode)
  }

  const installOptions = (
    <div className="install-options">
      <div className="dry-run-toggle">
        <Switch checked={installDeps} onChange={setInstallDeps}>
          Install missing dependencies (e.g. @CF)
        </Switch>
      </div>
    </div>
  )

  return (
    <div className="page mods-page">
      <div className="page-header">
        <h1>Mods</h1>
        <div className="action-bar">
          <Link className="btn btn-ghost" to={configLink('mod-xml')}>
            <HiOutlineCodeBracket aria-hidden="true" />
            {LINKS['mod-xml'].label}
          </Link>
        </div>
      </div>

      <PageIntro page="/mods" />

      <SteamLoginGate onLoggedIn={loadMods} />

      {/* Only when there is something to go back to AND a reason to want it:
          the last run never became joinable. A rollback button on a healthy
          server is an invitation to undo an update that worked (TODO C5). */}
      {rollback?.offer && (
        <div className="banner banner-warning rollback-banner">
          <p>
            <strong>The last mod update may have broken the server.</strong>
            <BetaBadge feature="mod-rollback" />
          </p>
          <p>
            The server did not come up after the update. Zedstead kept the mod versions from before the update, so
            you can go back to them.
          </p>
          <details>
            <summary>
              <FoldCaret /> What Zedstead found
            </summary>
            <p>{rollback.reason}</p>
          </details>
          <ul>
            <li>
              <strong>Restore the previous mods</strong> puts the old versions back into the server folder. Stop
              the server first.
            </li>
            <li>
              <strong>Discard them</strong> deletes the kept copies, if the new versions are fine. You can then not
              go back.
            </li>
          </ul>
          <div className="action-bar">
            <button className="btn btn-primary" onClick={restoreRollback}>
              <RestoreIcon />
              Restore the previous mods
            </button>
            <button className="btn btn-ghost" onClick={discardRollback}>
              <HiOutlineTrash aria-hidden="true" />
              Discard them
            </button>
          </div>
        </div>
      )}

      <Tabs
        tabs={TABS}
        active={tab}
        onSelect={setTab}
        renderLabel={modTabLabel}
      />

      {/* The tab's help shows here once it is open, so the tabs carry no tooltip. */}
      <PageIntro text={help.tabs?.[tab]} sub />

      {tab === 'Installed' && (
        <div className="card">
          <div className="page-header">
            <h2>Installed mods</h2>
            <div className="action-bar">
              {/* Save and Reset show only once the order changed. They sit
                  left of Re-configure, so that button never moves. Reset
                  throws the unsaved moves away: the saved order comes back
                  from the server, as on a page load. */}
              {orderDirty && (
                <button className="btn btn-ghost" onClick={() => loadOrder()}>
                  <HiOutlineArrowUturnLeft aria-hidden="true" />
                  Reset
                </button>
              )}
              {orderDirty && (
                <button className="btn btn-primary" onClick={saveOrder}>
                  <HiOutlineCheck aria-hidden="true" />
                  Save load order
                </button>
              )}
              {/* Nothing to merge with no mods installed — the whole-mission
                  merge would only rebuild custom/.
                  Manual XML files are applied as they are saved on Configs → Custom config. */}
              <Tooltip keep text={mods.length ? '' : 'No mods installed — nothing to configure.'}>
                <button
                  id="reconfigure-xml"
                  className="btn btn-ghost"
                  disabled={!mods.length}
                  onClick={() => reconfigureXml(null)}
                >
                  <HiOutlineCog6Tooth aria-hidden="true" />
                  Re-configure all XML
                </button>
              </Tooltip>
              {outdatedMods.length > 0 && (
                <NeedsSteam keep tip={help.actions?.update}>
                  <button className="btn btn-primary" onClick={() => updateMods(outdatedMods)}>
                    <HiOutlineArrowUpCircle aria-hidden="true" />
                    Update all <CountBadge value={outdatedMods.length} />
                  </button>
                </NeedsSteam>
              )}
            </div>
          </div>
          {/* Folded by default: it is read once, and it pushed the list down. */}
          <details className="panel-note installed-help">
            <summary>
              <FoldCaret /> How the load order works
            </summary>
            <p>
              Rows are in load order: {moveHint}, then Save.{' '}
              {manualOrder ? (
                <>
                  Manual order: <LockIcon open /> marks mods with dependencies, and any mod can move
                  anywhere. A mod that loads before one it requires is marked in amber.
                </>
              ) : (
                <>
                  <LockIcon /> marks dependency-constrained mods; they can’t move past mods they
                  require or that require them.
                </>
              )}{' '}
              A disabled mod is not loaded. It stays after the mod it follows and moves with that mod, so
              turning it back on loads it there.
            </p>
          </details>
          {/* Auto/Manual sits right above the rows it orders. The bulk
              actions join the same line at its right while rows are ticked. */}
          <div className="action-bar load-order-bar">
            <Tooltip keep text={help.actions?.manual} className="bare">
              <Segmented
                label="Load order"
                value={manualOrder}
                onChange={setOrderMode}
                options={[
                  { value: false, label: 'Auto' },
                  { value: true, label: 'Manual' },
                ]}
              />
            </Tooltip>
            {selectedCount > 0 && (
              <span className="installed-bulk" role="group" aria-label="Selected mods">
                <span className="installed-bulk-label">
                  <CountBadge value={selectedCount} /> selected
                </span>
                <NeedsSteam keep tip={help.actions?.update}>
                  <button className="btn btn-ghost" onClick={() => updateMods(selectedMods)}>
                    <HiOutlineArrowDownTray aria-hidden="true" />
                    Update
                  </button>
                </NeedsSteam>
                {selectedWithId.length > 0 && (
                  <NeedsSteam keep tip={help.actions?.reinstall}>
                    <button className="btn btn-ghost" onClick={() => updateMods(selectedWithId, 'reinstall')}>
                      <HiOutlineArrowPath aria-hidden="true" />
                      Reinstall
                    </button>
                  </NeedsSteam>
                )}
                {selectedMods.some((m) => !m.enabled) && (
                  <button className="btn btn-ghost" onClick={() => setEnabledMany(selectedMods, true)}>
                    <HiOutlinePower aria-hidden="true" />
                    Turn on
                  </button>
                )}
                {selectedMods.some((m) => m.enabled) && (
                  <button className="btn btn-ghost" onClick={() => setEnabledMany(selectedMods, false)}>
                    <HiOutlinePower aria-hidden="true" />
                    Turn off
                  </button>
                )}
                <Tooltip keep text={help.actions?.uninstall} className="bare">
                  <button className="btn btn-danger" onClick={() => askUninstall(selectedNames)}>
                    <HiOutlineTrash aria-hidden="true" />
                    Uninstall
                  </button>
                </Tooltip>
                <button className="btn btn-ghost" onClick={() => setSelected({})}>
                  <HiOutlineXMark aria-hidden="true" />
                  Deselect
                </button>
              </span>
            )}
          </div>
          {/* .installed-mods: one line per row; a wide table scrolls sideways,
              with its scrollbar kept at the window's bottom edge (`scroll`).
              Each row is a card on a phone, and on any screen the table
              does not fit (`cards="fit"`, index.css). */}
          <div className={compactCards ? 'installed-mods installed-compact' : 'installed-mods'}>
          {/* The cards hide the header row and its tick-all box, so this
              line stands in for it while they show (index.css). */}
          {installedNames.length > 0 && (
            <label className="inline-check installed-tick-all">
              <TickAllBox
                checked={allTicked}
                mixed={someTicked}
                onChange={tickAllInstalled}
                label={allTicked ? 'Deselect all mods' : 'Select all mods'}
              />
              {allTicked ? 'Deselect all mods' : 'Select all mods'}
            </label>
          )}
          <LoadOrderDnd order={order} inOrder={inOrder} selected={selected} onDragEnd={onOrderDragEnd}>
            <DataTable
              scroll
              cards="fit"
              cardsBelow={COMPACT_CARDS_PX}
              rowKey={(m) => m.name}
              emptyMessage="No mods installed."
              rows={orderedMods}
              Row={InstalledRow}
              columns={[
                // Its own column so `#` sits directly above the numbers. The
                // padlock of a mod its dependencies hold sits first, at the
                // far left; its slot is kept on every row, so the grips line up.
                {
                  key: 'grip',
                  label: '',
                  className: 'installed-lead installed-handle',
                  render: (m) => (
                    <span className="grip-cell">
                      <span className="grip-lock">
                        {hasConstraints(m.name) && (
                          <Tooltip keep
                            text={(manualOrder ? help.actions?.lock_open : help.actions?.lock) || 'Dependency-constrained'}
                            className="bare"
                          >
                            <LockIcon open={manualOrder} />
                          </Tooltip>
                        )}
                      </span>
                      <RowGrip />
                    </span>
                  ),
                },
                {
                  key: 'select',
                  label: '',
                  className: 'installed-lead installed-handle',
                  header: (
                    <TickAllBox
                      checked={allTicked}
                      mixed={someTicked}
                      onChange={tickAllInstalled}
                      label={allTicked ? 'Deselect all mods' : 'Select all mods'}
                    />
                  ),
                  render: (m) => (
                    <input
                      type="checkbox"
                      checked={!!selected[m.name]}
                      onChange={(e) => toggleSelected(m.name, e)}
                    />
                  ),
                },
                {
                  key: 'order',
                  label: '#',
                  className: 'installed-lead',
                  help: help.actions?.reorder || 'Mod load order — lower loads first.',
                  render: (m) => {
                    const i = orderIndex.get(m.name)
                    if (i === undefined) return <span className="dep-info">—</span>
                    return <span className="order-index">{i + 1}</span>
                  },
                },
                {
                  key: 'name',
                  label: 'Name',
                  // Takes the spare width, so the columns after it sit at the right edge.
                  className: 'installed-name cell-name',
                  render: (m) => {
                    const requires = requiresOf(m.name)
                    const requiredBy = requiredByOf(m.name)
                    // Only manual order can put a mod here (plans/49).
                    const loadsBefore = requires.filter((d) => orderIndex.get(d) > orderIndex.get(m.name))
                    return (
                      <span className="load-order-item">
                        <Tooltip keep text={m.info}>
                          <Link className="mod-link" to={modLink(m.name)}>
                            {m.name}
                          </Link>
                        </Tooltip>
                        <WorkshopLink id={m.id} small />
                        {/* The only case worth a mark: the mod list is what lists a
                            row, so a folder on disk is the norm. */}
                        {!m.installed && (
                          <span
                            className="status-pill status-pill-failed"
                            {...tooltipAttrs(
                              `The mod list has ${m.name} but its folder is not in the server directory, so the server cannot load it. Update downloads and copies it again.`,
                              { keep: true }
                            )}
                          >
                            folder missing
                          </span>
                        )}
                        {m.set_aside && !m.enabled && (
                          <span
                            className={`status-pill ${m.set_aside.updated_at ? 'status-pill-success' : 'status-pill-neutral'}`}
                            {...tooltipAttrs(
                              m.set_aside.updated_at
                                ? `Set aside after DayZ build ${m.set_aside.build || '?'} because the server did not come up with it. Its author has published an update since (${new Date(m.set_aside.updated_at * 1000).toLocaleDateString()}). Turn it On to try it again; Zedstead never does that by itself.`
                                : `Set aside after DayZ build ${m.set_aside.build || '?'} because the server did not come up with it. Zedstead says when its author publishes an update, and never turns it back on by itself.`,
                              { keep: true }
                            )}
                          >
                            {m.set_aside.updated_at ? 'updated since' : 'set aside'}
                          </span>
                        )}
                        {loadsBefore.length > 0 && (
                          <span
                            className="status-pill status-pill-warning"
                            {...tooltipAttrs(
                              `${m.name} requires ${loadsBefore.join(', ')} but loads before it. The server may fail to start, or the mod may error. Move it below, then Save.`,
                              { keep: true }
                            )}
                          >
                            loads too early
                          </span>
                        )}
                        {/* Only the unusual states get a chip: Players + server
                            and On are the norm. */}
                        {m.server_side && (
                          <span
                            className="status-pill status-pill-neutral"
                            {...tooltipAttrs('In -serverMod=: players never download it. Change it in the row’s ⋯ menu.')}
                          >
                            server only
                          </span>
                        )}
                        {!m.enabled && (
                          <span
                            className="status-pill status-pill-neutral"
                            {...tooltipAttrs('Kept installed but not loaded.')}
                          >
                            off
                          </span>
                        )}
                        <DepChips verb="requires" names={requires} />
                        <DepChips verb="required by" names={requiredBy} />
                      </span>
                    )
                  },
                },
                {
                  key: 'xml',
                  label: 'XML',
                  className: 'installed-xml',
                  help: help.actions?.xml || 'Mission XML files this mod contributes.',
                  render: (m) => {
                    const s = xmlStatus[m.name]
                    const summary = xmlSummary(s)
                    // A mod with no XML says so when its ⋯ menu offers Auto
                    // configuration, and that item's icon button follows.
                    if (!summary) {
                      const items = autoConfigItems(m, s)
                      if (!items.length) return null
                      const auto = items.find((i) => i.key === 'auto-config')
                      return (
                        <>
                          <span className="field-hint">No XML</span>
                          {auto && (
                            // Wrapped, so a disabled button still says why.
                            <Tooltip keep text={auto.tip} className="bare">
                              <button
                                type="button"
                                className={rowActionClass()}
                                aria-label={auto.label}
                                disabled={auto.disabled}
                                onClick={auto.onClick}
                              >
                                <auto.icon aria-hidden="true" />
                              </button>
                            </Tooltip>
                          )}
                        </>
                      )
                    }
                    const parts = [
                      s.linked && `${s.linked} linked`,
                      s.merged && `${s.merged} merged`,
                      s.pending && `${s.pending} not applied yet`,
                      s.disabled && `${s.disabled} disabled`,
                      // A turned-off file is not "actionable", so only this
                      // cell says it is there.
                      s.off && `${s.off} turned off by you`,
                      // The admin's own XML bound to this mod lives on the
                      // Mod XML page; nothing else here hints it exists.
                      s.auto_file && '1 file written by Auto configuration',
                      s.manual - (s.auto_file ? 1 : 0) > 0 &&
                        `${s.manual - (s.auto_file ? 1 : 0)} manual XML file(s) of yours`,
                      s.skipped && `${s.skipped} skipped`,
                    ].filter(Boolean)
                    return (
                      <Tooltip keep text={parts.join(', ')} className="cell-nowrap">
                        {/* To Custom config, with this mod's files marked. */}
                        <Link className="mod-link" to={modXmlLink(m.name, { manualOnly: !s.files })}>
                          {summary}
                        </Link>
                      </Tooltip>
                    )
                  },
                },
                {
                  // Its own column, so the switch has a heading.
                  key: 'enabled',
                  label: 'Enabled',
                  className: 'installed-enabled',
                  render: (m) => (
                    <Switch
                      checked={m.enabled}
                      onChange={() => toggleEnabled(m)}
                      label={`${m.name} enabled`}
                      {...tooltipAttrs(
                        withHelp(
                          m.enabled ? 'On: loaded by the server, in load order.' : 'Off: kept installed but not loaded.',
                          help.actions?.enable_disable
                        ),
                        { keep: true }
                      )}
                    />
                  ),
                },
                {
                  // An Update button only while one is waiting, and the
                  // ⋯ menu with the rest (rowMenuItems).
                  key: 'actions',
                  label: '',
                  className: 'cell-actions',
                  render: (m) => {
                    // The cached update check (Updates tab) knows mods by id only.
                    const u = m.id ? updateById.get(String(m.id)) : null
                    return (
                      <span className="installed-actions">
                        {u && (
                          <NeedsSteam keep tip={`Workshop updated ${u.age_display} — re-download and re-copy this mod.`}>
                            <button className="btn btn-small btn-primary" onClick={() => updateMods([m])}>
                              Update
                            </button>
                          </NeedsSteam>
                        )}
                        <RowMenu label={`${m.name}: more actions`} items={rowMenuItems(m)} />
                      </span>
                    )
                  },
                },
              ]}
            />
          </LoadOrderDnd>
          </div>
        </div>
      )}

      {tab === 'Install' && (
        <>
          <Tabs
            tabs={INSTALL_SUBTABS}
            active={installTab}
            onSelect={setInstallTab}
            sub
            renderLabel={(t) => (
              <Tooltip text={help.tabs?.[t]} className="bare">
                {t}
              </Tooltip>
            )}
          />

          {installTab === 'Supported' && (
            <div className="card">
              <h2>Mods with editors in Zedstead</h2>
              <PanelNote>
                Each of these gets its own tab under Configs → Mods, and most also get layers in the
                Map Editor. A mod writes its settings files on the server's first start with it, so
                the editors fill in after that.
              </PanelNote>
              {installOptions}
              {supportedMods ? (
                <SupportedMods
                  mods={supportedMods}
                  onInstall={(m) => runInstall({ ids: [m.id], id_names: { [m.id]: m.name } })}
                  onPick={() => setInstallTab('Expansion')}
                  onUninstall={uninstallSupported}
                  tip={help.actions?.install}
                />
              ) : (
                <PanelNote>Loading…</PanelNote>
              )}
            </div>
          )}

          {installTab === 'By Name' && (
            <div className="card">
              <h2>Install by name</h2>
              <PanelNote>
                One or more mod names, separated by commas, e.g. <code>@CF, @TraderPlus</code>. A name
                Zedstead does not know has to match the mod's Workshop title (case, @, spaces and dashes
                do not matter). If it is not found, use By ID.
              </PanelNote>
              <input
                className="text-input"
                value={installNames}
                onChange={(e) => setInstallNames(e.target.value)}
                placeholder="@CF, @TraderPlus"
              />
              {installOptions}
              <div className="action-bar">
                <NeedsSteam keep tip={help.actions?.install_selected}>
                  <button className="btn btn-primary" onClick={handleInstallByName}>
                    <HiOutlineArrowDownTray aria-hidden="true" />
                    Install
                  </button>
                </NeedsSteam>
              </div>
            </div>
          )}

          {installTab === 'By ID' && (
            <div className="card">
              <h2>Install by workshop ID</h2>
              <PanelNote>
                The number at the end of the mod's Workshop address (<code>…?id=2458896948</code>).
                The name is optional: it becomes the mod's folder and its entry in the mod list. Left
                empty, Zedstead uses the name it already knows for that ID, or <code>@</code> and the ID.
              </PanelNote>
              <input
                className="text-input"
                inputMode="numeric"
                pattern="[0-9]*"
                value={installId}
                onChange={(e) => setInstallId(e.target.value)}
                placeholder="2458896948"
              />
              <input
                className="text-input"
                value={installIdName}
                onChange={(e) => setInstallIdName(e.target.value)}
                placeholder="@TraderPlus (optional)"
              />
              {installOptions}
              <div className="action-bar">
                <NeedsSteam keep tip={help.actions?.install}>
                  <button className="btn btn-primary" onClick={handleInstallById}>
                    <HiOutlineArrowDownTray aria-hidden="true" />
                    Install
                  </button>
                </NeedsSteam>
              </div>
            </div>
          )}

          {installTab === 'Workshop' && (
            <div className="card">
              <h2>Workshop folder</h2>
              <PanelNote>
                Mods SteamCMD has already downloaded but the server does not have yet. Tick the ones to
                copy into the server.
              </PanelNote>
              {installOptions}
              <ModPicker
                items={cachedMods}
                filter={cachedFilter}
                setFilter={setCachedFilter}
                selected={cachedSelected}
                setSelected={setCachedSelected}
                onAction={() => installPicked(cachedSelected, setCachedSelected, cachedMods, true)}
                needsSteam
                tip={help.actions?.install_selected}
                emptyMessage="No downloaded mods that the server does not have yet."
                linkById
              />
            </div>
          )}

          {installTab === 'Search' && (
            <div className="card">
              <h2>Search known mods</h2>
              <PanelNote>
                Not a Steam Workshop search. These are the mods Zedstead already knows by name and
                Workshop ID: its built-in list, the IDs looked up before, and what is in the Workshop
                folder. Installed mods are left out. For a mod that is not here, use By Name or By ID.
              </PanelNote>
              {installOptions}
              <ModPicker
                items={knownMods}
                filter={knownFilter}
                setFilter={setKnownFilter}
                selected={knownSelected}
                setSelected={setKnownSelected}
                onAction={() => installPicked(knownSelected, setKnownSelected, knownMods)}
                needsSteam
                tip={help.actions?.install_selected}
                emptyMessage="No matching mods."
              />
            </div>
          )}

          {installTab === 'Expansion' && (
            <div className="card">
              <h2>DayZ-Expansion modules</h2>
              <PanelNote>
                Expansion ships as 19 separate mods — tick the ones you want. Dependencies are
                ticked and locked for you (everything needs Core, Core needs the two frameworks,
                Vehicles also needs Licensed). Select all installs every module.
              </PanelNote>
              {installOptions}
              {expansionData ? (
                <ExpansionPicker
                  data={expansionData}
                  picked={expansionPicked}
                  setPicked={setExpansionPicked}
                  onInstall={installExpansion}
                  tip={help.actions?.install_selected}
                />
              ) : (
                <PanelNote>Loading…</PanelNote>
              )}
            </div>
          )}

          {installTab === 'Expansion' && (
            <div className="card">
              <h2>Expansion mission files</h2>
              <ExpansionMission onChanged={loadXmlStatus} />
            </div>
          )}
        </>
      )}

      {tab === 'Conflicts' && (
        <div className="card">
          <div className="page-header">
            <h2>{modTabLabel('Conflicts')}</h2>
            <Tooltip keep text={help.actions?.scan} className="bare">
              <button className="btn btn-primary" disabled={scanning} onClick={scanConflicts}>
                {scanning ? 'Scanning…' : 'Scan for conflicts'}
              </button>
            </Tooltip>
          </div>
          {conflicts && (
            <DataTable
              rowKey={(item, i) => `${item.category}-${item.mod_name}-${i}`}
              emptyMessage="No issues found."
              rows={conflicts.items.map((item, i) => ({ ...item, i }))}
              columns={[
                { key: 'severity', label: 'Severity' },
                { key: 'category', label: 'Category' },
                {
                  key: 'mod_name',
                  label: 'Mod',
                  render: (item) =>
                    item.mod_name ? (
                      <Link className="mod-link" to={modLink(item.mod_name)}>
                        {item.mod_name}
                      </Link>
                    ) : (
                      <span className="dep-info">—</span>
                    ),
                },
                {
                  key: 'message',
                  label: 'Message',
                  render: (item) => <ModNameText text={item.message} known={knownModNames} />,
                },
                {
                  key: 'dep',
                  label: 'Missing dep',
                  help: 'Dependency Zedstead can install for you — only missing_dep findings carry one. Its Install button is in the Fix column.',
                  render: (item) => {
                    const ref = item.dep_name || item.dep_id
                    if (!ref) return <span className="dep-info">—</span>
                    return (
                      <Link className="mod-link" to={modLink(ref)}>
                        {ref}
                      </Link>
                    )
                  },
                },
                {
                  key: 'fix',
                  label: 'Fix',
                  help: 'What the finding means and how to clear it. The buttons run the fix from here; the chips go to the view that owns it.',
                  render: (item) => (
                    <FixCell fix={item.fix} onAction={runFixAction} busyAction={fixBusy} />
                  ),
                },
              ]}
            />
          )}
          {/* Same block the Doctor page shows under its conflicts table: the
              dependency graph the scan found, every edge of it satisfied. The
              payload is the scan dataclass here, so the keys differ. */}
          {conflicts && (
            <DependencyEdges
              edges={conflicts.pbo_debug?.dep_edges}
              pbosScanned={conflicts.pbo_debug?.pbos_scanned}
            />
          )}
        </div>
      )}

      {tab === 'Updates' && (
        <div className="card">
          <div className="page-header">
            <h2>Available updates</h2>
            <div className="action-bar">
              {outdatedMods.length > 0 && (
                <NeedsSteam keep tip={help.actions?.update}>
                  <button className="btn btn-primary" onClick={() => updateMods(outdatedMods)}>
                    <HiOutlineArrowUpCircle aria-hidden="true" />
                    Update all <CountBadge value={outdatedMods.length} />
                  </button>
                </NeedsSteam>
              )}
              {/* Wrapped: without a key the button is disabled, and the tip
                  is what says why (a disabled button fires no mouseenter). */}
              <Tooltip keep text={noApiKey ? NO_API_KEY_TIP : help.tabs?.Updates} className="bare">
                <button
                  className="btn btn-ghost"
                  disabled={checkingUpdates || noApiKey}
                  onClick={checkForUpdates}
                >
                  <HiOutlineMagnifyingGlass aria-hidden="true" />
                  {checkingUpdates ? 'Checking…' : 'Check for updates'}
                </button>
              </Tooltip>
            </div>
          </div>
          {noApiKey && (
            <div className="banner banner-warning">
              Update checks need a Steam Web API key. Set{' '}
              <Link to="/settings?q=steam_api_key">steam_api_key</Link> in Settings.
            </div>
          )}
          <DataTable
            rowKey={(u) => u.mod_id}
            emptyMessage={updatesEmptyMessage(updateCheck)}
            rows={updates}
            columns={[
              {
                key: 'title',
                label: 'Mod',
                // Updates only carry the workshop id — the mod page resolves it.
                render: (u) => (
                  <Link className="mod-link" to={modLink(u.mod_id)}>
                    {u.title || u.mod_id}
                  </Link>
                ),
              },
              {
                key: 'mod_id',
                label: 'Workshop ID',
                help: 'Opens the mod on the Steam Workshop in a new tab.',
                render: (u) => <WorkshopLink id={u.mod_id} />,
              },
              { key: 'age_display', label: 'Updated' },
              {
                key: 'update',
                label: '',
                help: help.actions?.update,
                // Re-install the mod = update it. Only installed mods are
                // checked for updates, so the row always has one to act on.
                render: (u) => (
                  <NeedsSteam>
                    <button
                      className="btn btn-primary btn-small"
                      onClick={() =>
                        updateMods([
                          { id: String(u.mod_id), name: modsById.get(String(u.mod_id))?.name },
                        ])
                      }
                    >
                      Update
                    </button>
                  </NeedsSteam>
                ),
              },
            ]}
          />
        </div>
      )}

      {tab === 'Workshop Cache' && (
        <div className="card">
          <div className="page-header">
            <h2>Workshop cache</h2>
            <button
              className="btn btn-danger"
              disabled={!wsCache?.length}
              onClick={() =>
                removeWorkshopCache(null, 'Remove ALL downloaded workshop mod cache dirs?')
              }
            >
              Remove all
            </button>
          </div>
          <PanelNote>
            A removed mod that is still in the mod list is downloaded again on the next update.
          </PanelNote>
          {wsCache === null ? (
            <PanelNote>Workshop directory not found.</PanelNote>
          ) : wsCache.length === 0 ? (
            <PanelNote>No downloaded workshop mods.</PanelNote>
          ) : (
            <ModPicker
              items={wsCacheItems}
              filter={wsCacheFilter}
              setFilter={setWsCacheFilter}
              selected={wsCacheSelected}
              setSelected={setWsCacheSelected}
              keyOf={(m) => m.path}
              actionLabel="Remove selected"
              tip={help.actions?.remove_cache_selected}
              ActionIcon={HiOutlineTrash}
              danger
              onAction={() => {
                const paths = wsCache.filter((m) => wsCacheSelected[m.path]).map((m) => m.path)
                removeWorkshopCache(paths, `Remove ${paths.length} workshop mod cache dir(s)?`)
              }}
              emptyMessage="No downloaded workshop mods."
            />
          )}
        </div>
      )}

      <InstallOptionsModal
        request={installPrompt}
        onConfirm={(flags) => startInstall(installPrompt, flags)}
        onCancel={() => setInstallPrompt(null)}
      />

      <ConfirmModal
        open={!!uninstallNames}
        title={
          uninstallOne || uninstallLabel ? `Uninstall ${uninstallOne || uninstallLabel}?` : 'Uninstall selected mods?'
        }
        message={
          uninstallOne
            ? `This removes ${uninstallOne} from the server.`
            : `This removes ${uninstallNames?.length || 0} mod(s) from the server.`
        }
        confirmLabel="Uninstall"
        danger
        onConfirm={handleUninstall}
        onCancel={() => setUninstallNames(null)}
      >
        {uninstallWipeWarning && (
          <p className="banner banner-warning">{uninstallWipeWarning}</p>
        )}
        {uninstallMission > 0 && (
          <p className="modal-message">
            The {uninstallMission} applied Expansion mission file(s) are removed too, once the
            uninstall succeeds. The trader and object .map files stay in the mission.
          </p>
        )}
        <OrphanDepsField checked={removeDeps} onChange={setRemoveDeps} />
      </ConfirmModal>

      <ConfirmModal
        open={!!autoConfig}
        title={`Auto configure ${autoConfig?.mod || ''}?`}
        message={autoConfigMessage(autoConfig)}
        confirmLabel="Add entries"
        confirmDisabled={!autoConfig?.count}
        onConfirm={applyAutoConfig}
        onCancel={() => setAutoConfig(null)}
      >
        <div className="autoconfig-dialog">
        <div className="autoconfig-options">
          {AUTO_CONFIG_OPTIONS.map((o) => (
            <Switch
              key={o.key}
              checked={!!autoConfig?.options?.[o.key]}
              onChange={() =>
                previewAutoConfig(autoConfig.mod, { ...autoConfig.options, [o.key]: !autoConfig.options[o.key] })
              }
              label={o.label}
              help={o.tip}
            >
              {o.label}
            </Switch>
          ))}
        </div>
        {autoConfig?.guessed?.some((g) => g.not_loot) && (
          <p className="field-hint">
            No category and no starter nominal, since their base is no loot (a car, a building or an unknown class):{' '}
            {autoConfig.guessed.filter((g) => g.not_loot).map((g) => `${g.name} (${g.not_loot})`).join(', ')}.
          </p>
        )}
        {autoConfig?.split?.length > 0 && (
          <p className="field-hint">
            Shared: {autoConfig.split.map((g) => `${g.names.length} variants of ${g.parent} get nominal ${g.nominal}, min ${g.min} each`).join('; ')}.
          </p>
        )}
        {autoConfig?.workshop_note && <p className="banner banner-info">Workshop page: {autoConfig.workshop_note}</p>}
        {autoConfig?.zero_nominal?.length > 0 && (
          <p className="banner banner-warning">
            Nominal 0, so these never spawn as loot until you set nominal and min in the form that opens next: {autoConfig.zero_nominal.join(', ')}
          </p>
        )}
        {autoConfig?.skipped?.length > 0 && (
          <details>
            <summary>{autoConfig.skipped.length} class(es) skipped</summary>
            <ul>
              {autoConfig.skipped.map((row) => (
                <li key={row.name}>
                  {row.name}: {row.reason}
                </li>
              ))}
            </ul>
          </details>
        )}
        {autoConfig?.xml && <CodeArea language="xml" value={autoConfig.xml} onChange={() => {}} readOnly />}
        </div>
      </ConfirmModal>

    </div>
  )
}
