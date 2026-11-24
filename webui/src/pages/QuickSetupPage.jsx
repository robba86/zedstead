// Quick setup: a first server from a short text and one Apply. The "Your server"
// card and the "Difficulty and mods" card run POST /api/quick-setup/apply.
// The UI never calls a roll a mod pack or a bundle, and it links each mod to its
// Workshop page, so that the admin can read the author's terms.
import { Fragment, useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { HiOutlineXMark } from 'react-icons/hi2'
import { api } from '../api'
import ConfirmModal from '../components/ConfirmModal.jsx'
import { Switch } from '../components/FlagSwitch.jsx'
import InstallServerCard from '../components/InstallServerCard.jsx'
import NeedsSteam from '../components/NeedsSteam.jsx'
import PageIntro from '../components/PageIntro.jsx'
import PasswordField from '../components/PasswordField.jsx'
import PresetPreview from '../components/PresetPreview.jsx'
import Select from '../components/Select.jsx'
import Question from '../components/SettingQuestion.jsx'
import SteamLoginGate from '../components/SteamLoginGate.jsx'
import { useToast } from '../components/Toast.jsx'
import { useGuide } from '../guide.jsx'
import { resolveHelpSlug } from '../helpLinks.js'
import { RUNNING_STATUSES, TERMINAL_STATUSES, useJobs } from '../jobs'
import { useQuickSetupState } from '../quickSetupState.js'
import { useCan, useRole } from '../role.jsx'
import { useRookie } from '../rookie.jsx'
import { PLAYER_COUNT_HINT, fieldsByKey, serverAnswers } from '../serverAnswers.js'

const DEFAULT_PRESET = 'vanilla_balanced'
// The install card's job: the server install alone, through Quick setup's own
// route, so the setup stays unfinished for the preset and the mods.
const INSTALL_ONLY_JOB = {
  endpoint: '/api/quick-setup/apply',
  body: { install_only: true },
  name: 'Install DayZ server',
  kind: 'update',
}

const BOOT_FAILED = new Set(['crashed', 'not_joinable', 'not_started'])
const DEFAULT_SERVER = { hostname: '', passwordOn: false, password: '', passwordAdmin: '', maxPlayers: '60', map: '' }

// The page's answers while the panel stays open: a page change keeps them, a
// reload clears them, so no password reaches browser storage. An instance
// switch is a full page load, so each server starts with its own empty answers.
let kept = null

// What the "Your server" card still needs before Apply, each as `{ label, id }`:
// plain words, and the id of the field to fill in.
function serverMissing(server, locked) {
  const missing = []
  if (!locked.hostname && !server.hostname.trim()) missing.push({ label: 'a server name', id: 'qs-hostname' })
  if (server.passwordOn && !server.password) missing.push({ label: 'a join password', id: 'qs-join-password' })
  if (!server.passwordAdmin) missing.push({ label: 'an admin password', id: 'qs-admin-password' })
  if (!(Number(server.maxPlayers) > 0)) missing.push({ label: 'a player count', id: 'qs-max-players' })
  return missing
}

// Scroll to a "Your server" field and put the cursor in it.
function focusField(id) {
  const field = document.getElementById(id)
  if (!field) return
  field.scrollIntoView({ block: 'center', behavior: 'smooth' })
  field.focus({ preventScroll: true })
}

// "a server name and an admin password": each one a link to its field.
function MissingFields({ missing }) {
  return missing.map((item, i) => (
    <Fragment key={item.label}>
      {i > 0 && (i === missing.length - 1 ? ' and ' : ', ')}
      {item.id ? (
        <button type="button" className="btn-link" onClick={() => focusField(item.id)}>
          {item.label}
        </button>
      ) : (
        item.label
      )}
    </Fragment>
  ))
}

// The card's answers as the apply body's `server`.
function serverBody(server) {
  return {
    hostname: server.hostname,
    password: server.passwordOn ? server.password : '',
    password_admin: server.passwordAdmin,
    max_players: Number(server.maxPlayers),
    map: server.map,
  }
}

// The card's answers as serverDZ.cfg keys, the same as server_values in
// quick_setup.py: a key the container's environment sets is left out.
function serverCfgValues(server, locked) {
  const values = {
    hostname: server.hostname.trim(),
    password: server.passwordOn ? server.password : '',
    passwordAdmin: server.passwordAdmin,
    maxPlayers: Number(server.maxPlayers),
  }
  return Object.fromEntries(Object.entries(values).filter(([key]) => !locked[key]))
}

// The first questions of My server, answered before Apply. The job writes them
// right after the install, so a new server starts with its own name and passwords.
// Save writes them at once on an installed server, without a difficulty or mods.
function YourServerCard({ server, onChange, maps, locked, disabled, installed, saving, onSave }) {
  const set = (key, value) => onChange({ ...server, [key]: value })
  const missing = serverMissing(server, locked)
  const dlcNote = maps.find((m) => m.name === server.map)?.dlc_note
  return (
    <div className="card quick-setup-server">
      <h2>Your server</h2>
      <p>Answer these first. Apply writes them into the server when it is installed, and you can change them later on My server.</p>
      <ul className="my-server-list">
        <Question label="What is the server called?" hint={locked.hostname || 'Players see this name in the server browser.'}>
          <input
            id="qs-hostname"
            className="text-input"
            aria-label="Server name"
            disabled={disabled || !!locked.hostname}
            value={server.hostname}
            onChange={(e) => set('hostname', e.target.value)}
          />
        </Question>
        <Question label="Does it need a password?" hint="Players type this password to join.">
          <Switch
            checked={server.passwordOn}
            disabled={disabled}
            label="Players need a password"
            onChange={(on) => set('passwordOn', on)}
          >
            Players need a password
          </Switch>
          {server.passwordOn && (
            <PasswordField
              id="qs-join-password"
              aria-label="Join password"
              disabled={disabled}
              value={server.password}
              onChange={(e) => set('password', e.target.value)}
            />
          )}
        </Question>
        <Question
          label="What is your in-game admin password?"
          hint="Only you use this one, to log in as an admin inside the game. Players never need it."
        >
          <PasswordField
            id="qs-admin-password"
            aria-label="In-game admin password"
            disabled={disabled}
            value={server.passwordAdmin}
            onChange={(e) => set('passwordAdmin', e.target.value)}
          />
        </Question>
        <Question label="How many players?" hint={PLAYER_COUNT_HINT}>
          <input
            className="text-input text-input-narrow"
            type="number"
            min={1}
            id="qs-max-players"
            aria-label="Maximum players"
            disabled={disabled}
            value={server.maxPlayers}
            onChange={(e) => set('maxPlayers', e.target.value)}
          />
        </Question>
        <Question
          label="Which map runs?"
          hint="Bohemia's maps, which come with the server. Other maps are on the Maps page after the setup."
        >
          <Select
            aria-label="Map"
            disabled={disabled || maps.length === 0}
            value={server.map}
            onChange={(v) => set('map', v)}
            options={maps.map((m) => ({ value: m.name, label: m.dlc ? `${m.name} (${m.dlc} DLC)` : m.name }))}
          />
          {dlcNote && <div className="banner banner-warning">{dlcNote}</div>}
        </Question>
      </ul>
      <div className="inline-form">
        <button
          className="btn btn-primary"
          disabled={disabled || saving || !installed || missing.length > 0}
          onClick={onSave}
        >
          Save
        </button>
      </div>
      {missing.length > 0 && (
        <p className="hint">
          Save and Apply unlock when you add <MissingFields missing={missing} />.
        </p>
      )}
      {installed === false && (
        <p className="hint">Save unlocks when the server is installed. Until then, Apply writes these answers.</p>
      )}
      {installed && missing.length === 0 && (
        <p className="hint">Save writes the name, the passwords and the player count now. The map changes with Apply.</p>
      )}
    </div>
  )
}

// What Apply does, in the order the job does it.
function applySteps({ installed, mods, preset, start, map }) {
  const steps = []
  if (!installed) steps.push('Install the DayZ server files. This download is several GB.')
  steps.push(`Set the map to ${map}.`)
  if (mods.length > 0) {
    steps.push(`Install ${mods.join(', ')} from the Steam Workshop with your Steam account.`)
  }
  steps.push(`Apply the ${preset} difficulty. Zedstead saves a copy of the files first.`)
  steps.push('Write the server name, the passwords and the player count.')
  if (start) steps.push('Start the server and watch it boot.')
  return steps
}

// The preview without the preset's player count: the card's answer is written
// after the preset, so that row would never land.
function withoutPlayerCount(preview) {
  const changes = preview.changes.filter((c) => !(c.file === 'serverDZ.cfg' && c.parameter === 'maxPlayers'))
  return { ...preview, changes }
}

// The difficulty list and what the chosen preset changes. The list shows for
// the default difficulty too, without a pick. A server that is not installed
// has no mission files, so it has nothing to show. A finished job
// (`jobResult`) loads the list again, because the files changed.
function DifficultyPicker({ presets, value, onChange, installed, jobResult, disabled, cardSetsPlayers }) {
  const [preview, setPreview] = useState(null)
  const pushToast = useToast()

  useEffect(() => {
    setPreview(null)
    if (!installed || !value) return undefined
    // A slow answer for an earlier choice must not replace this one.
    let current = true
    api
      .post(`/api/presets/${value}/preview`)
      .then((data) => current && setPreview(data))
      .catch((err) => current && pushToast(err.message || 'Preview failed', 'error'))
    return () => {
      current = false
    }
  }, [value, installed, jobResult, pushToast])

  return (
    <>
      <div className="inline-form">
        <Select
          aria-label="Difficulty"
          value={value}
          onChange={onChange}
          disabled={disabled}
          options={presets.map((p) => ({ value: p.id, label: p.name, description: p.description }))}
        />
      </div>
      {installed === false && (
        <p className="hint">The server is not installed yet, so Zedstead cannot list the file changes before Apply.</p>
      )}
      {preview && (
        <>
          <h3 className="quick-setup-preview-title">What changes?</h3>
          <PresetPreview preview={cardSetsPlayers ? withoutPlayerCount(preview) : preview} />
        </>
      )}
    </>
  )
}

// One rolled mod with its Workshop page, where the admin reads the author's terms.
function ModRow({ mod }) {
  return (
    <li>
      <a href={mod.url} target="_blank" rel="noreferrer">
        {mod.name}
      </a>
      {mod.blurb && <span className="hint"> {mod.blurb}</span>}
    </li>
  )
}

// The rolled mod set, its dependencies, and the button that picks another set.
// The page shows it only when the map has a set (`modsOn`).
function ModdedBody({ offer, onRoll, disabled }) {
  const modsLink = resolveHelpSlug('mods')
  const { mods, dependencies, dayz_version: version, tested_on: testedOn } = offer.set
  return (
    <>
      <ul className="quick-setup-mods">
        {mods.map((m) => (
          <ModRow key={m.workshop_id} mod={m} />
        ))}
      </ul>
      {dependencies.length > 0 && (
        <p className="hint">
          These mods also need:{' '}
          {dependencies.map((d, i) => (
            <span key={d.workshop_id}>
              {i > 0 && ', '}
              <a href={d.url} target="_blank" rel="noreferrer">
                {d.name}
              </a>
            </span>
          ))}
        </p>
      )}
      <p className="hint">
        {testedOn ? `Last tested on DayZ ${version}, ${testedOn}.` : 'Test candidate: this set has no boot test yet.'}{' '}
        A mod update can change this. Each author sets the terms for their mod on its Workshop page.
      </p>
      {offer.outside.length > 0 && (
        <div className="banner banner-warning">
          Your mod list already holds {offer.outside.join(', ')}, so Zedstead does not add a random set to it. Use the{' '}
          <Link to={modsLink}>Mods page</Link>.
        </div>
      )}
      {offer.resume ? (
        <p className="hint">The last setup stopped before it finished, so this card keeps the same mods.</p>
      ) : (
        <button className="btn btn-ghost btn-small" onClick={onRoll} disabled={disabled}>
          Pick other mods
        </button>
      )}
    </>
  )
}

// The last setup's result. A setup that worked while the admin stayed on this page
// sends them to My server instead, so the success line shows only after they left.
function ResultBanner({ result }) {
  if (result.status === 'cancelled') {
    return <div className="banner banner-warning">Setup stopped. Press Apply again to continue.</div>
  }
  if (result.status !== 'success') {
    return <div className="banner banner-error">Setup stopped: {result.error}. Press Apply again to retry.</div>
  }
  const boot = result.boot
  if (boot && BOOT_FAILED.has(boot.outcome)) {
    return (
      <div className="banner banner-error">
        Setup finished, but the server did not boot. {boot.detail} Read the{' '}
        <Link to={resolveHelpSlug('mods?tab=Conflicts')}>mod conflicts</Link> for the cause.
      </div>
    )
  }
  return (
    <div className="banner banner-success">
      Setup is done. Change more settings on <Link to={resolveHelpSlug('my-server')}>My server</Link>.
    </div>
  )
}

// The newest finished Quick setup job of this instance in the Activity list, as a result.
function lastSetupResult(jobs, instanceId) {
  const job = jobs
    .filter((j) => j.kind === 'quick_setup' && TERMINAL_STATUSES.has(j.status))
    .filter((j) => j.instance == null || j.instance === instanceId)
    .reduce((newest, j) => (!newest || (j.created_at ?? 0) > (newest.created_at ?? 0) ? j : newest), null)
  return job && { id: job.id, status: job.status, error: job.error, boot: job.result?.boot }
}

// A setup that finished and, if it started the server, saw it boot.
function setupWorked(result) {
  return result.status === 'success' && !(result.boot && BOOT_FAILED.has(result.boot.outcome))
}

export default function QuickSetupPage() {
  const [presets, setPresets] = useState([])
  const [installed, setInstalled] = useState(null)
  const [offer, setOffer] = useState(null)
  // The answers from the last visit in this page load (`kept`), or none.
  const [initial] = useState(() => kept)
  const [preset, setPreset] = useState(initial?.preset ?? DEFAULT_PRESET)
  // Mods are an extra: off unless a stopped setup with mods is to be finished.
  const [withMods, setWithMods] = useState(initial?.withMods ?? false)
  const [pending, setPending] = useState(null) // { modIds, modNames, preset } while the dialog is open
  const [start, setStart] = useState(true)
  const [server, setServer] = useState(initial?.server ?? DEFAULT_SERVER)
  const [saving, setSaving] = useState(false)
  const [confirmOff, setConfirmOff] = useState(false)
  const [confirmOn, setConfirmOn] = useState(false)
  const { quickSetupDismissed, setQuickSetupDismissed } = useRookie()
  // Set by the admin's first edit in the card, so a late load never overwrites it.
  const serverTouched = useRef(initial?.touched ?? false)
  const pushToast = useToast()
  const navigate = useNavigate()
  // The job's callback outlives the page, so a redirect happens only while the admin is still here.
  const mounted = useRef(true)
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])
  const { startJob, jobs, jobRunning, focusJob } = useJobs()
  const { markDone } = useGuide()
  // The server runs one job at a time, so any running job locks the setup.
  const setupJob = jobs.find((j) => j.kind === 'quick_setup' && RUNNING_STATUSES.has(j.status))
  // A server that is set up gets a new instance for a second server, not a second run.
  const setUp = useQuickSetupState()?.offered === false
  const busy = jobRunning || setUp
  // Read from the Activity list, so that the result is still here after the admin left and came back.
  const { instance } = useRole()
  // A success is stale when the server is gone since (a deleted folder, a Docker recreate).
  const last = lastSetupResult(jobs, instance?.id)
  const result = last && !(last.status === 'success' && installed === false) ? last : null
  const canApply = useCan('admin')

  const loadOffer = useCallback(
    async (params = '') => {
      try {
        setOffer(await api.get(`/api/quick-setup/mods${params}`))
      } catch (err) {
        pushToast(err.message || 'Failed to load the mods', 'error')
      }
    },
    [pushToast]
  )

  const loadInstalled = useCallback(async () => {
    try {
      const data = await api.get('/api/setup-state')
      setInstalled(!!data.steps?.install?.done)
    } catch (err) {
      pushToast(err.message || 'Failed to read the server state', 'error')
    }
  }, [pushToast])

  useEffect(() => {
    api
      .get('/api/presets')
      .then((all) => setPresets(all.filter((p) => p.is_builtin)))
      .catch((err) => pushToast(err.message || 'Failed to load presets', 'error'))
    // A kept map offers its own sets again.
    loadOffer(initial?.server.map ? `?map=${encodeURIComponent(initial.server.map)}` : '')
  }, [initial, loadOffer, pushToast])

  // Keep the answers for the next visit in this page load.
  useEffect(() => {
    kept = { server, preset, withMods, touched: serverTouched.current }
  }, [server, preset, withMods])

  // Also after every job, so that an install from the card above shows at once.
  useEffect(() => {
    if (!jobRunning) loadInstalled()
  }, [jobRunning, loadInstalled])

  // A setup that stopped with mods in the list finishes with the same mods.
  useEffect(() => {
    if (offer?.resume) setWithMods(true)
  }, [offer?.resume])

  // The card starts on the map the server runs, or DayZ's stock map.
  useEffect(() => {
    if (offer?.map && !server.map) setServer((prev) => ({ ...prev, map: offer.map }))
  }, [offer, server.map])

  // An installed server fills the card with its own answers, as My server shows them.
  useEffect(() => {
    if (!installed || serverTouched.current) return
    api
      .get('/api/configs/serverdz')
      .then((config) => {
        if (serverTouched.current) return
        const answers = serverAnswers(fieldsByKey(config))
        setServer((prev) => ({ ...prev, ...answers, maxPlayers: answers.maxPlayers || prev.maxPlayers }))
      })
      .catch((err) => pushToast(err.message || 'Failed to read the server settings', 'error'))
  }, [installed, pushToast])

  // A new map in the card offers the sets tested on that map.
  function changeServer(next) {
    serverTouched.current = true
    if (next.map !== server.map) loadOffer(`?map=${encodeURIComponent(next.map)}`)
    setServer(next)
  }

  // A new seed, and the set on screen, so that the button always shows a change.
  function rollAgain() {
    const previous = offer.set.mods.map((m) => `previous=${m.workshop_id}`).join('&')
    loadOffer(`?seed=${offer.seed + 1}&${previous}&map=${encodeURIComponent(server.map)}`)
  }

  function presetName(id) {
    return presets.find((p) => p.id === id)?.name || id
  }

  async function handleApply() {
    const { modIds, preset } = pending
    setPending(null)
    try {
      await startJob({
        endpoint: '/api/quick-setup/apply',
        body: { preset, mod_ids: modIds, start, server: serverBody(server) },
        name: 'Quick setup',
        kind: 'quick_setup',
        onStatusChange: async (status, job) => {
          if (RUNNING_STATUSES.has(status)) return
          let full = job
          try {
            full = await api.get(`/api/jobs/${job.id}`)
          } catch {
            // The status alone still gives the admin a result line.
          }
          const done = { status, error: full?.error, boot: full?.result?.boot }
          // The card's map is the admin's pick, Chernarus included.
          if (status === 'success') markDone('maps')
          // A finished setup has nothing left to keep.
          if (setupWorked(done)) kept = null
          if (setupWorked(done) && mounted.current) {
            navigate(resolveHelpSlug('my-server'), { state: { setupDone: true } })
            return
          }
          loadInstalled()
          loadOffer()
        },
      })
    } catch (err) {
      pushToast(err.message || 'Setup could not start', 'error')
    }
  }

  // The card's Save: its answers into serverDZ.cfg, the same save as My server's.
  async function saveServer() {
    setSaving(true)
    try {
      await api.put('/api/configs/serverdz', { values: serverCfgValues(server, offer?.locked || {}) })
      pushToast('Saved. A running server uses the new answers after its next restart.', 'info')
    } catch (err) {
      pushToast(err.message || 'Save failed', 'error')
    } finally {
      setSaving(false)
    }
  }

  // "Turn off these mods" (a quick backup of the player data, then every loaded
  // mod off) and its way back, "Turn them on again". The mods stay installed.
  async function switchMods(on) {
    setConfirmOff(false)
    setConfirmOn(false)
    try {
      await startJob({
        endpoint: on ? '/api/quick-setup/mods-on' : '/api/quick-setup/mods-off',
        name: on ? 'Turn on mods' : 'Turn off mods',
        kind: on ? 'mods_on' : 'mods_off',
        onStatusChange: (status) => {
          if (!RUNNING_STATUSES.has(status)) loadOffer(`?map=${encodeURIComponent(server.map)}`)
        },
      })
    } catch (err) {
      pushToast(err.message || `Could not turn ${on ? 'on' : 'off'} the mods`, 'error')
    }
  }

  // Out of the menu, and every page shown unless the admin made an own menu.
  function dismiss() {
    setQuickSetupDismissed(true)
    pushToast('Quick setup is hidden. Search for it to open it again.', 'info')
    navigate('/')
  }

  const modded = offer?.set
  // No set has passed a boot test on this map, so the Mods switch is hidden and off.
  const noSets = !!offer && !modded
  const modsOn = withMods && !!modded
  const turnedOff = offer?.turned_off || []
  const locked = offer?.locked || {}
  const missing = serverMissing(server, locked)
  if (!server.map) missing.push({ label: 'a map' })
  // Why Apply is off, in words beside the button, or '' when it is on.
  const applyReason = busy
    ? 'Apply unlocks when the running job is done.'
    : missing.length > 0
      ? (
          <>
            Add <MissingFields missing={missing} /> under Your server to unlock Apply.
          </>
        )
      : modsOn && offer.outside.length > 0
          ? 'Your mod list holds other mods. Use the Mods page.'
          : !modsOn && offer?.vanilla_refusal
            ? 'Turn off the mods your server loads to unlock Apply.'
            : ''

  // The one Apply: with the rolled set when mods are on, else with none.
  function openApply() {
    const mods = modsOn ? modded.mods : []
    setPending({ modIds: mods.map((m) => m.workshop_id), modNames: mods.map((m) => m.name), preset })
  }

  return (
    <div className="page quick-setup-page">
      <div className="page-header">
        <h1>Quick setup</h1>
        {canApply && !setUp && !quickSetupDismissed && (
          <button className="btn btn-ghost btn-small" onClick={dismiss}>
            <HiOutlineXMark aria-hidden /> Dismiss
          </button>
        )}
      </div>
      <PageIntro page="/quick-setup" />
      {quickSetupDismissed && !setUp && (
        <div className="banner banner-info quick-setup-mods-off">
          <span>Quick setup is hidden from the menu. You can still use it here.</span>
          <button className="btn btn-ghost btn-small" onClick={() => setQuickSetupDismissed(false)}>
            Show it in the menu again
          </button>
        </div>
      )}
      <SteamLoginGate />
      {setUp && !jobRunning ? (
        <>
          <div className="banner banner-info">
            This server is set up already. Change its settings on{' '}
            <Link to={resolveHelpSlug('my-server')}>My server</Link>. To set up another server, add an instance on
            the <Link to={resolveHelpSlug('instances')}>Instances</Link> page and open Quick setup there.
          </div>
          {/* A setup that ended with a server that did not start says so here too. */}
          {result && !setupWorked(result) && <ResultBanner result={result} />}
        </>
      ) : busy ? (
        <div className="banner banner-info quick-setup-busy">
          <span>
            {setupJob
              ? 'Quick setup is running. You can follow each step in Activity. The setup unlocks when it is done.'
              : 'Another job is running. The setup unlocks when it is done.'}
          </span>
          {setupJob && (
            <button className="btn btn-ghost btn-small" onClick={() => focusJob(setupJob.id)}>
              Show progress
            </button>
          )}
        </div>
      ) : (
        result && <ResultBanner result={result} />
      )}
      {canApply && !setUp && (
        <InstallServerCard
          job={INSTALL_ONLY_JOB}
          done="The DayZ server is installed. Answer the questions below, pick a difficulty and press Apply."
        />
      )}
      {canApply && !setUp && (
        <YourServerCard
          server={server}
          onChange={changeServer}
          maps={offer?.maps || []}
          locked={locked}
          disabled={busy}
          installed={installed}
          saving={saving}
          onSave={saveServer}
        />
      )}

      {/* A finished setup has nothing to apply: the difficulty changes on the Presets page. */}
      {!setUp && (
        <div className={`quick-setup-cards${busy ? ' is-busy' : ''}`} aria-busy={busy}>
          <div className="card">
            <h2>Difficulty and mods</h2>
            <p>Pick a difficulty. The server runs as Bohemia made it, and players need only the game.</p>
            <DifficultyPicker
              presets={presets}
              value={preset}
              onChange={setPreset}
              installed={installed}
              jobResult={result?.id}
              disabled={busy}
              cardSetsPlayers={canApply && !setUp && !locked.maxPlayers}
            />
            <h3 className="quick-setup-preview-title">Mods</h3>
            {noSets ? (
              <p className="hint">
                No mod set has passed a boot test on {offer.map || 'this map'} yet, so the server runs with no mods.
                You can add mods later on the <Link to={resolveHelpSlug('mods')}>Mods page</Link>.
              </p>
            ) : (
              <>
                <Switch checked={withMods} disabled={busy} onChange={setWithMods} label="Add a few mods">
                  Add a few mods
                </Switch>
                <p className="hint">
                  {withMods
                    ? 'Zedstead picks a set that booted in a test. Players download the same mods when they join.'
                    : 'Off: the server runs with no mods. You can add mods later on the Mods page.'}
                </p>
              </>
            )}
            {modsOn && <ModdedBody offer={offer} onRoll={rollAgain} disabled={busy} />}
            {!modsOn && offer?.vanilla_refusal && (
              <div className="banner banner-warning quick-setup-mods-off">
                <span>
                  {offer.vanilla_refusal} <Link to={resolveHelpSlug('mods')}>Open the Mods page</Link>.
                </span>
                {canApply && (
                  <button className="btn btn-ghost btn-small" disabled={busy} onClick={() => setConfirmOff(true)}>
                    Turn off these mods
                  </button>
                )}
              </div>
            )}
            {turnedOff.length > 0 && (
              <div className="banner banner-info quick-setup-mods-off">
                <span>
                  Quick setup turned off {turnedOff.length} {turnedOff.length === 1 ? 'mod' : 'mods'}. They stay
                  installed until you turn them on again.
                </span>
                {canApply && (
                  <button className="btn btn-ghost btn-small" disabled={busy} onClick={() => setConfirmOn(true)}>
                    Turn them on again
                  </button>
                )}
              </div>
            )}
            {canApply && (
              <div className="inline-form">
                <NeedsSteam>
                  <button className="btn btn-primary" disabled={!!applyReason} onClick={openApply}>
                    {modsOn && offer?.resume ? 'Continue setup' : 'Apply'}
                  </button>
                </NeedsSteam>
              </div>
            )}
            {canApply && applyReason && <p className="hint">{applyReason}</p>}
          </div>
        </div>
      )}

      <ConfirmModal
        open={confirmOff}
        title="Turn off these mods?"
        confirmLabel="Turn off"
        onConfirm={() => switchMods(false)}
        onCancel={() => setConfirmOff(false)}
      >
        <ol className="quick-setup-steps">
          <li>
            Take a quick backup of the player data first, the same as Create on the{' '}
            <Link to={resolveHelpSlug('backups')}>Backups</Link> page. If the backup fails, no mod is turned off.
          </li>
          <li>Turn off every mod the server loads. The mods stay installed, and the Mods page turns them on again.</li>
        </ol>
        <p className="hint">
          Items, vehicles and buildings that come from these mods leave the world at the next start. The backup holds them.
        </p>
      </ConfirmModal>

      <ConfirmModal
        open={confirmOn}
        title="Turn these mods on again?"
        confirmLabel="Turn on"
        onConfirm={() => switchMods(true)}
        onCancel={() => setConfirmOn(false)}
      >
        <p>Zedstead turns on the mods that Quick setup turned off, in the order of your mod list:</p>
        <p className="hint">{turnedOff.join(', ')}</p>
        <p className="hint">
          Their mod XML goes back into the mission. Items that left the world at a start without them come back only
          from the quick backup on the <Link to={resolveHelpSlug('backups')}>Backups</Link> page.
        </p>
      </ConfirmModal>

      <ConfirmModal
        open={pending !== null}
        title="Apply this setup?"
        confirmLabel="Apply"
        onConfirm={handleApply}
        onCancel={() => setPending(null)}
      >
        {pending && (
          <>
            <ol className="quick-setup-steps">
              {applySteps({
                installed,
                mods: pending.modNames,
                preset: presetName(pending.preset),
                start,
                map: server.map,
              }).map((step) => (
                <li key={step}>{step}</li>
              ))}
            </ol>
            <Switch checked={start} onChange={setStart} label="Start the server when setup is done">
              Start the server when setup is done
            </Switch>
            <p className="hint">Players can join a started server. Turn this off to change more settings on My server first.</p>
          </>
        )}
      </ConfirmModal>
    </div>
  )
}
