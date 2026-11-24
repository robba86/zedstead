// The log analyzer's findings as a list (app/ops/log_analysis.py decides
// them): title, repeat count, origin badge, detail and fix advice.

import CountBadge from './CountBadge.jsx'
import { tooltipAttrs } from './Tooltip.jsx'
import { Link } from 'react-router-dom'
import FixCell from './FixCell.jsx'
import { withBase } from '../instance.js'
import { useCan } from '../role.jsx'
import FoldCaret from './FoldCaret.jsx'
import { HiOutlineArrowDownTray } from 'react-icons/hi2'
import IconButton from './IconButton.jsx'

// Renders the .RPT analysis that `app/ops/log_analysis.py` produces, for both
// surfaces that show it: the Doctor page's Log Analyzer card (the verdict and
// the counts only) and the Analysis page's Logs tab (everything). One component, so a field
// added on the backend appears on both — the same arrangement FixCell has with
// the two conflict tables, and FixCell itself is reused here: a finding already
// carries the `{why, how, links}` shape it wants.
//
// Nothing here classifies anything. Severity, wording, counts and links are all
// decided server-side; if a finding reads wrong, fix the rule, not this file.

// A link that downloads one whole .RPT (`GET /api/analysis/logs/download`), so
// the admin can read every line the analysis and the live tail summarise. No
// `name` means the newest log. Moderators and admins only: the log holds
// player names and Steam IDs (app/web/permissions.py). `iconOnly` shows a
// download glyph in place of the text (the Activity drawer's Server Log pane).
export function LogDownloadLink({ name = '', className = 'btn btn-ghost btn-small', iconOnly = false }) {
  const canModerate = useCan('moderator')
  if (!canModerate) return null
  const query = name ? `?file=${encodeURIComponent(name)}` : ''
  const href = withBase(`/api/analysis/logs/download${query}`)
  const tip = 'Download the whole server log (.RPT) as it is on disk'
  if (iconOnly) {
    return <IconButton icon={HiOutlineArrowDownTray} label="Download log" tip={tip} href={href} download={name || true} />
  }
  return (
    <a className={className} href={href} download={name || true} {...tooltipAttrs(tip)}>
      Download log
    </a>
  )
}

const SEVERITY_LABEL = { error: 'error', warning: 'warning', notice: 'notice' }

// `summary` counts only findings the admin owns. The stock-DayZ ones and a
// mod's own quirks get their own muted pills instead of inflating the headline
// — see VANILLA_BASELINE and MOD_BASELINE in app/ops/log_analysis.py for why
// that distinction exists at all.
// The pill tone for each severity (webui/DESIGN.md "Badge").
const SEVERITY_PILL = { error: 'status-pill-failed', warning: 'status-pill-warning', notice: 'status-pill-success' }

export function LogSeveritySummary({ summary, noise }) {
  if (!summary) return null
  const parts = ['error', 'warning', 'notice'].filter((s) => summary[s] > 0)
  return (
    <p className="log-severity-summary">
      {parts.length === 0 ? (
        <span className="status-pill status-pill-success status-pill-plain">Nothing worth reporting</span>
      ) : (
        parts.map((s) => (
          <span key={s} className={`status-pill status-pill-plain ${SEVERITY_PILL[s]}`}>
            {summary[s]} {SEVERITY_LABEL[s]}
            {summary[s] === 1 ? '' : 's'}
          </span>
        ))
      )}
      {summary.vanilla > 0 && (
        <span
          className="status-pill status-pill-neutral status-pill-plain"
          {...tooltipAttrs("Things Bohemia's own mission files make every stock server print.")}
        >
          {summary.vanilla} stock DayZ quirk{summary.vanilla === 1 ? '' : 's'}
        </span>
      )}
      {summary.mod > 0 && (
        <span
          className="status-pill status-pill-neutral status-pill-plain"
          {...tooltipAttrs("Things a mod's own files make every server running it print.")}
        >
          {summary.mod} mod quirk{summary.mod === 1 ? '' : 's'}
        </span>
      )}
      {noise?.suppressed > 0 && (
        <span
          className="status-pill status-pill-neutral status-pill-plain"
          {...tooltipAttrs((noise.categories || []).map((c) => `${c.label}: ${c.count}`).join('\n'))}
        >
          {noise.suppressed.toLocaleString()} cosmetic engine lines hidden
        </span>
      )}
    </p>
  )
}

