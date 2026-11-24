// The Loot finder (plans/82): a wide panel over the map's right edge, open
// while the Loot layer is on. Three views:
//
//   Item      pick any type the economy loads from a grouped, filterable list
//             (category groups, usage chips, a mod filter). The map marks every
//             building it can spawn in; the panel says why, in plain words.
//             A click on a marked building opens it in the Building view.
//   Mod       the same for every item of one mod, and the same click.
//   Building  click a building on the map: what it is, its loot containers,
//             and every item that can spawn in it (GET /loot/at). The item
//             picked in the Item view stays on top, marked in the list.
//
// The picked item or mod and the view live in the URL (?loot= / ?lootmod= /
// ?lootview=), so a view is linkable; the filters are local. The match is the
// server's. The Building view also shows the Buildings layer, and the
// Buildings switch opens it (MapEditorProvider `showBuildings`). Closing the
// panel from that view leaves the buildings on (?bld=1).

import L from 'leaflet'
import { useEffect, useMemo, useRef, useState } from 'react'
import { HiXMark } from 'react-icons/hi2'
import FilterBox from '../../../components/FilterBox.jsx'
import Segmented from '../../../components/Segmented.jsx'
import Select from '../../../components/Select.jsx'
import { Switch } from '../../../components/FlagSwitch.jsx'
import { fuzzyFilter } from '../../../fuzzy.js'
import { ItemName, ItemSwatch } from '../../../components/ItemIcon.jsx'
import ListSection from '../../../components/ListSection.jsx'
import ListRow, { RowTag } from '../../../components/ListRow.jsx'
import CountBadge from '../../../components/CountBadge.jsx'
import PanelNote from '../../../components/PanelNote.jsx'
import { tooltipAttrs } from '../../../components/Tooltip.jsx'
import BuildingIcon from '../icons/BuildingIcon.jsx'
import { meApi } from '../api'
import { gameToLatLng } from '../coords'
import { pickRadius } from '../leaflet/useBuildingLayer'
import { buildingBadgeAt } from '../leaflet/useBuildingBadges'
import { lootPointAt } from '../leaflet/useLootLayer'
import { badgeZoom } from '../leaflet/badges'
import { isMapAlive, useMapEvent } from '../leaflet/useLeafletMap'
import { useEditor } from '../state/MapEditorProvider'
import { MOBILE_QUERY, useMediaQuery } from '../../../useMediaQuery.js'
import SheetResizeHandle, { LOOT_SHEET, useSheetHeight } from './SheetResizeHandle.jsx'

// The item picker never renders more than this many options at once: a
// mission with mods loads tens of thousands of types.
const MAX_SEARCH_RESULTS = 200
const MAX_PER_CATEGORY = 80
export const NO_MISSION = 'No installed mission is for this map, so there are no buildings to draw'

const TIER_NOTE =
  "Value tiers (Tier1–4) come from areaflags.map, which Zedstead cannot read, so a building shown may still never get this item if its area's tier is not in the item's list."

const CLICK_HINT = 'Click a marked building on the map to see what else it can hold.'

const NOT_SHOWN =
  'Not counted: attachments and cargo, dynamic event loot (heli crashes, police cars), and wrecks and other cluster loot.'

const PROBLEMS = {
  no_mission: NO_MISSION,
  no_mapgroupproto: "This map's mission has no mapgroupproto.xml, so Zedstead cannot tell what each building holds.",
  no_mapgrouppos: "This map's mission has no mapgrouppos.xml, so there are no buildings to mark.",
}

// The server's short reasons (loot_map._never_placed, _no_match_reason).
const REASONS = {
  'nominal 0': 'Its nominal is 0, so the economy never places it.',
  crafted: 'Players craft it. The economy never places it as loot.',
  'event loot only': 'It spawns only at dynamic events, such as heli crashes, never in buildings.',
  'no building has its usage': 'No building on this map has one of its usages.',
  'no container takes its category/tag': 'Buildings with its usage exist, but none has a container for its category and tag.',
  'no building of its kind is on the map': 'The buildings that can hold it are not placed on this map.',
}

function problemText(problem) {
  if (!problem) return ''
  if (PROBLEMS[problem]) return PROBLEMS[problem]
  if (problem.startsWith('unreadable: ')) {
    return `${problem.slice('unreadable: '.length)} could not be read, so nothing can be marked.`
  }
  return problem
}

