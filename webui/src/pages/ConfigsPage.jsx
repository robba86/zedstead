import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { HiOutlineCodeBracket } from 'react-icons/hi2'
import { api } from '../api'
import PageIntro from '../components/PageIntro.jsx'
import FilterBox from '../components/FilterBox.jsx'
import ConfigEditorDrawer from '../components/ConfigEditorDrawer.jsx'
import BespokeEditorDrawer, { isBespokeDrawer } from '../components/BespokeEditorDrawer.jsx'
import ZoneOfferCard from '../components/ZoneOfferCard.jsx'
import { EDITORS, PAGE_HELP } from '../pageHelp.js'
import { MAP_EDITOR_LINKS, editorLink, modLink } from '../crossLinks.js'
import { fuzzyFilter } from '../fuzzy.js'
import { useUrlTabs } from '../urlTabs.js'
import { useSearchPrefill } from '../useSearchPrefill.js'
import {
  CONFIGS_MOD_XML_TAB as MOD_XML_TAB,
  CONFIGS_MODS_SUBTABS as MODS_SUBTABS,
  CONFIGS_TABS as TABS,
} from '../pageTabs.js'
import INTERACTIVE_VIEWS from '../interactiveViews.jsx'
import { useToast } from '../components/Toast.jsx'
import { tooltipAttrs } from '../components/Tooltip.jsx'
import { useCan } from '../role.jsx'
import PanelNote from '../components/PanelNote.jsx'
import Tabs from '../components/Tabs.jsx'
import LoadingState from '../components/LoadingState.jsx'

// The tab lists (one tab or Mods subtab per EditorSpec.group) live in
// pageTabs.js, which the Ctrl+K palette reads too. Mission is one page: the
// everyday four first, in this hand-picked order, then everything else in the
// group in the backend's order, so nothing can fall out of the page by being
// forgotten here. (It was two subtabs, Common and More, until 2026-11-05.)
const COMMON_MISSION_IDS = ['types', 'messages', 'weather', 'ignorelist']

// The Custom config tab is the Mod XML report itself, embedded rather than
// linked as a card.
const MOD_XML_ID = 'mod-xml'
const GROUPS = ['Server', 'Mission', ...MODS_SUBTABS]

const ModXmlPage = lazy(() => import('./ModXmlPage.jsx'))

function subtabsFor(tab) {
  if (tab === 'Mods') return MODS_SUBTABS
  return null
}

// Per-browser, like zsm.theme — it describes the viewer, not the server, so it
// is not a DayzConfig field and has no endpoint.
const DRAWER_WIDTH_KEY = 'zsm.configs.drawer.width'

// Where an editor's card sits: the tab and subtab its group is shown under.
// On /configs/:id the grid behind the drawer is placed from this, not from
// ?tab=, which there names the editor's own Interactive/Basic/Raw tab.
function placeOf(item) {
  if (item.group === 'Mission') {
    return { tab: 'Mission', subtab: null }
  }
  if (GROUPS.includes(item.group) && item.group !== 'Server') return { tab: 'Mods', subtab: item.group }
  return { tab: TABS[0], subtab: null }
}

// The grid's own URL for a tab/subtab, writing neither when it is the default,
// as useUrlTabs does.
function gridUrl(tab, subtab) {
  const params = new URLSearchParams()
  if (tab && tab !== TABS[0]) params.set('tab', tab)
  const subs = subtabsFor(tab)
  if (subtab && subs && subtab !== subs[0]) params.set('subtab', subtab)
  const query = params.toString()
  return query ? `/configs?${query}` : '/configs'
}

// The intro text a card shows. Registry editors are keyed by editor id in
// EDITORS; the bespoke pages (kind "custom") already have their own route text
// in PAGE_HELP. Both maps are written copy — don't add a third.
function helpFor(item) {
  return EDITORS[item.id] || PAGE_HELP[`/configs/${item.id}`] || ''
}

