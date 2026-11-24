// An events.xml event in the Smart view (SmartView.jsx): how many it keeps,
// how long each lasts, where it spawns, its flags and its children, each
// subject in a box as on the Types page (SmartType.jsx). It edits the same
// cells and tree as the Raw row in TableEditor, through `onCell(key, value)`
// and `onTree(key, next)`.
import NestedParts from './NestedParts.jsx'
import Select, { withCurrent } from './Select.jsx'
import UnitInput from './UnitInput.jsx'
import { DurationField, FlagSentences, durationText } from './SmartView.jsx'
import { tooltipAttrs } from './Tooltip.jsx'

// events_xml.py FIELD_DESCRIPTIONS is the tooltip.
const EVENT_FLAGS = [
  { key: 'deletable', text: 'Cleanup can delete it' },
  { key: 'init_random', text: 'It picks random positions when the server starts' },
  { key: 'remove_damaged', text: 'Cleanup removes it once it is damaged or ruined' },
]

// The <settings> leaves the Spawns box writes as sentences. A leaf not
// listed here (an event a mod wrote by hand) stays in the tree under them.
const RADII = [
  { key: 'saferadius', before: 'Spawns no closer than', after: 'to a player.' },
  { key: 'distanceradius', before: 'Keeps', after: 'between two of them.' },
  { key: 'cleanupradius', before: 'Cleanup leaves it while a player is within', after: 'of it.' },
]
const PICKS = [
  { key: 'position', label: 'Spawns at' },
  { key: 'limit', label: 'Counts by' },
]
const SENTENCE_KEYS = new Set([...RADII.map((r) => r.key), ...PICKS.map((p) => p.key), 'secondary'])

// One line for a closed row: "Keeps 0 (refills below 0, at most 40) · lasts 33 s · refills after 30 s".
export function eventSummary(values) {
  return [
    `Keeps ${values.nominal} (refills below ${values.min}, at most ${values.max})`,
    `lasts ${durationText(values.lifetime)}`,
    `refills ${Number(values.restock) === 0 ? 'at once' : `after ${durationText(values.restock)}`}`,
  ].join(' · ')
}

// Counts that contradict each other: each as a sentence, or an empty list.
// Max is the hard cap (FIELD_DESCRIPTIONS["max"]), so it is only compared
// when the event sets one above 0.
export function eventWarnings(values) {
  const nominal = Number(values.nominal)
  const min = Number(values.min)
  const max = Number(values.max)
  const warnings = []
  if (Number.isFinite(min) && Number.isFinite(nominal) && min > nominal) {
    warnings.push('Spawn more below is above Keep, so the economy keeps trying to spawn more than it keeps.')
  }
  if (max > 0 && Number.isFinite(nominal) && nominal > max) {
    warnings.push('Keep is above the cap, so the cap wins and the event never reaches Keep.')
  }
  return warnings
}

// A narrow number box for a count.
function NumberBox({ label, value, disabled, onChange }) {
  return (
    <input className="text-input text-input-narrow" inputMode="numeric" aria-label={label} value={value ?? ''} disabled={disabled} onChange={(e) => onChange(e.target.value)} />
  )
}