const reasonText = (reason) => REASONS[reason] || reason
const fmt = (n) => Number(n || 0).toLocaleString()
const plural = (n, one, many = `${one}s`) => `${fmt(n)} ${n === 1 ? one : many}`

// Land_Ship_Big_BackA -> "Ship Big Back A". The classname stays beside it.
export function buildingLabel(name) {
  return name
    .replace(/^land_/i, '')
    .replace(/_/g, ' ')
    .replace(/([a-z])([A-Z0-9])/g, '$1 $2')
    .replace(/\s+/g, ' ')
    .trim()
}

const capital = (text) => (text ? text[0].toUpperCase() + text.slice(1) : text)

function Chips({ values, empty = 'none' }) {
  if (!values?.length) return <span className="me-loot-muted">{empty}</span>
  return (
    <span className="me-loot-chips">
      {values.map((v) => (
        <RowTag key={v}>{v}</RowTag>
      ))}
    </span>
  )
}

function Fact({ label, children }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{children}</dd>
    </>
  )
}

// Rows grouped under a heading, each group a foldable ListSection
// (webui/DESIGN.md). `groups` is [{key, title, rows, count?}], `row` renders
// one. Up to four groups start open, as the <details> list did.
function GroupedList({ groups, row, header }) {
  return (
    <div className="me-loot-groups">
      {header}
      {groups.map((g) => (
        <ListSection
          key={g.key}
          title={g.title}
          count={g.count ?? g.rows.length}
          foldable
          defaultOpen={groups.length <= 4}
        >
          {g.rows.map(row)}
        </ListSection>
      ))}
    </div>
  )
}

function groupBy(rows, keyOf) {
  const map = new Map()
  for (const r of rows) {
    const key = keyOf(r)
    if (!map.has(key)) map.set(key, [])
    map.get(key).push(r)
  }
  return map
}

function byCategory(rows) {
  return [...groupBy(rows, (r) => r.category || '').entries()]
    .sort(([a], [b]) => (a === '' ? 1 : b === '' ? -1 : a.localeCompare(b)))
    .map(([key, list]) => ({ key: key || 'none', title: capital(key) || 'No category', rows: list }))
}

// One item in a result list: its icon and class name, a second line, and
// `meta` on the right. A click shows only that item, when `onPick` is given.
// `selected` marks the item picked in the Item view.
function ItemRow({ row, sub, meta, onPick, selected = false }) {
  return (
    <ListRow
      compact
      name={
        <ItemName name={row.name}>
          <span className="me-loot-mono">{row.name}</span>
        </ItemName>
      }
      sub={sub}
      meta={meta}
      selected={selected}
      onClick={onPick ? () => onPick(row.name) : undefined}
    />
  )
}

// ── Item picker ──────────────────────────────────────────────────────────

