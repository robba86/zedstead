import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import useDismissDetails from '../useDismissDetails.js'
import {
  DndContext,
  KeyboardSensor,
  MouseSensor,
  closestCenter,
  useSensor,
  useSensors,
} from '@dnd-kit/core'
import { SortableContext, arrayMove, rectSortingStrategy, sortableKeyboardCoordinates } from '@dnd-kit/sortable'
import { HiOutlineArrowUturnLeft, HiOutlinePlus } from 'react-icons/hi2'
import { resolveHelpSlug } from '../helpLinks.js'
import { PageIntroTip } from '../components/PageIntro.jsx'
import { PAGE_HELP } from '../pageHelp.js'
import { useToast } from '../components/Toast.jsx'
import { useServerStatus } from '../serverStatus'
import { roleAtLeast, useRole } from '../role.jsx'
import {
  DEFAULT_STATUS_LAYOUT,
  clearLayout,
  isDefaultLayout,
  readLayout,
  useStatusLayout,
  writeLayout,
} from '../dashboardLayout.js'
import { tooltipAttrs } from '../components/Tooltip.jsx'
import RunCommandCard from '../components/RunCommandCard.jsx'
import SpawnEditorCard from '../components/SpawnEditorCard.jsx'
import InstallServerCard from '../components/InstallServerCard.jsx'
import QuickSetupStart from '../components/QuickSetupStart.jsx'
import SteamLoginGate from '../components/SteamLoginGate.jsx'
import ErrorBanner, { REOPEN_DAILY_MS } from '../components/ErrorBanner.jsx'
import ServiceSetupCard from '../components/ServiceSetupCard.jsx'
import DoctorCard from '../components/DoctorCard.jsx'
import GuideCard from '../components/GuideCard.jsx'
import PlayersCard from '../components/PlayersCard.jsx'
import BansCard from '../components/BansCard.jsx'
import BackupsCard from '../components/BackupsCard.jsx'
import ModsCard from '../components/ModsCard.jsx'
import ScheduleCard from '../components/ScheduleCard.jsx'
import EconomyCard from '../components/EconomyCard.jsx'
import PerformanceCard from '../components/PerformanceCard.jsx'
import SortableCard, { CardTouchSensor } from '../components/SortableCard.jsx'
import TileSkeleton from '../components/Skeleton.jsx'
import CountBadge from '../components/CountBadge.jsx'

// The dashboard's tiles, in shipped order. `span` is grid columns (the grid is
// 4 wide, one column below 900px) and is only the *default*: the viewer resizes
// a tile by dragging its right edge. Each tile owns its own data fetching — this
// list only decides placement, so adding one is a single entry here.
//
// `id` is a permanent key in every saved layout (dashboardLayout.js): renaming
// one drops the tile to the end of every existing user's arrangement. That is
// why the Guide's id is still 'wizard' after the rename.
//
// `minRole` drops a tile for a role that could only reach a refusal through
// it: the Map Editor tile exists to open an admin page, Run Command reads an
// admin-only route, and Service Setup's one control is an admin write. Such a
// tile is neither drawn nor offered in Add tile; the saved layout is untouched.
//
// `defaultHidden` ships a tile in the Add-tile menu instead of on the page.
// Service Setup uses it because the Doctor tile shows the same buttons under
// its "Service" row while the service is missing (2026 Q4). Economy is shown
// by default on the maintainer's word, although GET /api/analysis/economy
// parses every types.xml: the tile mounts only near the window (useNearWindow).
const CARDS = [
  { id: 'doctor', title: 'Doctor', span: 2, render: () => <DoctorCard /> },
  { id: 'wizard', title: 'Guide', span: 2, render: () => <GuideCard /> },
  { id: 'performance', title: 'Performance', span: 2, render: () => <PerformanceCard /> },
  { id: 'bans', title: 'Bans', span: 2, render: () => <BansCard /> },
  { id: 'mods', title: 'Mods', span: 2, render: () => <ModsCard /> },
  { id: 'schedule', title: 'Scheduled tasks', span: 2, render: () => <ScheduleCard /> },
  { id: 'economy', title: 'Economy score', span: 2, render: () => <EconomyCard /> },
  { id: 'backups', title: 'Backups', span: 2, render: () => <BackupsCard /> },
  { id: 'mapeditor', title: 'Map Editor', span: 4, minRole: 'admin', render: () => <SpawnEditorCard /> },
  { id: 'players', title: 'Players online', span: 2, render: () => <PlayersCard /> },
  {
    id: 'runcommand',
    title: 'Run Command',
    span: 4,
    minRole: 'admin',
    defaultHidden: true,
    render: () => <RunCommandCard showEdit />,
  },
  {
    id: 'service',
    title: 'Service Setup',
    span: 4,
    minRole: 'admin',
    defaultHidden: true,
    render: () => <ServiceSetupCard />,
  },
]

