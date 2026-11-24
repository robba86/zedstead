import { Fragment, Suspense, createRef, lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, NavLink, Outlet, matchPath, useLocation, useNavigate } from 'react-router-dom'
import { Tooltip as ReactTooltip } from 'react-tooltip'
import {
  HiOutlineArrowRightOnRectangle,
  HiOutlineArrowUturnLeft,
  HiOutlineEye,
  HiOutlineEyeSlash,
  HiOutlineBars3,
  HiOutlineCheck,
  HiOutlineChevronDown,
  HiOutlineChevronDoubleLeft,
  HiOutlineChevronDoubleRight,
  HiOutlineLifebuoy,
  HiOutlineMagnifyingGlass,
  HiOutlineUserCircle,
  HiOutlineXMark,
} from 'react-icons/hi2'
import { api } from '../api'
import { useWs } from '../ws'
import { useToast } from './Toast.jsx'
import { useConfirm } from './useConfirm.jsx'
import { LEAVE_DIALOG, discardUnsavedChanges, hasUnsavedChanges } from '../useUnsavedChanges.js'
import { ServerStatusProvider } from '../serverStatus'
import { JobsProvider, useJobs } from '../jobs'
import { GuideProvider, useGuide } from '../guide.jsx'
import { RookieProvider, useRookie } from '../rookie.jsx'
import { useQuickSetupState } from '../quickSetupState.js'
import InfoBar, { InfoBarCompact, InfoBarToggle } from './InfoBar.jsx'
import Breadcrumbs from './Breadcrumbs.jsx'
import ActivityDrawer from './ActivityDrawer.jsx'
import GuideDrawer from './GuideDrawer.jsx'
import ErrorBoundary from './ErrorBoundary.jsx'
import StyleToggle from './StyleToggle.jsx'
import ThemeToggle from './ThemeToggle.jsx'
import { RichTooltipContent, tooltipAttrs } from './Tooltip.jsx'
import RowAction from './RowAction.jsx'
import { MOBILE_QUERY, useMediaQuery } from '../useMediaQuery.js'
import { FULL_BLEED_PATHS, minRoleFor, navItemsFor } from '../routes'
import { roleAtLeast, useRole } from '../role.jsx'
import { instanceHref, instanceKey, instanceOptionLabel } from '../instance'
import ErrorBanner, { REOPEN_DAILY_MS } from './ErrorBanner.jsx'
import IconButton from './IconButton.jsx'
import Wordmark from './Wordmark.jsx'
import { showDesktopNotice } from '../desktopNotify.js'
import LoadingState from './LoadingState.jsx'

// The Ctrl+K palette is its own chunk (plans/75): the shell only knows how to
// open it. The chunk is fetched once the page is idle, so the first Ctrl+K
// opens at once: before that, text typed straight after the shortcut fell
// into the page while the chunk loaded.
const loadPalette = () => import('./CommandPalette.jsx')
const CommandPalette = lazy(loadPalette)

// '/' opens the palette only where it cannot be text being typed.
function isTextTarget(el) {
  if (!el) return false
  if (el.isContentEditable) return true
  const tag = el.tagName
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT'
}

// Collapsed sidebar — per-browser localStorage, best-effort like theme.jsx and
// guide.jsx: it describes the viewer's screen, not the server, so it is not a
// DayzConfig field and has no endpoint.
const COLLAPSED_KEY = 'zsm.sidebar.collapsed'

function readCollapsed() {
  try {
    return localStorage.getItem(COLLAPSED_KEY) === '1'
  } catch {
    return false
  }
}

// The instance's name under the wordmark in the sidebar. Its own case (the
// brand row is upper-case), and a long name wraps. The tip says what the name is.
// The name links to the Instances page for a role that may open it (admin,
// routes.js), the place to add, rename or switch servers.
function InstanceLabel({ label, className = '' }) {
  const { role } = useRole()
  if (roleAtLeast(role, minRoleFor('/instances'))) {
    return (
      <Link
        to="/instances"
        className={`brand-instance brand-instance-link${className}`}
        {...tooltipAttrs(`Instance: ${label}. Open the Instances page`)}
      >
        {label}
      </Link>
    )
  }
  return (
    <span className={`brand-instance${className}`} {...tooltipAttrs(`Instance: ${label}`)}>
      {label}
    </span>
  )
}

