// Event detail editor — the events.xml side of an event (timing, radii, flags,
// spawn strategy), shown in place of the event list.
//
// The form is local state seeded from the server, not a controlled view of the
// resource: a WS ping fires on every spawn drag-save, and reseeding from that
// would wipe half-typed numbers. Reseeding is therefore skipped while the form
// is dirty, and resumes after a save.

import { Switch } from '../../../components/FlagSwitch.jsx'
import Select, { withCurrent } from '../../../components/Select.jsx'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { meApi } from '../api'
import { api } from '../../../api'
import { useEditor } from '../state/MapEditorProvider'
import { useWs } from '../../../ws'
import { useToast } from '../../../components/Toast.jsx'
import { tooltipAttrs } from '../../../components/Tooltip.jsx'
import { useConfirmDiscard, useUnsavedChanges } from '../../../useUnsavedChanges.js'
import PanelNote from '../../../components/PanelNote.jsx'
import ListSection from '../../../components/ListSection.jsx'
import SpawnIcon from '../icons/SpawnIcon.jsx'
import EventFileLinks from './EventFileLinks.jsx'

// The unsaved-changes name of this form: opening another event replaces it.
export const EVENT_FORM = 'mapeditor-event'

const TIMING_FIELDS = [
  ['nominal', 'Nominal'],
  ['min', 'Min'],
  ['max', 'Max'],
  ['lifetime', 'Lifetime'],
  ['restock', 'Restock'],
]

const RADIUS_FIELDS = [
  ['saferadius', 'Safe'],
  ['distanceradius', 'Distance'],
  ['cleanupradius', 'Cleanup'],
]

const FLAGS = [
  ['deletable', 'Deletable'],
  ['init_random', 'Init Random'],
  ['remove_damaged', 'Remove Damaged'],
]


function toForm(detail) {
  const form = {}
  for (const [key] of [...TIMING_FIELDS, ...RADIUS_FIELDS]) form[key] = String(detail[key])
  for (const [key] of FLAGS) form[key] = Boolean(detail.flags?.[key])
  form.position = detail.position
  form.limit = detail.limit
  return form
}

