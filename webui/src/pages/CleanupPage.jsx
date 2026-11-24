import Select from '../components/Select.jsx'
import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { HiOutlineArrowPath } from "react-icons/hi2";
import BetaBadge from "../components/BetaBadge.jsx";
import PageIntro from "../components/PageIntro.jsx";
import SteamLoginGate from "../components/SteamLoginGate.jsx";
import NeedsSteam from "../components/NeedsSteam.jsx";
import { useToast } from "../components/Toast.jsx";
import { useJobs } from "../jobs";
import { useHashTarget } from "../hashTarget.js";
import PanelNote from '../components/PanelNote.jsx'
import { useConfirm } from "../components/useConfirm.jsx";
import Tooltip from "../components/Tooltip.jsx";
import { useServerStatus } from "../serverStatus";

// The backend refuses these while the server runs (cleanup.py::_refuse_while_running),
// so the button says so before its confirm dialog asks (night test 2026-11-22).
const STOP_FIRST_TIP = "Stop the server first.";

// Human labels for the long-running (job-backed) cleanup actions.
const JOB_NAMES = {
  workshop: "Remove workshop mods",
  storage: "Wipe server storage",
  "mission-reset": "Reset mission",
  "reinstall-server": "Reinstall server",
  "reinstall-steamcmd": "Reinstall SteamCMD",
};