const OUTCOME_TONE = {
  clean_shutdown: 'ok',
  stopped: 'warning',
  running_or_cut_short: 'warning',
  startup_incomplete: 'error',
  unreadable: 'error',
}

function fmtBytes(n) {
  if (!n && n !== 0) return null
  const units = ['B', 'KB', 'MB', 'GB']
  let v = n
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i += 1
  }
  return `${i === 0 ? v : v.toFixed(1)} ${units[i]}`
}

// The run's one-line verdict ("Server shut down cleanly."), tinted by outcome.
// The Doctor card shows only this line; LogRunFacts puts the facts under it.
export function LogOutcome({ run }) {
  if (!run) return null
  return (
    <p className={`log-outcome sev-${OUTCOME_TONE[run.outcome] || 'warning'}`}>
      {run.outcome_detail || run.outcome}
    </p>
  )
}

// The facts the old analyzer threw away. Rows are omitted rather than shown
// empty — a bare server that never started has no mission to name.
export function LogRunFacts({ run, stats, log }) {
  if (!run) return null
  const rows = []
  if (run.version) rows.push(['Version', run.version])
  if (run.started_at) rows.push(['Started', run.started_at])
  if (run.mission)
    rows.push(['Mission', run.terrain ? `${run.mission} (${run.terrain})` : run.mission])
  const serverMods = run.server_mod_args || []
  rows.push([
    'Mods requested',
    run.mod_args?.length
      ? run.mod_args.join(', ')
      : serverMods.length
        ? 'none in -mod='
        : 'none — this run was vanilla',
  ])
  if (serverMods.length) rows.push(['Server-side mods', serverMods.join(', ')])
  if (run.mod_addons?.length) rows.push(['Mod addons loaded', run.mod_addons.join(', ')])
  if (run.port) rows.push(['Port', String(run.port)])
  if (run.ready_seconds != null) rows.push(['Joinable after', `${run.ready_seconds}s`])
  if (run.hive) rows.push(['Hive', run.hive])
  if (stats?.loot_in_map != null)
    rows.push([
      'Economy',
      `${stats.loot_in_map.toLocaleString()} items in map (nominal ${stats.loot_nominal?.toLocaleString()})`,
    ])
  if (stats?.dynamic_events != null) rows.push(['Dynamic events', String(stats.dynamic_events)])
  if (stats?.storage_load_seconds != null)
    rows.push(['Storage load', `${stats.storage_load_seconds}s`])
  if (log?.size_bytes != null)
    rows.push([
      'Log file',
      `${fmtBytes(log.size_bytes)}, ${log.lines?.toLocaleString()} lines${log.truncated ? ` (${log.skipped_lines?.toLocaleString()} lines in the middle not read; download the log to see them)` : ''}`,
    ])

  return (
    <>
      <LogOutcome run={run} />
      <dl className="log-facts">
        {rows.map(([label, value]) => (
          <div key={label} className="log-fact">
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
    </>
  )
}

function FindingList({ items, onAction, busyAction, showSamples }) {
  return (
    <ul className="doctor-list log-findings">
      {items.map((f) => {
        // A finding with no extracted subject falls back to its own first log
        // line as the detail — so when the raw lines are on screen too, that
        // would print the same sentence twice.
        const detail = showSamples && f.samples?.[0]?.text === f.detail ? null : f.detail
        const vanilla = f.origin === 'vanilla'
        const fromMod = f.origin === 'mod'
        return (
          <li
            key={`${f.id}-${f.origin || 'yours'}-${f.mod || ''}`}
            className={`log-finding sev-${f.severity}${vanilla || fromMod ? ' log-finding-baseline' : ''}`}
          >
            <div className="log-finding-body">
              <div className="log-finding-head">
                <span className="log-finding-title">{f.title}</span>
                {f.count > 1 && <CountBadge value={`×${f.count}`} />}
                {/* The badge rides on the finding, not on the group, so the
                    origin still reads when one is shown out of its section —
                    the Doctor card lists them flat. */}
                {vanilla && (
                  <span className="log-finding-origin" {...tooltipAttrs('Ships with vanilla DayZ')}>
                    vanilla
                  </span>
                )}
                {fromMod && (
                  <span
                    className="log-finding-origin log-finding-origin-mod"
                    {...tooltipAttrs('Ships with this mod')}
                  >
                    {f.mod || 'mod'}
                  </span>
                )}
              </div>
              {detail && <div className="log-finding-detail">{detail}</div>}
              <FixCell fix={f} onAction={onAction} busyAction={busyAction} />
              {showSamples && f.samples?.length > 0 && (
                <details className="log-finding-samples">
                  <summary>
                    <FoldCaret /> Log line{f.samples.length === 1 ? '' : 's'} (from line {f.first_line})
                  </summary>
                  <pre className="log-tail">{f.samples.map((s) => s.text).join('\n')}</pre>
                </details>
              )}
            </div>
          </li>
        )
      })}
    </ul>
  )
}

// One collapsed group of findings that are real and readable but not anything
// the admin did. On the compact Doctor card (`limit`) it stays one line so the
// card is not half trivia.
function BaselineGroup({ items, limit, lead, title, intro, ...listProps }) {
  if (items.length === 0) return null
  if (limit) {
    return (
      <p className="doctor-detail log-findings-more">
        {items.length} more {lead} —{' '}
        <Link className="doctor-link" to="/analysis?tab=Server+logs">
          open the full log report
        </Link>
      </p>
    )
  }
  return (
    <details className="log-baseline-group">
      <summary>
        <FoldCaret /> {title} <CountBadge value={items.length} />
      </summary>
      <p className="doctor-detail">{intro}</p>
      <FindingList items={items} {...listProps} />
    </details>
  )
}

export default function LogFindings({ findings, limit, onAction, busyAction, showSamples = true }) {
  const all = findings || []
  if (all.length === 0) return null
  // Backend order already puts the admin's findings first; splitting here is
  // what lets the stock ones collapse instead of padding the list.
  const mine = all.filter((f) => (f.origin || 'yours') === 'yours')
  const vanilla = all.filter((f) => f.origin === 'vanilla')
  const fromMods = all.filter((f) => f.origin === 'mod')
  const listProps = { onAction, busyAction, showSamples }
  const shown = limit ? mine.slice(0, limit) : mine
  const hidden = mine.length - shown.length

  return (
    <>
      {shown.length > 0 ? (
        <FindingList
          items={shown}
          onAction={onAction}
          busyAction={busyAction}
          showSamples={showSamples}
        />
      ) : (
        <p className="doctor-detail">Nothing in this run needs your attention.</p>
      )}
      {hidden > 0 && (
        <p className="doctor-detail log-findings-more">
          {hidden} more finding{hidden === 1 ? '' : 's'} —{' '}
          <Link className="doctor-link" to="/analysis?tab=Server+logs">
            open the full log report
          </Link>
        </p>
      )}

      <BaselineGroup
        items={vanilla}
        limit={limit}
        lead="come with vanilla DayZ"
        title="Comes with vanilla DayZ"
        intro="Every stock server prints these on every boot — they come out of Bohemia's own mission files, not anything you configured. Listed so they are explained rather than mysterious."
        {...listProps}
      />
      <BaselineGroup
        items={fromMods}
        limit={limit}
        lead="come with your mods"
        title="Comes with your mods"
        intro="Every server running these mods prints them on every boot — they come out of the mods' own files, not anything you configured. The badge names the mod."
        {...listProps}
      />
    </>
  )
}