export default function EventDetail({ name }) {
  const { dispatch } = useEditor()
  const pushToast = useToast()
  const [detail, setDetail] = useState(null)
  const [form, setForm] = useState(null)
  const [saving, setSaving] = useState(false)
  const [capturing, setCapturing] = useState(false)
  const [error, setError] = useState('')
  const dirtyRef = useRef(false)
  const confirmDiscard = useConfirmDiscard()
  // Leaving the page or the tab asks first, as the config editors do.
  const dirty = useMemo(
    () => Boolean(detail && form) && JSON.stringify(form) !== JSON.stringify(toForm(detail)),
    [detail, form]
  )
  useUnsavedChanges(dirty, { form: EVENT_FORM })

  const apply = useCallback((result) => {
    setDetail(result)
    setForm(toForm(result))
    dirtyRef.current = false
  }, [])

  const load = useCallback(
    async ({ force = false } = {}) => {
      if (dirtyRef.current && !force) return
      try {
        apply(await meApi.get(`/events/${encodeURIComponent(name)}`))
        setError('')
      } catch (err) {
        setError(err.message || 'Failed to load event.')
      }
    },
    [name, apply]
  )

  useEffect(() => {
    dirtyRef.current = false
    load({ force: true })
  }, [load])

  // Someone else (or our own save) changed events.xml.
  useWs(
    useCallback(
      (msg) => {
        if (msg.type === 'mapeditor_events_changed' || msg.type === 'mapeditor_reloaded') load()
      },
      [load]
    )
  )

  function setField(key, value) {
    dirtyRef.current = true
    setForm((prev) => ({ ...prev, [key]: value }))
  }

  async function toggleActive() {
    try {
      apply(await meApi.put(`/events/${encodeURIComponent(name)}/active`, {}))
    } catch (err) {
      pushToast(`Failed to toggle active: ${err.message}`, 'error')
    }
  }

  async function save() {
    const updates = {}
    for (const [key] of [...TIMING_FIELDS, ...RADIUS_FIELDS]) {
      const parsed = parseInt(form[key], 10)
      if (Number.isNaN(parsed)) {
        pushToast(`${key} must be a number.`, 'error')
        return
      }
      updates[key] = parsed
    }
    // events.xml stores flags as 1/0 attributes, not booleans.
    for (const [key] of FLAGS) updates[key] = form[key] ? 1 : 0
    updates.position = form.position
    updates.limit = form.limit

    setSaving(true)
    try {
      apply(await meApi.put(`/events/${encodeURIComponent(name)}`, updates))
    } catch (err) {
      pushToast(`Save failed: ${err.message}`, 'error')
    } finally {
      setSaving(false)
    }
  }

  // Positions dragged onto the map are written where they are kept: the
  // mission's own into cfgeventspawns.xml (they stay there through a merge),
  // a manual file's into that file, which is then applied again; a mod's first
  // copy the mod's file into a manual file, since the next merge puts the
  // mod's block back (spawnOwner.js, CopyIntoManualFile.jsx). This MOVES
  // them into a manual XML file, which buys the two things the mission file
  // can't give: surviving a map switch or a server reinstall, and being applied
  // after every mod (see app/xml/xml_manual.py).
  async function captureAsLink() {
    setCapturing(true)
    try {
      const link = await api.post('/api/modxml/files/from-event', { event: name })
      pushToast(
        `Moved into manual XML file "${link.id}" — applied after every mod, and kept ` +
          'outside the mission.',
        'info'
      )
    } catch (err) {
      pushToast(err.message || 'Could not save these positions.', 'error')
    } finally {
      setCapturing(false)
    }
  }

  // Back plus the two files this event actually lives in: events.xml (the
  // fields edited below) and cfgeventspawns.xml (its positions).
  const back = (
    <div className="me-detail-nav">
      <button
        type="button"
        className="me-back-btn"
        onClick={async () => {
          if (await confirmDiscard(EVENT_FORM)) dispatch({ type: 'SELECT_EVENT', payload: null })
        }}
      >
        ← Back
      </button>
      <EventFileLinks name={name} />
      <button
        type="button"
        className="me-link-btn me-link-btn-text"
        onClick={captureAsLink}
        disabled={capturing}
        {...tooltipAttrs(
          'Move this event\u2019s own positions into a manual XML file. Positions ' +
            'outside any mod\u2019s or manual file\u2019s block already survive a merge; a ' +
            'manual file also survives a map switch or a server reinstall, and is ' +
            'applied after every mod so it wins when a mod defines the same event. ' +
            'A position that already comes from a manual file is saved into that ' +
            'file when you move it; moving a mod\u2019s first copies the mod\u2019s file ' +
            'into a manual XML file, after asking.'
        )}
      >
        {capturing ? 'Saving\u2026' : 'Save as manual XML file'}
      </button>
    </div>
  )

  if (error) {
    return (
      <>
        {back}
        <PanelNote>{error}</PanelNote>
      </>
    )
  }
  if (!detail || !form) {
    return (
      <>
        {back}
        <PanelNote>Loading event…</PanelNote>
      </>
    )
  }

  const densityPct =
    detail.nominal > 0 ? Math.min(100, Math.round((detail.spawn_count / detail.nominal) * 100)) : 0

  return (
    <>
      {back}
      <div className="me-event-detail">
        <div className="me-event-detail-header">
          <span className="me-event-detail-name">
            <SpawnIcon name={detail.name} />
            {detail.name}
          </span>
          <div className="me-active-toggle-label">
            <Switch checked={Boolean(detail.active)} onChange={toggleActive}>
              {detail.active ? 'Active' : 'Inactive'}
            </Switch>
          </div>
        </div>

        <div className="me-density-bar">
          <div className="me-density-fill" style={{ width: `${densityPct}%` }} />
          <span className="me-density-label">
            {detail.spawn_count}/{detail.nominal} spawns
          </span>
        </div>

        <ListSection title="Timing" />
        <div className="me-detail-fields">
          {TIMING_FIELDS.map(([key, label]) => (
            <div key={key} className="me-detail-field-row">
              <label htmlFor={`me-detail-${key}`}>{label}</label>
              <input
                id={`me-detail-${key}`}
                type="number"
                value={form[key]}
                onChange={(e) => setField(key, e.target.value)}
              />
            </div>
          ))}
        </div>

        <ListSection title="Radii" />
        <div className="me-detail-fields">
          {RADIUS_FIELDS.map(([key, label]) => (
            <div key={key} className="me-detail-field-row">
              <label htmlFor={`me-detail-${key}`}>{label}</label>
              <input
                id={`me-detail-${key}`}
                type="number"
                value={form[key]}
                onChange={(e) => setField(key, e.target.value)}
              />
            </div>
          ))}
        </div>

        <ListSection title="Flags" />
        <div className="me-detail-flags">
          {FLAGS.map(([key, label]) => (
            <div key={key} className="me-detail-flag">
              <Switch checked={!!form[key]} onChange={(on) => setField(key, on)}>
                {label}
              </Switch>
            </div>
          ))}
        </div>

        <ListSection title="Position & Limit" />
        <div className="me-detail-fields">
          <div className="me-detail-field-row">
            <label htmlFor="me-detail-position">Position</label>
            <Select
              id="me-detail-position"
              className="field"
              value={form.position}
              onChange={(v) => setField('position', v)}
              options={withCurrent(detail.position_choices, form.position)}
            />
          </div>
          <div className="me-detail-field-row">
            <label htmlFor="me-detail-limit">Limit</label>
            <Select
              id="me-detail-limit"
              className="field"
              value={form.limit}
              onChange={(v) => setField('limit', v)}
              options={withCurrent(detail.limit_choices, form.limit)}
            />
          </div>
        </div>

        {detail.children.length > 0 && (
          <>
            <ListSection title="Children" />
            {/* Five columns at 11px already crowd the 320px panel; in the
                mobile bottom sheet they need somewhere to scroll. */}
            <div className="data-table-wrap">
              <table className="me-detail-children-table">
                <thead>
                  <tr>
                    <th>Type</th>
                    <th>Min</th>
                    <th>Max</th>
                    <th>LootMin</th>
                    <th>LootMax</th>
                  </tr>
                </thead>
                <tbody>
                  {detail.children.map((child) => (
                    <tr key={child.type}>
                      <td>{child.type}</td>
                      <td>{child.min}</td>
                      <td>{child.max}</td>
                      <td>{child.lootmin}</td>
                      <td>{child.lootmax}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}

        <button type="button" className="me-detail-save-btn" disabled={saving} onClick={save}>
          {saving ? 'Saving...' : 'Save'}
        </button>
      </div>
    </>
  )
}
