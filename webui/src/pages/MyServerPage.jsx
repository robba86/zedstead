// My server: the settings a new admin asks about first, as plain questions. It
// has no route of its own. It reads and saves through the serverDZ editor, the
// quick tweaks and the Scheduler, so their checks and snapshots stay in one place.
// It sends only what the admin changed, so a value the admin did not touch keeps
// its own text in the file. Each question links to its full page.
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { api } from '../api'
import { Switch } from '../components/FlagSwitch.jsx'
import PageIntro from '../components/PageIntro.jsx'
import PasswordField from '../components/PasswordField.jsx'
import Select from '../components/Select.jsx'
import Question from '../components/SettingQuestion.jsx'
import StickyActionBar from '../components/StickyActionBar.jsx'
import { useToast } from '../components/Toast.jsx'
import { editorLink } from '../crossLinks.js'
import { resolveHelpSlug } from '../helpLinks.js'
import { useJobs } from '../jobs'
import { PLAYER_COUNT_HINT, fieldValue, fieldsByKey, serverAnswers } from '../serverAnswers.js'
import { useServerStatus } from '../serverStatus'

const TWEAK_QUESTIONS = [
  { id: 'zombie_count', question: 'How many zombies?' },
  { id: 'food_amount', question: 'How much food?' },
  { id: 'vehicle_count', question: 'How many vehicles?' },
]
const CUSTOM = 'custom'
const NO_RESTART = 'off'
const DAY_SPEEDS = [1, 2, 3, 4, 6, 8, 12, 24]
const NIGHT_SPEEDS = [1, 2, 4, 8, 16, 32]
const DAILY_CRON = /^(\d+) (\d+) \* \* \*$/

function dayLabel(speed) {
  const hours = Math.round((24 / speed) * 100) / 100
  return `A game day lasts ${hours} ${hours === 1 ? 'hour' : 'hours'}`
}

function nightLabel(speed) {
  return speed === 1 ? 'Nights pass at the same speed as days' : `Nights pass ${speed} times faster than days`
}

// The list of speeds, plus the file's own value when it is a hand-edited one.
function speedOptions(speeds, current, label) {
  const all = speeds.includes(current) ? speeds : [current, ...speeds]
  return all.map((speed) => ({ value: String(speed), label: label(speed) }))
}

// The simple case only: no restart task, or one enabled daily restart. Any other
// schedule is Custom, and this page never overwrites it.
function readRestart(tasks) {
  const restarts = tasks.filter((t) => t.task_type === 'restart')
  if (restarts.length === 0) return { value: NO_RESTART, task: null }
  const daily = restarts.length === 1 && restarts[0].enabled ? DAILY_CRON.exec(restarts[0].cron) : null
  if (!daily) return { value: CUSTOM, task: null }
  return { value: `${Number(daily[2])}:${Number(daily[1])}`, task: restarts[0] }
}

function clock(value) {
  const [hour, minute] = value.split(':')
  return `${hour.padStart(2, '0')}:${minute.padStart(2, '0')}`
}

function restartOptions(current) {
  const hours = Array.from({ length: 24 }, (_, hour) => `${hour}:0`)
  const times = hours.includes(current) || current === NO_RESTART ? hours : [current, ...hours]
  return [{ value: NO_RESTART, label: 'No daily restart' }, ...times.map((t) => ({ value: t, label: clock(t) }))]
}

// The tier the tweak is on now. An empty answer means no mission to read: 1x.
function currentTier(tweak) {
  const current = tweak.current?.[tweak.variants[0]?.key ?? '']
  if (current === null) return CUSTOM
  return String(current ?? 1)
}

// What a night speed means in real time: the day speed times the night speed.
function nightHint(day, night) {
  const total = Number(day) * Number(night)
  return `So a night runs ${total} ${total === 1 ? 'time' : 'times'} faster than real time. The length of a night also changes with the map and the season.`
}

// The messages of the day as the text box shows them: one message per line.
function motdText(fields) {
  return (fields.motd?.value ?? []).join('\n')
}

// The text box's lines as motd[] items. A blank line is no message.
function motdLines(text) {
  return text.split('\n').filter((line) => line.trim())
}

function numberOr(value, fallback) {
  const number = Number(value)
  return value == null || value === '' || Number.isNaN(number) ? fallback : number
}