// The Spawns box: the radii as sentences in metres, position and limit as
// pickers, the linked event as a text box, and any other leaf as the tree.
function SpawnSettings({ settings, choices, help, disabled, onChange }) {
  const set = (key, v) => onChange({ ...settings, [key]: v })
  const rest = Object.fromEntries(Object.entries(settings).filter(([k]) => !SENTENCE_KEYS.has(k)))
  return (
    <>
      {RADII.filter((r) => r.key in settings).map((r) => (
        <p className="table-nested-sentence" key={r.key} {...tooltipAttrs(help?.[r.key] || '')}>
          {r.before}{' '}
          <UnitInput unit="m" className="text-input text-input-narrow" inputMode="numeric" aria-label={r.key} value={settings[r.key] ?? ''} disabled={disabled} onChange={(e) => set(r.key, e.target.value)} />
          {' '}{r.after}
        </p>
      ))}
      {PICKS.filter((p) => p.key in settings).map((p) => (
        <div className="table-nested-sentence" key={p.key} {...tooltipAttrs(help?.[p.key] || '')}>
          {p.label}{' '}
          <Select aria-label={p.key} value={settings[p.key]} disabled={disabled} options={withCurrent(choices?.[p.key] ?? [], settings[p.key])} onChange={(v) => set(p.key, v)} />
        </div>
      ))}
      {'secondary' in settings && (
        <p className="table-nested-sentence" {...tooltipAttrs(help?.secondary || '')}>
          Also starts{' '}
          <input className="text-input" aria-label="secondary" value={settings.secondary ?? ''} placeholder="none" disabled={disabled} onChange={(e) => set('secondary', e.target.value)} />{' '}
          with it.
        </p>
      )}
      {Object.keys(rest).length > 0 && (
        <NestedParts nested={{ other: rest }} choices={choices} help={help} titles={{ other: 'Other settings' }} disabled={disabled} onChange={(_k, next) => onChange({ ...settings, ...next })} />
      )}
    </>
  )
}

// The open row's form: one boxed group per subject (index.css "boxed").
export default function SmartEventForm({ values, nested, choices, templates, help, trashPath, disabled, onCell, onTree }) {
  const warnings = eventWarnings(values)
  const settings = nested?.settings
  const others = nested && Object.fromEntries(Object.entries(nested).filter(([k]) => k !== 'flags' && k !== 'settings'))
  // Counts and timing side by side, then where it spawns beside its flags,
  // then the children across.
  return (
    <div className="table-nested-parts table-nested-parts-boxed">
      {warnings.length > 0 && (
        <div className="banner banner-warning table-nested-part-wide" role="status">
          {warnings.length === 1 ? (
            warnings[0]
          ) : (
            <ul className="banner-list">
              {warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          )}
        </div>
      )}
      <div className="table-nested-part">
        <h3>How many</h3>
        <p className="table-nested-sentence">
          Keep <NumberBox label="Nominal" value={values.nominal} disabled={disabled} onChange={(v) => onCell('nominal', v)} /> in the world.
        </p>
        <p className="table-nested-sentence">
          Spawn more below <NumberBox label="Min" value={values.min} disabled={disabled} onChange={(v) => onCell('min', v)} /> left.
        </p>
        <p className="table-nested-sentence">
          Never more than <NumberBox label="Max" value={values.max} disabled={disabled} onChange={(v) => onCell('max', v)} /> at once.
        </p>
      </div>
      <div className="table-nested-part">
        <h3>How long</h3>
        <p className="table-nested-sentence">
          Lasts{' '}
          <DurationField label="Lifetime" value={values.lifetime} disabled={disabled} onChange={(v) => onCell('lifetime', v)} />{' '}
          before cleanup.
        </p>
        <p className="table-nested-sentence">
          Refills after{' '}
          <DurationField label="Restock" value={values.restock} disabled={disabled} onChange={(v) => onCell('restock', v)} />{' '}
          once fewer than the minimum are left. 0 means at once.
        </p>
      </div>
      {settings && (
        <div className="table-nested-part">
          <h3>Where it spawns</h3>
          <SpawnSettings settings={settings} choices={choices} help={help} disabled={disabled} onChange={(next) => onTree('settings', next)} />
        </div>
      )}
      {nested?.flags && (
        <div className="table-nested-part">
          <h3>Cleanup and start</h3>
          <FlagSentences items={EVENT_FLAGS} flags={nested.flags} help={help} disabled={disabled} onChange={(next) => onTree('flags', next)} />
        </div>
      )}
      {others && Object.keys(others).length > 0 && (
        <div className="table-nested-part table-nested-part-wide">
          <h3>What it spawns</h3>
          <NestedParts
            nested={others}
            choices={choices}
            templates={templates}
            help={help}
            titles={{ children: 'Children' }}
            trashPath={trashPath}
            disabled={disabled}
            onChange={onTree}
          />
        </div>
      )}
    </div>
  )
}
