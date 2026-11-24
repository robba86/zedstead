// The Analysis page: the economy report, the spawn collision report and the
// other read-only checks over the mission files.

import { Switch } from '../components/FlagSwitch.jsx'
import Select from '../components/Select.jsx'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import PageIntro from '../components/PageIntro.jsx'
import { useToast } from '../components/Toast.jsx'
import PerformanceSparklines, { useLastHourMetrics } from '../components/PerformanceSparklines.jsx'
import BarChart from '../components/BarChart.jsx'
import FilterBox from '../components/FilterBox.jsx'
import { fuzzyFilter } from '../fuzzy.js'
import { tooltipAttrs } from '../components/Tooltip.jsx'
import { configLink } from '../crossLinks.js'
import { humanizeKey } from '../fieldLabel.js'
import LogFindings, { LogDownloadLink, LogRunFacts, LogSeveritySummary } from '../components/LogFindings.jsx'
import ChangeTimeline from '../components/ChangeTimeline.jsx'
import Pager from '../components/Pager.jsx'
import { formatScore } from '../format.js'
import { useUrlTabs } from '../urlTabs.js'
import { useCan } from '../role.jsx'
import { ANALYSIS_ECONOMY_SUBTABS, ANALYSIS_TABS as TABS, ANALYSIS_TAB_RENAMES } from '../pageTabs.js'
import { COLLISION_KIND_LABELS } from './mapeditor/collisionModes.js'
import { ItemName } from '../components/ItemIcon.jsx'
import PanelNote from '../components/PanelNote.jsx'
import Tabs from '../components/Tabs.jsx'
import CountBadge from '../components/CountBadge.jsx'
import ListRow from '../components/ListRow.jsx'
import LoadingState from '../components/LoadingState.jsx'

const SEVERITIES = [
  ['error', 'Errors'],
  ['warning', 'Warnings'],
  ['info', 'Info'],
]

// A chart whose bars filter a list is only worth drawing with two bars or
// more: with one, every item already sits in it.
const canPick = (rows) => rows.length > 1

// The Economy report as four bar charts: where the score is low (by category
// and by rule), how the items spread over the rarity tiers, and how serious
// the issues are. Worst category first, the tiers in the analyzer's order.
// Each bar filters the item list under the charts: `pick` holds one key per
// chart, `onPick(chart, key)` sets or clears one (ITEM_FILTERS).
function EconomyCharts({ report, pick, onPick }) {
  const categories = Object.entries(report.category_scores)
    .sort((a, b) => a[1] - b[1])
    .map(([cat, v]) => {
      const n = report.category_counts[cat] || 0
      return {
        key: cat,
        label: humanizeKey(cat),
        value: v,
        text: `${formatScore(v)} · ${n}`,
        tip: `${humanizeKey(cat)}: score ${formatScore(v)} over ${n} item${n === 1 ? '' : 's'}`,
      }
    })
  const rules = Object.entries(report.rule_scores).map(([rule, v]) => ({
    key: rule,
    label: humanizeKey(rule),
    value: v,
    text: formatScore(v),
    tip: report.rule_descriptions?.[rule],
  }))
  const tiers = Object.entries(report.rarity_counts || {}).map(([tier, n]) => {
    const full = report.rarity_labels?.[tier] || humanizeKey(tier)
    return { key: tier, label: full.split(' (')[0], value: n, tip: full }
  })
  const severities = SEVERITIES.map(([key, label]) => ({ key, label, value: report.issues_by_severity?.[key] || 0 }))
  return (
    <div className="bar-chart-grid">
      {canPick(categories) && (
        <BarChart
          title="Score by category (worst first) · items"
          rows={categories}
          max={1}
          active={pick.category}
          onPick={(k) => onPick('category', k)}
        />
      )}
      <BarChart title="Score by rule" rows={rules} max={1} active={pick.rule} onPick={(k) => onPick('rule', k)} />
      {canPick(tiers) && (
        <BarChart
          title={report.vanilla ? 'Items by rarity (all items; the list holds only those vanilla lacks)' : 'Items by rarity (all items)'}
          rows={tiers}
          active={pick.tier}
          onPick={(k) => onPick('tier', k)}
        />
      )}
      <BarChart title="Issues by severity (issues, not items)" rows={severities} active={pick.severity} onPick={(k) => onPick('severity', k)} />
    </div>
  )
}

