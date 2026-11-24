import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { HiOutlineArrowDownTray, HiOutlineArrowPath, HiOutlineCheckCircle, HiOutlineArrowsRightLeft, HiOutlineMap, HiOutlineTrash } from "react-icons/hi2";
import { api } from "../api";
import BetaBadge from "../components/BetaBadge.jsx";
import ListSection from "../components/ListSection.jsx";
import PageIntro from "../components/PageIntro.jsx";
import SteamLoginGate from "../components/SteamLoginGate.jsx";
import { useToast } from "../components/Toast.jsx";
import ConfirmModal from "../components/ConfirmModal.jsx";
import MapSwitchDialog from "../components/MapSwitchDialog.jsx";
import NeedsSteam from "../components/NeedsSteam.jsx";
import { useJobs } from "../jobs";
import { useGuide } from "../guide.jsx";
import { formatBytes } from "../format.js";
import { modLink } from "../crossLinks.js";
import { tooltipAttrs } from "../components/Tooltip.jsx";
import PanelNote from '../components/PanelNote.jsx'
import ListRow, { RowTag } from '../components/ListRow.jsx'

// A community map's mods and dependencies, as the card's "Mods" block. Each
// name links to its mod page by name, so an installed mod resolves through the
// mod list even when its workshop id was never cached. An official map has
// neither, so the block is left out.
function MapMods({ map }) {
  const rows = [
    ...Object.entries(map.mods || {}).map(([name, wid]) => [name, wid, false]),
    ...Object.entries(map.deps || {}).map(([name, wid]) => [name, wid, true]),
  ];
  if (!rows.length) return null;
  return (
    <ListSection className="map-section" title="Mods">
      <ul className="doctor-list">
        {rows.map(([name, wid, dep]) => (
          <ListRow
            as="li"
            key={name}
            wrap
            name={<Link to={modLink(name)}>{name}</Link>}
            sub={wid}
            meta={dep ? <RowTag>Dependency</RowTag> : null}
          />
        ))}
      </ul>
    </ListSection>
  );
}

// What the Map Editor can draw for a map (registry.editor_features), as the
// card's "Map Editor" rows. A server without the world file still draws less,
// which the editor itself explains.
function EditorFeatures({ features }) {
  if (!features) return null;
  const yesNo = (on) => (on ? "Yes" : "No");
  const rows = [
    [
      "Map image",
      features.maker_image ? "From its maker, or your own upload" : "Your own upload",
    ],
    // An encrypted world file (Sakhal): the terrain comes from the navmesh,
    // which has no roads.
    [
      "Terrain (heights)",
      features.terrain_from_navmesh ? "Yes, from the navmesh" : yesNo(features.terrain),
    ],
    ["Roads", yesNo(features.roads)],
    ["Place names", yesNo(features.names)],
  ];
  return (
    <ListSection className="map-section map-section-wide" title="Map Editor">
      <ul className="doctor-list map-editor-rows">
        {rows.map(([label, detail]) => (
          <ListRow as="li" key={label} wrap name={label} sub={detail} />
        ))}
      </ul>
      {features.credit && <p className="map-description">{features.credit}</p>}
    </ListSection>
  );
}

