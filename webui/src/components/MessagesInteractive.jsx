import Select, { withCurrent } from './Select.jsx'
import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import { useToast } from './Toast.jsx'
import StickyActionBar from './StickyActionBar.jsx'
import Tooltip, { tooltipAttrs } from './Tooltip.jsx'
import { useConfigSync } from '../configSync.js'
import { Link } from 'react-router-dom'
import PanelNote from './PanelNote.jsx'
import { DurationField } from './SmartView.jsx'

// A messages.xml time: the file keeps whole minutes, typed in the unit the
// admin picks (minutes, hours or days). `tip` is the box's tooltip, and a
// row's `placeholder` names the field while the box is empty.
function MinutesField({ label, value, onChange, tip, placeholder }) {
  return (
    <span className="msg-num">
      <DurationField
        label={label}
        base={60}
        value={value}
        onChange={onChange}
        inputProps={{ placeholder, ...(tip ? tooltipAttrs(tip, { keep: true }) : {}) }}
      />
    </span>
  )
}

// Scheduler tasks that stop the server on a clock. A Shutdown message is a
// restart timer of its own (the service starts the server again after it
// exits), so the two together restart on two clocks.
const RESTART_TASKS = ['restart', 'update']

// The enabled Scheduler tasks that restart the server, or null when the list
// cannot be read (a role below admin gets 403). Read once per mount.
function useScheduledRestarts() {
  const [count, setCount] = useState(null)
  useEffect(() => {
    let live = true
    api
      .get('/api/schedule')
      .then((d) => live && setCount((d.tasks || []).filter((t) => t.enabled && RESTART_TASKS.includes(t.task_type)).length))
      .catch(() => {})
    return () => {
      live = false
    }
  }, [])
  return count
}

// messages.xml's Edit view: groups the file by type — a single OnConnect
// welcome, a list of scheduled Repeat broadcasts, and a single Shutdown
// countdown. Reshapes the same {rows} the table endpoint expects, so it saves
// through the existing PUT /api/configs/messages with no extra backend. Each
// row carries its file id and every value the file gave it; the save starts
// from that message, so a card never drops what it does not show (2026 Q4:
// Shutdown lost its delay/repeat, Repeat and OnConnect their deadline, on any
// save).
//
// Every other message (a countdown without a shutdown, a second OnConnect or
// Shutdown) is a row of its own under "Other messages", with all five values
// and its type editable. Until 2026 Q4 those were only kept, and edited on
// a separate Basic tab; that tab is gone, so nothing in the file may be
// out of reach here. The field help is the table's column help.
const TYPES = ['OnConnect', 'Repeat', 'Shutdown', 'Other']
// What each type does, shown under it in the open Type list (no tooltip).
const TYPE_DESCRIPTIONS = {
  OnConnect: 'Shown to each player who connects, after the delay',
  Repeat: 'Sent to everyone at the repeat interval',
  Shutdown: 'A countdown that stops the server at zero',
  Other: 'A countdown that does not stop the server',
}
// "The maximum length of the message is 160 characters." (DayZ:Server
// Messages); the save refuses a longer one too.
const MAX_TEXT = 160
let _rk = 0
const rkey = () => `r${_rk++}`

