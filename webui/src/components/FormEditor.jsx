// The form view of a config file in ConfigEditorDrawer: one field per key,
// with nested objects and lists drawn as JsonTree cards.

import CountBadge from './CountBadge.jsx'
import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import StickyActionBar from './StickyActionBar.jsx'
import Tooltip, { tooltipAttrs } from './Tooltip.jsx'
import PageIntro from './PageIntro.jsx'
import FilterBox from './FilterBox.jsx'
import ListField from './ListField.jsx'
import MultiSelectList from './MultiSelectList.jsx'
import JsonTree from './JsonTree.jsx'
import FileMissing from './FileMissing.jsx'
import FlagSwitch, { Switch } from './FlagSwitch.jsx'
import ColorField from './ColorField.jsx'
import PasswordField from './PasswordField.jsx'
import Select, { withCurrent } from './Select.jsx'
import DefaultPill, { atDefault, notInFileText, pillDefault } from './DefaultPill.jsx'
import { SECTION_HELP } from '../pageHelp.js'
import { fuzzyKeysThenText } from '../fuzzy.js'
import { displayFloat, inputAttrsForType, isFlagKey } from '../format.js'
import { colorFormat } from '../color.js'
import { fieldLabel, helpWithKey, humanizeKey } from '../fieldLabel.js'
import { useEditorSync } from '../configSync.js'
import { useUrlSubtab } from '../urlTabs.js'
import { TrashContext, TrashTray, insertAt, useTrashState } from '../trash.jsx'
import BetaBadge from './BetaBadge.jsx'
import HeaderToggle from './HeaderToggle.jsx'
import PanelNote from './PanelNote.jsx'
import Tabs from './Tabs.jsx'
import UnitInput from './UnitInput.jsx'
import { DurationField } from './SmartView.jsx'
import RangeField, { sliderScale } from './RangeField.jsx'

// A section whose feature nobody has run on a real server yet (betaFeatures.js).
const SECTION_BETA = { 'Restart warnings': 'restart-warnings' }

// The bar's first tab, for every section without a `tab` of its own.
const MAIN_TAB = 'Settings'

// Below this, scrolling is faster than typing — matches MultiSelectList's
// threshold for showing its own filter box.
const FILTER_THRESHOLD = 8

// Whether the file leaves the field out while the server has a known
// `default` for it (serverDZ.cfg's, from Bohemia's wiki).
function usesDefault(field) {
  return field.default != null && (field.value == null || field.value === '')
}

// Each field's value as the form shows it. A switch or a closed list the file
// leaves out shows the server's default rather than off or a blank, and a
// text box shows it as its placeholder (see renderControl). Each of them
// shows a "Default" pill while it is on that value (atDefault). Only a changed
// field is sent, so the default is never written unless the admin picks it.
function flatten(data) {
  const flat = {}
  for (const section of data.sections) {
    for (const field of section.fields) {
      const value = usesDefault(field) && (field.type === 'bool' || field.choices) ? field.default : field.value
      flat[field.key] =
        field.type === 'bool'
          ? !!value
          : field.type === 'list'
            ? [...(value || [])]
            : value ?? ''
    }
  }
  return flat
}

// How each field is drawn, read once per load (a switch set to 0 must stay
// a switch): 'flag' for a 0/1 int whose name reads like one, a colour
// format for a colour key, else the field's own type decides.
function widgetsOf(data) {
  const out = {}
  for (const section of data.sections) {
    for (const f of section.fields) {
      if (f.widget) out[f.key] = f.widget
      // A closed set of values is a select, even one that reads 0 or 1 now.
      else if (f.choices) continue
      else if (f.type === 'int' && isFlagKey(f.key, f.value)) out[f.key] = 'flag'
      else if ((f.type === 'str' || f.type === 'int') && colorFormat(f.key, f.value)) out[f.key] = 'color'
    }
  }
  return out
}

// A list that wants the whole row rather than one column.
function isWide(field) {
  const v = field.value
  return field.type === 'list' && Array.isArray(v) && (v.length > 6 || v.join(' ').length > 80)
}

// Display order within a section: a file's master switch (widget "header"),
// inputs, then lists, then on/off fields in a row of their own. The fields'
// order in the file is never changed; the save sends a key per changed field.
function groupFields(fields, widgets) {
  const rank = (f) =>
    widgets[f.key] === 'header' ? -1 : f.type === 'bool' || widgets[f.key] === 'flag' ? 2 : f.type === 'list' ? 1 : 0
  return {
    main: fields.filter((f) => rank(f) < 2).sort((a, b) => rank(a) - rank(b)),
    flags: fields.filter((f) => rank(f) === 2),
  }
}