// With several instances the name is a button that opens a small menu of
// them (plans/62 step 7; a select until 2026 Q4, which took a row of its own).
// Choosing one is a full page load of the same page on that instance: every
// per-instance cache, socket and form starts over at once, and the page is
// kept where the other instance has it. Unsaved edits get the question a
// navigation gets. An Experimental server (plans/66) says so in the text and
// takes the warning colour. The menu closes on Escape or a click outside.
function InstanceSwitch({ label }) {
  const { instance, instances, startup, role } = useRole()
  const { pathname, search, hash } = useLocation()
  const confirm = useConfirm()
  const [open, setOpen] = useState(false)
  const boxRef = useRef(null)
  useEffect(() => {
    if (!open) return undefined
    const onDown = (e) => {
      if (!boxRef.current?.contains(e.target)) setOpen(false)
    }
    const onKey = (e) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('pointerdown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('pointerdown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])
  const branch = instance?.branch === 'experimental' ? ' branch-experimental' : ''
  if (!instances || instances.length < 2) {
    return label ? (
      <InstanceLabel label={instanceOptionLabel({ label, branch: instance?.branch })} className={branch} />
    ) : null
  }
  const choose = async (id) => {
    setOpen(false)
    if (id === instance.id) return
    if (hasUnsavedChanges()) {
      if (!(await confirm(LEAVE_DIALOG))) return
      discardUnsavedChanges()
    }
    window.location.assign(instanceHref(id, startup, pathname + search + hash))
  }
  return (
    <span className="instance-menu" ref={boxRef}>
      <button
        type="button"
        className={`brand-instance instance-menu-btn${branch}`}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`Instance: ${instanceOptionLabel(instance)}. Switch instance`}
        {...(open ? {} : tooltipAttrs('Switch instance'))}
        onClick={() => setOpen((o) => !o)}
      >
        <span className="instance-menu-name">{instanceOptionLabel(instance)}</span>
        <HiOutlineChevronDown aria-hidden="true" />
      </button>
      {open && (
        <div className="instance-menu-list" role="menu">
          {instances.map((row) => {
            const current = row.id === instance.id
            return (
              <button
                key={row.id}
                type="button"
                role="menuitemradio"
                aria-checked={current}
                className={`instance-menu-item${row.branch === 'experimental' ? ' branch-experimental' : ''}`}
                onClick={() => choose(row.id)}
              >
                <span className="instance-menu-check">{current && <HiOutlineCheck aria-hidden="true" />}</span>
                <span className="instance-menu-name">{instanceOptionLabel(row)}</span>
              </button>
            )
          })}
          {roleAtLeast(role, minRoleFor('/instances')) && (
            <Link to="/instances" role="menuitem" className="instance-menu-item instance-menu-manage" onClick={() => setOpen(false)}>
              Manage instances
            </Link>
          )}
        </div>
      )}
    </span>
  )
}

// Inner shell — runs inside JobsProvider/ServerStatusProvider so the sidebar,
// content and drawer can all read the global job/status state.
// True while the scrolling element in `ref` hides content below its bottom
// edge: the sidebar nav list on a short screen. It updates on scroll and on a
// resize of the list, and again when `content` (the nav items) changes.
// A list that is laid out but off screen (the closed phone drawer) still
// measures, so the state is right the moment the drawer opens.
// One of the menu's pill switches in the sidebar's bottom row (index.css
// .theme-switch): `knob` is the class that draws the icon on its knob.
function SidebarSwitch({ checked, onChange, label, tip, knob }) {
  return (
    <button
      type="button"
      className="sidebar-action pages-toggle-btn"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={() => onChange(!checked)}
      {...tooltipAttrs(tip)}
    >
      <span className={`theme-switch ${knob}`} aria-hidden="true" />
    </button>
  )
}

function useHiddenBelow(ref, content) {
  const [hidden, setHidden] = useState(false)
  useEffect(() => {
    const el = ref.current
    if (!el) return undefined
    const update = () => setHidden(el.scrollTop + el.clientHeight < el.scrollHeight - 1)
    update()
    el.addEventListener('scroll', update, { passive: true })
    const observer = new ResizeObserver(update)
    observer.observe(el)
    return () => {
      el.removeEventListener('scroll', update)
      observer.disconnect()
    }
  }, [ref, content])
  return hidden
}

// sessionStorage: set when this tab sent the admin from the Dashboard to Quick setup.
const QUICK_SETUP_LANDED_KEY = 'zsm.quickSetupLanded'