// The file's own name is the card's identity line. The titles are human names
// now ("Weather", not "cfgweather.xml"), which is what makes this the only
// place the filename is spelled. The directories are context, not identity, so
// they are dropped from the line and live in the tooltip's full path instead.
// A card's files on one line, each name with its full path as the tooltip,
// a small dot between them.
function FileNames({ files }) {
  return (
    <p className="config-card-path">
      {files.map((f, i) => {
        const cut = f.path.lastIndexOf('/')
        return (
          <span key={f.path}>
            {i > 0 && <span className="config-card-sep" aria-hidden="true"> · </span>}
            <strong className="config-card-filename" {...tooltipAttrs(f.path)}>
              {cut < 0 ? f.path : f.path.slice(cut + 1)}
            </strong>
          </span>
        )
      })}
    </p>
  )
}

// What a click on the card does: open the file's editor. Until 2026 Q4 a
// card had a button per view (Interactive, Basic, Raw); now an editor has one
// Edit view, so the card itself is the way in and only Raw keeps a button.
// The title is the real link (keyboard, middle-click, copy address); a click
// anywhere else on the card follows it too, unless it lands on another link
// or button, or ends a text selection.
function openOnCardClick(navigate, to) {
  return (e) => {
    if (e.target.closest('a, button, input, select, textarea, label')) return
    if (window.getSelection()?.toString()) return
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return
    navigate(to)
  }
}

function ConfigCard({ item, open }) {
  const isAdmin = useCan('admin')
  const navigate = useNavigate()
  const help = helpFor(item)
  const bespoke = item.kind === 'custom'
  const files = item.files || []
  const interactive = Boolean(INTERACTIVE_VIEWS[item.id])
  // Mirrors ConfigEditorDrawer's tab rule: a text-kind editor's Edit view is
  // the file text, which its Raw view already shows with more tools, so that
  // editor has Raw as its only way in, and the card opens Raw.
  // Raw is admin-only — the files hold passwords — so below admin a card
  // offers what the role can open.
  const raw = item.raw && isAdmin
  const rawOnly = item.kind === 'text' && raw && !interactive
  const mapLink = MAP_EDITOR_LINKS[item.id]
  const to = rawOnly ? editorLink(item.id, { tab: 'raw' }) : editorLink(item.id)
  // The open card is brought into view only when it is out of it (a deep
  // link, or the drawer opened from elsewhere); a click on a visible card
  // leaves the grid where it is.
  const ref = useRef(null)
  useEffect(() => {
    if (open) ref.current?.scrollIntoView({ block: 'nearest' })
  }, [open])

  return (
    <div
      ref={ref}
      className={`card config-card config-card-link${open ? ' config-card-open' : ''}`}
      onClick={openOnCardClick(navigate, to)}
    >
      <div className="config-card-head">
        <h3>
          <Link to={to}>{item.title}</Link>
        </h3>
        <span
          className={`status-pill ${item.exists ? 'status-pill-success' : 'status-pill-warning'}`}
        >
          {item.exists ? 'found' : 'missing'}
        </span>
      </div>

      {files.length > 0 ? (
        <FileNames files={files} />
      ) : (
        <p className="config-card-path">
          {item.files_note ||
            (bespoke ? 'Several files — see the page' : "No file — stored in Zedstead's config.json")}
        </p>
      )}

      {help && <p className="config-card-help">{help}</p>}

      <div className="config-card-actions">
        {!bespoke && raw && !rawOnly && (
          // An icon, not a word: the card opens Edit, and Raw is the one
          // other way in, so it is a small </> beside the "?" (2026 Q4).
          <Link
            className="btn btn-icon config-card-raw-btn"
            to={editorLink(item.id, { tab: 'raw' })}
            aria-label={`${item.title}: Raw file`}
            {...tooltipAttrs('Raw — the file text')}
          >
            <HiOutlineCodeBracket aria-hidden="true" />
          </Link>
        )}
        {/* A card that opens nothing but Raw reads as a dead end. Where the
            map editor owns the same file it is the guided editor for it, so
            name it here too — not only inside the editor. */}
        {!bespoke && rawOnly && mapLink && (
          <Link className="btn btn-ghost btn-small" to={mapLink}>
            Map editor
          </Link>
        )}
        {help && (
          // The help text is clamped to 3 lines, so the rest of a long EDITORS
          // entry needs somewhere to live. It used to be the paragraph's own
          // hover tip, which for the short entries repeated what was already on
          // screen and gave no sign it was there at all. Now it is an explicit
          // affordance, an icon button pushed to the card's bottom-right
          // corner.
          <button
            className="btn btn-icon config-card-help-btn"
            type="button"
            aria-label={`About ${item.title}`}
            {...tooltipAttrs(help, { keep: true })}
          >
            {/* Heroicons 2 "question-mark-circle", inlined — one glyph is not
                worth a route through routes.js's icon field. */}
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden>
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M9.879 7.519c1.171-1.025 3.071-1.025 4.242 0 1.172 1.025 1.172 2.687 0 3.712-.203.179-.43.326-.67.442-.745.361-1.45.999-1.45 1.827v.75M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9 5.25h.008v.008H12v-.008Z"
              />
            </svg>
          </button>
        )}
      </div>
    </div>
  )
}

