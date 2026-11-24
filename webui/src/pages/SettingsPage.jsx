import { Switch } from '../components/FlagSwitch.jsx'
import Select, { withCurrent } from '../components/Select.jsx'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../api'
import AccessLogCard from '../components/AccessLogCard.jsx'
import UsersCard from '../components/UsersCard.jsx'
import { useToast } from '../components/Toast.jsx'
import StickyActionBar from '../components/StickyActionBar.jsx'
import Tooltip, { tooltipAttrs } from '../components/Tooltip.jsx'
import PageIntro from '../components/PageIntro.jsx'
import FilterBox from '../components/FilterBox.jsx'
import { SECTION_HELP } from '../pageHelp.js'
import { fuzzyKeysThenText } from '../fuzzy.js'
import { useSearchPrefill } from '../useSearchPrefill.js'
import { inputAttrsForType } from '../format.js'
import { helpWithKey, humanizeKey } from '../fieldLabel.js'
import { useRole } from '../role.jsx'
import { instanceHref } from '../instance'
import { useTheme } from '../theme'
import PanelNote from '../components/PanelNote.jsx'
import UnitInput from '../components/UnitInput.jsx'
import { desktopEnabled, desktopSupport, setDesktopEnabled } from '../desktopNotify.js'

// The section whose keys drive an untested feature: the in-game restart
// warnings and the join lock before a restart.
// The Appearance card's two choices. They describe this browser, not the
// server, so they live in localStorage through theme.jsx (zsm.theme,
// zsm.style), apply at once and never go through Save or /api/settings.
const THEME_OPTIONS = [
  ['tactical', 'Dark'],
  ['tactical-light', 'Light'],
]
const STYLE_OPTIONS = [
  ['classic', 'Classic'],
  ['slim', 'Slim (compact)'],
]
const EFFECTS_OPTIONS = [
  { value: 'auto', label: 'Automatic', description: 'Low on a machine without a graphics card, or when the system asks for less motion or transparency' },
  { value: 'full', label: 'Full' },
  { value: 'low', label: 'Low (for a slow computer)', description: 'No glass blur, pointer light or page animations' },
]
const APPEARANCE_WORDS = 'appearance theme style light dark tactical system classic slim compact effects blur animation slow fast performance'

function AppearanceCard() {
  const { theme, setTheme, style, setStyle, effects, effectsChoice, setEffects } = useTheme()
  return (
    <div className="card appearance-card">
      <div className="page-header">
        <h2>Appearance</h2>
      </div>
      <PanelNote>
        Saved in this browser only and applied at once. Another browser, or another person
        signed in elsewhere, keeps its own.
      </PanelNote>
      <ul className="step-list">
        <li>
          <label htmlFor="appearance-style">Style</label>
          <Select
            id="appearance-style"
            className="text-input text-input-narrow"
            value={style}
            onChange={setStyle}
            options={STYLE_OPTIONS.map(([value, label]) => ({ value, label }))}
          />
        </li>
        <li>
          <label htmlFor="appearance-theme">Theme</label>
          <Select
            id="appearance-theme"
            className="text-input text-input-narrow"
            value={theme}
            onChange={setTheme}
            options={THEME_OPTIONS.map(([value, label]) => ({ value, label }))}
          />
        </li>
        <li>
          <label htmlFor="appearance-effects">Effects</label>
          <Select
            id="appearance-effects"
            className="text-input text-input-narrow"
            value={effectsChoice}
            onChange={setEffects}
            options={EFFECTS_OPTIONS}
          />
          {effectsChoice === 'auto' && (
            <span className="hint">Now: {effects === 'low' ? 'low' : 'full'}</span>
          )}
        </li>
      </ul>
    </div>
  )
}

// The Notifications card's per-browser row. It applies at once and never goes
// through Save or /api/settings, like the Appearance card: the permission it
// asks for belongs to this browser, not to the server (desktopNotify.js).
function DesktopNoticeSwitch() {
  const [on, setOn] = useState(desktopEnabled)
  const [why, setWhy] = useState(desktopSupport)
  async function toggle(next) {
    setOn(await setDesktopEnabled(next))
    setWhy(desktopSupport())
  }
  return (
    <li>
      <Switch
        help="Show these notifications on this computer while the Zedstead tab is hidden. Saved in this browser only and applied at once. Turning it on asks the browser for permission."
        checked={on}
        disabled={!!why && !on}
        onChange={toggle}
      >
        Desktop notifications (this browser)
      </Switch>
      {why && <span className="field-note">{why}</span>}
    </li>
  )
}

// A setting's label in words: the registry's own label when it has one, else
// the key humanized (server_dir → Server folder). The key itself, which
// config.json and `zsm settings set` use, goes into the tooltip.
function settingLabel(item) {
  if (item.label) return item.label
  return humanizeKey(item.key).replace(/\b([Dd])ir\b/, (_, d) => (d === 'D' ? 'Folder' : 'folder'))
}

function settingHelp(item) {
  return helpWithKey(item.help, item.key, 'Setting name (config.json, zsm settings set)')
}

