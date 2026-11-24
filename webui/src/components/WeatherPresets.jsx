import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import HeaderToggle from './HeaderToggle.jsx'
import { useToast } from './Toast.jsx'
import PanelNote from './PanelNote.jsx'
import { useConfirm } from './useConfirm.jsx'

// The Presets card above the weather form: a preset table + Apply. Applying a
// preset rebuilds cfgweather.xml from scratch, so the drawer re-reads the form
// afterwards (`onApplied`). It sat in its own Interactive tab, on top of a
// copy of the form, until 2026 Q4. The Presets page shows the same card on its
// Weather tab, with no form to re-read: there `editorTo` (the Weather editor's
// link) adds the editor's Enable Weather switch, saved at once, and a link to
// the full form in the card's header.
export default function WeatherPresets({ onApplied, editorTo }) {
  const [presets, setPresets] = useState([])
  const confirm = useConfirm()
  const [enabledField, setEnabledField] = useState(null) // the form's `enabled` field
  const [toggling, setToggling] = useState(false)
  const pushToast = useToast()

  // The switch reads the same form the Weather editor loads. No file yet
  // means no `enabled` field, and then no switch.
  const loadEnabled = useCallback(() => {
    if (!editorTo) return
    api
      .get('/api/configs/weather')
      .then((d) => {
        const fields = (d.sections || []).flatMap((s) => s.fields || [])
        setEnabledField(fields.find((f) => f.key === 'enabled') || null)
      })
      .catch(() => setEnabledField(null))
  }, [editorTo])

  useEffect(loadEnabled, [loadEnabled])

  // Saves only `enabled`, through the editor's own save (a version is kept).
  async function toggle(on) {
    setToggling(true)
    try {
      await api.put('/api/configs/weather', { values: { enabled: on } })
      pushToast(on ? 'Weather turned on.' : 'Weather turned off.', 'info')
    } catch (err) {
      pushToast(err.message || 'Save failed', 'error')
    } finally {
      setToggling(false)
      loadEnabled()
    }
  }

  useEffect(() => {
    api
      .get('/api/configs/weather/presets')
      .then((d) => setPresets(d.presets || []))
      .catch((err) => pushToast(err.message || 'Failed to load presets', 'error'))
  }, [pushToast])

  // True when cfgweather.xml says enable="0": the server then ignores the file,
  // so a preset changes nothing in game. Read fresh, as the drawer has no switch.
  async function weatherOff() {
    try {
      const d = await api.get('/api/configs/weather')
      const field = (d.sections || []).flatMap((s) => s.fields || []).find((f) => f.key === 'enabled')
      return field != null && [0, '0', false].includes(field.value)
    } catch {
      return false
    }
  }

  async function apply(name) {
    const off = await weatherOff()
    const ok = await confirm({
      title: 'Apply weather preset?',
      message:
        `Rebuild cfgweather.xml from the "${name}" preset? Current per-element settings will be discarded.` +
        (off ? ' Weather is off in cfgweather.xml, so the preset has no effect in game until you turn it on.' : ''),
      confirmLabel: 'Apply',
    })
    if (!ok) return
    try {
      await api.post('/api/configs/weather/apply-preset', { name })
      pushToast(`Applied "${name}".`, 'info')
      onApplied?.()
      loadEnabled()
    } catch (err) {
      pushToast(err.message || 'Apply failed', 'error')
    }
  }

  return (
    <div className="card">
      {editorTo ? (
        <div className="card-header">
          <h2>Presets</h2>
          {enabledField && (
            <HeaderToggle
              field={enabledField}
              value={enabledField.value}
              onChange={toggle}
              disabled={toggling}
              note="the click saves it."
            />
          )}
          <Link className="card-link" to={editorTo}>
            Full config →
          </Link>
        </div>
      ) : (
        <h2>Presets</h2>
      )}
      <PanelNote>
        Pre-tuned weather profiles. Applying one rebuilds cfgweather.xml from scratch — current
        per-element settings are discarded (a version snapshot is saved first).
      </PanelNote>
      <ul className="preset-list">
        {presets.map((p) => (
          <li key={p.name}>
            <div>
              <strong>{p.name}</strong>
              <span className="text-muted"> — {p.description}</span>
            </div>
            <button className="btn btn-ghost btn-small" onClick={() => apply(p.name)}>
              Apply
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}