// TraderPlus is a mod, so its whole tab is empty until it is installed — eight
// cards all reading "missing" and no hint as to why. The cards stay (they are
// real editors, and someone may have the configs without the mod listed); this
// says what to do about them.
function TraderPlusMissing() {
  return (
    <div className="card config-missing-mod">
      <div>
        <h2>TraderPlus is not installed</h2>
        <PanelNote>
          None of these config files exist yet. They are created by the TraderPlus mod — install it,
          and Zedstead copies its default configs into your server profile.
        </PanelNote>
      </div>
      <Link className="btn btn-primary" to={modLink('@TraderPlus')}>
        Install TraderPlus
      </Link>
    </div>
  )
}

// Mods that write their own files on their first boot. Before that every card in
// the tab reads "missing" and nothing on this page can create them, so the tab
// says what to do instead. One entry per tab; the banner is the same shape.
const INSTALLED_MODS_LINK = '/mods?tab=Installed'

const MOD_FILES_MISSING = {
  Expansion: {
    title: 'Expansion has not written its settings yet',
    to: '/mods?tab=Install&subtab=Expansion',
    label: 'Expansion modules',
    body: (
      <>
        These files are created by DayZ-Expansion the first time the server starts with the mods
        loaded — 21 under <code>profiles/ExpansionMod/Settings/</code> and 9 under the mission's{' '}
        <code>expansion/settings/</code>. Install the modules,{' '}
        <strong>start the server once, and they appear here.</strong> The per-category Market, Trader, Loadout and Quest files are not editable
        here.
      </>
    ),
  },
  'Dynamic AI': {
    title: 'Dynamic AI has not written its settings yet',
    folder: 'profiles/ExpansionMod/AI/Spatial/',
    note: ' If the folder is still missing after a boot, create it yourself: the mod cannot always.',
    to: INSTALLED_MODS_LINK,
    label: 'Mods',
    body: (
      <>
        The mod writes its files into <code>profiles/ExpansionMod/AI/Spatial/</code> the first
        time the server starts with it loaded. Install it (it needs Expansion AI),{' '}
        <strong>start the server once, and they appear here.</strong>
      </>
    ),
  },
  RadZone: {
    title: 'RadZone has not written its settings yet',
    folder: 'profiles/RadZone/RZConfig/',
    to: INSTALLED_MODS_LINK,
    label: 'Mods',
    body: (
      <>
        The mod writes three files into <code>profiles/RadZone/RZConfig/</code> the first time the
        server starts with it loaded, with zones for Chernarus' two airfields. Install it (it needs
        CF), <strong>start the server once, and they appear here.</strong>
      </>
    ),
  },
  ToxicZone: {
    title: 'ToxicZone has not written its settings yet',
    folder: 'profiles/ToxicZone/TZConfig/',
    to: INSTALLED_MODS_LINK,
    label: 'Mods',
    body: (
      <>
        The mod writes five files into <code>profiles/ToxicZone/TZConfig/</code> the first time the
        server starts with it loaded, with zones for Chernarus. Install it (it needs CF),{' '}
        <strong>start the server once, and they appear here.</strong>
      </>
    ),
  },
}

// The banner's button. Before the mod is installed it opens Install → Supported,
// where the mod has its Install button. After the install the files still wait
// for the first boot, so the banner says so and opens the Dashboard, which
// starts the server. "installed" comes from GET /api/mods/supported
// (app/ops/supported_mods.py), matched by group. A per-mod link (Expansion's
// module picker) is kept as it is.
const SUPPORTED_MODS_LINK = '/mods?tab=Install&subtab=Supported'

