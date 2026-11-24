import { useCallback, useEffect, useRef, useState } from "react";
import DetailText from "../components/DetailText.jsx";
import { Link } from "react-router-dom";
import { HiOutlineArrowPath, HiOutlineHeart } from "react-icons/hi2";
import { api } from "../api";
import AutoRefreshIndicator from "../components/AutoRefreshIndicator.jsx";
import PageIntro from "../components/PageIntro.jsx";
import SteamLoginGate from "../components/SteamLoginGate.jsx";
import Tooltip from "../components/Tooltip.jsx";
import { useToast } from "../components/Toast.jsx";
import { useConfirm } from "../components/useConfirm.jsx";
import { useJobs } from "../jobs";
import ModNameText from "../components/ModNameText.jsx";
import FixCell from "../components/FixCell.jsx";
import DependencyEdges from "../components/DependencyEdges.jsx";
import { LogOutcome, LogSeveritySummary } from "../components/LogFindings.jsx";
import { INSTALL_SERVER_JOB, STEAM_ACTION_KINDS, copyBikeys, installBodyFor } from "../fixActions.js";
import { NeedsSteamIf } from "../components/NeedsSteam.jsx";
import { modLink } from "../crossLinks.js";
import useAutoRefresh from "../useAutoRefresh.js";
import { useHashTarget } from "../hashTarget.js";
import { useWs } from "../ws";
import { useCan } from "../role.jsx";
import FoldCaret from "../components/FoldCaret.jsx";
import CountBadge from "../components/CountBadge.jsx";
import ListRow, { SeverityDot } from '../components/ListRow.jsx'

const SEVERITY_ORDER = { error: 0, warning: 1, info: 2, ok: 3 };

const MODS_PAGE = "/mods?tab=Installed";

const AUTO_REFRESH_MS = 60000;
const AUTO_REFRESH_KEY = "zsm.doctor.autorefresh";

// The latest log's counts beside the health checks'. Without them the chips
// read "0 Errors" above a Log Analyzer card listing an error (test pass 1, F48).
function logCountsText(log) {
  const parts = [];
  if (log.error) parts.push(`${log.error} error${log.error === 1 ? "" : "s"}`);
  if (log.warning) parts.push(`${log.warning} warning${log.warning === 1 ? "" : "s"}`);
  return parts.join(" · ");
}

function SummaryBar({ summary, logSummary, conflicts }) {
  if (!summary) return null;
  const logText = logSummary ? logCountsText(logSummary) : "";
  const pills = [
    { label: "Errors", count: summary.error, cls: "status-pill-failed" },
    { label: "Warnings", count: summary.warning, cls: "status-pill-pending" },
    { label: "OK", count: summary.ok, cls: "status-pill-success" },
  ];
  return (
    <div className="doctor-summary">
      {pills.map((p) => (
        <span key={p.label} className={`status-pill ${p.cls}`}>
          {p.count} {p.label}
        </span>
      ))}
      {logText && (
        <a className="doctor-summary-sep" href="#log-analyzer">
          latest log: {logText}
        </a>
      )}
      {conflicts &&
        (conflicts.error_count > 0 || conflicts.warning_count > 0) && (
          <span className="doctor-summary-sep">
            conflicts: {conflicts.error_count} err / {conflicts.warning_count}{" "}
            warn
          </span>
        )}
    </div>
  );
}

// `check.actions` are fixes the page can run itself (see doctor_report._actions_for);
// `check.links` only route somewhere. Buttons come after the chips so the row still
// reads detail → where → do it.
function CheckRow({ check, onAction, busyAction }) {
  // Every fix a check offers is an admin write (install, update, regenerate,
  // reset) — below admin the finding and its links stay, the buttons go.
  const isAdmin = useCan("admin");
  return (
    <ListRow as="li" wrap lead={<SeverityDot severity={check.severity} />} name={check.label} sub={<><Tooltip keep text={check.tooltip}>
          <DetailText text={check.detail} phrases={check.link_phrases} />
        </Tooltip>
        {check.remediation && (
          <em className="doctor-fix"> — {check.remediation}</em>
        )}
        {(check.links || []).map((l) => (
          <Link key={l.to + l.label} className="link-chip" to={l.to}>
            {l.label}
          </Link>
        ))}
        {(isAdmin ? check.actions || [] : []).map((a) => (
          <NeedsSteamIf key={a.kind + a.label} when={STEAM_ACTION_KINDS.has(a.kind)}>
            <button
              className="btn btn-ghost btn-small doctor-action"
              disabled={!!busyAction}
              onClick={() => onAction(a)}
            >
              {busyAction === a.kind + a.label ? "Working…" : a.label}
            </button>
          </NeedsSteamIf>
        ))}</>} />
  );
}