function ItemPicker({ catalog, value, onPick }) {
  const owners = useMemo(() => catalog?.owners || [], [catalog])
  const categories = useMemo(() => catalog?.categories || [], [catalog])
  const usages = useMemo(() => catalog?.usages || [], [catalog])
  const items = useMemo(() => catalog?.items || [], [catalog])
  const [search, setSearch] = useState('')
  const [usageOn, setUsageOn] = useState(() => new Set())
  const [owner, setOwner] = useState('')
  const [placedOnly, setPlacedOnly] = useState(true)

  const usageCounts = useMemo(() => {
    const counts = usages.map(() => 0)
    for (const row of items) for (const u of row[3]) counts[u] += 1
    return counts
  }, [items, usages])

  const filtered = useMemo(
    () =>
      items.filter(
        (row) =>
          (!placedOnly || !row[5]) &&
          (owner === '' || String(row[1]) === owner) &&
          (!usageOn.size || row[3].some((u) => usageOn.has(u)))
      ),
    [items, placedOnly, owner, usageOn]
  )

  // A search result is a flat list, so it names its category; a browsed
  // option sits under its category's heading.
  const toOption = (row, grouped) => ({
    value: row[0],
    label: row[0],
    description: [
      !grouped && (categories[row[2]] || 'no category'),
      owners[row[1]]?.label,
      row[5] ? `never spawns: ${row[5]}` : `nominal ${row[4]}`,
    ]
      .filter(Boolean)
      .join(' · '),
  })

  const options = useMemo(() => {
    if (search.trim()) {
      return fuzzyFilter(filtered, search, (row) => row[0]).slice(0, MAX_SEARCH_RESULTS).map((row) => toOption(row, false))
    }
    const groups = groupBy(filtered, (row) => row[2])
    return [...groups.entries()]
      .sort(([a], [b]) => (a < 0 ? 1 : b < 0 ? -1 : categories[a].localeCompare(categories[b])))
      .map(([cat, rows]) => {
        const shown = rows.slice(0, MAX_PER_CATEGORY).map((row) => toOption(row, true))
        if (rows.length > shown.length) {
          shown.push({
            value: `__more_${cat}`,
            label: `${fmt(rows.length - shown.length)} more: type to narrow`,
            disabled: true,
          })
        }
        return { label: `${capital(categories[cat]) || 'No category'} (${fmt(rows.length)})`, options: shown }
      })
    // toOption reads only the catalog, which the deps already cover.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filtered, search, categories, owners])

  function toggleUsage(index) {
    setUsageOn((prev) => {
      const next = new Set(prev)
      if (next.has(index)) next.delete(index)
      else next.add(index)
      return next
    })
  }

  return (
    <div className="me-loot-picker">
      <Select
        className="text-input me-loot-select"
        aria-label="Item"
        placeholder={`Search ${fmt(filtered.length)} items, or open the list…`}
        value={value}
        options={options}
        searchable
        filterOption={null}
        onSearch={setSearch}
        onChange={onPick}
        clearable
        disabled={!items.length}
      />
      {usages.length > 0 && (
        <div className="me-loot-filter">
          <span className="me-loot-filter-label">Found in</span>
          <div className="me-loot-chips">
            {usages.map((u, i) => (
              <button
                key={u}
                type="button"
                className={`me-loot-chip me-loot-chip-btn${usageOn.has(i) ? ' on' : ''}`}
                aria-pressed={usageOn.has(i)}
                onClick={() => toggleUsage(i)}
              >
                {u} <CountBadge value={usageCounts[i]} />
              </button>
            ))}
          </div>
        </div>
      )}
      <div className="me-loot-filter-row">
        <Select
          className="text-input me-loot-select"
          aria-label="Mod"
          value={owner}
          onChange={setOwner}
          options={[
            { value: '', label: 'All mods' },
            ...owners.map((o, i) => ({ value: String(i), label: `${o.label} (${fmt(o.count)})` })),
          ]}
        />
        <Switch checked={placedOnly} onChange={setPlacedOnly}>
          Only items that spawn
        </Switch>
      </div>
    </div>
  )
}

// ── Results ──────────────────────────────────────────────────────────────

// `active` is the kind whose row was clicked last: that row stays marked, as
// its building is on the map.
function BuildingKinds({ data, usages, onFly, active }) {
  // Under the first of the item's usages each building has, so "Military"
  // and "Town" read as sections; a mod's items use the building's own first.
  const wanted = new Set(usages || [])
  const groups = [...groupBy(data.groups, (g) => g.usages.find((u) => wanted.has(u)) || g.usages[0] || '').entries()]
    .map(([usage, rows]) => ({
      key: usage || 'none',
      title: (
        <span className="item-name">
          <BuildingIcon usage={usage} />
          <span>{usage || 'No usage'}</span>
        </span>
      ),
      rows,
      total: rows.reduce((n, g) => n + g.buildings, 0),
    }))
    .sort((a, b) => b.total - a.total)
    .map((g) => ({ ...g, count: g.total }))
  return (
    <GroupedList
      groups={groups}
      header={<PanelNote>Click a building kind to fly there. The number is how many are on the map.</PanelNote>}
      row={(g) => (
        <ListRow
          key={g.name}
          compact
          lead={
            <span className="item-icon-wrap">
              <BuildingIcon name={g.name} usages={g.usages} />
              <ItemSwatch name={g.name} />
            </span>
          }
          name={buildingLabel(g.name)}
          sub={g.name}
          meta={<CountBadge value={g.buildings} />}
          selected={g.name === active}
          aria-current={g.name === active ? 'true' : undefined}
          data-tooltip-id="app-tip"
          data-tooltip-content={
            `Loot containers that take it: ${g.containers.join(', ')}` +
            (g.items != null ? `. ${plural(g.items, 'item')} can spawn in it.` : '')
          }
          onClick={() => onFly(g.first, g.name)}
        />
      )}
    />
  )
}

// Every building kind on the map that holds loot (GET /loot/catalog's
// `buildings`), filterable, in the same list the item view uses.
function AllBuildingKinds({ kinds, onOpen }) {
  const [filter, setFilter] = useState('')
  const shown = useMemo(
    () => fuzzyFilter(kinds, filter, (g) => [g.name, buildingLabel(g.name), ...g.usages]),
    [kinds, filter]
  )
  if (!kinds.length) return null
  return (
    <ListSection title="Building kinds with loot" count={kinds.length}>
      <FilterBox
        value={filter}
        onChange={setFilter}
        placeholder="Filter by name or usage…"
        shown={shown.length}
        total={kinds.length}
      />
      <BuildingKinds data={{ groups: shown }} onFly={onOpen} />
    </ListSection>
  )
}

function ItemResult({ data, onFly, active }) {
  const item = data.items?.[0]
  if (!item) return null
  return (
    <>
      <div className="me-loot-card">
        <div className="me-loot-card-title">
          <ItemName name={item.name} category={item.category} />
        </div>
        {item.buildings > 0 ? (
          <div className="me-loot-big">
            Can spawn in <strong>{fmt(data.count)}</strong> buildings of {plural(data.kinds, 'kind')}
          </div>
        ) : (
          <div className="me-loot-big me-loot-none">Never spawns in a building. {reasonText(item.reason)}</div>
        )}
        {item.buildings > 0 && <PanelNote>{CLICK_HINT}</PanelNote>}
        <dl className="me-loot-facts">
          <Fact label="From">{item.owner_label}</Fact>
          <Fact label="Category">
            <Chips values={item.category ? [item.category] : []} empty="none (any container)" />
          </Fact>
          <Fact label="Tag">
            <Chips values={item.tag ? [item.tag] : []} empty="none" />
          </Fact>
          <Fact label="Found in">
            <Chips values={item.usages} empty="any building (no usage)" />
          </Fact>
          {item.values.length > 0 && (
            <Fact label="Tiers">
              <Chips values={item.values} />
            </Fact>
          )}
          <Fact label="Nominal">
            {fmt(item.nominal)} <span className="me-loot-muted">(how many the economy keeps on the map)</span>
          </Fact>
        </dl>
        <PanelNote>
          A building can hold it when it has one of these usages and a loot container that takes this category
          {item.tag ? ' and tag' : ''}.
        </PanelNote>
      </div>
      {data.tiers_unchecked && <div className="banner banner-info me-loot-banner">{TIER_NOTE}</div>}
      {data.groups?.length > 0 && <BuildingKinds data={data} usages={item.usages} onFly={onFly} active={active} />}
    </>
  )
}

function ModResult({ data, onPick, onFly, active }) {
  const spawnable = (data.items || []).filter((row) => row.buildings > 0)
  const cannot = (data.items || []).filter((row) => !row.buildings)
  const reasons = [...groupBy(cannot, (r) => r.reason).entries()].map(([reason, rows]) => ({
    key: reason,
    title: reasonText(reason),
    rows,
  }))
  return (
    <>
      <div className="me-loot-card">
        <div className="me-loot-card-title">{data.selection.label}</div>
        <div className="me-loot-big">
          <strong>{fmt(data.matched)}</strong> of {plural(data.items?.length || 0, 'item')} can spawn, in{' '}
          {fmt(data.count)} buildings of {plural(data.kinds, 'kind')}
        </div>
        {data.count > 0 && <PanelNote>{CLICK_HINT}</PanelNote>}
      </div>
      {data.truncated && <PanelNote>The item list is cut at 2,000. The map shows them all.</PanelNote>}
      {data.tiers_unchecked && <div className="banner banner-info me-loot-banner">{TIER_NOTE}</div>}
      {spawnable.length > 0 && (
        <ListSection title="Items that can spawn" count={spawnable.length}>
          <PanelNote>Click one to see only it.</PanelNote>
          <GroupedList
            groups={byCategory(spawnable)}
            row={(row) => (
              <ItemRow
                key={row.name}
                row={row}
                sub={row.usages.join(', ') || 'any building'}
                meta={<CountBadge value={row.buildings} />}
                onPick={onPick}
              />
            )}
          />
        </ListSection>
      )}
      {data.groups?.length > 0 && (
        <ListSection title="Buildings">
          <BuildingKinds data={data} onFly={onFly} active={active} />
        </ListSection>
      )}
      {cannot.length > 0 && (
        <ListSection title="Never in a building" count={cannot.length}>
          <GroupedList groups={reasons} row={(row) => <ItemRow key={row.name} row={row} />} />
        </ListSection>
      )}
    </>
  )
}

// ── Building view ────────────────────────────────────────────────────────

// `item` is the pick from the Item view (?loot=). It shows above the list,
// with a plain answer when this building cannot hold it.
function BuildingResult({ building, item, onPick }) {
  const [filter, setFilter] = useState('')
  const shown = useMemo(
    () => fuzzyFilter(building.items, filter, (row) => [row.name, row.category || '']),
    [building, filter]
  )
  const pickedRow = item ? building.items.find((row) => row.name === item) : null
  const nominalMeta = (row) => (
    <span {...tooltipAttrs('Nominal: how many the economy keeps on the map')}>n{fmt(row.nominal)}</span>
  )
  return (
    <>
      <div className="me-loot-card">
        <div className="me-loot-card-title">
          <span className="item-name">
            <span className="item-icon-wrap">
              <BuildingIcon name={building.name} usages={building.usages} />
              <ItemSwatch name={building.name} />
            </span>
            {buildingLabel(building.name)}
          </span>
        </div>
        <div className="me-loot-row-sub">
          {building.name} · {plural(building.on_map, 'building')} of this kind on the map
        </div>
        <dl className="me-loot-facts">
          <Fact label="Usage">
            <Chips values={building.usages} empty="none (only items with no usage)" />
          </Fact>
          {building.values.length > 0 && (
            <Fact label="Tiers">
              <Chips values={building.values} />
            </Fact>
          )}
          <Fact label="Containers">
            {building.containers.length ? (
              <ul className="me-loot-containers">
                {building.containers.map((c) => (
                  <li key={c.name}>
                    <span className="me-loot-mono">{c.name}</span> <span className="me-loot-muted">({c.points} spots)</span>
                    <Chips values={[...c.categories, ...c.tags.map((t) => `tag: ${t}`)]} empty="takes anything" />
                  </li>
                ))}
              </ul>
            ) : (
              <span className="me-loot-muted">none with spawn points</span>
            )}
          </Fact>
        </dl>
      </div>
      {item && (
        <ListSection title="Your item">
          {pickedRow ? (
            <ItemRow row={pickedRow} sub={`Can spawn here · ${pickedRow.owner_label}`} meta={nominalMeta(pickedRow)} selected />
          ) : (
            <PanelNote>
              <span className="me-loot-mono">{item}</span> cannot spawn in this building.
            </PanelNote>
          )}
        </ListSection>
      )}
      <ListSection title="Can spawn here" count={building.items.length}>
        {building.items.length > 0 && (
          <>
            <FilterBox value={filter} onChange={setFilter} placeholder="Filter by name or category…" shown={shown.length} total={building.items.length} />
            <GroupedList
              groups={byCategory(shown)}
              row={(row) => (
                <ItemRow
                  key={row.name}
                  row={row}
                  sub={row.owner_label}
                  meta={nominalMeta(row)}
                  onPick={onPick}
                  selected={row.name === item}
                />
              )}
            />
          </>
        )}
      </ListSection>
    </>
  )
}

// ── The panel ────────────────────────────────────────────────────────────

export default function LootDrawer({ map }) {
  const { url, terrain, ui, lootCatalog, lootCatalogLoading, lootData, lootLoading, mapInfo, dispatch, isActiveTerrain } =
    useEditor()
  const { item, mod, view } = url.loot
  const setView = (next) => url.setLoot({ open: true, item, mod, view: next })
  const [picked, setPicked] = useState(null) // {loading, building, error}
  // On a phone the panel is a bottom sheet with a drag handle, like Tools.
  const isMobile = useMediaQuery(MOBILE_QUERY)
  const sheetRef = useRef(null)
  const [sheetHeight, setSheetHeight] = useSheetHeight(sheetRef, LOOT_SHEET)

  const inspecting = view === 'building' && !ui.addMode

  // An empty name (the list's ×) clears the pick, and with it the map's points.
  function pickItem(name) {
    url.setLoot({ open: true, item: name })
  }

  function flyTo(first) {
    const pos = gameToLatLng(first[0], first[1], mapInfo?.bounds, mapInfo?.scale)
    // Close enough for the icon badges, and never farther out than zoom 5.
    const zoom = Math.max(5, badgeZoom(mapInfo.scale))
    if (pos) dispatch({ type: 'FLY_TO', payload: { lat: pos.lat, lng: pos.lng, zoom } })
    return pos
  }

  // A row in the Item or Mod view's list: fly to its first building and ring
  // that one, so the map says which point the row meant. A new pick drops it.
  const [flown, setFlown] = useState(null) // {name, lat, lng, at}
  useEffect(() => setFlown(null), [item, mod])
  function flyToKind(first, name) {
    const pos = flyTo(first)
    // `at` restarts the pulse when the same row is clicked again.
    if (pos) setFlown({ name, lat: pos.lat, lng: pos.lng, at: Date.now() })
  }

  // The building nearest to a point within *radius* metres, into the view.
  async function inspectAt(lat, lng, radius) {
    setPicked({ loading: true })
    try {
      const q = new URLSearchParams({ terrain, lat, lng, radius: radius.toFixed(1) })
      const data = await meApi.get(`/loot/at?${q}`)
      setPicked({ building: data.building, missed: !data.building })
    } catch (err) {
      setPicked({ error: err.message })
    }
  }

  // A row in the list of every building kind: fly to the first one and open
  // its report, as a click on it would.
  function openKind(first) {
    flyTo(first)
    const pos = gameToLatLng(first[0], first[1], mapInfo?.bounds, mapInfo?.scale)
    if (pos) inspectAt(pos.lat, pos.lng, 3)
  }

  // A click picks the nearest building within PICK_PX screen pixels
  // (pickRadius), so the reach follows the zoom.
  // In the Item and Mod views, a click on a marked point (or its badge) opens
  // that building in the Building view, with the pick kept for the way back.
  useMapEvent(map, 'click', async (e) => {
    if (ui.addMode || !isMapAlive(map)) return
    if (!inspecting) {
      const hit = (view === 'item' && item) || (view === 'mod' && mod)
        ? lootPointAt(map, mapInfo, lootData?.points, e.latlng, view === 'item')
        : null
      if (!hit) return
      url.setLoot({ open: true, item, mod, view: 'building' })
      inspectAt(hit.lat, hit.lng, 3)
      return
    }
    // Zoomed in, a click on a building's badge picks that building.
    const badge = buildingBadgeAt(map, mapInfo, lootCatalog, e.latlng)
    if (badge) {
      inspectAt(badge.lat, badge.lng, 3)
      return
    }
    inspectAt(e.latlng.lat, e.latlng.lng, pickRadius(map, mapInfo, e.latlng))
  })

  // A building clicked on the map while this panel was closed (MapEditor.jsx):
  // the click opened the Building view, and here it is looked up.
  const lootPick = ui.lootPick
  useEffect(() => {
    if (!lootPick) return
    inspectAt(lootPick.lat, lootPick.lng, lootPick.radius)
    dispatch({ type: 'LOOT_PICK', payload: null })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lootPick])

  // The picked building in the Building view, or the building a list row
  // flew to in the others, ringed on the map. The flown one also pulses.
  const building = view === 'building' ? picked?.building : null
  const ringed = building || (view !== 'building' ? flown : null)
  useEffect(() => {
    if (!isMapAlive(map) || !ringed) return
    const ring = L.circleMarker([ringed.lat, ringed.lng], {
      radius: building ? 11 : 16,
      className: building ? 'me-loot-picked' : 'me-loot-picked me-loot-flown',
      interactive: false,
    }).addTo(map)
    return () => ring.remove()
  }, [map, ringed, building])

  const problem = problemText(lootCatalog?.problem)
  const data = lootData

  return (
    <aside
      ref={sheetRef}
      style={isMobile && sheetHeight != null ? { height: sheetHeight } : undefined}
      className={`me-loot-drawer${inspecting ? ' inspecting' : ''}`}
      aria-label="Loot finder"
    >
      {isMobile && (
        <SheetResizeHandle
          sheetRef={sheetRef}
          setHeight={setSheetHeight}
          sheet={LOOT_SHEET}
          label="Drag or tap to resize the loot finder"
        />
      )}
      {/* Title, views and close on one row, so a phone keeps more map. */}
      <header className="me-loot-head">
        <span className="me-loot-head-title">Loot finder</span>
        <Segmented
          label="Find loot by"
          value={view}
          onChange={setView}
          options={[
            { value: 'item', label: 'Item', tip: 'Where can this item spawn?' },
            { value: 'mod', label: 'Mod', tip: "Where can this mod's items spawn?" },
            { value: 'building', label: 'Building', tip: 'What can spawn in this building?' },
          ]}
        />
        <button
          type="button"
          className="btn btn-ghost btn-small me-loot-close"
          aria-label="Close the loot finder"
          // Closed from the Building view, the buildings it showed stay on
          // (one URL write); the Buildings switch turns them off.
          onClick={() =>
            view === 'building' ? url.setMapLayers({ buildings: true, buildingView: false }) : url.setLoot({ open: false })
          }
        >
          <HiXMark aria-hidden="true" />
        </button>
      </header>

      <div className="me-loot-body">
        {lootCatalogLoading && !lootCatalog && <PanelNote>Loading the item list…</PanelNote>}
        {problem && <div className="banner banner-warning me-loot-banner">{problem}</div>}
        {lootCatalog && !isActiveTerrain && (
          <PanelNote>
            This map's mission is not the running one, so only the types merged into it before are listed.
          </PanelNote>
        )}
        {lootCatalog?.unreadable?.length > 0 && (
          <PanelNote>Could not be read: {lootCatalog.unreadable.join('; ')}</PanelNote>
        )}

        {view === 'item' && (
          <>
            <ItemPicker catalog={lootCatalog} value={item} onPick={pickItem} />
            {!item && (
              <PanelNote>
                Pick an item to mark every building where it can spawn. Open the list to browse by category, or type
                part of a name.
              </PanelNote>
            )}
            {item && lootLoading && !data && <PanelNote>Matching…</PanelNote>}
            {item && data?.selection?.kind === 'item' && <ItemResult data={data} onFly={flyToKind} active={flown?.name} />}
          </>
        )}

        {view === 'mod' && (
          <>
            <Select
              className="text-input me-loot-select"
              aria-label="Mod"
              placeholder="Pick a mod…"
              value={mod}
              onChange={(key) => url.setLoot({ open: true, mod: key })}
              options={(lootCatalog?.owners || []).map((o) => ({
                value: o.key,
                label: o.label,
                description: plural(o.count, 'item'),
              }))}
            />
            {mod && lootLoading && !data && <PanelNote>Matching…</PanelNote>}
            {mod && data?.selection?.kind === 'mod' && <ModResult data={data} onPick={pickItem} onFly={flyToKind} active={flown?.name} />}
          </>
        )}

        {view === 'building' && (
          <>
            {!picked && (
              <>
                <PanelNote>
                  Click a building on the map, or pick a kind below, to see its loot containers and every item that
                  can spawn in it.
                </PanelNote>
                <AllBuildingKinds kinds={lootCatalog?.buildings || []} onOpen={openKind} />
              </>
            )}
            {picked && !picked.loading && (
              <button type="button" className="btn btn-ghost btn-small me-loot-back" onClick={() => setPicked(null)}>
                ← All building kinds
              </button>
            )}
            {picked?.loading && <PanelNote>Looking…</PanelNote>}
            {picked?.missed && (
              <PanelNote>No building there. Zoom in and click on a building.</PanelNote>
            )}
            {picked?.error && <div className="banner banner-error me-loot-banner">{picked.error}</div>}
            {picked?.building && <BuildingResult key={picked.building.lat + ',' + picked.building.lng} building={picked.building} item={item} onPick={pickItem} />}
          </>
        )}

        <PanelNote>{NOT_SHOWN}</PanelNote>
      </div>
    </aside>
  )
}