function toInputString(type, value) {
  if (type === 'int_list') return Array.isArray(value) ? value.join(',') : ''
  if (type === 'bool') return value ? 'true' : 'false'
  return value == null ? '' : String(value)
}

// A closed setting's picker options. The empty choice (service_scope) means
// auto, and a hand-edited bad value stays listed so it can be corrected.
function choiceOptions(choices, current) {
  return withCurrent(choices, current).map((value) => ({ value, label: value === '' ? 'auto' : value }))
}

// A setting the page shows but cannot change here (panel, path lock, env).
function ReadOnlyValue({ item, value }) {
  return item.type === 'bool' ? (
    <Switch checked={value === 'true'} disabled label={settingLabel(item)} />
  ) : (
    <input className="text-input text-input-narrow" disabled readOnly value={value ?? ''} />
  )
}

export default function SettingsPage() {
  const [sections, setSections] = useState([])
  const [values, setValues] = useState({})
  const [dirty, setDirty] = useState({})
  // `?q=` is the app-wide search-prefill key (same contract as /configs/:id):
  // read once, never written back, so the box stays plain local state.
  const [searchParams] = useSearchParams()
  const [filter, setFilter] = useState(() => searchParams.get('q') || '')
  // A new ?q= while the page is open (the Ctrl+K palette) replaces the box.
  useSearchPrefill(setFilter)
  const pushToast = useToast()
  // With several instances a bare `zsm settings set` changes the default
  // one, which need not be this page's.
  const { instance, instances } = useRole()
  const cliInstance = instances.length > 1 ? `--instance ${instance.id} ` : ''

  // Read after an await, where the render's own values would be stale.
  const valuesRef = useRef(values)
  valuesRef.current = values
  const dirtyRef = useRef(dirty)
  dirtyRef.current = dirty
  // What the server last answered, so a field typed back to it is not unsaved.
  const loadedRef = useRef({})

  // `saved` is what a save just wrote ({key: value}). Only those keys take the
  // server's value; an edit elsewhere on the page (another section's, or one
  // typed while the save was on its way) stays dirty as it was typed. Without
  // it, a section's Save reverted every other section's unsaved edits.
  const loadSettings = useCallback(async (saved = null) => {
    try {
      const data = await api.get('/api/settings')
      setSections(data)
      const flat = {}
      for (const section of data) {
        for (const item of section.keys) {
          flat[item.key] = toInputString(item.type, item.value)
        }
      }
      loadedRef.current = { ...flat }
      const keep = {}
      if (saved) {
        for (const [key, on] of Object.entries(dirtyRef.current)) {
          if (!on || (key in saved && saved[key] === valuesRef.current[key])) continue
          keep[key] = true
          flat[key] = valuesRef.current[key]
        }
      }
      setValues(flat)
      setDirty(keep)
    } catch (err) {
      pushToast(err.message || 'Failed to load settings', 'error')
    }
  }, [pushToast])

  useEffect(() => {
    loadSettings()
  }, [loadSettings])

  function setValue(key, raw) {
    setValues((prev) => ({ ...prev, [key]: raw }))
    setDirty((prev) => {
      const { [key]: _, ...rest } = prev
      return raw === loadedRef.current[key] ? rest : { ...rest, [key]: true }
    })
  }

  async function saveSection(section) {
    const changed = {}
    for (const item of section.keys) {
      if (dirty[item.key]) changed[item.key] = values[item.key]
    }
    if (!Object.keys(changed).length) return
    try {
      await api.put('/api/settings', { values: changed })
      pushToast(`${section.section} settings saved.`, 'info')
      loadSettings(changed)
    } catch (err) {
      pushToast(err.message || 'Failed to save settings', 'error')
    }
  }

  async function saveAllDirty() {
    const changed = {}
    for (const key of Object.keys(dirty)) {
      if (dirty[key]) changed[key] = values[key]
    }
    if (!Object.keys(changed).length) return
    try {
      await api.put('/api/settings', { values: changed })
      pushToast('Settings saved.', 'info')
      loadSettings(changed)
    } catch (err) {
      pushToast(err.message || 'Failed to save settings', 'error')
    }
  }

  // Display-only. Each entry keeps a reference to the *original* section so the
  // per-section Save button still writes every key in it, not just the visible
  // ones (and edits made before filtering are never dropped).
  const visibleSections = useMemo(() => {
    if (!filter.trim()) return sections.map((s) => ({ section: s, keys: s.keys }))
    // A filter that names a section shows that section, whole and alone: the
    // palette links a section as /settings?q=<its title>, and fuzzy matches
    // in the sections above it pushed it below the fold.
    const named = filter.trim().toLowerCase()
    const exact = sections.filter((s) => s.section.toLowerCase() === named)
    if (exact.length) return exact.map((s) => ({ section: s, keys: s.keys }))
    return sections
      .map((s) => ({
        section: s,
        keys: fuzzyKeysThenText(s.keys, filter, (i) => `${i.key} ${settingLabel(i)}`, (i) => i.help),
      }))
      .filter((s) => s.keys.length > 0)
  }, [sections, filter])

  const totalKeys = sections.reduce((n, s) => n + s.keys.length, 0)
  const q = filter.trim().toLowerCase()
  const showAppearance = !q || APPEARANCE_WORDS.includes(q)
  const shownKeys = visibleSections.reduce((n, s) => n + s.keys.length, 0)

  return (
    <div className="page settings-page">
      <div className="page-header">
        <h1>Settings</h1>
      </div>

      <PageIntro page="/settings" />

      <FilterBox
        value={filter}
        onChange={setFilter}
        placeholder="Filter settings…"
        shown={shownKeys}
        total={totalKeys}
        label="settings"
      />

      {showAppearance && <AppearanceCard />}

      {filter.trim() && visibleSections.length === 0 && !showAppearance && (
        <PanelNote>No settings match “{filter}”.</PanelNote>
      )}

      {visibleSections.map(({ section, keys }) => (
        <div className="card" key={section.section}>
          <div className="page-header">
            <h2>
              {section.section}
            </h2>
            <button className="btn btn-primary btn-small" onClick={() => saveSection(section)}>
              Save
            </button>
          </div>
          <PageIntro text={SECTION_HELP.settings[section.section]} sub />
          <ul className="step-list">
            {keys.map((item) => (
              // data-tip-at-label: the control's tip opens on the label
              // (Layout.jsx's fieldTipLabel), so the two carry the same text.
              <li key={item.key} data-tip-at-label="">
                {/* An editable switch carries the key as its own label. */}
                {!(item.type === 'bool' && !item.panel && !item.locked && !item.env) && <Tooltip keep text={settingHelp(item)}>{settingLabel(item)}</Tooltip>}
                {item.panel ? (
                  // A setting of the panel's listener, which only the instance
                  // the panel started with applies (plans/62 D4). The link is
                  // a full load: that instance's page is unprefixed.
                  <>
                    <ReadOnlyValue item={item} value={values[item.key]} />
                    <span className="field-locked-note">
                      {item.panel.note}{' '}
                      <a href={instanceHref(item.panel.instance, item.panel.instance, `/settings?q=${item.key}`)}>
                        Open it there
                      </a>
                    </span>
                  </>
                ) : item.env ? (
                  // Set from the container's environment at every start, so
                  // an edit here would come back after a restart.
                  <>
                    <ReadOnlyValue item={item} value={values[item.key]} />
                    <span className="field-locked-note">{item.env.note}</span>
                  </>
                ) : item.locked ? (
                  // web_lock_paths is on: this path/program setting is locked
                  // from the web and can only be changed on the box (plans/58).
                  <>
                    <ReadOnlyValue item={item} value={values[item.key]} />
                    <span className="field-locked-note">
                      Locked from the web UI. Change it on the server:{' '}
                      <code>zsm {cliInstance}settings set {item.key} …</code>
                    </span>
                  </>
                ) : item.type === 'bool' ? (
                  <Switch
                    help={settingHelp(item)}
                    checked={values[item.key] === 'true'}
                    onChange={(on) => setValue(item.key, on ? 'true' : 'false')}
                  >
                    {settingLabel(item)}
                  </Switch>
                ) : item.choices ? (
                  <Select
                    className="text-input text-input-narrow"
                    help={settingHelp(item)}
                    optionHelp={item.choice_help}
                    value={values[item.key] ?? ''}
                    onChange={(v) => setValue(item.key, v)}
                    options={choiceOptions(item.choices, values[item.key])}
                  />
                ) : (
                  <UnitInput
                    unit={item.unit}
                    className="text-input text-input-narrow"
                    {...inputAttrsForType(item.type)}
                    {...tooltipAttrs(settingHelp(item), { keep: true })}
                    value={values[item.key] ?? ''}
                    onChange={(e) => setValue(item.key, e.target.value)}
                  />
                )}
                {item.link && (
                  // Where the value comes from (steam_api_key), shown under the field.
                  <span className="field-note">
                    {item.link.text}{' '}
                    <a href={item.link.url} target="_blank" rel="noopener noreferrer">
                      {item.link.url}
                    </a>
                  </span>
                )}
              </li>
            ))}
            {section.section === 'Notifications' && <DesktopNoticeSwitch />}
          </ul>
        </div>
      ))}

      <UsersCard />

      <AccessLogCard />

      {/* The form moved to /account (TODO B5). It only ever changed the
          caller's own password, and this page is admin-only since roles were
          enforced — so a moderator could not reach it to rotate theirs. The
          pointer stays because this is where people have learned to look. */}
      <div className="card">
        <h2>Your own password and sign-in</h2>
        <PanelNote>
          Your password and two-factor sign-in live on{' '}
          <Link to="/account">Your account</Link>, along with everyone else&apos;s — it
          is the one page every role can reach, because nothing on it changes
          anybody but you.
        </PanelNote>
      </div>

      <StickyActionBar
        dirty={!!Object.keys(dirty).length}
        saving={false}
        onSave={saveAllDirty}
        onReset={() => loadSettings()}
        saveLabel="Save changes"
      />
    </div>
  )
}