function formatSize(bytes) {
  if (bytes == null) return "—";
  if (bytes > 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  if (bytes > 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${bytes} B`;
}

// The Cleanup page: destructive maintenance cards (wipe, migrate players.db,
// clear logs, reset mission, remove Workshop mods, reinstall, TraderPlus reset).
export default function CleanupPage() {
  const [overview, setOverview] = useState(null);
  const confirm = useConfirm();
  const [selectedMission, setSelectedMission] = useState("");
  const [missions, setMissions] = useState([]);
  const [migrateSource, setMigrateSource] = useState("");
  const [migrateTarget, setMigrateTarget] = useState("");
  const pushToast = useToast();
  const { trackJob } = useJobs();
  const running = !!useServerStatus().status?.running;
  // Deep links from the Doctor page (#reset-traderplus) land on their card.
  useHashTarget(!!overview);

  const loadOverview = useCallback(async () => {
    try {
      setOverview(await api.get("/api/cleanup/overview"));
      const data = await api.get("/api/cleanup/missions");
      setMissions(data.missions);
    } catch (err) {
      pushToast(err.message || "Failed to load cleanup overview", "error");
    }
  }, [pushToast]);

  useEffect(() => {
    loadOverview();
  }, [loadOverview]);

  async function handleMigratePlayers() {
    try {
      const data = await api.post("/api/cleanup/migrate-players", {
        source: migrateSource,
        target: migrateTarget,
        backup_target: true,
      });
      pushToast(
        `Migrated players.db: ${data.source} → ${data.target}` +
          (data.backed_up ? ` (target backed up to ${data.backed_up})` : ""),
        "info",
      );
      loadOverview();
    } catch (err) {
      pushToast(err.message || "Migration failed", "error");
    }
  }

  // Where the two TraderPlus reset buttons copy from: "mod", "github" (the
  // 2026-11-09 Workshop version ships no defaults, so the author's repo), or
  // null when @TraderPlus is not installed.
  const tpReset = overview?.traderplus_reset;
  const tpFromGithub = tpReset?.json === "github" || tpReset?.xml === "github";
  const traderPlusNote = !tpReset
    ? null
    : !tpReset.installed
      ? "@TraderPlus is not installed on this server."
      : tpFromGithub
        ? `This @TraderPlus version ships no default files, so the reset downloads them from ${tpReset.upstream_url}.`
        : null;

  // POSTs one TraderPlus reset ("json" or "xml") and toasts the result.
  async function handleResetTraderPlus(kind) {
    try {
      const data = await api.post("/api/cleanup/reset-traderplus", { kind });
      pushToast(
        data?.messages?.length
          ? data.messages.join(". ") + "."
          : `TraderPlus ${kind.toUpperCase()} configs reset.`,
        "info",
      );
    } catch (err) {
      pushToast(err.message || "TraderPlus reset failed", "error");
    }
  }

  async function handleRemoveModData(names) {
    try {
      const data = await api.post("/api/cleanup/mod-data", {
        confirm: true,
        names,
      });
      const removed = data?.removed || [];
      pushToast(
        removed.length
          ? `Deleted ${removed.length} leftover mod data folder(s).`
          : "Nothing to delete.",
        "info",
      );
      loadOverview();
    } catch (err) {
      pushToast(err.message || "Delete failed", "error");
    }
  }

  async function handleRepairWorkshop() {
    // Stale entries in appworkshop_221100.acf (mod folder deleted, manifest
    // still lists it) make SteamCMD abort every workshop download with
    // "Download item <id> failed (Failure)" while exiting 0.
    try {
      const data = await api.post("/api/cleanup/repair-workshop");
      const removed = data?.removed || [];
      pushToast(
        removed.length
          ? `Removed ${removed.length} stale manifest entry(s) — mod downloads should work again.`
          : "Workshop manifest is consistent — nothing to repair.",
        "info",
      );
    } catch (err) {
      pushToast(err.message || "Repair failed", "error");
    }
  }

  // Every action on this page asks first: `{ key, label }` runs runAction(key),
  // `{ run, label }` runs its own handler.
  async function ask(action) {
    const ok = await confirm({ title: "Confirm action", message: action.label, confirmLabel: "Proceed", danger: true });
    if (ok) action.run ? action.run() : runAction(action.key);
  }

  async function runAction(key) {
    try {
      let data;
      if (key === "logs")
        data = await api.post("/api/cleanup/logs", { confirm: true });
      else if (key === "xml-backups")
        data = await api.post("/api/cleanup/xml-backups", { confirm: true });
      else if (key === "workshop")
        data = await api.post("/api/cleanup/workshop", { confirm: true });
      else if (key === "storage")
        data = await api.post("/api/cleanup/storage", {
          confirm: true,
          backup_before: true,
        });
      else if (key === "mission-reset")
        data = await api.post("/api/cleanup/mission-reset", {
          confirm: true,
          mission: selectedMission,
          preserve_storage: true,
        });
      else if (key === "reinstall-server")
        data = await api.post("/api/maintenance/reinstall-server", {
          confirm: true,
        });
      else if (key === "reinstall-steamcmd")
        data = await api.post("/api/maintenance/reinstall-steamcmd", {
          confirm: true,
        });

      if (data?.job_id) {
        // Long-running action → track it in the Activity drawer; the store
        // toasts on completion. Refresh the overview once it finishes.
        trackJob(data.job_id, {
          name: JOB_NAMES[key] || "Cleanup",
          kind: "cleanup",
          onStatusChange: (status) => {
            if (status === "success") loadOverview();
          },
        });
      } else {
        loadOverview();
        // Clear logs keeps the files a running server still writes.
        pushToast(
          data?.kept
            ? `Deleted ${data.deleted} log files. ${data.kept} are in use by the running server and are kept until it stops.`
            : "Action completed.",
          "info",
        );
      }
    } catch (err) {
      pushToast(err.message || "Action failed", "error");
    }
  }

  if (!overview) return <div className="page cleanup-page" />;

  const leftoverModData = overview.leftover_mod_data || [];

  return (
    <div className="page cleanup-page">
      <div className="page-header">
        <h1>Cleanup</h1>
      </div>

      <PageIntro page="/cleanup" />

      <SteamLoginGate />

      <div className="cards">
        <div className="card">
          <h2>Log files</h2>
          {overview.log_files ? (
            <PanelNote>
              {overview.log_files.files.length} file(s),{" "}
              {formatSize(overview.log_files.total_size)}
            </PanelNote>
          ) : (
            <PanelNote>Profiles directory not found.</PanelNote>
          )}
          <button
            className="btn btn-danger"
            disabled={!overview.log_files?.files.length}
            onClick={() =>
              ask({ key: "logs", label: "Delete the log files? A running server keeps the files it is writing." })
            }
          >
            Clear logs
          </button>
        </div>

        <div className="card">
          <h2>XML backup files (.backup)</h2>
          {overview.xml_backups ? (
            <PanelNote>
              {overview.xml_backups.files.length} file(s),{" "}
              {formatSize(overview.xml_backups.total_size)} in{" "}
              {overview.xml_backups.mission}
            </PanelNote>
          ) : (
            <PanelNote>No mission directory found.</PanelNote>
          )}
          <button
            className="btn btn-danger"
            disabled={!overview.xml_backups?.files.length}
            onClick={() =>
              ask({
                key: "xml-backups",
                label: "Delete all XML backup files?",
              })
            }
          >
            Clear XML backups
          </button>
        </div>

        <div className="card">
          <h2>
            Workshop mods <BetaBadge feature="cleanup-mods" />
          </h2>
          {overview.workshop_mods ? (
            <PanelNote>
              {overview.workshop_mods.length} mod dir(s) in SteamCMD workshop
              cache.
            </PanelNote>
          ) : (
            <PanelNote>Workshop directory not found.</PanelNote>
          )}
          <div className="inline-form">
            <button
              className="btn btn-danger"
              disabled={!overview.workshop_mods?.length}
              onClick={() =>
                ask({
                  key: "workshop",
                  label: "Remove ALL workshop mod cache dirs?",
                })
              }
            >
              Remove all workshop mods
            </button>
            <button className="btn" onClick={handleRepairWorkshop}>
              Repair workshop manifest
            </button>
          </div>
        </div>

        <div className="card">
          <h2>Storage wipe (persistence)</h2>
          {overview.storage ? (
            <PanelNote>
              Mission: {overview.storage.mission}
              <br />
              {overview.storage.path}
            </PanelNote>
          ) : (
            <PanelNote>
              No {overview.storage_name} found for the current mission.
            </PanelNote>
          )}
          <Tooltip text={running ? STOP_FIRST_TIP : ""} className="bare">
            <button
              className="btn btn-danger"
              disabled={!overview.storage || running}
              onClick={() =>
                ask({
                  key: "storage",
                  label: `Wipe ${overview.storage_name} (a backup is taken first)?`,
                })
              }
            >
              Wipe server
            </button>
          </Tooltip>
        </div>

        <div className="card" id="reset-mission">
          <h2>Reset mission</h2>
          <div className="inline-form">
            <Select
              value={selectedMission}
              onChange={setSelectedMission}
              placeholder="Select a mission…"
              options={overview.resettable_missions.map((m) => ({ value: m.template, label: `${m.template} (${m.map_name})` }))}
            />
            {/* A reset downloads the stock mission through SteamCMD. */}
            <NeedsSteam tip={running ? STOP_FIRST_TIP : undefined} className={running ? "bare" : undefined}>
              <button
                className="btn btn-danger"
                disabled={!selectedMission || running}
                onClick={() =>
                  ask({
                    key: "mission-reset",
                    label: `Reset ${selectedMission} to default?`,
                  })
                }
              >
                Reset mission
              </button>
            </NeedsSteam>
          </div>
        </div>

        <div className="card">
          <h2>Migrate players.db</h2>
          <PanelNote>
            Copy player persistence (players.db) from one mission's{" "}
            {overview.storage_name} to another's. Every character keeps its
            position, so use it between missions on the same map: on another
            map, a player can wake up in the sea, under the ground or off the
            map. The existing target file is backed up first.
          </PanelNote>
          <div className="inline-form">
            <Select
              value={migrateSource}
              onChange={setMigrateSource}
              placeholder="Source mission…"
              options={missions
                .filter((m) => m.has_players_db)
                .map((m) => ({ value: m.mission, label: `${m.mission} (${formatSize(m.players_db_size)})` }))}
            />
            <Select
              value={migrateTarget}
              onChange={setMigrateTarget}
              placeholder="Target mission…"
              options={missions
                .filter((m) => m.mission !== migrateSource)
                .map((m) => ({ value: m.mission, label: `${m.mission}${m.has_players_db ? " (has players.db)" : ""}` }))}
            />
            <button
              className="btn btn-primary"
              disabled={!migrateSource || !migrateTarget}
              onClick={() =>
                ask({
                  label: `Copy players.db from ${migrateSource} to ${migrateTarget}?`,
                  run: handleMigratePlayers,
                })
              }
            >
              Migrate
            </button>
          </div>
        </div>

        <div className="card" id="leftover-mod-data">
          <h2>Leftover mod data</h2>
          <PanelNote>
            Uninstalling a mod keeps the <code>{"<profiles>/<Mod>/"}</code>{" "}
            folder it wrote — TraderPlus prices and banking, garage vehicles
            and the like live there, so it survives an uninstall/reinstall.
            Delete it here once you are sure you don't need it.
          </PanelNote>
          {leftoverModData.length ? (
            <div className="data-table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Folder</th>
                    <th>Left by</th>
                    <th>Size</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {leftoverModData.map((d) => (
                    <tr key={d.name}>
                      <td>
                        <code>{d.path}</code>
                      </td>
                      <td>{d.mod}</td>
                      <td>
                        {d.files} file(s), {formatSize(d.size)}
                      </td>
                      <td>
                        <button
                          className="btn btn-danger btn-small"
                          onClick={() =>
                            ask({
                              label: `Delete ${d.path}? The data ${d.mod} stored there is lost.`,
                              run: () => handleRemoveModData([d.name]),
                            })
                          }
                        >
                          Delete
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <PanelNote>
              No data left behind by an uninstalled mod.
            </PanelNote>
          )}
          <button
            className="btn btn-danger"
            disabled={!leftoverModData.length}
            onClick={() =>
              ask({
                label: `Delete all ${leftoverModData.length} leftover mod data folder(s)?`,
                run: () => handleRemoveModData(null),
              })
            }
          >
            Delete all leftover mod data
          </button>
        </div>

        <div className="card" id="reset-traderplus">
          <h2>Reset TraderPlus configs</h2>
          <PanelNote>
            Replace the TraderPlus configs with fresh defaults from the
            @TraderPlus mod. Bank accounts, garages, trader stock and logs are
            kept.
          </PanelNote>
          {traderPlusNote && <PanelNote>{traderPlusNote}</PanelNote>}
          <div className="inline-form">
            <button
              className="btn btn-danger"
              disabled={!tpReset?.json}
              onClick={() =>
                ask({
                  label:
                    "Reset the TraderPlus JSON configs (profiles/TraderPlus/TraderPlusConfig) to mod defaults? Each file is kept as a version first, in Analysis → Change history. Bank accounts, garages, trader stock and logs are kept.",
                  run: () => handleResetTraderPlus("json"),
                })
              }
            >
              Reset TraderPlus JSON
            </button>
            <button
              className="btn btn-danger"
              disabled={!tpReset?.xml}
              onClick={() =>
                ask({
                  label:
                    tpReset?.xml === "github"
                      ? "Download TraderPlus_types.xml and save it as the manual XML file traderplus-types? An existing file with that name is replaced and kept as a version first."
                      : "Reset TraderPlus mission XMLs to mod defaults?",
                  run: () => handleResetTraderPlus("xml"),
                })
              }
            >
              Reset TraderPlus XML
            </button>
          </div>
        </div>

        <div className="card">
          <h2>Maintenance</h2>
          <div className="action-bar">
            <NeedsSteam tip={running ? STOP_FIRST_TIP : undefined} className={running ? "bare" : undefined}>
              <button
                className="btn btn-danger"
                disabled={running}
                onClick={() =>
                  ask({
                    key: "reinstall-server",
                    label: "Wipe and reinstall the DayZ server via SteamCMD?",
                  })
                }
              >
                <HiOutlineArrowPath aria-hidden="true" />
                Reinstall server
              </button>
            </NeedsSteam>
            <button
              className="btn btn-danger"
              onClick={() =>
                ask({
                  key: "reinstall-steamcmd",
                  label: "Wipe and reinstall SteamCMD?",
                })
              }
            >
              <HiOutlineArrowPath aria-hidden="true" />
              Reinstall SteamCMD
            </button>
          </div>
        </div>
      </div>

    </div>
  );
}