function Shell({ onLogout }) {
  const navigate = useNavigate()
  const pushToast = useToast()
  const { jobRunning, setDrawerOpen } = useJobs()
  const { open: guideOpen, toggleGuide } = useGuide()
  // A link the role cannot use is gone, not disabled: a greyed-out Mods entry
  // on a viewer's panel invites a support question about a decision their
  // admin already made. Hiding it is courtesy — the API refuses regardless.
  const { role, username, instance, refreshMe } = useRole()
  const { rookie, setRookie, menu, setMenu, resetMenu, quickSetupDismissed } = useRookie()
  const quickSetupState = useQuickSetupState()
  // Set up, or dismissed by the admin: either way it leaves the menu.
  const quickSetupDone = quickSetupState?.offered === false || quickSetupDismissed
  const { pathname: currentPath } = useLocation()
  // A new server's first page is Quick setup, not the Dashboard: an admin who
  // lands on the Dashboard while Quick setup is offered goes there once per tab,
  // so the Dashboard link still opens the Dashboard after that.
  useEffect(() => {
    if (!quickSetupState?.offered || quickSetupDismissed || currentPath !== '/' || !roleAtLeast(role, 'admin')) return
    const key = instanceKey(QUICK_SETUP_LANDED_KEY)
    try {
      if (sessionStorage.getItem(key)) return
      sessionStorage.setItem(key, '1')
    } catch {
      // No session storage: send the admin once per page load instead.
    }
    navigate('/quick-setup', { replace: true })
  }, [quickSetupState, quickSetupDismissed, currentPath, role, navigate])
  const navItems = useMemo(
    () => navItemsFor(role, rookie, quickSetupDone, menu),
    [role, rookie, quickSetupDone, menu]
  )
  // Edit menu: every page this role can use, each with an eye button. The
  // first change stores the viewer's own menu (rookie.jsx).
  const [editingMenu, setEditingMenu] = useState(false)
  const allNavItems = useMemo(() => navItemsFor(role, false, quickSetupDone), [role, quickSetupDone])
  const shownPaths = useMemo(() => new Set(navItems.map((item) => item.to)), [navItems])
  const listedItems = editingMenu ? allNavItems : navItems
  const toggleMenuItem = useCallback(
    (path) => {
      const next = new Set(shownPaths)
      if (next.has(path)) next.delete(path)
      else next.add(path)
      setMenu([...next])
    },
    [shownPaths, setMenu]
  )
  const navListRef = useRef(null)
  const navHidesMore = useHiddenBelow(navListRef, listedItems)
  // Which server this panel serves (plans/62 step 1, D2: always shown), so two
  // panels in two tabs can be told apart. Empty only on the pre-instances
  // layout, which has no instance to name.
  const instanceLabel = instance?.label || ''
  useEffect(() => {
    document.title = instanceLabel ? `Zedstead · ${instanceLabel}` : 'Zedstead'
  }, [instanceLabel])
  const [newVersion, setNewVersion] = useState(null)
  const [version, setVersion] = useState('')
  const [exposure, setExposure] = useState(null)
  const [collapsed, setCollapsed] = useState(readCollapsed)
  // Below the mobile breakpoint the sidebar is an off-canvas drawer instead of
  // a column: `navOpen` slides it in over a scrim. Deliberately NOT persisted
  // the way `collapsed` is — a drawer covering the page is a transient state,
  // and restoring it on load would hide the page you navigated to.
  const isMobile = useMediaQuery(MOBILE_QUERY)
  const [navOpen, setNavOpen] = useState(false)
  const { pathname } = useLocation()
  // Read inside the WS handler without re-subscribing it on every job tick.
  const jobRunningRef = useRef(jobRunning)
  jobRunningRef.current = jobRunning
  // Pages that fill the viewport (the map editor) opt out of content padding
  // via routes.js, so the flag stays with the rest of the route metadata.
  const fullBleed = FULL_BLEED_PATHS.some((p) => matchPath(p, pathname))

  useEffect(() => {
    api
      .get('/api/health')
      .then((data) => setVersion(data.version || ''))
      .catch(() => {})
    api
      .get('/api/version-check')
      .then((data) => setNewVersion(data.latest))
      .catch(() => {})
  }, [])

  // How `zsm web` was started cannot change while it is running, so this
  // is fetched once. A failure leaves it null and warns about nothing —
  // a security banner has to be raised on evidence. Only an admin asks: the
  // route is admin-only (permissions.NON_VIEWER_READS), and every other role
  // got a 403 on each page load.
  const isAdmin = roleAtLeast(role, 'admin')
  useEffect(() => {
    if (!isAdmin) return
    api
      .get('/api/security')
      .then(setExposure)
      .catch(() => {})
  }, [isAdmin])

  // The width lives on <body>, not on the sidebar, because the bottom-docked
  // activity drawer is positioned against it too and is not a descendant —
  // both read the --sidebar-w the class swaps (see index.css).
  useEffect(() => {
    document.body.classList.toggle('sidebar-collapsed', collapsed)
    try {
      localStorage.setItem(COLLAPSED_KEY, collapsed ? '1' : '')
    } catch {
      // ignore — the sidebar just won't remember its state
    }
    return () => document.body.classList.remove('sidebar-collapsed')
  }, [collapsed])

  // Tapping a nav link has to dismiss the drawer, or the page it navigated to
  // is behind the thing you just used. Keyed on pathname rather than wired into
  // each link's onClick so it also covers the breadcrumbs, the guide's links
  // and a browser Back.
  useEffect(() => {
    setNavOpen(false)
  }, [pathname])

  // Rotating to landscape (or resizing a desktop window back up) leaves the
  // drawer state stranded: the sidebar is a column again but `nav-open` is
  // still on <body>, holding the scrim over the page.
  useEffect(() => {
    if (!isMobile) setNavOpen(false)
  }, [isMobile])

  // The search palette: Ctrl+K / Cmd+K and Ctrl+P / Cmd+P from anywhere, text
  // fields included (the browser's own Ctrl+K is a search-bar shortcut and its
  // Ctrl+P prints; print stays in the browser menu), and
  // '/' from anywhere that is not a text field (plans/75 D2).
  const [searchOpen, setSearchOpen] = useState(false)
  const closeSearch = useCallback(() => setSearchOpen(false), [])
  useEffect(() => {
    const onKey = (e) => {
      if ((e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && ['k', 'p'].includes(e.key.toLowerCase())) {
        e.preventDefault()
        setSearchOpen((o) => !o)
      } else if (
        e.key === '/' &&
        !e.ctrlKey &&
        !e.metaKey &&
        !e.altKey &&
        !isTextTarget(e.target) &&
        !document.querySelector('.modal-backdrop')
      ) {
        e.preventDefault()
        setSearchOpen(true)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])
  useEffect(() => {
    const warm = () => loadPalette().catch(() => {})
    if ('requestIdleCallback' in window) {
      const id = window.requestIdleCallback(warm, { timeout: 3000 })
      return () => window.cancelIdleCallback(id)
    }
    const id = window.setTimeout(warm, 1500)
    return () => window.clearTimeout(id)
  }, [])

  // Escape closes it — same affordance as every other overlay in the app.
  useEffect(() => {
    if (!navOpen) return
    const onKey = (e) => {
      if (e.key === 'Escape') setNavOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [navOpen])

  // On <body> for the same reason `sidebar-collapsed` is: the scrim and the
  // bottom-docked activity drawer are not descendants of the sidebar but both
  // have to react (see "Mobile shell" in index.css). `overflow: hidden` on the
  // same class stops the page behind the drawer from scrolling under it.
  useEffect(() => {
    document.body.classList.toggle('nav-open', navOpen)
    return () => document.body.classList.remove('nav-open')
  }, [navOpen])

  // Collapsed, the labels are clipped (index.css) and the icon is all that is
  // left on screen — so the label becomes the tooltip. Expanded it is already
  // right there, and a tip repeating it is noise. Never on mobile: the drawer
  // always shows its labels, and a tooltip on a tap target is noise there.
  const labelTip = useCallback(
    (text) => (collapsed && !isMobile ? tooltipAttrs(text) : {}),
    [collapsed, isMobile]
  )

  const confirm = useConfirm()
  const handleLogout = useCallback(async () => {
    // Logging out unmounts every form, so unsaved edits get the same question
    // a navigation gets (the guard itself never sees this one: see below).
    if (hasUnsavedChanges()) {
      if (!(await confirm(LEAVE_DIALOG))) return
      discardUnsavedChanges()
    }
    try {
      await api.post('/api/auth/logout')
    } catch {
      // ignore — we're logging out regardless
    }
    onLogout()
    navigate('/login', { replace: true })
  }, [confirm, navigate, onLogout])

  // Search opens over the page, so the nav drawer closes first. Shared by the
  // sidebar's Search row and the phone's floating search button.
  const openSearch = useCallback(() => {
    setNavOpen(false)
    setSearchOpen(true)
  }, [])

  const handleWsMessage = useCallback(
    (msg) => {
      if (msg.type === 'instances_changed') {
        // Added, removed or renamed anywhere: the switcher follows.
        refreshMe()
      } else if (msg.type === 'service_changed' && (msg.action === 'start' || msg.action === 'restart')) {
        // Server (re)started from the web UI — pop the drawer open so its
        // startup output (Server Log accordion) is immediately visible.
        setDrawerOpen(true)
      } else if (msg.type === 'notification') {
        // Server-pushed notifications from the headless WebNotifier (restart
        // warnings, doctor checks, crash detection, mod updates, log
        // cleanup), shown as toasts. A `link` makes the toast clickable,
        // deep-linking into the app.
        //
        // Doctor toasts are dropped while a job is mutating the server: an
        // uninstall/reinstall legitimately makes files vanish mid-run, and
        // "server files missing" is misleading, not useful. The notifier
        // pauses its own loop (WebNotifier.busy); this catches the check that
        // was already in flight when the job started.
        if (msg.link === '/doctor' && jobRunningRef.current) return
        const opts = msg.link ? { onClick: () => navigate(msg.link) } : {}
        // Also to the computer's notification area, while this tab is hidden
        // and this browser has it switched on (desktopNotify.js).
        showDesktopNotice(msg.message, opts.onClick)
        pushToast(
          msg.message,
          msg.severity === 'error' ? 'error' : 'info',
          (msg.timeout || 5) * 1000,
          opts
        )
      }
    },
    [pushToast, navigate, setDrawerOpen, refreshMe]
  )
  useWs(handleWsMessage)

  return (
    <div className="shell">
      {/* Mobile only (index.css hides it above the breakpoint): the sidebar is
          off-canvas down there, so the hamburger is the only way to it, and the
          brand + job dot would otherwise have gone off-screen with it. */}
      <header className="topbar">
        <button
          className="topbar-nav-btn"
          onClick={() => setNavOpen(true)}
          aria-expanded={navOpen}
          aria-controls="app-sidebar"
          aria-label="Open navigation"
        >
          <HiOutlineBars3 className="topbar-nav-icon" aria-hidden="true" />
        </button>
        <span className="topbar-brand">
          <Link to="/" className="brand-home">
            <Wordmark />
          </Link>
        </span>
        {/* No instance picker up here: the sidebar has it, and the
            room beside the wordmark goes to the collapsed status strip. */}
        {jobRunning && (
          <span className="job-indicator" {...tooltipAttrs('A process is running')}>
            ●
          </span>
        )}
        {/* The status strip's collapse toggle, up here on a phone so the
            strip does not spend a column on it. Before the buttons in the
            source, so the mirrored bar draws it to the right of Start. */}
        <InfoBarToggle />
        <InfoBarCompact />
      </header>
      {/* Click-away for the drawer. Rendered only while it is open so it can
          never swallow a click on the desktop layout, and it is a <button> so
          the dismiss is reachable by keyboard and announced, not a dead div. */}
      {navOpen && (
        <button
          className="nav-scrim"
          onClick={() => setNavOpen(false)}
          aria-label="Close navigation"
          tabIndex={-1}
        />
      )}
      {/* Phone only: the sidebar's Search, account and Log out rows float as
          round icon buttons on the scrim beside the open drawer (index.css
          hides the rows there). Same handlers as the rows below. */}
      {navOpen && isMobile && (
        <div className="nav-float-actions">
          <IconButton
            className="btn btn-icon nav-float-btn"
            icon={HiOutlineMagnifyingGlass}
            label="Search"
            tip="Search the app"
            onClick={openSearch}
            aria-haspopup="dialog"
          />
          <IconButton
            className="btn btn-icon nav-float-btn"
            icon={HiOutlineUserCircle}
            label={username ? `Signed in as ${username}` : 'Your account'}
            onClick={() => navigate('/account')}
            aria-current={pathname === '/account' ? 'page' : undefined}
          />
          <IconButton
            className="btn btn-icon nav-float-btn"
            icon={HiOutlineArrowRightOnRectangle}
            label="Log out"
            onClick={handleLogout}
          />
        </div>
      )}
      <nav id="app-sidebar" className="sidebar" aria-hidden={isMobile && !navOpen}>
        <div className="sidebar-title">
          {/* Two jobs, one button, because there is only ever one of them on
              screen: on desktop it collapses the column to the icon rail (with
              the labels clipped it is the only way back out), on mobile it
              dismisses the drawer — collapsing is meaningless there, since the
              drawer is either fully open or fully gone. */}
          <button
            className="sidebar-collapse-btn"
            onClick={() => (isMobile ? setNavOpen(false) : setCollapsed((c) => !c))}
            aria-expanded={isMobile ? navOpen : !collapsed}
            aria-label={
              isMobile ? 'Close navigation' : collapsed ? 'Expand sidebar' : 'Collapse sidebar'
            }
            {...(isMobile ? {} : tooltipAttrs(collapsed ? 'Expand sidebar' : 'Collapse sidebar'))}
          >
            {isMobile ? (
              <HiOutlineXMark className="sidebar-collapse-icon" aria-hidden="true" />
            ) : collapsed ? (
              <HiOutlineChevronDoubleRight className="sidebar-collapse-icon" aria-hidden="true" />
            ) : (
              <HiOutlineChevronDoubleLeft className="sidebar-collapse-icon" aria-hidden="true" />
            )}
          </button>
          {/* Pushed right by margin-left:auto, keeping the row's original
              look; hidden when collapsed, where the wordmark does not fit.
              The instance's name or menu sits under the wordmark. */}
          {/* Left of the wordmark, so the sidebar's edge never clips it. */}
          {jobRunning && (
            <span className="job-indicator" {...tooltipAttrs('A process is running')}>
              ●
            </span>
          )}
          <span className="sidebar-brand">
            <Link to="/" className="brand-home">
              <Wordmark />
            </Link>
            {instanceLabel && (
              <span className="sidebar-instance">
                <InstanceSwitch label={instanceLabel} />
              </span>
            )}
          </span>
        </div>
        <ul className="nav-list" ref={navListRef}>
          {listedItems.map(({ to, href, label, Icon }) => (
            <Fragment key={to}>
            {to === '/help' && (
              // The guide is a drawer, not a page, so its way in is a button
              // that works from wherever you already are. It sits just above
              // Help, the other "how do I" entry. On mobile it also closes the
              // nav drawer, which would otherwise cover the guide it opened.
              <li>
                <button
                  className={`sidebar-action guide-toggle-btn${guideOpen ? ' active' : ''}`}
                  onClick={() => {
                    toggleGuide()
                    if (isMobile) setNavOpen(false)
                  }}
                  aria-pressed={guideOpen}
                  {...tooltipAttrs('Step-by-step setup guide')}
                >
                  <HiOutlineLifebuoy className="nav-icon" aria-hidden="true" />
                  <span className="nav-label">Guide</span>
                </button>
              </li>
            )}
            <li className={editingMenu ? `nav-edit-row${shownPaths.has(to) ? '' : ' is-hidden'}` : undefined}>
              {href ? (
                // Help: the docs on zedstead.com, in a new tab so the panel stays
                // where it was.
                <a href={href} target="_blank" rel="noopener noreferrer" className="nav-link" {...labelTip(label)}>
                  {Icon && <Icon className="nav-icon" aria-hidden="true" />}
                  <span className="nav-label">{label}</span>
                </a>
              ) : (
              <NavLink
                to={to}
                // A subpage keeps its section lit (/configs/tp-price lights
                // Configs). Only the Dashboard ('/') matches exactly, since
                // every path starts with '/'.
                end={to === '/'}
                className={({ isActive }) => (isActive ? 'nav-link active' : 'nav-link')}
                {...labelTip(label)}
              >
                {/* Heroicons 2 glyph from routes.js — decorative, the label
                    right beside it is the accessible name. */}
                {Icon && <Icon className="nav-icon" aria-hidden="true" />}
                <span className="nav-label">{label}</span>
              </NavLink>
              )}
              {editingMenu && (
                <button
                  type="button"
                  className="nav-edit-toggle"
                  onClick={() => toggleMenuItem(to)}
                  aria-pressed={shownPaths.has(to)}
                  aria-label={`${shownPaths.has(to) ? 'Hide' : 'Show'} ${label} in the menu`}
                  {...tooltipAttrs(shownPaths.has(to) ? 'Hide from the menu' : 'Show in the menu')}
                >
                  {shownPaths.has(to) ? <HiOutlineEye aria-hidden="true" /> : <HiOutlineEyeSlash aria-hidden="true" />}
                </button>
              )}
            </li>
            </Fragment>
          ))}
          {/* Rookie mode: a quiet way out to every page, right under the short menu. */}
          {rookie && !menu && !editingMenu && (
            <li>
              <button type="button" className="sidebar-advanced-link" onClick={() => setRookie(false)}>
                Show all pages
              </button>
            </li>
          )}
        </ul>
        {/* Actions, not routes — but they read as the rest of the list, so they
            carry .sidebar-action (the nav link's box on a <button>) and the
            same Heroicons 2 glyph at .nav-icon size. */}
        <div className={`sidebar-actions${navHidesMore ? ' has-more' : ''}`}>
          <button
            className="sidebar-action search-toggle-btn"
            onClick={openSearch}
            aria-haspopup="dialog"
            aria-keyshortcuts="Control+K /"
            {...tooltipAttrs('Search the app (Ctrl+P, Ctrl+K or /)')}
          >
            <HiOutlineMagnifyingGlass className="nav-icon" aria-hidden="true" />
            <span className="nav-label">Search</span>
          </button>
          {/* Who you are signed in as, and the way to your own password and
              second factor. It sits here rather than in the nav because the
              nav is places to work and this is about the person working —
              and because it was the only chrome that never said whose panel
              this is. Nothing is hidden by role: everything behind it acts
              on the caller's own account (TODO B5). */}
          <NavLink
            to="/account"
            className={({ isActive }) =>
              `sidebar-action${isActive ? ' active' : ''}`
            }
            {...labelTip(username ? `Signed in as ${username}` : 'Your account')}
          >
            <HiOutlineUserCircle className="nav-icon" aria-hidden="true" />
            <span className="nav-label">{username || 'Your account'}</span>
          </NavLink>
          <button
            className="sidebar-action logout-btn"
            onClick={handleLogout}
            {...labelTip('Log out')}
          >
            <HiOutlineArrowRightOnRectangle className="nav-icon" aria-hidden="true" />
            <span className="nav-label">Log out</span>
          </button>
        </div>
        {/* Compact/classic and light/dark sit apart at the very bottom: they
            are about how the panel looks, not something you do in it. */}
        <div className="sidebar-theme">
          <div className="sidebar-toggles">
            {/* The menu's own switches: all pages or the short menu, and
                editing it. An own menu replaces both modes, so the pages
                switch goes with it, and Reset takes its place. */}
            {!menu && !editingMenu && (
              <SidebarSwitch
                checked={!rookie}
                onChange={(all) => setRookie(!all)}
                label="Show all pages"
                tip={rookie ? 'Show all pages' : 'Show the short menu (Rookie mode)'}
                knob="pages-switch"
              />
            )}
            <SidebarSwitch
              checked={editingMenu}
              onChange={(on) => {
                // The eye buttons need the labels, so editing opens a collapsed sidebar.
                if (on && collapsed && !isMobile) setCollapsed(false)
                setEditingMenu(on)
              }}
              label="Edit the menu"
              tip={editingMenu ? 'Done editing the menu' : 'Choose the pages in this menu'}
              knob="menu-edit-switch"
            />
            {menu && (
              <RowAction
                icon={HiOutlineArrowUturnLeft}
                label="Reset the menu to the default"
                onClick={() => {
                  resetMenu()
                  setEditingMenu(false)
                }}
              />
            )}
            <StyleToggle />
            <ThemeToggle />
          </div>
        </div>
      </nav>
      {/* Flex sibling, between the sidebar and the content: opening the guide
          narrows the page instead of covering it, so its links can be used as
          navigation while it stays open. */}
      <GuideDrawer />
      {searchOpen && (
        <Suspense fallback={null}>
          <CommandPalette role={role} onClose={closeSearch} />
        </Suspense>
      )}
      <main className={fullBleed ? 'content content-bleed' : 'content'}>
        <InfoBar />
        {/* Not dismissible on purpose: it describes a state of the machine,
            not a message, and it goes away the moment either fix is made. */}
        {exposure?.exposed && (
          <ErrorBanner dismissKey={`exposure:${exposure.reason}`} reopenAfterMs={REOPEN_DAILY_MS}>
            {/* One span: .banner-error is a flex row (message + Retry button),
                so bare text and <code> would each become a column. */}
            <span>
              <strong>This app is reachable from the network without TLS.</strong>{' '}
              {exposure.reason} Either bind it to <code>127.0.0.1</code> and reach it over
              an SSH tunnel, or give it a certificate (<code>zsm web --self-signed</code>,
              or a real one behind a reverse proxy).
            </span>
          </ErrorBanner>
        )}
        {newVersion && (
          <div className="banner banner-warning">
            Zedstead {newVersion} is available (you run an older version).{' '}
            <Link to="/update">Update it on the Update page</Link>.
          </div>
        )}
        <Breadcrumbs />
        {/* A crash in any page shows a message + retry instead of blanking the
            whole SPA. Keyed on the page so navigating away clears the error.
            Opening an editor (/configs/<id>) is the same page with a drawer,
            so it keeps the key: a new one remounted the grid and lost its
            scroll position on every card click. */}
        <ErrorBoundary key={boundaryKey(pathname)} name={`route ${pathname}`}>
          {/* Pages are lazy (see App.jsx), so the boundary sits here and not
              around <Routes>: only the content area waits for the chunk, the
              sidebar and breadcrumbs stay on screen and clickable. */}
          <Suspense fallback={<LoadingState />}>
            <Outlet />
          </Suspense>
        </ErrorBoundary>
      </main>
      <ActivityDrawer version={version} />
    </div>
  )
}

// How long the pointer rests on something before its tip shows. Longer on a
// desktop, so a tip does not flash up under a pointer only passing over; on a
// phone the tip comes from a tap (focus), where a wait reads as lag.
const TIP_DELAY_DESKTOP = 300
const TIP_DELAY_MOBILE = 150

// A touch screen has no hover. A tap on a control is the action itself, so a
// tip there opened under the finger, stayed until the next tap and covered
// what the tap had just changed (the status strip's Collapse on a phone). So
// on a touch screen a plain tip never opens on a control or on a box that
// holds one, and every tip closes on a tap elsewhere or a scroll. A tip on
// text (a label, a status, why a button is disabled) and the "?" help tips
// still open on a tap.
const TOUCH_QUERY = '(hover: none)'
const TOUCH_CONTROL =
  'a[href], button:not(:disabled), input:not(:disabled), select, textarea, [role="button"], [role="tab"], [role="switch"], [role="separator"]'
const TOUCH_CLOSE = { clickOutsideAnchor: true, scroll: true }

function isTouchControl(anchor) {
  return !!anchor && (anchor.matches(TOUCH_CONTROL) || !!anchor.querySelector(TOUCH_CONTROL))
}

// A form field's label and its control carry the same tip. Opened from the
// control it would sit on the control and cover the label, so it opens on the
// label instead, in the one place whichever of the two the pointer or the
// focus is on. The field's box is marked `data-tip-at-label` (FormEditor's
// <li>) and its label is the <Tooltip> right inside it. Returns that label for
// a control's anchor, or null for any other anchor.
function fieldTipLabel(anchor) {
  const label = anchor?.closest?.('[data-tip-at-label]')?.querySelector(':scope > .tooltip-trigger')
  if (!label || label.contains(anchor)) return null
  return label.getAttribute('data-tooltip-content') === anchor.getAttribute('data-tooltip-content') ? label : null
}

// disableTooltip for the shared instances: a field's control never anchors a
// tip itself (useFieldTipsAtLabel opens the label's), and on a touch screen a
// plain tip never opens on a control.
const isFieldControl = (anchor) => !!fieldTipLabel(anchor)
const isFieldOrTouchControl = (anchor) => isFieldControl(anchor) || isTouchControl(anchor)

// Hover and focus on a field's control, passed on to the field's label as the
// same event, so react-tooltip (which listens on the document) opens and
// closes the label's tip. The event sent from the label resolves to no field
// control, so it is never passed on twice. Capture phase, so the label's event
// reaches react-tooltip before the control's own: its focusin handler spends
// its 50 ms debounce on the first event it sees, and the control's (an anchor
// that is switched off) opens nothing, so Tab into a field showed no tip.
const FIELD_TIP_EVENTS = ['mouseover', 'mouseout', 'focusin', 'focusout']

function useFieldTipsAtLabel(touch) {
  useEffect(() => {
    const forward = (e) => {
      const anchor = e.target instanceof Element ? e.target.closest('[data-tooltip-id]') : null
      const label = fieldTipLabel(anchor)
      if (!label) return
      // A move inside the control is not an enter or a leave.
      if (e.relatedTarget instanceof Node && anchor.contains(e.relatedTarget)) return
      if (touch && anchor.getAttribute('data-tooltip-id') === 'app-tip' && isTouchControl(anchor)) return
      const Event = e.type.startsWith('focus') ? FocusEvent : MouseEvent
      label.dispatchEvent(new Event(e.type, { bubbles: true }))
    }
    for (const type of FIELD_TIP_EVENTS) document.addEventListener(type, forward, true)
    return () => {
      for (const type of FIELD_TIP_EVENTS) document.removeEventListener(type, forward, true)
    }
  }, [touch])
}

// A touch screen has no pointer to move off a tip, so a tap on an open tip
// closes it, and blurs the anchor that opened it (a tip opens on focus there),
// so the next tap on it opens it again. A long press still selects the tip's
// text: it fires no click, and a tap while text in the tip is selected only
// clears the selection. A tap on a link or button in a rich tip does its job.
function useTapClosesTips(enabled, tips) {
  useEffect(() => {
    if (!enabled) return undefined
    const onClick = (e) => {
      const tip = e.target instanceof Element ? e.target.closest('.app-tooltip') : null
      if (!tip || e.target.closest('a[href], button')) return
      const selection = window.getSelection()
      if (selection && !selection.isCollapsed && tip.contains(selection.anchorNode)) return
      for (const ref of tips) ref.current?.close()
      const anchor = document.activeElement
      if (anchor?.hasAttribute?.('data-tooltip-id')) anchor.blur()
    }
    document.addEventListener('click', onClick)
    return () => document.removeEventListener('click', onClick)
  }, [enabled, tips])
}

// /configs/<editor id> is ConfigsPage with its drawer open, Types.xml and
// TraderPlus Prices included (plans/91); /configs/mod-xml is a redirect of its own (App.jsx).
const CONFIGS_PAGES = new Set(['/configs/mod-xml'])

function boundaryKey(pathname) {
  return pathname.startsWith('/configs/') && !CONFIGS_PAGES.has(pathname) ? '/configs' : pathname
}

export default function Layout({ onLogout }) {
  const tipDelay = useMediaQuery(MOBILE_QUERY) ? TIP_DELAY_MOBILE : TIP_DELAY_DESKTOP
  const touch = useMediaQuery(TOUCH_QUERY)
  const touchClose = touch ? TOUCH_CLOSE : undefined
  // app-tip, app-tip-keep, app-tip-rich, for useTapClosesTips.
  const tips = useMemo(() => [createRef(), createRef(), createRef()], [])
  useTapClosesTips(touch, tips)
  useFieldTipsAtLabel(touch)
  return (
    <ServerStatusProvider>
      <JobsProvider>
        <RookieProvider>
          <GuideProvider>
            <Shell onLogout={onLogout} />
          </GuideProvider>
        </RookieProvider>
        {/* Single shared tooltip instance — every <Tooltip> wrapper (and every
            input carrying tooltipAttrs()) anchors here via
            data-tooltip-id="app-tip". Tune behavior in one place.
            openEvents/closeEvents REPLACE the defaults, so mouseenter/mouseleave
            must be listed explicitly alongside focus/blur or label hovers break.
            `clickable` gives the bubble pointer-events and keeps it open while
            the pointer is inside it (100ms grace on the way over), so a long tip
            can be read and its text selected. It only wires those keep-open
            listeners when closeEvents includes mouseleave/mouseout — which the
            line below is, so don't drop mouseleave from it.
            `noArrow` because .app-tooltip scrolls: the arrow is an absolutely
            positioned child that hangs ~4px outside the box, which an
            `overflow` other than `visible` counts as scrollable overflow — a
            phantom scrollbar on every tip short enough to fit. Nothing is lost
            visually; the arrow is `background: inherit` behind the body and its
            overhang was clipped by the old `overflow: hidden` anyway. */}
        {/* Plain tips close as soon as the pointer leaves the anchor: they
            are not `clickable`, so a passing pointer never leaves one in the
            way. */}
        <ReactTooltip
          ref={tips[0]}
          id="app-tip"
          className="app-tooltip"
          delayShow={tipDelay}
          noArrow
          openEvents={{ mouseenter: true, focus: true }}
          closeEvents={{ mouseleave: true, blur: true }}
          globalCloseEvents={touchClose}
          disableTooltip={touch ? isFieldOrTouchControl : isFieldControl}
        />
        {/* The same bubble for a tip worth reading to the end (tooltipAttrs'
            `keep`, the "?" help tips): `clickable`, so the pointer can move
            into it and select the text. */}
        <ReactTooltip
          ref={tips[1]}
          id="app-tip-keep"
          className="app-tooltip"
          delayShow={tipDelay}
          clickable
          noArrow
          openEvents={{ mouseenter: true, focus: true }}
          closeEvents={{ mouseleave: true, blur: true }}
          globalCloseEvents={touchClose}
          disableTooltip={isFieldControl}
        />
        {/* Same bubble, React content instead of a string, so a tip can hold
            links the pointer can travel into and click. Every prop above is
            repeated deliberately — `clickable` is what makes those links
            reachable, and it only attaches its keep-open listeners while
            `closeEvents` still lists `mouseleave`. */}
        <ReactTooltip
          ref={tips[2]}
          id="app-tip-rich"
          className="app-tooltip app-tooltip-rich"
          delayShow={tipDelay}
          clickable
          noArrow
          openEvents={{ mouseenter: true, focus: true }}
          closeEvents={{ mouseleave: true, blur: true }}
          globalCloseEvents={touchClose}
          disableTooltip={isFieldControl}
          render={({ content }) => <RichTooltipContent content={content} />}
        />
      </JobsProvider>
    </ServerStatusProvider>
  )
}