function ModFilesMissing({ group, title, body, folder, note = '', to, label }) {
  const [installed, setInstalled] = useState(null)
  useEffect(() => {
    let live = true
    api
      .get('/api/mods/supported')
      .then((r) => live && setInstalled(Boolean(r.mods.find((m) => m.group === group)?.installed)))
      .catch(() => live && setInstalled(null))
    return () => {
      live = false
    }
  }, [group])
  if (installed === false && to === INSTALLED_MODS_LINK) {
    to = SUPPORTED_MODS_LINK
    label = 'Supported mods'
  } else if (installed === true && to === INSTALLED_MODS_LINK) {
    title = `${group} is installed: start the server once`
    body = (
      <>
        The mod writes its files into <code>{folder}</code> the first time the server starts with
        it loaded. <strong>Start the server once, and they appear here.</strong>
      </>
    )
    to = '/'
    label = 'Dashboard'
  }
  return (
    <div className="card config-missing-mod">
      <div>
        <h2>{title}</h2>
        <PanelNote>
          {body}
          {note}
        </PanelNote>
      </div>
      <Link className="btn btn-primary" to={to}>
        {label}
      </Link>
    </div>
  )
}

export default function ConfigsPage() {
  // /configs/:id is this page with that editor open in the right-hand drawer
  // (plans/79). Its ?tab= and ?q= are the editor's, not the grid's.
  const { id = null } = useParams()
  const navigate = useNavigate()
  const [configs, setConfigs] = useState(null)
  // ?q= seeds the card filter (the app-wide search-prefill key), and a new
  // one while the page is open replaces it — on /configs only.
  const [searchParams] = useSearchParams()
  const [filter, setFilter] = useState(() => (id ? '' : searchParams.get('q') || ''))
  const idRef = useRef(id)
  idRef.current = id
  useSearchPrefill(
    useCallback((q) => {
      if (!idRef.current) setFilter(q)
    }, [])
  )
  const urlTabs = useUrlTabs(TABS, subtabsFor)
  // A bespoke editor (kind "custom") opens here too when it has a drawer body
  // (Types.xml, TraderPlus Prices, plans/91). Custom config is a tab instead.
  const editorEntry =
    id && configs ? configs.find((c) => c.id === id && (c.kind !== 'custom' || isBespokeDrawer(c.id))) : null
  const EditorShell = editorEntry?.kind === 'custom' ? BespokeEditorDrawer : ConfigEditorDrawer
  // An editor opened from the Custom config tab (a manual XML file's "Goes
  // into" link, ModXmlPage) keeps that tab behind it. Router state, not a URL
  // key, so a shared link still opens on the editor's own tab.
  // Kept for the editor's whole stay: its own ?tab=/?group= writes drop the
  // router state, and the grid behind jumped to the editor's own tab.
  const location = useLocation()
  const customFor = useRef(null)
  if (!id) customFor.current = null
  else if (location.state?.gridTab === MOD_XML_TAB) customFor.current = id
  const fromCustom = Boolean(id) && customFor.current === id
  const place = editorEntry ? (fromCustom ? { tab: MOD_XML_TAB, subtab: null } : placeOf(editorEntry)) : null
  const tab = place ? place.tab : urlTabs.tab
  const subtab = place ? place.subtab : urlTabs.subtab
  // With an editor open a tab click is a navigation back to the grid, which
  // closes the drawer (and asks first when it holds unsaved edits).
  const setTab = (t) => (id ? navigate(gridUrl(t, null)) : urlTabs.setTab(t))
  const setSubtab = (t) => (id ? navigate(gridUrl(tab, t)) : urlTabs.setSubtab(t))
  const closeEditor = useCallback(() => navigate(gridUrl(tab, subtab)), [navigate, tab, subtab])
  // The EditorSpec.group on screen: a Mods subtab names its group directly.
  // Null on the Custom config tab, which is a page rather than a grid of cards.
  // Nothing at all while an editor's listing entry is still on its way, so the
  // grid behind the drawer does not flash the first tab.
  const waiting = Boolean(id) && !editorEntry
  const group = waiting
    ? undefined
    : tab === MOD_XML_TAB
      ? null
      : tab === 'Mods'
        ? subtab
        : tab
  const pushToast = useToast()

  const load = useCallback(async () => {
    try {
      setConfigs(await api.get('/api/configs'))
    } catch (err) {
      pushToast(err.message || 'Failed to load configs', 'error')
    }
  }, [pushToast])

  useEffect(() => {
    load()
  }, [load])

  // An id with no editor behind it (a typo, a removed editor) goes back to the
  // grid instead of leaving an empty drawer open.
  useEffect(() => {
    if (id && configs && !editorEntry) {
      pushToast(`No config editor named "${id}".`, 'error')
      navigate('/configs', { replace: true })
    }
  }, [id, configs, editorEntry, navigate, pushToast])

  // A group the tab bar doesn't know about would otherwise vanish silently, so
  // anything unrecognised falls into the first tab rather than nowhere. The Mod
  // XML entry is never a card: it is a tab of its own.
  //
  // Mission puts COMMON_MISSION_IDS first, in that order, and the rest after
  // in the backend's order, so an editor added later shows up without a second
  // edit here.
  const inTab = useMemo(() => {
    if (!group || !configs) return []
    const cards = configs.filter(
      (c) => c.id !== MOD_XML_ID && (GROUPS.includes(c.group) ? c.group === group : group === GROUPS[0])
    )
    if (group === 'Mission') {
      return [
        ...COMMON_MISSION_IDS.map((id) => cards.find((c) => c.id === id)).filter(Boolean),
        ...cards.filter((c) => !COMMON_MISSION_IDS.includes(c.id)),
      ]
    }
    return cards
  }, [configs, group])

  // Filter within the active tab, so the counter answers for what's on screen.
  // Display-only, as everywhere else — it never touches the loaded list.
  const matchKeys = (c) => [c.title, c.id, ...(c.files || []).map((f) => f.name)]
  const visible = useMemo(() => fuzzyFilter(inTab, filter, matchKeys), [inTab, filter])

  // Off the unfiltered tab list, so typing in the filter box can't make the
  // banner appear or vanish. `inTab.length` guards the pre-load state, where
  // "nothing exists" is not yet an answer.
  const traderPlusMissing =
    group === 'TraderPlus' && inTab.length > 0 && !inTab.some((c) => c.exists)
  // Same shape, different cause: these mods write their files on their first
  // boot, so before that every card reads "missing" and nothing the user does
  // on this page can create them.
  const modFilesMissing =
    MOD_FILES_MISSING[group] && inTab.length > 0 && !inTab.some((c) => c.exists)
      ? MOD_FILES_MISSING[group]
      : null

  return (
    <div className={`page configs-page${editorEntry ? ' has-side-drawer' : ''}`}>
      <div className="page-header">
        <h1>Config Editors</h1>
      </div>

      <PageIntro page="/configs" />

      {!waiting && (
      <Tabs tabs={TABS} active={tab} onSelect={setTab} />
      )}

      {!waiting && subtab && (
        <Tabs tabs={subtabsFor(tab)} active={subtab} onSelect={setSubtab} sub />
      )}

      {traderPlusMissing && <TraderPlusMissing />}
      {modFilesMissing && <ModFilesMissing group={group} {...modFilesMissing} />}
      {group === 'Expansion' && <ZoneOfferCard system="market" />}

      {group === null && (
        <Suspense fallback={<LoadingState />}>
          <ModXmlPage embedded editorOpen={Boolean(editorEntry)} onCloseEditor={closeEditor} />
        </Suspense>
      )}

      {group && (
        <>
          <FilterBox
            value={filter}
            onChange={setFilter}
            placeholder="Filter config files…"
            shown={visible.length}
            total={inTab.length}
            label="files"
          />

          {filter.trim() && visible.length === 0 && (
            <PanelNote>
              No config files in {group} match “{filter}”.
            </PanelNote>
          )}

          <div className="config-card-grid">
            {visible.map((item) => (
              <ConfigCard
                key={item.id}
                item={item}
                open={item.id === id}
              />
            ))}
          </div>
        </>
      )}

      {editorEntry && (
        // Not keyed by id: the drawer stays put while another card's editor
        // replaces this one, as /configs/:id always behaved across links.
        <EditorShell
          id={id}
          title={editorEntry.title}
          storageKey={DRAWER_WIDTH_KEY}
          onClose={closeEditor}
        />
      )}
    </div>
  )
}