// Turn what the three endpoints answer into the form's values.
function toForm(fields, tweaks, tasks) {
  const value = (key) => fieldValue(fields, key)
  return {
    ...serverAnswers(fields),
    // DayZ 1.30 ships thirdPersonView in place of disable3rdPerson. An older
    // server ignores the new key, and Bohemia does not document how the two
    // combine, so either one can make the server first person only.
    firstPerson: fields.disable3rdPerson?.value === true || fields.thirdPersonView?.value === 0,
    noCrosshair: !!fieldValue(fields, 'disableCrosshair'),
    keepTime: !!fieldValue(fields, 'serverTimePersistent'),
    motd: motdText(fields),
    day: String(numberOr(value('serverTimeAcceleration'), 1)),
    night: String(numberOr(value('serverNightTimeAcceleration'), 1)),
    tweaks: Object.fromEntries(tweaks.map((t) => [t.id, currentTier(t)])),
    restart: readRestart(tasks).value,
  }
}

export default function MyServerPage() {
  const [installed, setInstalled] = useState(null)
  const [fields, setFields] = useState({})
  const [tweaks, setTweaks] = useState([])
  const [schedule, setSchedule] = useState(null)
  const [loaded, setLoaded] = useState(null)
  const [form, setForm] = useState(null)
  const [saved, setSaved] = useState(false)
  const pushToast = useToast()
  const { startJob } = useJobs()
  const { status } = useServerStatus()
  const running = !!status?.running
  // Set when Quick setup just finished and sent the admin here. The banner text follows the live status.
  const setupDone = useLocation().state?.setupDone

  const load = useCallback(async () => {
    try {
      const setup = await api.get('/api/setup-state')
      const isInstalled = !!setup.steps?.install?.done
      setInstalled(isInstalled)
      if (!isInstalled) return
      const [config, allTweaks, sched] = await Promise.all([
        api.get('/api/configs/serverdz'),
        api.get('/api/tweaks'),
        api.get('/api/schedule'),
      ])
      const byKey = fieldsByKey(config)
      const asked = allTweaks.filter((t) => TWEAK_QUESTIONS.some((q) => q.id === t.id))
      const next = toForm(byKey, asked, sched.tasks)
      setFields(byKey)
      setTweaks(asked)
      setSchedule(sched)
      setLoaded(next)
      setForm(next)
    } catch (err) {
      pushToast(err.message || 'Failed to load your server settings', 'error')
    }
  }, [pushToast])

  useEffect(() => {
    load()
  }, [load])

  const set = (key, value) => setForm((prev) => ({ ...prev, [key]: value }))
  const dirty = useMemo(() => JSON.stringify(form) !== JSON.stringify(loaded), [form, loaded])

  // Only what changed goes to the serverDZ editor.
  function changedConfig() {
    const values = {}
    if (form.hostname !== loaded.hostname) values.hostname = form.hostname
    const password = form.passwordOn ? form.password : ''
    const before = loaded.passwordOn ? loaded.password : ''
    if (password !== before) values.password = password
    if (form.passwordAdmin !== loaded.passwordAdmin) values.passwordAdmin = form.passwordAdmin
    if (form.maxPlayers !== loaded.maxPlayers) values.maxPlayers = Number(form.maxPlayers)
    // thirdPersonView alone: the save takes the old disable3rdPerson out of
    // the file (servercfg.REPLACED_CFG_KEYS).
    if (form.firstPerson !== loaded.firstPerson) values.thirdPersonView = form.firstPerson ? 0 : 1
    if (form.noCrosshair !== loaded.noCrosshair) values.disableCrosshair = form.noCrosshair
    if (form.day !== loaded.day) values.serverTimeAcceleration = Number(form.day)
    if (form.night !== loaded.night) values.serverNightTimeAcceleration = Number(form.night)
    if (form.keepTime !== loaded.keepTime) values.serverTimePersistent = form.keepTime
    if (form.motd !== loaded.motd) values.motd = motdLines(form.motd)
    return values
  }

  async function saveRestart() {
    const { task } = readRestart(schedule.tasks)
    if (form.restart === NO_RESTART) {
      await api.del(`/api/schedule/${encodeURIComponent(task.id)}`)
    } else {
      const [hour, minute] = form.restart.split(':')
      const cron = `${minute} ${hour} * * *`
      if (task) {
        await api.put(`/api/schedule/${encodeURIComponent(task.id)}`, { cron })
      } else {
        await api.post('/api/schedule', { id: 'restart_daily', task_type: 'restart', cron, enabled: true, options: {} })
      }
    }
    try {
      await startJob({ endpoint: '/api/schedule/install-timers', name: 'Install timers', kind: 'timers_install' })
    } catch (err) {
      pushToast(`The restart is saved, but its timer is not installed: ${err.message} Open the Scheduler page.`, 'error')
    }
  }

  async function save() {
    setSaved(false)
    try {
      const values = changedConfig()
      if (Object.keys(values).length > 0) await api.put('/api/configs/serverdz', { values })
      for (const { id } of TWEAK_QUESTIONS) {
        if (form.tweaks[id] !== loaded.tweaks[id]) {
          await api.post(`/api/tweaks/${id}`, { multiplier: Number(form.tweaks[id]) })
        }
      }
      if (form.restart !== loaded.restart) await saveRestart()
      setSaved(true)
      pushToast('Saved.', 'info')
    } catch (err) {
      pushToast(err.message || 'Save failed', 'error')
    }
    // Also after a failure, so that the form shows what the files hold now.
    await load()
  }

  const header = (
    <>
      <div className="page-header">
        <h1>My server</h1>
      </div>
      <PageIntro page="/my-server" />
    </>
  )

  if (installed === false) {
    return (
      <div className="page my-server-page">
        {header}
        <div className="banner banner-warning">
          The server is not installed yet, so it has no settings to change. Start with{' '}
          <Link to={resolveHelpSlug('quick-setup')}>Quick setup</Link>.
        </div>
      </div>
    )
  }
  if (!form) return <div className="page my-server-page">{header}</div>

  const serverdz = editorLink('serverdz')
  const fullEditor = 'Full editor'
  const locked = (key) => running || !!fields[key]?.locked
  const restartInfo = readRestart(schedule.tasks)
  const lockedNote = (key) => fields[key]?.locked_note

  return (
    <div className="page my-server-page">
      {header}
      {setupDone && !saved && (
        <div className="banner banner-success my-server-banner">
          <span>
            {running
              ? 'Quick setup is done, and your server is up. Players can join it now. To change a setting below, stop the server first with Stop at the top.'
              : 'Quick setup is done, and the server is stopped. Change more settings below and press Save, or start it with Start at the top.'}
          </span>
        </div>
      )}
      {running && !setupDone && (
        <div className="banner banner-warning my-server-banner">
          <span>Stop the server to change these settings. Stop is at the top of the page.</span>
        </div>
      )}
      {saved && !running && !dirty && (
        <div className="banner banner-success my-server-banner">
          <span>Saved. The server uses the new settings when it starts.</span>
        </div>
      )}
      <ul className="my-server-list card">
        <Question label="What is the server called?" hint={lockedNote('hostname')} to={serverdz} linkLabel={fullEditor}>
          <input
            className="text-input"
            aria-label="Server name"
            disabled={locked('hostname')}
            value={form.hostname}
            onChange={(e) => set('hostname', e.target.value)}
          />
        </Question>
        <Question
          label="Does it need a password?"
          hint="Players type this password to join."
          to={serverdz}
          linkLabel={fullEditor}
        >
          <Switch
            checked={form.passwordOn}
            disabled={locked('password')}
            label="Players need a password"
            onChange={(on) => set('passwordOn', on)}
          >
            Players need a password
          </Switch>
          {form.passwordOn && (
            <PasswordField
              aria-label="Join password"
              disabled={locked('password')}
              value={form.password}
              onChange={(e) => set('password', e.target.value)}
            />
          )}
        </Question>
        <Question
          label="What is your in-game admin password?"
          hint="Only you use this one, to log in as an admin inside the game. Players never need it."
          to={serverdz}
          linkLabel={fullEditor}
        >
          <PasswordField
            aria-label="In-game admin password"
            disabled={locked('passwordAdmin')}
            value={form.passwordAdmin}
            onChange={(e) => set('passwordAdmin', e.target.value)}
          />
        </Question>
        <Question
          label="How many players?"
          hint={lockedNote('maxPlayers') || PLAYER_COUNT_HINT}
          to={serverdz}
          linkLabel={fullEditor}
        >
          <input
            className="text-input text-input-narrow"
            type="number"
            aria-label="Maximum players"
            min={fields.maxPlayers?.min}
            max={fields.maxPlayers?.max}
            disabled={locked('maxPlayers')}
            value={form.maxPlayers}
            onChange={(e) => set('maxPlayers', e.target.value)}
          />
        </Question>
        <Question
          label="First person only?"
          hint={form.firstPerson ? 'Players see through their character’s eyes only.' : 'Players can also use the third-person camera.'}
          to={serverdz}
          linkLabel={fullEditor}
        >
          <Switch
            checked={form.firstPerson}
            disabled={locked('thirdPersonView')}
            label="First person only"
            onChange={(on) => set('firstPerson', on)}
          >
            First person only
          </Switch>
        </Question>
        <Question
          label="Hide the crosshair?"
          hint={
            form.noCrosshair
              ? 'Players aim without a dot in the middle of the screen. Together with first person only, this makes the hardest setup.'
              : 'Players see a small aiming dot in the middle of the screen.'
          }
          to={serverdz}
          linkLabel={fullEditor}
        >
          <Switch
            checked={form.noCrosshair}
            disabled={locked('disableCrosshair')}
            label="Hide the crosshair"
            onChange={(on) => set('noCrosshair', on)}
          >
            Hide the crosshair
          </Switch>
        </Question>
        <Question label="How fast is time?" to={serverdz} linkLabel={fullEditor}>
          <Select
            aria-label="Time speed"
            disabled={locked('serverTimeAcceleration')}
            value={form.day}
            onChange={(v) => set('day', v)}
            options={speedOptions(DAY_SPEEDS, Number(form.day), dayLabel)}
          />
        </Question>
        <Question label="How fast is night?" hint={nightHint(form.day, form.night)} to={serverdz} linkLabel={fullEditor}>
          <Select
            aria-label="Night speed"
            disabled={locked('serverNightTimeAcceleration')}
            value={form.night}
            onChange={(v) => set('night', v)}
            options={speedOptions(NIGHT_SPEEDS, Number(form.night), nightLabel)}
          />
        </Question>
        <Question
          label="Keep the time of day after a restart?"
          hint={
            form.keepTime
              ? 'The server saves the time of day and goes on from there at the next start.'
              : 'Each start begins at the time that serverDZ.cfg sets.'
          }
          to={serverdz}
          linkLabel={fullEditor}
        >
          <Switch
            checked={form.keepTime}
            disabled={locked('serverTimePersistent')}
            label="Keep the time of day"
            onChange={(on) => set('keepTime', on)}
          >
            Keep the time of day
          </Switch>
        </Question>
        <Question
          label="What do players read in the chat?"
          hint="One message per line, for example your rules or a link to your community. Leave it empty for no messages."
          to={serverdz}
          linkLabel={fullEditor}
        >
          <textarea
            className="text-input"
            rows={3}
            aria-label="Messages of the day"
            disabled={locked('motd')}
            value={form.motd}
            onChange={(e) => set('motd', e.target.value)}
          />
        </Question>
        {TWEAK_QUESTIONS.map(({ id, question }) => {
          const tweak = tweaks.find((t) => t.id === id)
          if (!tweak) return null
          const value = form.tweaks[id]
          return (
            <Question
              key={id}
              label={question}
              hint={value === CUSTOM ? 'Your files match no level here, so Zedstead leaves them as they are.' : undefined}
              to="/presets?tab=Quick+Tweaks"
              linkLabel="All tweaks"
            >
              <Select
                aria-label={tweak.name}
                disabled={running}
                value={value}
                onChange={(v) => set('tweaks', { ...form.tweaks, [id]: v })}
                options={[
                  ...(value === CUSTOM ? [{ value: CUSTOM, label: 'Custom' }] : []),
                  ...tweak.tiers.map((t) => ({ value: t.multiplier, label: t.name, description: t.description })),
                ]}
              />
            </Question>
          )
        })}
        <Question
          label="When does the server restart each day?"
          hint={
            restartInfo.value === CUSTOM
              ? 'This server has its own restart schedule. Change it on the Scheduler page.'
              : schedule.time_zone && `Times use the server’s clock (${schedule.time_zone}).`
          }
          to={resolveHelpSlug('scheduler')}
          linkLabel="Scheduler"
        >
          {restartInfo.value === CUSTOM ? (
            <span>Custom</span>
          ) : (
            <Select
              aria-label="Daily restart"
              disabled={running}
              value={form.restart}
              onChange={(v) => set('restart', v)}
              options={restartOptions(form.restart)}
            />
          )}
        </Question>
        <Question label="Which map runs?" to={resolveHelpSlug('maps')} linkLabel="Change on the Maps page">
          <span>{status?.map || 'Unknown'}</span>
        </Question>
      </ul>
      {!running && (
        <StickyActionBar dirty={dirty} onSave={save} onReset={() => setForm(loaded)} />
      )}
    </div>
  )
}
