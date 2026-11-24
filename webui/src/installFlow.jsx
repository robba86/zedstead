// Shared install/uninstall dialogs for the Mods page and the per-mod page.
//
// The install flags used to be checkboxes sitting next to the Install button;
// they are asked at the moment they apply instead, and only when they apply —
// so this module owns both the wording and the "is this a TraderPlus install"
// test the two pages would otherwise each re-derive.
import { Switch } from './components/FlagSwitch.jsx'
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import ConfirmModal from './components/ConfirmModal.jsx'
import { configLink } from './crossLinks.js'

// Mirrors app/modmgr/mod_ops.py::TRADERPLUS_WORKSHOP_ID. The install job gates
// the config copy on its own server-side is_traderplus_installed(); this only
// decides which question the dialog asks.
export const TRADERPLUS_WORKSHOP_ID = '2458896948'

// True when the install set contains @TraderPlus, by name or by workshop id.
export function isTraderPlusRequest({ names = [], ids = [] } = {}) {
  if (names.some((n) => String(n).toLowerCase().replace(/^@/, '') === 'traderplus')) return true
  return ids.some((i) => String(i) === TRADERPLUS_WORKSHOP_ID)
}

// The "auto config" step of an install: applying the mods' XML. Worded here once —
// the dialog copy and the install job's skip message must agree.
export const CONFIGURE_XML_TIP =
  "Applies each mod's XML to the mission: its types/events/spawnabletypes are linked (custom/ files registered in " +
  'cfgeconomycore.xml) and its spawn positions are merged into the mission XML files.\n' +
  'Turn off to download and register the mods only — apply it later with "Re-configure all mod XML" ' +
  'on the Installed tab, or by running the full pipeline.'

// Same two paragraphs, as separate strings for the dialog. A third one (the
// "it stays yours to edit" note) carries a link, so it lives in the JSX below.
export const CONFIGURE_XML_DIALOG = [
  "Applies each mod's XML to the mission: its types/events/spawnabletypes are linked (custom/ files registered in " +
    'cfgeconomycore.xml) and its spawn positions are merged into the mission XML files.',
  'Leave it off to download and register the mods only — nothing the mods add will spawn until ' +
    'their XML is applied. You can do it later with "Re-configure all mod XML" on the Installed tab, ' +
    'per mod from its row, or by running the full pipeline.',
]

// The TraderPlus half. Every TraderPlus install copies the defaults when the
// profiles folder has none (the XML merge's ensure_upstream_defaults, then
// copy_traderplus_default_config), so the dialog only says so. The switch it
// had did nothing: the merge copied them anyway (night test 2026-11-22).
export const TRADERPLUS_CONFIG_NOTE =
  'The mod\'s default trader, banking and garage configs are copied into the server profiles folder ' +
  'when it has none, so TraderPlus starts with a working setup. An existing TraderPlus config is never overwritten.'

// The first boot with TraderPlus drops the vehicles already in the world: its
// car lock changes what a vehicle saves. Measured in the test container on
// 2026 Q3 (83 vehicles, vanilla and Expansion, plans/48 D), and the mod's
// Workshop page warns about it. The log analyzer files that boot's losses
// under TraderPlus (log_analysis.MOD_BASELINE).
export const TRADERPLUS_VEHICLES_NOTE =
  'On the first boot with TraderPlus, the vehicles already on the map are lost: its car lock ' +
  'changes what a vehicle saves, and the older saves cannot be read. If they matter, take a quick ' +
  'backup first; restoring it without TraderPlus brings them back.'

// The Expansion half: the mission files live in the mod team's separate
// DayZ-Expansion-Missions repository, not in the mods (ExpansionMission.jsx).
export const EXPANSION_MISSION_NOTE =
  "Traders, vehicle spawns, airdrops and the Expansion economy come from the mod team's " +
  'DayZ-Expansion-Missions repository, not from the mods. After the install, Zedstead downloads ' +
  "it if needed and applies this map's template, filtered to the installed modules. Leave it off " +
  'to apply it later from the Expansion mission files card.'

// Shown inside the uninstall confirm on both pages.
export const ORPHAN_DEPS_NOTE =
  'An orphaned dependency is a mod that was installed only because another mod required it ' +
  '(e.g. @CF for @TraderPlus). Once the mod that needed it is gone, nothing on this server loads ' +
  'it. Leaving it installed is harmless — it just keeps its folder, its load-order slot and its ' +
  'mission XML. @CF is the one to think about before ticking this: removing it requires wiping ' +
  'the server.'

// @CF is marked "removing this mod requires wiping" on Expansion's own
// installation cheatsheet (Steam Workshop item 2430317812). It is the only mod
// zsm knows that carries that warning, so this is a named case rather than a
// rule — don't generalise it to "framework mods" without a source.
const WIPE_ON_REMOVE = { '1559212036': '@CF' }

/**
 * The wipe warning for an uninstall request, or null.
 *
 * Matches by workshop id and by name, because the two pages address mods
 * differently (ModsPage has names from the mod list, ModPage a resolved id).
 */