export default function MessagesInteractive({ id }) {
  const [onConnect, setOnConnect] = useState({ text: '', delay: '0' })
  const [shutdown, setShutdown] = useState({ text: '', deadline: '600' })
  const [repeats, setRepeats] = useState([])
  const [others, setOthers] = useState([])
  const [help, setHelp] = useState({}) // column key -> help text
  const scheduledRestarts = useScheduledRestarts()
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [loaded, setLoaded] = useState(false)
  // The rows as the file last handed them over. The four states above are the
  // reshaped, edited version of these, so they are no answer to "did the file
  // change" — configSync compares against this instead.
  const [fileRows, setFileRows] = useState(null)
  const pushToast = useToast()

  const path = `/api/configs/${id}`

  const apply = useCallback((data) => {
    const rows = data.rows || []
    let ocUsed = false
    let sdUsed = false
    const reps = []
    const oth = []
    let oc = { text: '', delay: '0' }
    let sd = { text: '', deadline: '600' }
    for (const r of rows) {
      // Every row keeps all of the file's values and its id, not only the ones
      // its card shows: a save sends them back, so a Shutdown message's delay
      // or a Repeat message's deadline is not dropped by a save that never
      // touched it. The defaults below are for a message the file has not got.
      const v = { ...r.values, _id: r.id }
      if (v.type === 'OnConnect' && !ocUsed) {
        ocUsed = true
        oc = { ...v, text: v.text || '' }
      } else if (v.type === 'Shutdown' && !sdUsed) {
        sdUsed = true
        sd = { ...v, text: v.text || '' }
      } else if (v.type === 'Repeat') {
        reps.push({ ...v, _key: rkey(), text: v.text || '' })
      } else {
        oth.push({ ...v, _key: rkey() }) // Other, or extra singletons
      }
    }
    setOnConnect(oc)
    setShutdown(sd)
    setRepeats(reps)
    setOthers(oth)
    setHelp(Object.fromEntries((data.columns || []).map((c) => [c.key, c.help])))
    setFileRows(rows)
    setDirty(false)
  }, [])

  const load = useCallback(async () => {
    setLoaded(false)
    try {
      apply(await api.get(path))
      setLoaded(true)
    } catch (err) {
      pushToast(err.message || 'Failed to load', 'error')
    }
  }, [apply, path, pushToast])

  useEffect(() => {
    load()
  }, [load])

  const sync = useConfigSync({
    path,
    showing: fileRows,
    select: (d) => d.rows || [],
    dirty,
    busy: saving || !loaded,
    apply,
  })

  function touch() {
    setDirty(true)
  }

  function setSingleton(setter, key, value) {
    setter((prev) => ({ ...prev, [key]: value }))
    touch()
  }

  function setRepeat(k, key, value) {
    setRepeats((prev) => prev.map((r) => (r._key === k ? { ...r, [key]: value } : r)))
    touch()
  }

  function setOther(k, key, value) {
    setOthers((prev) => prev.map((r) => (r._key === k ? { ...r, [key]: value } : r)))
    touch()
  }

  function addOther() {
    setOthers((prev) => [...prev, { _key: rkey(), type: 'Other', text: '', delay: '', repeat: '', deadline: '' }])
    touch()
  }

  function removeOther(k) {
    setOthers((prev) => prev.filter((r) => r._key !== k))
    touch()
  }

  function addRepeat() {
    // No delay: DayZ applies a delay only to an OnConnect message.
    setRepeats((prev) => [...prev, { _key: rkey(), text: '', repeat: '5' }])
    touch()
  }

  function removeRepeat(k) {
    setRepeats((prev) => prev.filter((r) => r._key !== k))
    touch()
  }

  async function handleSave() {
    setSaving(true)
    try {
      // A row goes back with the id it came with, so the backend starts from
      // that message and keeps what no card shows.
      const toRow = ({ _key, _id, ...values }, type) => ({ id: _id, values: type ? { ...values, type } : values })
      const rows = []
      if (onConnect.text.trim()) rows.push(toRow(onConnect, 'OnConnect'))
      for (const r of repeats) {
        if (r.text.trim()) rows.push(toRow(r, 'Repeat'))
      }
      if (shutdown.text.trim()) rows.push(toRow(shutdown, 'Shutdown'))
      for (const o of others) {
        if ((o.text || '').trim()) rows.push(toRow(o))
      }
      // The file's order, new messages after it: the cards group by type, and
      // sending them in card order moved the file's messages on every save.
      const at = (r) => (r.id == null ? Infinity : Number(r.id))
      rows.sort((a, b) => at(a) - at(b))
      await api.put(`/api/configs/${id}`, { rows })
      pushToast('Saved.', 'info')
      load()
    } catch (err) {
      pushToast(err.message || 'Save failed', 'error')
    } finally {
      setSaving(false)
    }
  }

  if (!loaded) return <div className="form-editor" />

  return (
    <div className="form-editor">
      <div className="card">
        <h2>On Connect</h2>
        <PanelNote>Single welcome message shown when a player joins. Leave text empty to omit.</PanelNote>
        <ul className="step-list">
          <li>
            <Tooltip keep text={help.text}>Message text</Tooltip>
            <input
              className="text-input"
              maxLength={MAX_TEXT}
              value={onConnect.text}
              onChange={(e) => setSingleton(setOnConnect, 'text', e.target.value)}
            />
          </li>
          <li>
            <Tooltip keep text={help.delay}>Delay (0 = immediate)</Tooltip>
            <MinutesField
              label="Delay"
              value={onConnect.delay ?? ''}
              onChange={(v) => setSingleton(setOnConnect, 'delay', v)}
            />
          </li>
          <li>
            <Tooltip keep text={help.repeat}>Also broadcast every (empty = never)</Tooltip>
            <MinutesField
              label="Also broadcast every"
              value={onConnect.repeat || ''}
              onChange={(v) => setSingleton(setOnConnect, 'repeat', v)}
            />
          </li>
        </ul>
      </div>

      <div className="card">
        <h2>Repeat</h2>
        <PanelNote>
          Scheduled broadcasts that fire on a loop while the server runs, each at its own interval. A
          delay applies only to an OnConnect message, so to greet a player and then repeat, use the
          On Connect card's repeat.
        </PanelNote>
        {repeats.length === 0 && <PanelNote>No repeat messages.</PanelNote>}
        {repeats.map((r) => (
          <div className="msg-repeat-row" key={r._key}>
            <input
              className="text-input msg-text"
              placeholder="Message text"
              maxLength={MAX_TEXT}
              value={r.text}
              onChange={(e) => setRepeat(r._key, 'text', e.target.value)}
            />
            <MinutesField
              label="Repeat interval"
              placeholder="interval"
              tip="Repeat interval"
              value={r.repeat ?? ''}
              onChange={(v) => setRepeat(r._key, 'repeat', v)}
            />
            <button className="btn btn-danger btn-small" onClick={() => removeRepeat(r._key)}>
              Remove
            </button>
          </div>
        ))}
        <button className="btn btn-ghost" onClick={addRepeat}>
          Add repeat message
        </button>
      </div>

      <div className="card">
        <h2>Shutdown</h2>
        <PanelNote>
          Single countdown message ending in automatic server termination (DayZ auto-pings at 90/60/45/30/20/15/10/5/2/1
          min before). Leave text empty to omit.
        </PanelNote>
        <p className="hint">
          This is a restart timer of its own: the server stops this many minutes after it starts, and the service
          starts it again. The <Link to="/scheduler">Scheduler</Link> can also restart the server at fixed times, with
          warnings to players. Use one of the two, not both.
        </p>
        {shutdown.text.trim() && scheduledRestarts > 0 && (
          <div className="banner banner-warning">
            The Scheduler has {scheduledRestarts} enabled restart or update task(s). With this Shutdown message too,
            the server restarts on two clocks, and each restart starts this countdown again. Empty the message text
            above, or turn the tasks off on the <Link to="/scheduler">Scheduler</Link> page.
          </div>
        )}
        <ul className="step-list">
          <li>
            <Tooltip keep text={help.text}>Message text</Tooltip>
            <input
              className="text-input"
              maxLength={MAX_TEXT}
              value={shutdown.text}
              onChange={(e) => setSingleton(setShutdown, 'text', e.target.value)}
            />
          </li>
          <li>
            <Tooltip keep text={help.deadline}>Deadline (from server start, above 0)</Tooltip>
            <MinutesField
              label="Deadline"
              value={shutdown.deadline ?? ''}
              onChange={(v) => setSingleton(setShutdown, 'deadline', v)}
            />
          </li>
        </ul>
      </div>

      <div className="card">
        <h2>Other messages</h2>
        <PanelNote>
          Anything that is not one of the three above: a countdown that does not stop the server, or
          a second welcome or shutdown message. The type decides what DayZ does with it, and a repeat
          above 0 makes it a Repeat message; a message left without text is removed.
        </PanelNote>
        {others.length === 0 && <PanelNote>No other messages.</PanelNote>}
        {others.map((o) => (
          <div className="msg-repeat-row msg-other-row" key={o._key}>
            <Select
              className="text-input msg-type"
              aria-label="Type"
              value={o.type}
              onChange={(v) => setOther(o._key, 'type', v)}
              options={withCurrent(TYPES, o.type).map((t) => ({ value: t, label: t, description: TYPE_DESCRIPTIONS[t] }))}
            />
            <input
              className="text-input msg-text"
              placeholder="Message text"
              {...tooltipAttrs(help.text)}
              maxLength={MAX_TEXT}
              value={o.text || ''}
              onChange={(e) => setOther(o._key, 'text', e.target.value)}
            />
            {['delay', 'repeat', 'deadline'].map((k) => (
              <MinutesField
                key={k}
                label={k}
                placeholder={k}
                tip={help[k]}
                value={o[k] || ''}
                onChange={(v) => setOther(o._key, k, v)}
              />
            ))}
            <button className="btn btn-danger btn-small" onClick={() => removeOther(o._key)}>
              Remove
            </button>
          </div>
        ))}
        <button className="btn btn-ghost" onClick={addOther}>
          Add other message
        </button>
      </div>

      <StickyActionBar dirty={dirty} saving={saving} onSave={handleSave} onReset={load} {...sync} />
    </div>
  )
}