// What a picked bar keeps in the item list, and its name above the list.
const ITEM_FILTERS = {
  category: { test: (item, k) => item.category === k, name: (k) => humanizeKey(k) },
  rule: { test: (item, k) => (item.rule_scores?.[k] ?? 1) < 1, name: (k) => `Fails ${humanizeKey(k)}` },
  tier: {
    test: (item, k) => item.rarity_tier === k,
    name: (k, report) => (report.rarity_labels?.[k] || humanizeKey(k)).split(' (')[0],
  },
  severity: {
    test: (item, k) => item.issues.some((i) => i.severity === k),
    name: (k) => SEVERITIES.find(([key]) => key === k)?.[1] || k,
  },
}

// The picked bars above a list: one chip each, a click drops it, and Clear
// drops them all. Nothing shows while no bar is picked.
// `noun` says what the list counts, which can differ from a chart's own count
// (Items by rarity counts every item, the list only those vanilla lacks).
function ChartFilters({ chips, shown, total, noun = 'items', onClear }) {
  if (!chips.length) return null
  return (
    <div className="chart-filters" role="status">
      <span>
        {shown} of {total} {noun}
:
      </span>
      {chips.map((c) => (
        <button key={c.key} type="button" className="btn btn-ghost btn-small" onClick={c.onClear} {...tooltipAttrs('Remove this filter.')}>
          {c.label} ×
        </button>
      ))}
      {chips.length > 1 && (
        <button type="button" className="btn btn-ghost btn-small" onClick={onClear}>
          Clear filters
        </button>
      )}
    </div>
  )
}

// An item's classname as a link to the Types page, on the file that holds it
// with the item in the search box, so a change is one click away. A removed
// item has no file and stays plain text. An item in custom/ is a mod's copy,
// which every merge rebuilds (app/xml/CLAUDE.md), so its row says so before
// the click and points to Custom config, where a manual file keeps a change.
const inCustom = (file) => /[\\/]custom[\\/][^\\/]+$/.test(file)

function ItemLink({ item }) {
  if (!item.file) return <ItemName name={item.classname} category={item.category} />
  const name = item.file.split(/[\\/]/).pop()
  const custom = inCustom(item.file)
  return (
    <>
      <ItemName name={item.classname} category={item.category}>
      <Link
        to={configLink('types', { q: item.classname, file: item.file })}
        {...tooltipAttrs(
          custom
            ? `Open ${item.classname} in the Types editor (${name}, a mod's copy). A change there lasts only until the next mod XML merge.`
            : `Open ${item.classname} in the Types editor (${name}).`
        )}
      >
        {item.classname}
      </Link>
      </ItemName>
      {custom && (
        <>
          {' '}
          <Link
            className="item-mod-copy"
            to={configLink('mod-xml')}
            {...tooltipAttrs(
              `${item.classname} comes from a mod's copy in custom/, which every mod XML merge rebuilds, so an edit ` +
                'there is lost. To keep a change, add a manual XML file on Custom config.'
            )}
          >
            mod copy
          </Link>
        </>
      )}
    </>
  )
}

// A types.xml value as text: a list joined, a flags object as name=value.
function typeValue(v) {
  if (Array.isArray(v)) return v.join(', ') || '—'
  if (v && typeof v === 'object') return Object.entries(v).map(([k, n]) => `${k}=${n}`).join(' ') || '—'
  return v === '' || v == null ? '—' : String(v)
}