export function wipeWarningFor({ names = [], ids = [] } = {}) {
  const wanted = new Set([
    ...names.map((n) => String(n).replace(/^@/, '').toLowerCase()),
    ...ids.map((i) => String(i)),
  ])
  const hit = Object.entries(WIPE_ON_REMOVE).find(
    ([id, name]) => wanted.has(id) || wanted.has(name.replace(/^@/, '').toLowerCase())
  )
  if (!hit) return null
  return (
    `${hit[1]} is on this list. Expansion's installation cheatsheet marks it as requiring a ` +
    'server wipe when removed — players, base building and anything else in the persistence ' +
    'files will not survive it. Back up first.'
  )
}

// The uninstall confirm's extra control. Same markup on both pages.
export function OrphanDepsField({ checked, onChange }) {
  return (
    <>
      <div className="dry-run-toggle">
        <Switch checked={checked} onChange={onChange}>
          Also remove now-orphaned dependencies
        </Switch>
      </div>
      <p className="modal-message">{ORPHAN_DEPS_NOTE}</p>
    </>
  )
}

// "@CF, @TraderPlus" / "1559212036" — what the confirm is about.
function describeRequest({ names = [], ids = [], id_names = {} }) {
  const parts = [...names, ...ids.map((i) => id_names[i] || id_names[String(i)] || String(i))]
  if (parts.length <= 4) return parts.join(', ')
  return `${parts.slice(0, 4).join(', ')} and ${parts.length - 4} more`
}

// The dialog's title and button word for each mode.
const MODE_VERBS = { install: 'Install', update: 'Update', reinstall: 'Reinstall' }

// What a reinstall does beyond an update: POST /api/mods/install with `reinstall`.
const REINSTALL_NOTE =
  'Deletes the workshop cache copy first, so SteamCMD downloads every file again. ' +
  'The server copy is then replaced. Dependencies keep their cached copy.'

// Pre-install confirm. `request` is the install payload plus a `mode`
// ('install' | 'update' | 'reinstall'), or null when closed; `onConfirm` receives the flag
// object to merge into the POST body.
//
// One question at a time on purpose: a TraderPlus install asks nothing and
// keeps the XML merge on (it is the mod that most needs it), every other
// install asks about the merge.
//
// An Expansion install (`request.expansion`) adds one more switch: apply the
// Expansion mission files after the install. It travels as
// `apply_expansion_mission`, which the page acts on and never posts.
export default function InstallOptionsModal({ request, onConfirm, onCancel }) {
  const [checked, setChecked] = useState(true)
  const [expansionMission, setExpansionMission] = useState(true)
  // A "no" must not stick to the next install.
  useEffect(() => {
    setChecked(true)
    setExpansionMission(true)
  }, [request])

  if (!request) return null

  const traderplus = isTraderPlusRequest(request)
  const verb = MODE_VERBS[request.mode] || 'Install'
  const count = (request.names?.length || 0) + (request.ids?.length || 0)
  const expansion = !!request.expansion && (request.mode || 'install') === 'install'

  function handleConfirm() {
    onConfirm({
      configure_xml: traderplus || checked,
      ...(request.mode === 'reinstall' ? { reinstall: true } : {}),
      ...(expansion ? { apply_expansion_mission: expansionMission } : {}),
    })
  }

  return (
    <ConfirmModal
      open
      // Named for TraderPlus only when it is the whole request: with other
      // mods beside it the title read "Install @TraderPlus?".
      title={
        traderplus && count === 1
          ? `${verb} @TraderPlus?`
          : `${verb} ${count} mod${count === 1 ? '' : 's'}?`
      }
      message={describeRequest(request)}
      confirmLabel={verb}
      onConfirm={handleConfirm}
      onCancel={onCancel}
    >
      {request.mode === 'reinstall' && <p className="modal-message">{REINSTALL_NOTE}</p>}
      {traderplus ? (
        <>
          <p className="modal-message">{TRADERPLUS_CONFIG_NOTE}</p>
          {/* Only a first install meets the vehicle loss, never an update or a reinstall. */}
          {(request.mode || 'install') === 'install' && (
            <p className="modal-message">
              {TRADERPLUS_VEHICLES_NOTE}{' '}
              <Link to="/backups" onClick={onCancel}>
                Backups
              </Link>
            </p>
          )}
        </>
      ) : (
        <>
          <div className="dry-run-toggle">
            <Switch checked={checked} onChange={setChecked}>
              Auto-configure mod XML after install
            </Switch>
          </div>
          {CONFIGURE_XML_DIALOG.map((p) => (
            <p className="modal-message" key={p}>
              {p}
            </p>
          ))}
          <p className="modal-message">
            Either way the result stays yours: every file the merge generates is listed on the{' '}
            <Link to={configLink('mod-xml')} onClick={onCancel}>
              Custom config tab
            </Link>
            , where you can turn one off, edit it, or take it over as a manual XML file that
            survives the next merge.
          </p>
        </>
      )}
      {expansion && (
        <>
          <div className="dry-run-toggle">
            <Switch checked={expansionMission} onChange={setExpansionMission}>
              Apply Expansion mission files after install
            </Switch>
          </div>
          <p className="modal-message">{EXPANSION_MISSION_NOTE}</p>
        </>
      )}
    </ConfirmModal>
  )
}
