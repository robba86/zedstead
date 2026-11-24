// A types.xml entry in the Smart view (SmartView.jsx): how common it is, how
// long it stays, where it spawns and what counts against its limit, each as a
// sentence. It edits the same cells and tree as the Raw row on the Types page,
// through `onCell(key, value)` and `onTree(key, next)`.
import Select from './Select.jsx'
import NestedParts from './NestedParts.jsx'
import { DurationField, FlagSentences, durationText } from './SmartView.jsx'

// Rarity bands by nominal: `low` to `top` on the map at once. The Types
// page's rarity filter sends a band's range as nominal_min/nominal_max.
export const RARITY = [
  { value: 'never', label: 'Never spawns', low: 0, top: 0, description: 'Nominal 0: the economy spawns none.' },
  { value: 'very_rare', label: 'Very rare', low: 1, top: 3, description: '1 to 3 on the map.' },
  { value: 'rare', label: 'Rare', low: 4, top: 10, description: '4 to 10 on the map.' },
  { value: 'uncommon', label: 'Uncommon', low: 11, top: 25, description: '11 to 25 on the map.' },
  { value: 'common', label: 'Common', low: 26, top: 60, description: '26 to 60 on the map.' },
  { value: 'everywhere', label: 'Everywhere', low: 61, top: Infinity, description: 'More than 60 on the map.' },
]

// The min a new nominal gets when the item has no ratio of its own (nominal 0).
const DEFAULT_MIN_RATIO = 2 / 3

// The band a nominal falls in, or null when it is not a whole number.
function rarityOf(nominal) {
  const n = Number(nominal)
  if (String(nominal).trim() === '' || !Number.isInteger(n) || n < 0) return null
  return RARITY.find((r) => n <= r.top)
}

// The item's own values moved into *band* (the maintainer's choice, 2026-11-13:
// scale, never fixed numbers). Down: nominal becomes the band's top. Up: nominal
// doubles, held inside the band. Min keeps its ratio to nominal (AK74 40/60
// picked as Rare is 7/10). The band the item is in already changes nothing.
export function scaleToBand(values, band) {
  // A box typed into something that is not a number starts from 0.
  const nominal = Number.isFinite(Number(values.nominal)) ? Number(values.nominal) : 0
  const min = Number(values.min)
  if (band.value === 'never') return { nominal: 0, min: 0 }
  const current = rarityOf(values.nominal)
  if (current?.value === band.value) return null
  const next = nominal > band.top ? band.top : Math.min(band.top, Math.max(band.low, nominal * 2))
  const ratio = nominal > 0 && Number.isFinite(min) && min >= 0 ? Math.min(1, min / nominal) : DEFAULT_MIN_RATIO
  return { nominal: next, min: Math.round(next * ratio) }
}

// The flags that decide what counts against nominal and min, by where the
// copy is, and the two that change how it spawns at all (types_xml.py
// FIELD_DESCRIPTIONS is the tooltip).
const COUNT_FLAGS = [
  { key: 'count_in_map', text: 'it lies on the ground or in a building' },
  { key: 'count_in_player', text: 'a player carries it' },
  { key: 'count_in_cargo', text: 'it is inside a backpack, crate or car trunk' },
  { key: 'count_in_hoarder', text: 'it is in a tent, barrel or buried stash' },
]
const SPAWN_FLAGS = [
  { key: 'crafted', text: 'Players only craft it: it never spawns as loot' },
  { key: 'deloot', text: 'It spawns only at dynamic events, such as helicopter crashes' },
]

// Values far past what stock DayZ uses. Stock types.xml keeps nominal under
// 100 and lifetime at 45 days (3888000 s) at most, on tents and stashes.
const MANY_COPIES = 500
const LONGEST_LIFETIME = 3888000