// A long list a page at a time: the rows of the current page and the Pager
// that goes above and under them (null for one page). A new `resetKey` (another filter) starts
// again at page 1; the list itself is rebuilt on every render, so it cannot
// be the key.
const LIST_PAGE = 100

function usePagedList(items, resetKey) {
  const [page, setPage] = useState(1)
  useEffect(() => setPage(1), [resetKey])
  const totalPages = Math.max(1, Math.ceil(items.length / LIST_PAGE))
  const current = Math.min(page, totalPages)
  return {
    rows: items.slice((current - 1) * LIST_PAGE, current * LIST_PAGE),
    pager:
      totalPages > 1 ? (
        <Pager
          page={current}
          totalPages={totalPages}
          onPrev={() => setPage(current - 1)}
          onNext={() => setPage(current + 1)}
          extra={`${items.length} items`}
        />
      ) : null,
  }
}

// The scored items (Economy, under the charts), a page at a time.
function ItemScoresTable({ items, filterKey }) {
  const { rows, pager } = usePagedList(items, filterKey)
  return (
    <>
      {pager}
      <div className="data-table-wrap table-cards">
        <table className="data-table">
          <thead>
            <tr>
              <th>Classname</th>
              <th>Category</th>
              <th>Rarity</th>
              <th>Score</th>
              <th>Issues</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((item) => (
              <tr key={item.classname}>
                <td className="cell-name" data-label="Classname">
                  <ItemLink item={item} />
                </td>
                <td data-label="Category">{item.category}</td>
                <td data-label="Rarity">{item.rarity_tier}</td>
                <td data-label="Score">{formatScore(item.score)}</td>
                <td data-label="Issues">{item.issues.map((i) => i.message).join('; ')}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {pager}
    </>
  )
}

// Vanilla as the baseline: how many stock items keep their stock values, where
// the changes are, and the items that moved the most. Only shown when Zedstead
// holds the stock types.xml (economy_analyzer.compare_to_vanilla).
function VanillaSection({ v }) {
  const [cat, setCat] = useState(null)
  const [query, setQuery] = useState('')
  const byCat = cat ? v.changed.filter((item) => item.category === cat) : v.changed
  // Free text over the classname, the category and what changed.
  const changed = fuzzyFilter(byCat, query, (item) => [
    item.classname,
    humanizeKey(item.category),
    ...item.changes.map((c) => c.field),
  ])
  const { rows: shownChanged, pager } = usePagedList(changed, `${cat}|${query}`)
  const categories = Object.entries(v.changed_by_category)
    .filter(([, [changed]]) => changed > 0)
    .sort((a, b) => b[1][0] / b[1][1] - a[1][0] / a[1][1])
    .map(([cat, [changed, total]]) => ({
      key: cat,
      label: humanizeKey(cat),
      value: changed / total,
      text: `${changed} of ${total}`,
      tip: `${humanizeKey(cat)}: ${changed} of ${total} vanilla items changed`,
    }))
  return (
    <>
      <PanelNote>
        {v.unchanged} of {v.vanilla_items} vanilla items keep their stock values. {v.changed_count} changed
        {v.turned_off || v.removed ? ` (${v.turned_off} turned off, ${v.removed} removed)` : ''}. Vanilla itself
        scores 100%.
      </PanelNote>
      {canPick(categories) && (
        <div className="bar-chart-grid">
          <BarChart title="Changed from vanilla, by category" rows={categories} max={1} active={cat} onPick={setCat} />
        </div>
      )}
      <ChartFilters
        chips={cat ? [{ key: cat, label: humanizeKey(cat), onClear: () => setCat(null) }] : []}
        shown={changed.length}
        total={v.changed.length}
        noun="changed vanilla items"
        onClear={() => setCat(null)}
      />
      {v.changed.length > 0 && (
        <FilterBox
          value={query}
          onChange={setQuery}
          placeholder="Filter by classname, category or field…"
          shown={changed.length}
          total={byCat.length}
          label="changed items"
        />
      )}
      {query && changed.length === 0 && <PanelNote>No items match.</PanelNote>}
      {changed.length > 0 && pager}
      {changed.length > 0 && (
        <div className="data-table-wrap table-cards">
          <table className="data-table">
            <thead>
              <tr>
                <th>Classname</th>
                <th>Category</th>
                <th>Changed from vanilla (stock → now)</th>
              </tr>
            </thead>
            <tbody>
              {shownChanged.map((item) => (
                <tr key={item.classname}>
                  <td className="cell-name" data-label="Classname">
                    <ItemLink item={item} />
                  </td>
                  <td data-label="Category">{humanizeKey(item.category)}</td>
                  <td data-label="Changed">
                    {item.removed
                      ? 'Removed from the live files'
                      : [
                          item.turned_off && 'Turned off',
                          ...item.changes.map((c) => `${c.field} ${typeValue(c.stock)} → ${typeValue(c.now)}`),
                        ]
                          .filter(Boolean)
                          .join('; ')}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {pager}
    </>
  )
}

// The Economy tab. With the stock types.xml it holds two views, one per
// subtab (ANALYSIS_ECONOMY_SUBTABS): the vanilla items against their stock
// values, and the mod and custom items checked by rules of thumb. Without
// it there is one view, the rules of thumb over every item, and no subtab bar.
function EconomyTab({ pushToast, subtab, setSubtab }) {
  const [report, setReport] = useState(null)
  const [pick, setPick] = useState({})
  const [query, setQuery] = useState('')

  useEffect(() => {
    api.get('/api/analysis/economy').then(setReport).catch((err) => pushToast(err.message || 'Failed to load economy analysis', 'error'))
  }, [pushToast])

  if (!report) return <LoadingState label="Loading the economy analysis…" />
  const v = report.vanilla
  const onPick = (chart, key) => setPick((p) => ({ ...p, [chart]: key }))
  const picked = Object.entries(pick).filter(([, k]) => k != null)
  const byChart = report.item_scores.filter((item) => picked.every(([chart, k]) => ITEM_FILTERS[chart].test(item, k)))
  // Free text over the classname, category, rarity and the issues' words.
  const items = fuzzyFilter(byChart, query, (item) => [
    item.classname,
    item.category,
    item.rarity_tier,
    ...item.issues.map((i) => i.message),
  ])
  const chips = picked.map(([chart, k]) => ({
    key: chart,
    label: ITEM_FILTERS[chart].name(k, report),
    onClear: () => onPick(chart, null),
  }))

  const showVanilla = v && subtab === ANALYSIS_ECONOMY_SUBTABS[0]

  return (
    <>
      {v && (
        <Tabs tabs={ANALYSIS_ECONOMY_SUBTABS} active={subtab} onSelect={setSubtab} sub />
      )}
      {showVanilla && (
        <div className="card">
          <h2>Like vanilla: {formatScore(v.closeness)}</h2>
          <VanillaSection v={v} />
        </div>
      )}
      {!v && (
        <div className="card">
          <h2>Economy score: {formatScore(report.overall_score)}</h2>
          <PanelNote>
            Zedstead has no stock copy of this mission&apos;s types.xml yet: the untouched file as Bohemia ships it.
            Until it has one, every item is checked by rules of thumb. Zedstead saves the stock copy each time it
            downloads the server files, so run the Update server step on the{' '}
            <Link to="/update">Update page</Link>, and vanilla then becomes the baseline. A mission that does not
            ship with the DayZ server, such as a community map, never has a stock copy.
          </PanelNote>
        </div>
      )}
      {!showVanilla && (
        <div className="card">
          {v && (
            <>
              <h2>Items vanilla does not have: {formatScore(report.overall_score)}</h2>
              <PanelNote>
                Mod and custom items have no stock values, so rules of thumb check them: the min/nominal ratio,
                lifetime, restock and count_in_map.
              </PanelNote>
            </>
          )}
          <PanelNote>
            {report.items_analyzed} items checked, {report.items_with_issues} with issues, {report.disabled_count}{' '}
            disabled.
          </PanelNote>
          {report.items_analyzed > 0 && <EconomyCharts report={report} pick={pick} onPick={onPick} />}
          {report.recommendations.length > 0 && (
            <ul className="doctor-list">
              {report.recommendations.map((r, i) => (
                <ListRow as="li" key={i} wrap name={r} />
              ))}
            </ul>
          )}
          {report.item_scores.length > 0 && (
            <FilterBox
              value={query}
              onChange={setQuery}
              placeholder="Filter by classname, category, rarity or issue…"
              shown={items.length}
              total={byChart.length}
            />
          )}
          <ChartFilters
            chips={chips}
            shown={items.length}
            total={report.item_scores.length}
            noun={v ? 'items vanilla does not have' : 'items'}
            onClear={() => setPick({})}
          />
          {(picked.length > 0 || query) && items.length === 0 && <PanelNote>No items match.</PanelNote>}
          {items.length > 0 && <ItemScoresTable items={items} filterKey={`${JSON.stringify(picked)}|${query}`} />}
        </div>
      )}
    </>
  )
}

// Not in ANALYSIS_TABS for now (pageTabs.js): kept for the reworked tab.
function CollisionsTab({ pushToast }) {
  const [report, setReport] = useState(null)
  const [kind, setKind] = useState('all')
  const [eventTypes, setEventTypes] = useState([])
  const [typeFilter, setTypeFilter] = useState('')
  const [checkRadius, setCheckRadius] = useState(false)
  const [checkOverlap, setCheckOverlap] = useState(false)

  const runScan = useCallback(
    (params) => {
      setReport(null)
      const search = new URLSearchParams(params)
      api
        .get(`/api/analysis/collisions?${search}`)
        .then(setReport)
        .catch((err) => pushToast(err.message || 'Failed to load collisions', 'error'))
    },
    [pushToast]
  )

  useEffect(() => {
    runScan({ kind: 'all' })
    api.get('/api/analysis/collision-event-types').then((d) => setEventTypes(d.types)).catch(() => {})
  }, [runScan])

  function handleScan() {
    runScan({
      kind,
      types: kind === 'eventspawns' ? typeFilter : '',
      check_radius: kind === 'eventspawns' && checkRadius,
      check_overlap: kind === 'eventspawns' && checkOverlap,
    })
  }

  // In-app route, not the old iframe URL from /api/spawn-editor: the editor is
  // a React page now and reads these params itself (webui/src/pages/mapeditor/
  // state/urlState.js). map_editor_query is built by
  // app/ops/collision_ops.py::build_collision_query — the two ends must agree
  // on the key names, so pass it through verbatim.
  const editorUrl = report?.map_editor_query
    ? `/map-editor?${report.map_editor_query}`
    : null

  return (
    <div className="card">
      <div className="inline-form">
        <Select
          aria-label="Scan"
          value={kind}
          onChange={setKind}
          options={[
            { value: 'all', label: 'All scans' },
            { value: 'eventspawns', label: 'Eventspawn collisions' },
            { value: 'playerspawns', label: 'Player spawn collisions' },
            { value: 'territories', label: 'Territory collisions' },
          ]}
        />
        {kind === 'eventspawns' && (
          <>
            <Select
              multi
              creatable
              aria-label="Event types"
              placeholder="Every event type"
              value={typeFilter.split(',').map((t) => t.trim()).filter(Boolean)}
              onChange={(types) => setTypeFilter(types.join(','))}
              options={eventTypes}
            />
            <div className="dry-run-toggle">
              <Switch checked={checkRadius} onChange={setCheckRadius}>
                Radius conflicts
              </Switch>
            </div>
            <div className="dry-run-toggle">
              <Switch checked={checkOverlap} onChange={setCheckOverlap}>
                Cross-event overlaps
              </Switch>
            </div>
          </>
        )}
        <button className="btn btn-primary btn-small" onClick={handleScan}>
          Scan
        </button>
        {editorUrl && (
          <Link className="btn btn-ghost btn-small" to={editorUrl}>
            Open in Map Editor
          </Link>
        )}
      </div>
      {!report ? (
        <PanelNote>Scanning…</PanelNote>
      ) : (
        <CollisionResults report={report} />
      )}
    </div>
  )
}

// A whole-mission scan can report thousands of collisions (7,064 on a stock
// Chernarus with two mods, nearly all player spawn cross-sections), so the
// table is paged like TableEditor's and can be narrowed to one kind. The
// heading keeps the full count.
const COLLISIONS_PAGE_SIZE = 50
const ALL_KINDS = ''
const kindLabel = (kind) => COLLISION_KIND_LABELS[kind] || kind

function CollisionResults({ report }) {
  const [kindFilter, setKindFilter] = useState(ALL_KINDS)
  const [page, setPage] = useState(1)

  const kindCounts = useMemo(() => {
    const counts = new Map()
    for (const c of report.collisions) counts.set(c.kind, (counts.get(c.kind) || 0) + 1)
    return [...counts].sort((a, b) => kindLabel(a[0]).localeCompare(kindLabel(b[0])))
  }, [report])

  // No reset on a new scan is needed: runScan clears the report first, so this
  // component unmounts and the next scan starts on All, page 1.
  function pickKind(kind) {
    setKindFilter(kind)
    setPage(1)
  }

  const shown =
    kindFilter === ALL_KINDS
      ? report.collisions
      : report.collisions.filter((c) => c.kind === kindFilter)
  const totalPages = Math.max(1, Math.ceil(shown.length / COLLISIONS_PAGE_SIZE))
  const currentPage = Math.min(page, totalPages)
  const firstRow = (currentPage - 1) * COLLISIONS_PAGE_SIZE
  const pageRows = shown.slice(firstRow, firstRow + COLLISIONS_PAGE_SIZE)

  return (
    <>
      <h2>
        Collisions <CountBadge value={report.collisions.length} />
      </h2>
      {/* Without this, "No collisions found" would also be the answer for a
          file the scan could not read. */}
      {report.skipped?.length > 0 && (
        <div className="banner banner-warning">Not scanned, because it could not be read: {report.skipped.join('; ')}</div>
      )}
      {report.collisions.length === 0 ? (
        <PanelNote>No collisions found.</PanelNote>
      ) : (
        <>
          {kindCounts.length > 1 && (
            <div className="table-filter">
              <label>
                Kind
                <Select
                  value={kindFilter}
                  onChange={pickKind}
                  options={[
                    { value: ALL_KINDS, label: `All (${report.collisions.length})` },
                    ...kindCounts.map(([k, n]) => ({ value: k, label: `${kindLabel(k)} (${n})` })),
                  ]}
                />
              </label>
            </div>
          )}
          <div className="data-table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Kind</th>
                  <th>Severity</th>
                  <th>Event A</th>
                  <th>Event B</th>
                  <th>Distance</th>
                  <th>Description</th>
                </tr>
              </thead>
              <tbody>
                {pageRows.map((c, i) => (
                  <tr key={firstRow + i}>
                    <td>{kindLabel(c.kind)}</td>
                    <td>{c.severity}</td>
                    <td>{c.event_a}</td>
                    <td>{c.event_b}</td>
                    <td>{c.distance.toFixed(1)}</td>
                    <td>{c.description}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {totalPages > 1 && (
            <Pager
              page={currentPage}
              totalPages={totalPages}
              onPrev={() => setPage(currentPage - 1)}
              onNext={() => setPage(currentPage + 1)}
              extra={kindFilter === ALL_KINDS ? null : `${shown.length} of this kind`}
            />
          )}
        </>
      )}
    </>
  )
}

// The full .RPT report. The Doctor page's Log Analyzer card shows the top few
// findings of the newest log; this is where all of them live, plus the file
// picker for older runs. Both render components/LogFindings.jsx over the dict
// app/ops/log_analysis.py builds.
function LogsTab({ pushToast }) {
  const [index, setIndex] = useState(null)
  const [selected, setSelected] = useState('')
  const [report, setReport] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api
      .get('/api/analysis/logs')
      .then(setIndex)
      .catch((err) => pushToast(err.message || 'Failed to list server logs', 'error'))
  }, [pushToast])

  useEffect(() => {
    setLoading(true)
    // No `file` param means "the newest one, with the whole-install checks" —
    // service -mod= validation and the config cross-checks only make sense
    // against the run that is current.
    // A big .RPT takes seconds: the report of a log picked before this one
    // must not land over it.
    let cancelled = false
    api
      .get(`/api/analysis/logs/report${selected ? `?file=${encodeURIComponent(selected)}` : ''}`)
      .then((r) => !cancelled && setReport(r))
      .catch((err) => !cancelled && pushToast(err.message || 'Failed to analyse the log', 'error'))
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
  }, [selected, pushToast])

  const logs = index?.logs || []

  return (
    <>
      <div className="card">
        <div className="log-picker">
          <label>
            Log file{' '}
            <Select
              value={selected}
              onChange={setSelected}
              options={[{ value: '', label: `Newest (${logs[0]?.name || 'none'})` }, ...logs.slice(1).map((l) => l.name)]}
            />
          </label>
          {index?.dir && <span className="doctor-detail">{index.dir}</span>}
        </div>

        {loading ? (
          <PanelNote>Reading the log…</PanelNote>
        ) : !report?.log ? (
          <PanelNote>
            {index?.profiles_found === false
              ? 'Profiles directory not found — check the server directory in Settings.'
              : 'No .RPT files yet. Start the server once and they will appear here.'}
          </PanelNote>
        ) : (
          <>
            <h2>
              {report.log.name} <LogDownloadLink name={report.log.name} />
            </h2>
            <LogRunFacts run={report.run} stats={report.stats} log={report.log} />
            <LogSeveritySummary summary={report.summary} noise={report.noise} />
          </>
        )}
      </div>

      {report?.findings?.length > 0 && (
        <div className="card">
          <h2>Findings</h2>
          <LogFindings findings={report.findings} />
        </div>
      )}

      {report?.log && report.findings?.length === 0 && (
        <div className="card">
          <PanelNote>
            Nothing worth reporting in this log — every line in it is either a fact or
            engine chatter every DayZ server prints.
          </PanelNote>
        </div>
      )}

      {index?.crash_dumps?.length > 0 && (
        <div className="card">
          <h2>Crash dumps</h2>
          <div className="data-table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>File</th>
                  <th>Written</th>
                </tr>
              </thead>
              <tbody>
                {index.crash_dumps.map((d) => (
                  <tr key={d.name}>
                    <td>{d.name}</td>
                    <td className="doctor-detail">{d.modified}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </>
  )
}

function PerformanceTab({ pushToast }) {
  const isAdmin = useCan('admin')
  const { summary, recent } = useLastHourMetrics()
  const [peaks, setPeaks] = useState(null)

  const [hourly, setHourly] = useState([])
  const [a2s, setA2s] = useState(null)

  useEffect(() => {
    api.get('/api/metrics/peaks?hours=720').then(setPeaks).catch(() => {})
    api
      .get('/api/metrics/hourly?hours=168')
      .then((data) => setHourly(data.hourly || []))
      .catch(() => {})
    api.get('/api/a2s').then(setA2s).catch(() => {})
  }, [pushToast])

  return (
    <div className="card">
      <h2>Performance (last hour)</h2>
      <PerformanceSparklines summary={summary} recent={recent} />
      {peaks && Object.keys(peaks).length > 0 && (
        <PanelNote>
          30-day peaks — players: {peaks.players_peak ?? '—'}, min FPS: {peaks.fps_min ?? '—'}, memory:{' '}
          {peaks.memory_peak_mb ?? '—'} MB, CPU: {peaks.cpu_peak_pct ?? '—'}%
        </PanelNote>
      )}
      {a2s && (
        <PanelNote>
          {a2s.online
            ? `A2S: ${a2s.server_name} — map ${a2s.map_name}, ${a2s.player_count}/${a2s.max_players} players, ` +
              `ping ${a2s.ping_ms?.toFixed?.(0) ?? a2s.ping_ms} ms${a2s.vac_enabled ? ', VAC' : ''}`
            : 'A2S: server not responding to Steam queries.'}
        </PanelNote>
      )}
      {hourly.length > 0 && (
        <>
          <h2>7-day hourly averages</h2>
          <div className="data-table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Hour</th>
                  <th>FPS avg</th>
                  <th>Memory MB avg</th>
                  <th>Players avg</th>
                  <th>Players peak</th>
                  <th>CPU % avg</th>
                </tr>
              </thead>
              <tbody>
                {hourly.slice(-48).map((h) => (
                  <tr key={h.hour}>
                    <td>{new Date(h.hour * 1000).toLocaleString()}</td>
                    <td>{h.fps_avg ?? '—'}</td>
                    <td>{h.memory_avg ?? '—'}</td>
                    <td>{h.players_avg ?? '—'}</td>
                    <td>{h.players_peak ?? '—'}</td>
                    <td>{h.cpu_avg ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      {isAdmin && (
      <div className="inline-form">
        <button
          className="btn btn-ghost btn-small"
          onClick={async () => {
            try {
              const data = await api.post('/api/metrics/cleanup', { retention_days: 30 })
              pushToast(`Removed ${data.removed} metric row(s) older than ${data.retention_days} days.`, 'info')
            } catch (err) {
              pushToast(err.message || 'Metrics cleanup failed', 'error')
            }
          }}
        >
          Clean up metrics older than 30 days
        </button>
      </div>
      )}
    </div>
  )
}

// One line above a tab whose name alone does not say whose data it shows:
// the DayZ server's own files, or what Zedstead tracks and keeps of them.
const TAB_INTROS = {
  'Server logs': "The DayZ server's own log files (.RPT), as the server wrote them. Zedstead only reads them.",
}

// Only Economy has a nested bar.
const subtabsFor = (tab) => (tab === 'Economy' ? ANALYSIS_ECONOMY_SUBTABS : null)

export default function AnalysisPage() {
  const { tab, setTab, subtab, setSubtab } = useUrlTabs(TABS, subtabsFor, { renamed: ANALYSIS_TAB_RENAMES })
  const pushToast = useToast()

  return (
    <div className="page analysis-page">
      <div className="page-header">
        <h1>Analysis</h1>
      </div>

      <PageIntro page="/analysis" />

      <Tabs tabs={TABS} active={tab} onSelect={setTab} />

      {TAB_INTROS[tab] && <PanelNote>{TAB_INTROS[tab]}</PanelNote>}
      {tab === 'Economy' && <EconomyTab pushToast={pushToast} subtab={subtab} setSubtab={setSubtab} />}
      {tab === 'Server logs' && <LogsTab pushToast={pushToast} />}
      {tab === 'Change history' && <ChangeTimeline pushToast={pushToast} />}
      {tab === 'Performance' && <PerformanceTab pushToast={pushToast} />}
      {tab === 'Collisions' && <CollisionsTab pushToast={pushToast} />}
    </div>
  )
}
