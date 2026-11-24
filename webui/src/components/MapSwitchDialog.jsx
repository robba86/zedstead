import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import ConfirmModal from "./ConfirmModal.jsx";
import { Switch } from "./FlagSwitch.jsx";

// The map switch dialog (plans/83): what goes to the new map. It shows
// `GET /api/maps/switch-plan` and sends the choices with the switch. The new
// map's own mods and deps always follow and the old map's own mods leave, so
// both are shown, not asked. The admin may leave mods behind (turned off, not
// uninstalled), skip the mod XML, keep manual XML files with the old map and
// pick the serverDZ.cfg. Every default is what a switch did before the dialog
// asked, so a plan that fails to load still lets Switch run with them.
//
// MapsPage mounts it per open (keyed by the map), so nothing carries over
// from one open to the next.

function savedDate(modified) {
  return new Date(modified * 1000).toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

// The serverDZ.cfg question: the saved config for this map (the copy left
// from the last time it was active) or today's, plus the map's recommended
// keys for a community map whose archive has a cfg.
function CfgSection({ map, backup, recommended, keys, restore, onRestore, applyRecommended, onRecommended }) {
  const usesCurrent = !backup || !restore;
  return (
    <section className="map-switch-section">
      <h4 className="map-switch-heading">serverDZ.cfg</h4>
      {backup ? (
        <div role="radiogroup" aria-label="Which serverDZ.cfg to use">
          <label className="map-switch-row">
            <input
              type="radio"
              name="map-switch-cfg"
              checked={restore}
              onChange={() => onRestore(true)}
            />
            Your saved config for {map.id} (saved {savedDate(backup.modified)})
          </label>
          <label className="map-switch-row">
            <input
              type="radio"
              name="map-switch-cfg"
              checked={!restore}
              onChange={() => onRestore(false)}
            />
            Your current config
          </label>
          {restore ? (
            <p className="map-switch-note map-switch-warn">
              Brings back {map.id}&apos;s whole serverDZ.cfg as you left it.
              Settings you changed since then go back; only the passwords come
              from your current one.
            </p>
          ) : (
            <p className="map-switch-note map-switch-warn">
              Your saved config for {map.id} is not used this time. It stays
              saved for a later switch.
            </p>
          )}
        </div>
      ) : (
        <p className="map-switch-note">
          Keeps your current serverDZ.cfg and sets the map&apos;s mission.
        </p>
      )}
      {recommended && usesCurrent && (
        <div className="dry-run-toggle">
          <Switch checked={applyRecommended} onChange={onRecommended}>
            Apply the map&apos;s recommended {keys.join(", ")}
          </Switch>
        </div>
      )}
    </section>
  );
}

export default function MapSwitchDialog({ map, onConfirm, onCancel }) {
  const [plan, setPlan] = useState(null);
  const [error, setError] = useState(null);
  const [off, setOff] = useState(() => new Set());
  const [leave, setLeave] = useState(() => new Set());
  const [applyXml, setApplyXml] = useState(true);
  const [restore, setRestore] = useState(true);
  const [applyRecommended, setApplyRecommended] = useState(true);

  useEffect(() => {
    let live = true;
    api
      .get(`/api/maps/switch-plan?map_id=${encodeURIComponent(map.id)}`)
      .then((data) => live && setPlan(data))
      .catch((err) => live && setError(err.message || "Could not load what the switch takes along."));
    return () => {
      live = false;
    };
  }, [map.id]);

  const loading = !plan && !error;
  const from = plan?.from || plan?.from_terrain || "the current map";
  const mods = plan?.mods ?? [];
  const movable = (plan?.manual_files ?? []).filter((f) => f.scope === "every" && f.can_leave);

  // "@Expansion needs @CF": a ticked mod that requires an unticked one.
  const depWarnings = useMemo(() => {
    const out = [];
    for (const m of mods) {
      if (!off.has(m.name)) continue;
      for (const needer of m.required_by) {
        if (!off.has(needer)) out.push([needer, m.name]);
      }
    }
    return out;
  }, [mods, off]);

  function toggle(set, setter, key) {
    const next = new Set(set);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    setter(next);
  }

  function confirm() {
    if (!plan) {
      onConfirm({
        map_id: map.id,
        restore_previous: restore,
        apply_recommended: applyRecommended,
      });
      return;
    }
    onConfirm({
      map_id: map.id,
      restore_previous: restore,
      apply_recommended: applyRecommended,
      apply_mod_xml: applyXml,
      disable_mods: [...off],
      leave_manual_files: applyXml ? [...leave] : [],
    });
  }

  const offNames = mods.filter((m) => off.has(m.name)).map((m) => m.name);
  const scopedHere = (plan?.manual_files ?? []).filter((f) => f.scope === "to");
  const scopedThere = (plan?.manual_files ?? []).filter((f) => f.scope === "from");
  const stuck = (plan?.manual_files ?? []).filter((f) => f.scope === "every" && !f.can_leave);

  return (
    <ConfirmModal
      open
      title={`Switch map to ${map.id}?`}
      message="This downloads mods/mission files if needed and updates serverDZ.cfg. Make sure the server is stopped first."
      confirmLabel="Use"
      confirmDisabled={loading}
      onConfirm={confirm}
      onCancel={onCancel}
    >
      <div className="map-switch-dialog">
        {loading && <p className="map-switch-note">Loading what the switch takes along…</p>}
        {error && (
          <div className="banner banner-warning">
            {error} Switch still works, with every mod, the mod XML and every
            manual XML file going along.
          </div>
        )}

        {plan && (
          <>
            <section className="map-switch-section">
              <h4 className="map-switch-heading">Comes with {map.id}</h4>
              {plan.map_mods.length === 0 ? (
                <p className="map-switch-note">
                  {map.id} comes with the server and needs no mods.
                </p>
              ) : (
                <>
                  <ul className="map-switch-list">
                    {plan.map_mods.map((m) => (
                      <li key={m.name} className="map-switch-row map-switch-locked">
                        <input type="checkbox" checked disabled aria-label={`${m.name} always goes along`} />
                        <span>{m.name}</span>
                        {m.dep && <span className="status-pill status-pill-neutral">dependency</span>}
                        {m.turn_on && (
                          <span className="status-pill status-pill-warning">turned on by the switch</span>
                        )}
                      </li>
                    ))}
                  </ul>
                  <p className="map-switch-note">Always installed with the map.</p>
                </>
              )}
            </section>

            {(plan.leaving.removed.length > 0 || plan.leaving.kept_deps.length > 0) && (
              <section className="map-switch-section">
                <h4 className="map-switch-heading">Leaves with {from}</h4>
                <div className="banner banner-warning">
                  {plan.leaving.removed.length > 0 && (
                    <p>
                      Removed from the server, folder and keys:{" "}
                      {plan.leaving.removed.join(", ")}.
                    </p>
                  )}
                  {plan.leaving.kept_deps.length > 0 && (
                    <p>
                      Kept: {plan.leaving.kept_deps.join(", ")}. Other mods are
                      still installed and may need them.
                    </p>
                  )}
                </div>
              </section>
            )}

            <section className="map-switch-section">
              <h4 className="map-switch-heading">Your mods</h4>
              {mods.length === 0 ? (
                <p className="map-switch-note">No other mods are on.</p>
              ) : (
                <>
                  <div className="map-switch-bulk">
                    <button type="button" className="btn btn-ghost btn-small" onClick={() => setOff(new Set())}>
                      Select all
                    </button>
                    <button
                      type="button"
                      className="btn btn-ghost btn-small"
                      onClick={() => setOff(new Set(mods.map((m) => m.name)))}
                    >
                      Select none
                    </button>
                  </div>
                  <ul className="map-switch-list">
                    {mods.map((m) => (
                      <li key={m.name}>
                        <label className="map-switch-row">
                          <input
                            type="checkbox"
                            checked={!off.has(m.name)}
                            onChange={() => toggle(off, setOff, m.name)}
                          />
                          <span>{m.name}</span>
                          {m.server_only && (
                            <span className="status-pill status-pill-neutral">server-only</span>
                          )}
                          {m.local && <span className="status-pill status-pill-neutral">local</span>}
                        </label>
                      </li>
                    ))}
                  </ul>
                </>
              )}
              {offNames.length > 0 && (
                <p className="map-switch-note map-switch-warn">
                  {offNames.join(", ")}: turned off, not uninstalled. Files and
                  profiles data stay. Mods are server-wide, so{" "}
                  {offNames.length === 1 ? "it is" : "they are"} off on every
                  map, {from} included, until you turn{" "}
                  {offNames.length === 1 ? "it" : "them"} on again on the Mods
                  page. Switching back does not turn{" "}
                  {offNames.length === 1 ? "it" : "them"} on.
                </p>
              )}
              {depWarnings.map(([needer, needed]) => (
                <p key={`${needer}>${needed}`} className="map-switch-note map-switch-warn">
                  {needer} needs {needed}; with {needed} off it may not load.
                </p>
              ))}
              {plan.off_mods.length > 0 && (
                <p className="map-switch-note">
                  Already off: {plan.off_mods.join(", ")}. They stay off; turn
                  them on on the <Link to="/mods">Mods page</Link>.
                </p>
              )}
            </section>

            <section className="map-switch-section">
              <div className="dry-run-toggle">
                <Switch checked={applyXml} onChange={setApplyXml}>
                  Apply mod XML to the new mission
                </Switch>
              </div>
              {!applyXml && (
                <p className="map-switch-note map-switch-warn">
                  Your mods load on {map.id} without their types, events and
                  spawns, and the manual XML files are not applied, until you
                  run Re-apply all on the Mods page or <code>zsm run</code>.
                </p>
              )}
            </section>

            {(movable.length > 0 || scopedHere.length > 0 || scopedThere.length > 0 || stuck.length > 0) && (
              <section className="map-switch-section">
                <h4 className="map-switch-heading">Your manual XML files</h4>
                <fieldset className="map-switch-fieldset" disabled={!applyXml}>
                  {movable.length > 0 && (
                    <ul className="map-switch-list">
                      {movable.map((f) => (
                        <li key={f.id}>
                          <label className="map-switch-row">
                            <input
                              type="checkbox"
                              checked={!leave.has(f.id)}
                              onChange={() => toggle(leave, setLeave, f.id)}
                            />
                            <span>{f.id}</span>
                            {f.mod && <span className="status-pill status-pill-neutral">{f.mod}</span>}
                          </label>
                          {leave.has(f.id) && (
                            <p className="map-switch-note">
                              Stays with {from}: it applies there again when you
                              switch back, not on {map.id}.
                            </p>
                          )}
                        </li>
                      ))}
                    </ul>
                  )}
                  {stuck.length > 0 && (
                    <p className="map-switch-note">
                      {stuck.map((f) => f.id).join(", ")}: go along. Scoped to{" "}
                      {from} {stuck.length === 1 ? "it" : "they"} would still
                      apply on {map.id}, so {stuck.length === 1 ? "it" : "they"}{" "}
                      cannot stay behind.
                    </p>
                  )}
                  {scopedHere.length > 0 && (
                    <p className="map-switch-note">
                      {scopedHere.map((f) => f.id).join(", ")}: made for{" "}
                      {map.id}, applied there.
                    </p>
                  )}
                  {scopedThere.length > 0 && (
                    <p className="map-switch-note">
                      {scopedThere.map((f) => f.id).join(", ")}: made for {from},
                      stay with it.
                    </p>
                  )}
                </fieldset>
              </section>
            )}
          </>
        )}

        <CfgSection
          map={map}
          backup={plan ? plan.cfg.backup : map.cfg_backup}
          recommended={plan ? plan.cfg.recommended : map.recommended_cfg}
          keys={plan?.cfg.keys ?? ["lighting and gameplay keys"]}
          restore={restore}
          onRestore={setRestore}
          applyRecommended={applyRecommended}
          onRecommended={setApplyRecommended}
        />
      </div>
    </ConfirmModal>
  );
}