// What a reader is warned about before saving: each problem as a sentence,
// or an empty list when the values are within stock DayZ's range.
export function typeWarnings(values) {
  const nominal = Number(values.nominal)
  const min = Number(values.min)
  const lifetime = Number(values.lifetime)
  const warnings = []
  if (nominal > MANY_COPIES) {
    warnings.push(`Up to ${nominal} copies on the map is far more than stock DayZ uses. Many loose items slow the server and can make it lag or crash.`)
  }
  if (Number.isFinite(min) && Number.isFinite(nominal) && min > nominal) {
    warnings.push('The minimum is above the maximum, so the economy keeps trying to spawn more than it can keep.')
  }
  if (lifetime > LONGEST_LIFETIME) {
    warnings.push(`A copy stays ${durationText(lifetime)}, longer than anything in stock DayZ. Items pile up on the map and slow the server.`)
  }
  return warnings
}

// Plain titles for the tree's name lists.
const PLACE_TITLES = { usage: 'Spawns at (usage)', value: 'Map tier (value)', tag: 'Placement (tag)' }

// One line for a closed row: "Common · 40 to 60 on the map · stays 80 h".
export function typeSummary(values) {
  const band = rarityOf(values.nominal)
  if (band?.value === 'never') return 'Never spawns'
  return [
    band ? band.label : 'Nominal not a number',
    `${values.min} to ${values.nominal} on the map`,
    `stays ${durationText(values.lifetime)}`,
    `refills ${Number(values.restock) === 0 ? 'at once' : `after ${durationText(values.restock)}`}`,
  ].join(' · ')
}

// The open row's form: one boxed group per subject.
export default function SmartTypeForm({ values, nested, choices, help, disabled, onCell, onTree }) {
  const band = rarityOf(values.nominal)
  const warnings = typeWarnings(values)
  const places = nested && Object.fromEntries(['usage', 'value', 'tag'].filter((k) => k in nested).map((k) => [k, nested[k]]))
  // Boxed groups: rarity and timing side by side, then where it spawns
  // across, then the two flag groups side by side (index.css "boxed").
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
        <h3>How common it is</h3>
        <Select
          aria-label="How common it is"
          value={band?.value ?? ''}
          placeholder="Not a number"
          disabled={disabled}
          options={RARITY.map(({ value, label, description }) => ({ value, label, description }))}
          onChange={(v) => {
            const next = scaleToBand(values, RARITY.find((r) => r.value === v))
            if (!next) return
            onCell('nominal', String(next.nominal))
            onCell('min', String(next.min))
          }}
        />
        <p className="table-nested-sentence">
          Keep at least{' '}
          <input className="text-input text-input-narrow" inputMode="numeric" aria-label="Min" value={values.min} disabled={disabled} onChange={(e) => onCell('min', e.target.value)} />{' '}
          and up to{' '}
          <input className="text-input text-input-narrow" inputMode="numeric" aria-label="Nominal" value={values.nominal} disabled={disabled} onChange={(e) => onCell('nominal', e.target.value)} />{' '}
          on the map.
        </p>
      </div>
      <div className="table-nested-part">
        <h3>How long it stays</h3>
        <p className="table-nested-sentence">
          Stays{' '}
          <DurationField label="Lifetime" value={values.lifetime} disabled={disabled} onChange={(v) => onCell('lifetime', v)} />{' '}
          when nobody touches it. Then cleanup removes it.
        </p>
        <p className="table-nested-sentence">
          Refills after{' '}
          <DurationField label="Restock" value={values.restock} disabled={disabled} onChange={(v) => onCell('restock', v)} />{' '}
          once fewer than the minimum are left. 0 means at once.
        </p>
      </div>
      {places && Object.keys(places).length > 0 && (
        <div className="table-nested-part table-nested-part-wide">
          <h3>Where it spawns</h3>
          <NestedParts nested={places} choices={choices} help={help} titles={PLACE_TITLES} disabled={disabled} onChange={onTree} />
        </div>
      )}
      {nested?.flags && (
        <>
          <div className="table-nested-part">
            <h3>A copy counts against the limit when</h3>
            <FlagSentences items={COUNT_FLAGS} flags={nested.flags} help={help} disabled={disabled} onChange={(next) => onTree('flags', next)} />
          </div>
          <div className="table-nested-part">
            <h3>How it spawns</h3>
            <FlagSentences items={SPAWN_FLAGS} flags={nested.flags} help={help} disabled={disabled} onChange={(next) => onTree('flags', next)} />
          </div>
        </>
      )}
    </div>
  )
}