export default function MapsPage() {
  const [maps, setMaps] = useState([]);
  const [active, setActive] = useState(null);
  const [confirmMap, setConfirmMap] = useState(null);
  const [downloadMap, setDownloadMap] = useState(null);
  const [removeMap, setRemoveMap] = useState(null);
  // Bytes each Download only would still fetch, from Steam; a map missing
  // here just has no size on its button.
  const [sizes, setSizes] = useState({});
  const [busy, setBusy] = useState(false);
  // Each community mission's last upstream check, by map id, and the one
  // being checked now.
  const [checks, setChecks] = useState({});
  const [checking, setChecking] = useState(null);
  const pushToast = useToast();
  const { startJob } = useJobs();
  const { markDone } = useGuide();

  const load = useCallback(async () => {
    try {
      const data = await api.get("/api/maps");
      setMaps(data.maps);
      setActive(data.active);
    } catch (err) {
      pushToast(err.message || "Failed to load maps", "error");
    }
    api
      .get("/api/maps/download-sizes")
      .then((data) => setSizes(data.sizes || {}))
      .catch(() => {});
  }, [pushToast]);

  useEffect(() => {
    load();
  }, [load]);

  // Runs one map job and reloads the list when it succeeds. `onSuccess` is
  // the caller's extra step on success (a switch ticks the Guide's map step).
  async function runMapJob(job, failure, onSuccess) {
    setBusy(true);
    try {
      await startJob({
        ...job,
        onStatusChange: (status) => {
          if (status !== "success") return;
          load();
          onSuccess?.();
        },
      });
    } catch (err) {
      pushToast(err.message || failure, "error");
    } finally {
      setBusy(false);
    }
  }

  // `body` is the switch dialog's choices (MapSwitchDialog).
  function handleSwitch(body) {
    const mapId = body.map_id;
    setConfirmMap(null);
    runMapJob(
      {
        endpoint: "/api/maps/switch",
        body,
        name: `Switch map to ${mapId}`,
        kind: "map_switch",
      },
      "Failed to start map switch",
      () => markDone("maps"),
    );
  }

  // Download only: the map's mods and mission, not switched to, so it can be
  // looked at in the Map Editor first (POST /api/maps/download).
  function handleDownload() {
    const mapId = downloadMap;
    setDownloadMap(null);
    runMapJob(
      {
        endpoint: "/api/maps/download",
        body: { map_id: mapId },
        name: `Download ${mapId}`,
        kind: "map_download",
      },
      "Failed to start the download",
    );
  }

  // Remove: the downloaded map's mission folder and the mods only it uses
  // (POST /api/maps/remove). The undo of Download only.
  function handleRemove() {
    const mapId = removeMap;
    setRemoveMap(null);
    runMapJob(
      {
        endpoint: "/api/maps/remove",
        body: { map_id: mapId },
        name: `Remove ${mapId}`,
        kind: "map_remove",
      },
      "Failed to start the removal",
    );
  }

  // Asks GitHub whether the map's author changed the mission after Zedstead
  // installed it (POST /api/maps/mission-check). On a click only: GitHub
  // allows 60 checks an hour from one address.
  async function handleCheckMission(mapId) {
    setChecking(mapId);
    try {
      const result = await api.post("/api/maps/mission-check", { map_id: mapId });
      setChecks((prev) => ({ ...prev, [mapId]: result }));
    } catch (err) {
      pushToast(err.message || "Failed to check the mission", "error");
    } finally {
      setChecking(null);
    }
  }

  const confirmTarget = maps.find((m) => m.id === confirmMap);

  const official = maps.filter((m) => m.type === "vanilla");
  const community = maps.filter((m) => m.type !== "vanilla");

  // An announced-but-unreleased map (available: false) is a preview card: no
  // mods/mission to show and nothing to switch to, so it carries the release
  // note and a link out instead of the Mission block.
  const renderCard = (m) => {
    // `downloaded`: the map's mods and mission are on this machine (always
    // true for an official map), so the Map Editor has something to draw.
    const isCommunity = m.type !== "vanilla";
    const canDownload = isCommunity && m.available !== false && !m.downloaded;
    // A mission from the author's GitHub repository, installed here.
    const canCheck = isCommunity && m.downloaded && m.mission_source === "zip";
    // The map in use cannot go: its mission is the one the server runs.
    const canRemove = isCommunity && m.downloaded && !m.active;
    const check = checks[m.id];
    // The map in use gets a status at the header's right end, not a button:
    // there is nothing to click.
    const switchButton = m.active ? null : (
      <button
        className={`btn ${
          m.available === false
            ? "btn-danger btn-state btn-small"
            : "btn-primary btn-small"
        }`}
        disabled={busy || m.available === false}
        onClick={() => setConfirmMap(m.id)}
      >
        {m.available === false ? (
          "Not available yet"
        ) : (
          <>
            <HiOutlineArrowsRightLeft aria-hidden="true" />
            {canDownload ? "Download and use" : "Use"}
          </>
        )}
      </button>
    );

    return (
      <div
        key={m.id}
        className={`card map-card ${m.active ? "map-card-active" : ""} ${
          m.available === false ? "map-card-upcoming" : ""
        }`}
      >
        <div className="card-header map-card-header">
          <h2>
            {m.id}
            {m.available === false && (
              <span className="status-pill status-pill-neutral map-badge">Not released</span>
            )}
            {isCommunity && m.downloaded && !m.active && (
              <span className="status-pill status-pill-neutral map-badge">Downloaded</span>
            )}
          </h2>
          {m.active && (
            <span className="map-in-use">
              <HiOutlineCheckCircle aria-hidden="true" />
              Current map
            </span>
          )}
        </div>
        <PanelNote>
          {m.type === "vanilla"
            ? "Official (Bohemia) map"
            : "Community (Workshop) map"}
        </PanelNote>
        {m.description && <p className="map-description">{m.description}</p>}
        {m.dlc_note && <div className="banner banner-warning">{m.dlc_note}</div>}

        {/* Two columns: Mission (or Availability) and Mods, then Map Editor across both. */}
        <div className="map-sections">
        {m.available === false ? (
          <ListSection className="map-section" title="Availability">
            <ul className="doctor-list">
              <ListRow as="li" wrap name="Release" sub={m.release ?? "TBA"} />
              <ListRow as="li" wrap name="Zedstead support" sub="Added once the map ships" />
            </ul>
            {m.url && (
              <p className="map-description">
                <a href={m.url} target="_blank" rel="noreferrer">
                  Official page
                </a>
              </p>
            )}
          </ListSection>
        ) : (
          <ListSection className="map-section" title="Mission">
            <ul className="doctor-list">
              <ListRow as="li" wrap name="Template" sub={m.mission_template ?? "N/A"} />
              {m.mission_zip_url && (
                // The row is the link; the full URL is its tooltip.
                <ListRow
                  as="li"
                  name={
                    <a
                      href={m.mission_zip_url}
                      target="_blank"
                      rel="noreferrer"
                      {...tooltipAttrs(m.mission_zip_url)}
                    >
                      Mission ZIP
                    </a>
                  }
                />
              )}
              {canCheck && (
                <ListRow as="li" wrap name={<>Upstream version <BetaBadge feature="mission-check" /></>} sub={m.mission_commit ?? "Not recorded"} />
              )}
              {canCheck && check && (
                <ListRow as="li" wrap name={check.newer ? "Newer files upstream" : "Upstream"} sub={<>{check.detail}
                    {check.newer && (
                      <>
                        {" "}
                        <Link to="/cleanup#reset-mission">Reset mission</Link> takes them. It keeps
                        player storage and replaces your own edits to the mission.
                      </>
                    )}</>} />
              )}
              {canDownload && sizes[m.id] ? (
                <ListRow as="li" wrap name="Download size" sub={formatBytes(sizes[m.id])} />
              ) : null}
            </ul>
          </ListSection>
        )}

        <MapMods map={m} />
        <EditorFeatures features={m.editor_features} />
        </div>

        {/* The foot: the buttons, kept at the card's bottom edge. */}
        <div className="map-card-foot">
        <div className="map-card-actions">
          {/* Switching to a map still to download runs SteamCMD. */}
          {canDownload ? <NeedsSteam>{switchButton}</NeedsSteam> : switchButton}

          {canDownload && (
            <NeedsSteam>
              <button
                className="btn btn-ghost btn-small"
                type="button"
                disabled={busy}
                onClick={() => setDownloadMap(m.id)}
                {...tooltipAttrs(
                  "Download its mods and mission without switching to it, to look at it in the Map Editor first",
                )}
              >
                <HiOutlineArrowDownTray aria-hidden="true" />
                Download only
              </button>
            </NeedsSteam>
          )}

          {canCheck && (
            <button
              className="btn btn-ghost btn-small"
              type="button"
              disabled={checking === m.id}
              onClick={() => handleCheckMission(m.id)}
              {...tooltipAttrs(
                "Ask GitHub whether the map's author changed the mission files after Zedstead installed them",
              )}
            >
              <HiOutlineArrowPath aria-hidden="true" />
              {checking === m.id ? "Checking…" : "Check files"}
            </button>
          )}

          {m.downloaded && m.editor_terrain && (
            <Link
              className="btn btn-ghost btn-small"
              to={`/map-editor?terrain=${encodeURIComponent(m.editor_terrain)}`}
            >
              <HiOutlineMap aria-hidden="true" />
              View map
            </Link>
          )}

          {canRemove && (
            <button
              className="btn btn-danger btn-small"
              type="button"
              disabled={busy}
              onClick={() => setRemoveMap(m.id)}
              {...tooltipAttrs("Delete its mission folder and the mods only this map uses")}
            >
              <HiOutlineTrash aria-hidden="true" />
              Remove
            </button>
          )}

        </div>
        </div>

      </div>
    );
  };

  return (
    <div className="page maps-page">
      <div className="page-header">
        <h1>Maps</h1>

        <div className="action-bar">
          <Link className="btn btn-ghost" to="/map-editor">
            <HiOutlineMap aria-hidden="true" />
            Map Editor
          </Link>
        </div>
      </div>

      <PageIntro page="/maps" />

      <SteamLoginGate />

      <PanelNote>
        Active map: <strong>{active ?? "Unknown"}</strong>. Stop the server
        before switching.
      </PanelNote>

      {official.length > 0 && (
        <section>
          <h2 className="maps-group-title">Official maps</h2>
          <div className="maps-grid">{official.map(renderCard)}</div>
        </section>
      )}

      {community.length > 0 && (
        <section>
          <h2 className="maps-group-title">Community maps</h2>
          <div className="maps-grid">{community.map(renderCard)}</div>
        </section>
      )}

      {confirmTarget && (
        <MapSwitchDialog
          key={confirmTarget.id}
          map={confirmTarget}
          onConfirm={handleSwitch}
          onCancel={() => setConfirmMap(null)}
        />
      )}

      <ConfirmModal
        open={downloadMap !== null}
        title={`Download ${downloadMap} without switching?`}
        message={`This downloads the map's mods${
          sizes[downloadMap] ? ` (${formatBytes(sizes[downloadMap])})` : ""
        } and mission so you can look at it in the Map Editor. Your active map, mod list and serverDZ.cfg stay as they are, and the server can keep running. Map mods are large, so it can take a while.`}
        confirmLabel="Download"
        onConfirm={handleDownload}
        onCancel={() => setDownloadMap(null)}
      />

      <ConfirmModal
        open={removeMap !== null}
        title={`Remove ${removeMap}?`}
        message={`This deletes the map's mission folder, with any player storage from a time it was in use, and the mods only this map uses. A mod in your mod list or used by another downloaded map stays. Your active map, mod list and serverDZ.cfg stay as they are.`}
        confirmLabel="Remove"
        danger
        onConfirm={handleRemove}
        onCancel={() => setRemoveMap(null)}
      />
    </div>
  );
}
