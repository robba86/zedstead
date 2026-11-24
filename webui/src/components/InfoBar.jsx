import { Suspense, lazy, useRef, useSyncExternalStore } from 'react'
import { Link, useLocation, useSearchParams } from 'react-router-dom'
import { HiOutlineChevronDown, HiOutlineChevronUp, HiOutlinePencilSquare } from 'react-icons/hi2'
import { useServerStatus } from '../serverStatus'
import { useStatusLayout } from '../dashboardLayout.js'
import { resolveStatusFields } from '../statusFields.js'
import { useCan } from '../role.jsx'
import { STATUS_EDIT_LINK, STATUS_EDIT_PARAM, STATUS_EDIT_VALUE } from '../crossLinks.js'
import BranchBadge from './BranchBadge.jsx'
import ServerControls from './ServerControls.jsx'
import { tooltipAttrs } from './Tooltip.jsx'
import { useHeightAnimation } from '../useHeightAnimation.js'
import { MOBILE_QUERY, useMediaQuery } from '../useMediaQuery.js'

const STALE_MS = 15000

// Edit mode pulls in dnd-kit, so it is its own chunk: every page draws this
// strip, and the entry chunk must stay small.
const StatusFieldEditor = lazy(() => import('./StatusFieldEditor.jsx'))

// Edit mode is the Dashboard at STATUS_EDIT_LINK. In the URL, so the Edit link
// gets the unsaved-changes question like any link, and Back leaves edit mode.
function useStatusEditing() {
  const { pathname } = useLocation()
  const [params] = useSearchParams()
  return pathname === '/' && params.get(STATUS_EDIT_PARAM) === STATUS_EDIT_VALUE
}

// Open or collapsed, per browser like the sidebar's collapse: it describes the
// viewer, not the server. Open unless this browser closed it.
const OPEN_KEY = 'zsm.infobar.open'

function readOpen() {
  try {
    return localStorage.getItem(OPEN_KEY) !== '0'
  } catch {
    return true
  }
}

// A tiny external store, like crumbTitle.js: the toggle sits on the strip on
// a desktop and in the top bar on a phone (Layout.jsx), and the top bar is not
// inside the strip.
let open = readOpen()
const subs = new Set()

function subscribe(fn) {
  subs.add(fn)
  return () => subs.delete(fn)
}

function snapshot() {
  return open
}

function toggleOpen() {
  open = !open
  try {
    localStorage.setItem(OPEN_KEY, open ? '1' : '0')
  } catch {
    // Storage refused (private mode): the toggle still works for this page load.
  }
  for (const fn of subs) fn()
}

// Whether the strip shows all its fields. Always on a desktop, which has room
// for them and no toggle, and always in edit mode; on a phone otherwise, what
// this browser last chose.
function useInfoBarOpen() {
  // Both hooks run on every render: a `||` that skips the second one breaks
  // React's hook order the moment the stored value flips.
  const stored = useSyncExternalStore(subscribe, snapshot, snapshot)
  const isPhone = useMediaQuery(MOBILE_QUERY)
  const editing = useStatusEditing()
  return stored || !isPhone || editing
}

// The collapse toggle, in the phone's top bar only (Layout.jsx). The top bar
// is hidden above the mobile breakpoint, so a desktop has no toggle. It stays
// with every field hidden: the open strip holds the way back, the Edit link.
// Edit mode has no toggle, since Done is how it ends.
export function InfoBarToggle() {
  const isOpen = useInfoBarOpen()
  const editing = useStatusEditing()
  if (editing) return null
  return (
    <button
      type="button"
      className="info-bar-toggle"
      onClick={toggleOpen}
      aria-expanded={isOpen}
      aria-controls="info-bar-fields"
      aria-label={isOpen ? 'Collapse the server status bar' : 'Expand the server status bar'}
      {...tooltipAttrs(isOpen ? 'Collapse' : 'Show all fields')}
    >
      {isOpen ? <HiOutlineChevronUp aria-hidden="true" /> : <HiOutlineChevronDown aria-hidden="true" />}
    </button>
  )
}

// One status field: its label and its toned value.
function InfoBarField({ field, status }) {
  const tone = field.tone?.(status)
  const detail = field.detail?.(status)
  return (
    <div className="info-bar-field">
      <span className="info-bar-label">{field.label}</span>
      <span className={`info-bar-value${tone ? ` info-bar-value-${tone}` : ''}`}>
        {field.value(status)}
        {detail && <span className="info-bar-detail">{detail}</span>}
      </span>
    </div>
  )
}

// Whether the strip shows Start/Stop/Restart: a moderator, on every page.
// The Dashboard's header had its own set until 2026 Q4; it now uses these.
function useShowControls() {
  return useCan('moderator')
}

// The phone's top bar beside the wordmark: the server buttons only, with no
// status text, which the open strip shows. index.css hides it above the
// mobile breakpoint, where the top bar is gone. Below it, index.css hides the
// collapsed strip and the open strip's own buttons.
export function InfoBarCompact() {
  const showControls = useShowControls()
  if (!showControls) return null
  return (
    <div className="info-bar-compact">
      <div className="action-bar">
        <ServerControls />
      </div>
    </div>
  )
}

// The one header strip, the same on every page, the Dashboard included. Its
// fields, their order and which of them show are the saved status layout
// (dashboardLayout.js), arranged in its own edit mode (StatusFieldEditor). It
// follows in this tab and in any other one open at the time.
//
// It also carries Start/Stop/Restart, so they are one click away on every page.
// It always renders, even with no field and no buttons: Edit is the way back.
export default function InfoBar() {
  const { status, receivedAt, connected } = useServerStatus()
  const layout = useStatusLayout()
  const showControls = useShowControls()
  const stale = !connected || (receivedAt > 0 && Date.now() - receivedAt > STALE_MS)
  const isOpen = useInfoBarOpen()
  const editing = useStatusEditing()
  const barRef = useRef(null)
  useHeightAnimation(barRef, isOpen)

  const fields = resolveStatusFields(layout)
  // Collapsed, the strip keeps one line: the first field (the server's status
  // unless it was moved) and the buttons.
  const shown = isOpen ? fields : fields.slice(0, 1)

  return (
    <div
      ref={barRef}
      className={`info-bar${stale ? ' info-bar-stale' : ''}${isOpen ? '' : ' info-bar-collapsed'}${editing ? ' info-bar-editing' : ''}`}
    >
      {/* The live region is the fields only: the Start button's retry
          countdown changes every second and must not be read out each time. */}
      {editing ? (
        <Suspense fallback={null}>
          <StatusFieldEditor />
        </Suspense>
      ) : (
        <>
          <div id="info-bar-fields" className="info-bar-fields" role="status" aria-live="polite">
            {shown.map((f) => (
              <InfoBarField key={f.key} field={f} status={status} />
            ))}
            <BranchBadge branch={status?.server_branch} />
          </div>
          {/* The layout is per browser, so every role may arrange it. */}
          <div className="action-bar info-bar-edit">
            <Link
              to={STATUS_EDIT_LINK}
              className="btn btn-ghost"
              {...tooltipAttrs('Choose and arrange these fields on the Dashboard')}
            >
              <HiOutlinePencilSquare aria-hidden="true" />
              Edit
            </Link>
          </div>
        </>
      )}
      {showControls && (
        <div className="action-bar info-bar-actions">
          <ServerControls />
        </div>
      )}
    </div>
  )
}