const CARDS_BY_ID = Object.fromEntries(CARDS.map((c) => [c.id, c]))

// A tile below the window stays a placeholder card, with no fetch and no
// drawing, until it comes within this distance of the window; then it mounts
// for good (2026 Q4, a slow VM). The placeholder is a `.card`, so
// SortableCard never reads it as an empty tile.
const NEAR_WINDOW = '300px'

function useNearWindow() {
  const ref = useRef(null)
  const [near, setNear] = useState(() => typeof IntersectionObserver === 'undefined')
  useEffect(() => {
    if (near || !ref.current) return undefined
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) setNear(true)
      },
      { rootMargin: NEAR_WINDOW }
    )
    observer.observe(ref.current)
    return () => observer.disconnect()
  }, [near])
  return [near, ref]
}

// A tile's content is drawn through memo, so a status push (every 5 s) or a
// layout change redraws only the tiles whose own props changed. Each `render`
// is wrapped once here, so the components themselves stay plain.
const TileContent = memo(function TileContent({ card, pushToast }) {
  const [near, ref] = useNearWindow()
  if (!near) {
    return (
      <div className="card" ref={ref}>
        <div className="card-header">
          <h2>{card.title}</h2>
        </div>
        <TileSkeleton lines={3} />
      </div>
    )
  }
  return card.render({ pushToast })
})
const DEFAULT_LAYOUT = {
  order: CARDS.map((c) => c.id),
  hidden: CARDS.filter((c) => c.defaultHidden).map((c) => c.id),
  spans: {},
  status: DEFAULT_STATUS_LAYOUT,
}

// Resolved, not spelled out — same rule as every other route in the app.
const DOCTOR_LINK = resolveHelpSlug('doctor')