// A label that is a dotted path of keys (cfggameplay's StaminaData.meleeStaminaModifier
// under Player) names an object inside the section. Display only.
const DOTTED = /^\w+(\.\w+)+$/

// Splits fields into the section's own and one group per first path part, in
// the order the file has them, each grouped field's label shortened to the
// rest of its path (which can split again). Keys and values are untouched,
// so the save is the same as without groups.
function nestFields(fields) {
  const own = []
  const groups = new Map()
  for (const f of fields) {
    if (typeof f.label !== 'string' || !DOTTED.test(f.label)) {
      own.push(f)
      continue
    }
    const [head, ...rest] = f.label.split('.')
    if (!groups.has(head)) groups.set(head, [])
    groups.get(head).push({ ...f, label: rest.join('.') })
  }
  return { own, groups: [...groups] }
}

// Renders {kind:"form", sections:[{title, fields:[{key,label,type,value,help?,choices?,choice_help?}]}]}.
// A field of type "list" carries `item` and an array value (ListField). With
// `widget: "pick"` (TraderPlus's lists of class names and Steam IDs) it is a
// MultiSelectList instead: a filter, ticks and Remove selected suit a long list
// of names better than one box per item. That was the TraderPlus Interactive
// view's one advantage over this form, and why that view could go (2026 Q4).
// `nested` ({key: value}, optional) is what no field can show: objects and
// lists of objects, rendered as a tree in one card per key after the
// sections (plans/76 step 2). A changed key goes back under `$nested`, with
// the value as loaded under `$nested_base` so the server can refuse a save
// made over a newer file (app/web/editors/_nested.py). `nested_templates`
// ({key: new entry}, optional) is what Add puts into a list the file has
// empty, so an empty list of objects is not taken for a list of names.
// onSave receives only the fields the user actually changed.
// A section's `note` is a muted line under its title (what the form leaves to
// the Raw tab). A field with `widget: "header"` (BattlEye's or Weather's master
// switch) is an On/Off button first among its section's inputs; like any field it only marks the
// form changed, and Save writes it. A removed list item or tree entry goes to
// the trash (trash.jsx) and can be put back until Save or Reset.
// A section's `tab` (Core Settings' "Client only") puts it behind a tab of
// that name after the main one (?subtab=); a filter searches every tab.
// `initialSearch` prefills the filter box from a deep link's ?q= (read once —
// later renders must not clobber what the user typed).
export default function FormEditor({ data, onSave, saving, editorId, initialSearch = '' }) {
  const sectionHints = SECTION_HELP[editorId] || {}
  const [values, setValues] = useState({})
  const [dirty, setDirty] = useState({})
  const [nested, setNested] = useState({})
  const [nestedDirty, setNestedDirty] = useState({})
  const [filter, setFilter] = useState(initialSearch)
  const widgets = useMemo(() => widgetsOf(data), [data])
  // Puts a removed item back into the state it came from (trash.jsx).
  const trash = useTrashState(({ path: [root, key, ...trail], index, item }) => {
    if (root === 'field') {
      setValues((prev) => ({ ...prev, [key]: insertAt(prev[key], trail, index, item) }))
      setDirty((prev) => ({ ...prev, [key]: true }))
    } else {
      setNested((prev) => ({ ...prev, [key]: insertAt(prev[key], trail, index, item) }))
      setNestedDirty((prev) => ({ ...prev, [key]: true }))
    }
  })
  // Reports up to ConfigEditorDrawer, which owns `data` and so owns the refresh.
  const isDirty = !!Object.keys(dirty).length || !!Object.keys(nestedDirty).length
  const sync = useEditorSync(isDirty)

  useEffect(() => {
    setValues(flatten(data))
    setDirty({})
    setNested(structuredClone(data.nested || {}))
    setNestedDirty({})
    trash.clear()
  }, [data])

  // Display-only: filtering never touches `values`/`dirty`, so an edit made to
  // a field that is later filtered out still gets submitted by handleSave.
  const totalFields = useMemo(
    () => data.sections.reduce((n, s) => n + s.fields.length, 0),
    [data]
  )

  // [] when no section names a tab: no bar at all.
  const formTabs = useMemo(() => {
    const named = [...new Set(data.sections.map((s) => s.tab).filter(Boolean))]
    return named.length ? [MAIN_TAB, ...named] : []
  }, [data])
  const { subtab, setSubtab } = useUrlSubtab(formTabs)

  const sections = useMemo(() => {
    if (!filter.trim()) return subtab ? data.sections.filter((s) => (s.tab || MAIN_TAB) === subtab) : data.sections
    return data.sections
      .map((section) => ({
        ...section,
        fields: fuzzyKeysThenText(section.fields, filter, (f) => [fieldLabel(f).text, f.label, f.key], (f) => f.help),
      }))
      .filter((section) => section.fields.length > 0)
  }, [data, filter, subtab])

  const shownFields = sections.reduce((n, s) => n + s.fields.length, 0)

  function setValue(key, value) {
    setValues((prev) => ({ ...prev, [key]: value }))
    setDirty((prev) => ({ ...prev, [key]: true }))
  }

  function setNestedValue(key, value) {
    setNested((prev) => ({ ...prev, [key]: value }))
    setNestedDirty((prev) => ({ ...prev, [key]: true }))
  }

  function handleSave() {
    const changed = {}
    for (const key of Object.keys(dirty)) changed[key] = values[key]
    const keys = Object.keys(nestedDirty)
    if (keys.length) {
      changed.$nested = Object.fromEntries(keys.map((k) => [k, nested[k]]))
      changed.$nested_base = Object.fromEntries(keys.map((k) => [k, data.nested[k]]))
    }
    onSave(changed)
  }

  function handleReset() {
    setValues(flatten(data))
    setDirty({})
    setNested(structuredClone(data.nested || {}))
    setNestedDirty({})
    trash.clear()
  }

  const nestedKeys = Object.keys(data.nested || {})

  if (!data.sections.length && !nestedKeys.length) {
    return <FileMissing editorId={editorId} />
  }

  // A section's (or group's) fields: its own inputs, then its switches, then
  // one sub-group per object inside it, headed by the object's name as words
  // and drawn the same way, so Player → Stamina data → its fields.
  function renderFields(fields) {
    const { own, groups } = nestFields(fields)
    const { main, flags } = groupFields(own, widgets)
    return (
      <>
        {main.length > 0 && <ul className="step-list">{main.map(renderField)}</ul>}
        {flags.length > 0 && <ul className="step-list step-list-flags">{flags.map(renderField)}</ul>}
        {groups.map(([name, sub]) => (
          <section className="form-subgroup" key={name}>
            <h3 {...tooltipAttrs(helpWithKey('', name))}>{humanizeKey(name)}</h3>
            {renderFields(sub)}
          </section>
        ))}
      </>
    )
  }

  // A field as drawn: a label that is only the file's key becomes words, and
  // the key goes into the tooltip (fieldLabel.js). Key and value are untouched.
  function renderField(raw) {
    const name = fieldLabel(raw)
    const unset = usesDefault(raw) ? notInFileText(raw, String(raw.default)) : ''
    const help = [helpWithKey(raw.help, name.raw), unset].filter(Boolean).join('\n\n')
    const field = { ...raw, label: name.text, help }
    const widget = widgets[field.key]
    const cls = [
      field.widget === 'pick' ? 'field-list' : '',
      widget === 'header' ? 'field-master-toggle' : '',
      isWide(field) ? 'field-wide' : '',
    ].filter(Boolean).join(' ')
    // A switch carries its own label, beside the track.
    const isSwitch = field.type === 'bool' || (!field.locked && widget === 'flag')
    return (
      // data-tip-at-label: the control's tip opens on the label (Layout.jsx's
      // fieldTipLabel), so it sits in one place and never covers the label.
      <li key={field.key} className={cls || undefined} data-tip-at-label="">
        {!isSwitch && <Tooltip keep text={field.help}>{field.label}</Tooltip>}
        {elsewhereOnly(field) ? null : renderControl(field, widget)}
        {!field.readonly && <FieldNote field={field} />}
      </li>
    )
  }

  function renderControl(field, widget) {
    // A switch's pill: "Before 1.30" on a key a newer DayZ replaced
    // (`replaced_in`), else "Default" while it is on the server's default.
    const badge = field.replaced_in ? (
      <DefaultPill className="flag-switch-default">Before {field.replaced_in}</DefaultPill>
    ) : field.type === 'bool' && atDefault(values[field.key], pillDefault(field)) ? (
      <DefaultPill className="flag-switch-default" />
    ) : null
    if (field.locked) {
      // web_lock_paths is on (plans/58), or the container's environment sets
      // it (locked_note says which): the save would be refused. Show the
      // value read-only and say why, rather than let it fail.
      if (field.type === 'bool' && field.locked_note) {
        // A switch sits in a narrow tile beside its neighbours: the note's
        // first sentence goes under it and the whole note is the tooltip, or
        // the same paragraph repeats under every flag and squeezes its label.
        const first = field.locked_note.split(/(?<=\.)\s/)[0]
        return (
          <>
            <Switch checked={!!values[field.key]} disabled help={field.locked_note} badge={badge}>
              {field.label}
            </Switch>
            <span className="field-locked-note">{first}</span>
          </>
        )
      }
      return (
        <>
          {field.type === 'bool' ? (
            <Switch checked={!!values[field.key]} disabled badge={badge}>
              {field.label}
            </Switch>
          ) : field.type === 'list' ? (
            <ListField value={values[field.key]} item={field.item} label={field.label} disabled />
          ) : (
            <input className="text-input text-input-narrow" disabled readOnly value={values[field.key] ?? ''} />
          )}
          <span className="field-locked-note">
            {field.locked_note || <>Locked from the web UI (<code>web_lock_paths</code>).</>}
          </span>
        </>
      )
    }
    if (widget === 'header') {
      return <HeaderToggle field={field} value={values[field.key]} onChange={(v) => setValue(field.key, v)} />
    }
    if (field.type === 'bool') {
      return (
        <Switch help={field.help} checked={!!values[field.key]} badge={badge} onChange={(on) => setValue(field.key, on)}>
          {field.label}
        </Switch>
      )
    }
    if (widget === 'flag') {
      // Saved as the number 0 or 1, the file's own type.
      return (
        <FlagSwitch value={values[field.key]} help={field.help} onChange={(v) => setValue(field.key, v)}>
          {field.label}
        </FlagSwitch>
      )
    }
    if (field.type === 'list' && field.widget === 'pick') {
      return (
        <MultiSelectList
          values={(values[field.key] || []).map(String)}
          trashPath={['field', field.key]}
          name={field.label}
          onChange={(next) => setValue(field.key, next)}
        />
      )
    }
    if (field.type === 'list') {
      return (
        <ListField
          value={values[field.key]}
          item={field.item}
          label={field.label}
          help={field.help}
          trashPath={['field', field.key]}
          onChange={(next) => setValue(field.key, next)}
        />
      )
    }
    if (field.readonly) {
      // Shown, never edited here: serverDZ.cfg's Mission changes only through
      // the Maps page, which moves the mission folder with it. Never set, so
      // never part of the save.
      return (
        <span className="form-readonly">
          <code>{values[field.key] ?? '—'}</code>
          {field.link && <Link to={field.link.to}>{field.link.label}</Link>}
        </span>
      )
    }
    if (field.redacted) {
      // A password the API blanked for a non-admin (app/web/redact.py): say
      // so, or an empty box reads as "no password set".
      return (
        <input
          className="text-input text-input-narrow"
          type="text"
          disabled
          placeholder="Hidden — admin only"
          value=""
          readOnly
        />
      )
    }
    if (field.secret) {
      // A password: dots, with an eye button that shows it.
      return (
        <PasswordField
          aria-label={field.label}
          {...tooltipAttrs(field.help, { keep: true })}
          value={values[field.key] ?? ''}
          onChange={(e) => setValue(field.key, e.target.value)}
        />
      )
    }
    if (field.choices) {
      // A closed set of values (Dynamic AI's modes). The file's own value is
      // kept in the list even when it is not a valid one, so a bad value
      // stays visible to be corrected. An `open` list (lightingConfig) also
      // takes a typed value, since a mod can bring its own.
      return (
        <Select
          className="text-input text-input-narrow"
          aria-label={field.label}
          creatable={!!field.open}
          help={field.help}
          optionHelp={field.choice_help}
          value={String(values[field.key] ?? '')}
          // The server's default is marked in the list and by a "Default"
          // pill (Select), so a field the file leaves out reads as that value
          // and not as a blank.
          options={withCurrent(field.choices, values[field.key])}
          defaultHint={pillDefault(field)}
          onChange={(v) => setValue(field.key, v)}
        />
      )
    }
    if (widget === 'color') {
      return (
        <ColorField
          value={values[field.key]}
          format={colorFormat(field.key, field.value)}
          label={field.label}
          help={field.help}
          onChange={(v) => setValue(field.key, v)}
        />
      )
    }
    const raw = values[field.key] ?? ''
    // A short closed range (speedhackDetection 1-10, a 0.0-1.0 share) is a
    // slider with a tick per step; a wider one keeps the plain box below.
    // A value that is a 0.0-1.0 share only while a switch is on
    // (`share_when`: serverDZ.cfg's bandwidth limit) is a percentage slider
    // then, and the plain box while the switch is off or not in the file.
    const shareField = field.share_when && values[field.share_when] ? { ...field, min: 0, max: 1 } : field
    const scale = sliderScale(shareField)
    if (scale) {
      return (
        <RangeField
          field={shareField}
          scale={scale}
          value={raw}
          edited={!!dirty[field.key]}
          onChange={(v) => setValue(field.key, v)}
          tooltip={tooltipAttrs(field.help, { keep: true })}
        />
      )
    }
    // A time gets a unit picker (days, hours, minutes, and seconds for a
    // file that keeps seconds); the file keeps its own unit. Bohemia's
    // example value stays a plain box.
    const timeBase = TIME_BASE[field.unit]
    if (timeBase && !field.default_is_example) {
      return (
        <DurationField
          label={field.label}
          base={timeBase}
          value={raw}
          whole={field.type === 'int'}
          defaultHint={pillDefault(field)}
          inputProps={tooltipAttrs(field.help, { keep: true })}
          onChange={(v) => setValue(field.key, v)}
        />
      )
    }
    return (
      <UnitInput
        unit={field.unit}
        className="text-input text-input-narrow"
        {...inputAttrsForType(field.type)}
        // The range the backend checks on save (serverDZ.cfg's from Bohemia's
        // wiki); the save is still the authority.
        min={field.min}
        max={field.max}
        // A key the file leaves out shows what the server uses instead, with
        // a "Default" pill (UnitInput). A typed default keeps the pill. A
        // value from Bohemia's example file is only the placeholder.
        defaultHint={pillDefault(field)}
        placeholder={field.default_is_example ? String(field.default) : undefined}
        {...tooltipAttrs(field.help, { keep: true })}
        // A float the file wrote as a double (0.05000000074505806) is shown
        // short until edited; untouched, it is not sent, so the file keeps it.
        value={field.type === 'float' && !dirty[field.key] ? displayFloat(raw) : raw}
        onChange={(e) => setValue(field.key, e.target.value)}
      />
    )
  }

  return (
    <TrashContext.Provider value={trash.ctx}>
    <div className="form-editor">
      {totalFields > FILTER_THRESHOLD && (
        <FilterBox
          value={filter}
          onChange={setFilter}
          placeholder="Filter settings…"
          shown={shownFields}
          total={totalFields}
          label="settings"
        />
      )}
      {formTabs.length > 0 && !filter.trim() && (
        <Tabs tabs={formTabs} active={subtab} onSelect={setSubtab} sub />
      )}
      {filter.trim() && sections.length === 0 && (
        <PanelNote>No settings match “{filter}”.</PanelNote>
      )}
      {sections.map((section) => {
        return (
          <div className="card" key={section.title}>
            <div className="card-header">
              <h2>
                {section.title} <BetaBadge feature={SECTION_BETA[section.title]} />
              </h2>
            </div>
            {section.note && <PanelNote>{section.note}</PanelNote>}
            <PageIntro text={sectionHints[section.title]} sub />
            {renderFields(section.fields)}
          </div>
        )
      })}
      {nestedKeys.map((key) => (
        <div className="card card-nested" key={`nested-${key}`}>
          <h2>
            <span {...tooltipAttrs(helpWithKey('', key))}>{humanizeKey(key)}</span>
            <CountBadge value={nestedCount(nested[key])} />
          </h2>
          <JsonTree
            value={nested[key]}
            name={key}
            templates={data.nested_templates}
            switches={data.nested_switches}
            trashPath={['nested', key]}
            onChange={(next) => setNestedValue(key, next)}
          />
        </div>
      ))}
      <StickyActionBar
        dirty={isDirty}
        saving={saving}
        onSave={handleSave}
        onReset={handleReset}
        {...sync}
      >
        <TrashTray trash={trash} />
      </StickyActionBar>
    </div>
    </TrashContext.Provider>
  )
}

// A field's time unit as DurationField's `base`, in seconds.
const TIME_BASE = { seconds: 1, minutes: 60 }

function nestedCount(v) {
  if (Array.isArray(v)) return v.length === 1 ? '1 entry' : `${v.length} entries`
  if (v && typeof v === 'object') return ''
  return ''
}

// A field set on another page now (`elsewhere`: Run Command's server mods,
// set on the Mods page): with nothing in it when loaded, only its note and
// link are shown. A value the file already holds keeps its box, so it can
// still be seen, changed or cleared; the save is the same either way.
function elsewhereOnly(field) {
  return field.elsewhere && !field.locked && (field.value == null || field.value === '')
}

// A field's `note` and `link` under its control: where the setting is made,
// or what it is not (serverDZ.cfg's instanceId is not a Zedstead instance).
function FieldNote({ field }) {
  if (!field.note && !field.link) return null
  return (
    <span className="field-note">
      {field.note}
      {field.link && <Link to={field.link.to}>{field.link.label}</Link>}
    </span>
  )
}