function ChecksSection({ checks, onAction, busyAction }) {
  const sorted = [...checks].sort(
    (a, b) =>
      (SEVERITY_ORDER[a.severity] ?? 9) - (SEVERITY_ORDER[b.severity] ?? 9),
  );
  const failing = sorted.filter((c) => !c.ok);
  const passing = sorted.filter((c) => c.ok);
  return (
    <div className="card">
      <h2>Health Checks</h2>
      {failing.length === 0 ? (
        <p className="doctor-ok">All health checks passing.</p>
      ) : (
        <ul className="doctor-list">
          {failing.map((c) => (
            <CheckRow
              key={c.label}
              check={c}
              onAction={onAction}
              busyAction={busyAction}
            />
          ))}
        </ul>
      )}
      {passing.length > 0 && (
        <details className="doctor-passing">
          <summary>
          <FoldCaret /> Passing <CountBadge value={passing.length} />
        </summary>
          <ul className="doctor-list">
            {passing.map((c) => (
              <CheckRow
              key={c.label}
              check={c}
              onAction={onAction}
              busyAction={busyAction}
            />
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

function ConflictsSection({ conflicts, onAction, busyAction }) {
  if (!conflicts) return null;
  const { issues, dependencies } = conflicts;
  const hasIssues = issues && issues.length > 0;
  const hasDeps =
    dependencies && dependencies.edges && dependencies.edges.length > 0;
  if (!hasIssues && !hasDeps) return null;
  // Names the free-text cells may link — everything this report itself names.
  // The Doctor payload has no installed-mod list, and an @Token lifted out of an
  // .RPT line is not necessarily a mod at all.
  const knownModNames = new Set();
  for (const i of issues || []) {
    if (i.mod_name) knownModNames.add(i.mod_name);
    if (i.dep_name) knownModNames.add(i.dep_name);
  }
  for (const e of dependencies?.edges || []) {
    if (e.mod) knownModNames.add(e.mod);
    if (e.depends_on) knownModNames.add(e.depends_on);
  }
  return (
    <div className="card">
      <h2>Mod Conflicts</h2>
      {!hasIssues && <p className="doctor-ok">No mod conflicts found.</p>}
      {hasIssues && (
        <div className="data-table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Severity</th>
                <th>Category</th>
                <th>Mod</th>
                <th>Message</th>
                <th>Detail</th>
                <th>Fix</th>
              </tr>
            </thead>
            <tbody>
              {issues.map((i, idx) => (
                <tr key={idx} className={`sev-${i.severity}`}>
                  <td>{i.severity}</td>
                  <td>{i.category}</td>
                  <td>
                    {i.mod_name ? (
                      <Link className="mod-link" to={modLink(i.mod_name)}>
                        {i.mod_name}
                      </Link>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td>
                    <ModNameText text={i.message} known={knownModNames} />
                  </td>
                  <td className="doctor-detail">
                    {i.detail ? (
                      <ModNameText text={i.detail} known={knownModNames} />
                    ) : (
                      "—"
                    )}
                  </td>
                  <td>
                    <FixCell
                      fix={i.fix}
                      onAction={onAction}
                      busyAction={busyAction}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {/* The dependency graph the scan found — every edge satisfied, so it is
          reference and not a finding. Shared with the Mods page's Conflicts
          tab, which renders the same block off its own payload's keys. */}
      <DependencyEdges
        edges={dependencies?.edges}
        pbosScanned={dependencies?.pbos_scanned}
      />
    </div>
  );
}

// rows: [label, value] or [label, value, { to, tooltip }] — `to` makes the value
// a router link, `tooltip` hangs help off it.
function KvGrid({ rows }) {
  return (
    <dl className="kv-grid">
      {rows.map(([k, v, opts]) => (
        <div className="kv-row" key={k}>
          <dt>{k}</dt>
          <dd>
            <Tooltip keep text={opts?.tooltip}>
              {opts?.to ? (
                <Link className="doctor-link" to={opts.to}>
                  {v}
                </Link>
              ) : (
                v
              )}
            </Tooltip>
          </dd>
        </div>
      ))}
    </dl>
  );
}

function ServerInfoSection({ info }) {
  if (!info) return null;
  return (
    <div className="card">
      <h2>Server Info</h2>
      <KvGrid
        rows={[
          // Labelled "Local IP" until 2026 Q3, while showing the *public*
          // address on every VPS. The wire key stays `local_ip`; the backend
          // docstring (`_get_local_ip`) explains what it really is.
          [
            "Primary IP",
            info.local_ip,
            {
              tooltip:
                "The address on this box's default route — the public one on a " +
                "VPS, a private one behind NAT.",
            },
          ],
          ["Game port", info.game_port],
          ["Steam query port", info.steam_query_port],
          [
            "RCON port",
            info.rcon_configured
              ? info.rcon_port
              : `${info.rcon_port} (not configured)`,
          ],
        ]}
      />
    </div>
  );
}

function DiscUsageSection({ disc }) {
  if (!disc) return null;
  return (
    <div className="card">
      <h2>Disk Usage</h2>
      <KvGrid
        rows={[
          [
            "Apps config file",
            disc.config_file,
            { to: "/settings", tooltip: "Open Settings — the fields stored in this file." },
          ],
          ["Server dir", disc.server_dir],
          ["SteamCMD dir", disc.steamcmd_dir],
          ["Workshop dir", disc.workshop_dir],
          ["DayZ server", `${disc.server_gb} GB used`],
          ["Partition", `${disc.free_gb} GB free / ${disc.total_gb} GB total`],
          [
            "Mods installed",
            disc.mod_count > 0 ? (
              <>
                {disc.mod_count} mod(s) installed —{" "}
                <Link className="doctor-link" to={MODS_PAGE}>
                  go to Mods page
                </Link>
              </>
            ) : (
              <>
                No mods installed yet.{" "}
                <Link className="link-chip" to={MODS_PAGE}>
                  Manage mods
                </Link>
              </>
            ),
          ],
        ]}
      />
    </div>
  );
}

// The Log Analyzer card: a short summary of the newest .RPT (its verdict and
// the severity counts) and a link to the Analysis page's Server logs tab, which
// shows the facts, every finding and the download. app/ops/log_analysis.py
// decides everything shown; components/LogFindings.jsx renders it.
function LogSection({ log, onInstallMods, installing }) {
  const isAdmin = useCan("admin");
  if (!log) return null;
  return (
    <div className="card" id="log-analyzer">
      <h2>Log Analyzer</h2>
      {!log.profiles_found ? (
        <p className="doctor-detail">Profiles directory not found.</p>
      ) : !log.latest_log ? (
        <p className="doctor-detail">No .RPT files found in the profiles directory.</p>
      ) : (
        <>
          <p>
            Latest: <code>{log.latest_log}</code>
          </p>
          <LogOutcome run={log.run} />
          <LogSeveritySummary summary={log.summary} noise={log.noise} />
          <p>
            <Link className="card-link" to="/analysis?tab=Server+logs">
              Full report on the Analysis page →
            </Link>
          </p>
        </>
      )}

      {log.crash_dumps?.length > 0 && (
        <p className="doctor-detail">
          Crash dumps in the profiles folder: {log.crash_dumps.map((d) => d.name).join(", ")}
        </p>
      )}

      {log.mod_validation && log.mod_validation.length > 0 && (
        <>
          <h3 className="log-subhead">Installed launch command</h3>
          <ul className="doctor-list">
            {log.mod_validation.map((m, idx) => (
              <ListRow as="li" key={idx} wrap lead={<SeverityDot severity={m.level} />} name={<>{m.message}
                  {(m.links || []).map((l) => (
                    <Link key={l.to + l.label} className="link-chip" to={l.to}>
                      {l.label}
                    </Link>
                  ))}
                  {m.action === "install_mods" && isAdmin && (
                    <button
                      className="btn btn-ghost btn-small doctor-action"
                      disabled={installing}
                      onClick={onInstallMods}
                    >
                      {installing ? "Working…" : "Install mods now"}
                    </button>
                  )}</>} />
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

function ServiceFileSection({ service }) {
  if (!service) return null;
  const files =
    service.kind === "batch"
      ? service.files.filter((f) => f.installed)
      : service.installed
        ? [{ name: service.path, content: service.content }]
        : [];
  return (
    <div className="card">
      <h2>Service File</h2>
      {files.length === 0 ? (
        <p className="doctor-detail">Not installed.</p>
      ) : (
        files.map((f) => (
          <details key={f.name} className="doctor-service-file">
            <summary>
              <FoldCaret /> {f.name}
            </summary>
            <pre className="log-tail">{f.content}</pre>
          </details>
        ))
      )}
    </div>
  );
}

export default function DoctorPage() {
  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(true);
  const [installingMods, setInstallingMods] = useState(false);
  // Which check-row action is mid-flight, keyed `kind + label` — one at a time.
  const [busyAction, setBusyAction] = useState(null);
  const pushToast = useToast();
  const confirm = useConfirm();
  const { startJob, jobRunning } = useJobs();
  const isAdmin = useCan("admin");
  // The dashboard's Doctor tile links to #log-analyzer.
  useHashTarget(!!report);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setReport(await api.get("/api/doctor"));
    } catch {
      // ignore — a transient failure; the Re-run button retries
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Re-check on a timer, but never while this tab's job store shows a job
  // mutating the server: an install/uninstall deletes and re-adds files, so a
  // mid-job report is full of failures that aren't real.
  //
  // `report.busy` (a job started elsewhere — another browser, the CLI) only
  // banners: gating the timer on it too would deadlock, since the poll is the
  // only thing that would ever observe it clearing.
  const auto = useAutoRefresh(load, {
    interval: AUTO_REFRESH_MS,
    enabled: !jobRunning,
    storageKey: AUTO_REFRESH_KEY,
  });

  // ...and re-check once immediately after a job finishes, so the page shows
  // what the change actually did instead of waiting out the interval.
  const wasRunning = useRef(jobRunning);
  useEffect(() => {
    if (wasRunning.current && !jobRunning) load();
    wasRunning.current = jobRunning;
  }, [jobRunning, load]);

  // "No mods found in ExecStart." with mods in the mod list = the unit predates them.
  // Both halves of installing a mod are missing, so do both: regenerate the
  // service (rebuilds -mod= from the load order) then re-apply the mod XML.
  const installMods = useCallback(async () => {
    const ok = await confirm({
      title: "Configure mods now?",
      message:
        "Regenerates the service file so the launch command lists your mods, then re-applies every enabled mod's XML to the mission. Your own edits outside the mods' blocks stay. The server is stopped if it is running.",
      confirmLabel: "Configure mods",
      danger: true,
    });
    if (!ok) return;
    setInstallingMods(true);
    try {
      await api.post("/api/service/regenerate", {});
      await startJob({
        endpoint: "/api/mods/xml/merge",
        name: "Re-configure mod XML",
        kind: "mods_xml_merge",
        onStatusChange: (status) => {
          if (status === "success") load();
        },
      });
    } catch (err) {
      pushToast(err.message || "Failed to configure mods", "error");
    } finally {
      setInstallingMods(false);
    }
  }, [confirm, startJob, pushToast, load]);

  // Actions from a health-check row AND from the Mod Conflicts table's Fix
  // column — one dispatcher, since both render the same `{kind, label}` dicts
  // (app/ops/fix_hints.py). Install goes by workshop id when the finding carries
  // one and by name otherwise (`installBodyFor`), and the TraderPlus reset posts
  // to the same endpoint the Cleanup page uses.
  //
  // An action carrying `confirm` overwrites or deletes files (reinstall, keys/
  // re-sync), so it is asked about first — the backend decides which, so the
  // page never has to know what a new action kind costs.
  const runCheckAction = useCallback(
    async (action) => {
      const key = action.kind + action.label;
      if (action.confirm) {
        const ok = await confirm({
          title: `${action.label}?`,
          message: action.confirm,
          confirmLabel: action.label,
          danger: true,
        });
        if (!ok) return;
      }
      if (action.kind === "install_mods") {
        const { names, ids, id_names } = installBodyFor(action.mods);
        if (!ids.length && !names.length) return;
        setBusyAction(key);
        try {
          await startJob({
            endpoint: "/api/mods/install",
            body: { names, ids, id_names },
            name: action.label,
            kind: "mods_install",
            onStatusChange: (status) => {
              if (status === "success") load();
            },
          });
        } catch (err) {
          pushToast(err.message || "Failed to start install", "error");
        } finally {
          setBusyAction(null);
        }
        return;
      }
      if (action.kind === "install_server") {
        setBusyAction(key);
        try {
          await startJob({
            ...INSTALL_SERVER_JOB,
            onStatusChange: (status) => {
              if (status === "success") load();
            },
          });
        } catch (err) {
          pushToast(err.message || "Failed to start the server install", "error");
        } finally {
          setBusyAction(null);
        }
        return;
      }
      if (action.kind === "install_service") {
        setBusyAction(key);
        try {
          await api.post("/api/service/regenerate", {});
          pushToast("Service file generated and installed.", "success");
          load();
        } catch (err) {
          pushToast(err.message || "Installing the service failed", "error");
        } finally {
          setBusyAction(null);
        }
        return;
      }
      if (action.kind === "copy_bikeys") {
        setBusyAction(key);
        try {
          const res = await copyBikeys();
          pushToast(res.message, "success");
          load();
        } catch (err) {
          pushToast(err.message || "Copying bikeys failed", "error");
        } finally {
          setBusyAction(null);
        }
        return;
      }
      if (action.kind === "reset_traderplus_xml") {
        // This one's confirm is built here, not server-side: the action predates
        // `confirm` on the payload and its wording is specific to the reset.
        const ok = await confirm({
          title: "Reset TraderPlus XML files?",
          message:
            "Deletes @TraderPlus's custom/ XML files and their cfgeconomycore.xml entries, then copies them fresh from the mod. Edits you made to those files are lost.",
          confirmLabel: "Reset XML files",
          danger: true,
        });
        if (!ok) return;
        setBusyAction(key);
        try {
          await api.post("/api/cleanup/reset-traderplus", { kind: "xml" });
          pushToast("TraderPlus XML files reset.", "success");
          load();
        } catch (err) {
          pushToast(err.message || "Reset failed", "error");
        } finally {
          setBusyAction(null);
        }
      }
    },
    [confirm, startJob, pushToast, load],
  );

  // A pushed doctor notification means the health state changed — refresh.
  const handleWsMessage = useCallback(
    (msg) => {
      if (msg.type === "notification" && msg.link === "/doctor") load();
    },
    [load],
  );
  useWs(handleWsMessage);

  return (
    <div className="page doctor-page">
      <div className="page-header">
        <h1>Doctor</h1>
        <div className="action-bar">
          <AutoRefreshIndicator
            paused={auto.paused}
            blocked={jobRunning}
            progress={auto.progress}
            secondsLeft={auto.secondsLeft}
            interval={auto.interval}
            onToggle={auto.toggle}
          />
          <button
            className="btn btn-ghost"
            onClick={auto.runNow}
            disabled={loading}
          >
            <HiOutlineArrowPath aria-hidden="true" />
            {loading ? "Checking…" : "Re-run"}
          </button>
        </div>
      </div>

      <PageIntro page="/doctor" />

      {/* Its install fixes need Steam; only an admin can run them. */}
      {isAdmin && <SteamLoginGate />}

      {(jobRunning || report?.busy) && (
        <div className="banner banner-warning">
          A process is running — these findings describe a half-applied server.
          {jobRunning
            ? " Auto-refresh is paused and re-runs once it finishes."
            : " It was started outside this browser tab, so auto-refresh keeps running."}
        </div>
      )}

      {!report ? (
        <p className="page-loading page-loading-stack">
          {loading && (
            <HiOutlineHeart className="heartbeat-icon" aria-hidden="true" />
          )}
          {loading ? "Running checks…" : "No report."}
        </p>
      ) : (
        <>
          <SummaryBar
            summary={report.summary}
            logSummary={report.log_summary}
            conflicts={report.conflicts}
          />
          <ChecksSection
            checks={report.checks || []}
            onAction={runCheckAction}
            busyAction={busyAction}
          />
          <ConflictsSection
            conflicts={report.conflicts}
            onAction={runCheckAction}
            busyAction={busyAction}
          />
          <ServerInfoSection info={report.server_info} />
          <DiscUsageSection disc={report.disc_usage} />
          <LogSection
            log={report.log_analysis}
            onInstallMods={installMods}
            installing={installingMods}
          />
          <ServiceFileSection service={report.service_file} />
        </>
      )}
    </div>
  );
}