export default function DashboardPage() {
  // Server telemetry comes from the shared global store (polled once
  // server-side over the `status` WS channel), not a per-page HTTP poll. The
  // page only needs it for the banners below.
  const { status, refresh, error: statusError } = useServerStatus()
  const { role } = useRole()
  const isAdmin = roleAtLeast(role, 'admin')
  const addMenuRef = useDismissDetails()
  const [layout, setLayout] = useState(() => readLayout(DEFAULT_LAYOUT))
  // The status panel edits its own slice of the saved layout, so take it from
  // the store rather than from this page's copy. Otherwise a change made there
  // would be written back stale by the next card move here.
  const statusLayout = useStatusLayout()
  const statusRef = useRef(statusLayout)
  statusRef.current = statusLayout
  const layoutRef = useRef(layout)
  layoutRef.current = layout
  // Cards whose component rendered nothing (their fetch failed, or the feature
  // is not configured) — hidden, kept out of the sort order so they can't
  // become an invisible drop target, and marked unavailable in the Add menu.
  const [empty, setEmpty] = useState(() => new Set())
  const pushToast = useToast()

  const sensors = useSensors(
    // A few pixels of movement before a drag starts, so the grip can also be
    // focused/clicked without yanking the card. Touch is its own sensor: a
    // long press anywhere on a card, or a short drag on the grip.
    useSensor(MouseSensor, { activationConstraint: { distance: 4 } }),
    useSensor(CardTouchSensor),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates })
  )

  // Every layout change goes through here: one writer, one persist. `patch` is
  // the axis that changed; the status slice is refilled from the store unless
  // the patch is the status change itself.
  //
  // Deliberately NOT a functional setState: writeLayout notifies the status
  // store's subscribers, and a state updater runs during the render phase —
  // waking another component from there is what React warns about.
  const update = useCallback((patch) => {
    const next = { ...layoutRef.current, status: statusRef.current, ...patch }
    setLayout(next)
    writeLayout(next)
  }, [])

  const markEmpty = useCallback((id, isEmpty) => {
    setEmpty((prev) => {
      if (prev.has(id) === isEmpty) return prev // no-op keeps the render loop finite
      const next = new Set(prev)
      if (isEmpty) next.add(id)
      else next.delete(id)
      return next
    })
  }, [])

  const hiddenSet = useMemo(() => new Set(layout.hidden), [layout.hidden])
  // Tiles this role may see at all; everything below works on this list.
  const order = useMemo(
    () => layout.order.filter((id) => roleAtLeast(role, CARDS_BY_ID[id]?.minRole || 'viewer')),
    [layout.order, role]
  )
  const shown = useMemo(() => order.filter((id) => !hiddenSet.has(id)), [order, hiddenSet])
  // Draggable ids: rendered and non-empty.
  const sortableIds = useMemo(() => shown.filter((id) => !empty.has(id)), [shown, empty])
  const hiddenCards = useMemo(
    () => order.filter((id) => hiddenSet.has(id)).map((id) => CARDS_BY_ID[id]).filter(Boolean),
    [order, hiddenSet]
  )
  // The menu lists what the user hid, plus the tiles that are nominally on the
  // page but render nothing (no RCON, a failed fetch). Without the second half
  // those tiles are simply absent with no explanation anywhere.
  const menuCards = useMemo(
    () =>
      order
        .filter((id) => hiddenSet.has(id) || empty.has(id))
        .map((id) => CARDS_BY_ID[id])
        .filter(Boolean),
    [order, hiddenSet, empty]
  )

  const onDragEnd = useCallback(
    ({ active, over }) => {
      if (!over || active.id === over.id) return
      const from = layout.order.indexOf(active.id)
      const to = layout.order.indexOf(over.id)
      if (from < 0 || to < 0) return
      update({ order: arrayMove(layout.order, from, to) })
    },
    [layout, update]
  )

  const hideCard = useCallback(
    (id) => update({ hidden: [...new Set([...layout.hidden, id])] }),
    [layout.hidden, update]
  )

  const showCard = useCallback(
    (id) => update({ hidden: layout.hidden.filter((h) => h !== id) }),
    [layout.hidden, update]
  )

  const setSpan = useCallback(
    (id, span) => {
      const spans = { ...layout.spans }
      // A span back at the shipped default is an absence, not an entry — that
      // is what lets "Reset layout" disappear again.
      if (span === CARDS_BY_ID[id]?.span) delete spans[id]
      else spans[id] = span
      update({ spans })
    },
    [layout.spans, update]
  )

  const resetLayout = useCallback(() => {
    clearLayout(DEFAULT_LAYOUT)
    setLayout(DEFAULT_LAYOUT)
  }, [])

  const atDefault = isDefaultLayout({ ...layout, status: statusLayout }, DEFAULT_LAYOUT)

  return (
    <div className="page dashboard-page">
      <div className="page-header">
        <div className="page-title">
          <h1>Dashboard</h1>
          <PageIntroTip text={PAGE_HELP['/']} page="/" label="About the dashboard" />
        </div>
        <div className="action-bar">
          {menuCards.length > 0 && (
            <details className="dash-add" ref={addMenuRef}>
              {/* The label hides on a phone (.btn-text), where the
                  buttons share the title's row; the tip and the name stay. */}
              <summary
                className="btn btn-ghost"
                aria-label={`Add tile (${hiddenCards.length})`}
                {...tooltipAttrs(`Add tile (${hiddenCards.length})`)}
              >
                <HiOutlinePlus aria-hidden="true" />
                <span className="btn-text">
                  Add tile <CountBadge value={hiddenCards.length} />
                </span>
              </summary>
              <div className="dash-add-menu">
                {menuCards.map((card) => {
                  // Nothing to render: adding it back would change nothing, so
                  // say why instead of offering a button that does nothing.
                  const unavailable = empty.has(card.id)
                  return (
                    <button
                      key={card.id}
                      type="button"
                      className={`dash-add-item${unavailable ? ' dash-add-item-off' : ''}`}
                      disabled={unavailable}
                      data-tooltip-id="app-tip"
                      data-tooltip-content={
                        unavailable
                          ? `${card.title} has no data to show yet — it appears once it does.`
                          : undefined
                      }
                      onClick={() => showCard(card.id)}
                    >
                      <span>{card.title}</span>
                      <span className="dash-add-plus">{unavailable ? 'no data' : '+'}</span>
                    </button>
                  )
                })}
              </div>
            </details>
          )}
          {!atDefault && (
            <button
              className="btn btn-ghost"
              onClick={resetLayout}
              aria-label="Reset layout"
              {...tooltipAttrs('Reset layout')}
            >
              <HiOutlineArrowUturnLeft aria-hidden="true" />
              <span className="btn-text">Reset layout</span>
            </button>
          )}
          {/* Start/Stop/Restart are in the status strip, as on every page. */}
        </div>
      </div>

      {statusError && (
        <ErrorBanner dismissKey={`status:${statusError}`}>
          Server status unavailable: {statusError}
          <button className="btn btn-ghost" onClick={refresh}>Retry</button>
        </ErrorBanner>
      )}

      {isAdmin && <QuickSetupStart />}
      {isAdmin && <SteamLoginGate />}

      {isAdmin && <InstallServerCard />}

      {/* The watchdog, said out loud. `running: false` cannot tell a server
          somebody stopped from one crashing every thirty seconds, and the
          panel showed the same grey box for both until this. */}
      {status?.restarts?.limit_hit && (
        <ErrorBanner dismissKey={`restart-limit:${status.restarts.count}`} reopenAfterMs={REOPEN_DAILY_MS}>
          {/* One span, or the flex row splits the sentence into columns. */}
          <span>
            <strong>The server kept crashing, so systemd stopped restarting it.</strong>{' '}
            It will stay down until you start it. Read the Activity drawer or{' '}
            <Link to={DOCTOR_LINK}>Doctor</Link> for the reason before pressing Start — the
            crash is still there.
          </span>
        </ErrorBanner>
      )}

      {status?.restarts?.looping && (
        <div className="banner banner-warning">
          <strong>The server is crash-looping.</strong> It has restarted{' '}
          {status.restarts.count}{' '}
          {status.restarts.count === 1 ? 'time' : 'times'} since it was last started,
          and is waiting to try again.
        </div>
      )}

      <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
        <SortableContext items={sortableIds} strategy={rectSortingStrategy}>
          <div className="dash-grid">
            {shown.map((id) => {
              const card = CARDS_BY_ID[id]
              if (!card) return null
              return (
                <SortableCard
                  key={id}
                  id={id}
                  title={card.title}
                  span={layout.spans[id] ?? card.span}
                  defaultSpan={card.span}
                  hidden={empty.has(id)}
                  onEmptyChange={markEmpty}
                  onSpanChange={setSpan}
                  onHide={hideCard}
                >
                  <TileContent card={card} pushToast={pushToast} />
                </SortableCard>
              )
            })}
          </div>
        </SortableContext>
      </DndContext>
    </div>
  )
}
