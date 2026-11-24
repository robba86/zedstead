import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { HiOutlineArrowsPointingIn, HiOutlineArrowsPointingOut, HiOutlineXMark } from 'react-icons/hi2'
import { writeStored } from '../guideSteps.js'
import { tooltipAttrs } from './Tooltip.jsx'
import { useFocusReturn } from './useFocusTrap.js'

// A resizable right-hand drawer for file content.
//
// Overlay, not a flex sibling like GuideDrawer: that one narrows the content
// column because its links ARE navigation, while this holds the file you are
// editing and the page behind it is context, not a destination. The drag-resize
// is the same pattern (pointer capture, double-click to reset, re-clamp on
// window resize) — extend it here rather than rolling a third one.
//
// Width is per-browser localStorage under the caller's `storageKey`, same class
// as zsm.theme / zsm.guide.width: it describes the viewer, not the server, so
// it is not a DayzConfig field and has no endpoint.
//
// `maximizable` adds a header button that lets the drawer cover the whole
// content column (the config editors' wide tables need it, plans/79); the
// choice is remembered beside the width, under `<storageKey>.max`. `actions`
// renders in the header between the title and the buttons. `defaultWidth` is
// the width before the user has dragged one.
//
// Escape closes the drawer only when nothing else wanted the key: not while a
// field has focus (a filter box clears itself on Escape, a code editor closes
// its search), not when a handler already called preventDefault, and not while
// a modal is open over the page (the modal closes, the drawer stays).
//
// The drawer is not modal, so it does not trap focus: it takes focus on open
// and gives it back on close (useFocusReturn).

const DEFAULT_WIDTH = 560
const MIN_WIDTH = 320
// Leave the page underneath room for its tables whatever the window size is:
// at 320px the Mod XML page's tables scrolled sideways under a wide drawer.
// Measured from the content column, since the sidebar takes its own share.
const MIN_PAGE_WIDTH = 800
const maxWidth = () => {
  const left = document.querySelector('.content')?.getBoundingClientRect().left || 0
  return Math.max(MIN_WIDTH, window.innerWidth - left - MIN_PAGE_WIDTH)
}

function clampWidth(px) {
  return Math.min(Math.max(px, MIN_WIDTH), maxWidth())
}

function loadWidth(key, fallback) {
  try {
    const raw = parseInt(localStorage.getItem(key) || '', 10)
    return clampWidth(Number.isFinite(raw) ? raw : fallback)
  } catch {
    return clampWidth(fallback)
  }
}

function loadMax(key) {
  try {
    return localStorage.getItem(`${key}.max`) === '1'
  } catch {
    return false
  }
}

// Every open drawer's reserved width, in the order they opened. Two drawers
// can overlap for one commit when one replaces the other (the Custom config
// tab's form drawer closes a config editor's). The closing one used to remove
// --side-drawer-width outright, so the drawer that stayed reserved nothing and
// the page ran under it until a drag set the width again.
const reservedWidths = new Map()

function reserveWidth(id, px) {
  if (px == null) reservedWidths.delete(id)
  else reservedWidths.set(id, px)
  const style = document.documentElement.style
  if (!reservedWidths.size) {
    style.removeProperty('--side-drawer-width')
    return
  }
  style.setProperty('--side-drawer-width', `${[...reservedWidths.values()].at(-1)}px`)
}

// The content column's left edge: what a maximized drawer covers up to.
const contentLeft = () => document.querySelector('.content')?.getBoundingClientRect().left || 0

// A key press some other control should have: typing in a field, or a modal
// open over the page.
function escapeBelongsElsewhere(e) {
  if (e.defaultPrevented) return true
  const el = e.target
  if (el instanceof HTMLElement && (el.isContentEditable || el.closest('input, textarea, select'))) {
    return true
  }
  return Boolean(document.querySelector('.modal-backdrop'))
}

// A resizable drawer on the right edge. `badge` is a short pill after the
// title; `badgeTone` picks its status-pill variant (`readonly` for a file the
// drawer only shows).
export default function SideDrawer({
  title,
  subtitle,
  badge,
  badgeTone = 'neutral',
  storageKey,
  onClose,
  actions = null,
  maximizable = false,
  defaultWidth = DEFAULT_WIDTH,
  className = '',
  children,
}) {
  const [width, setWidth] = useState(() => loadWidth(storageKey, defaultWidth))
  const [dragging, setDragging] = useState(false)
  const [maximized, setMaximized] = useState(() => maximizable && loadMax(storageKey))
  const [left, setLeft] = useState(contentLeft)
  // Focus moves into the drawer on open and back to its opener on close.
  const asideRef = useRef(null)
  useFocusReturn(asideRef)

  // Drag the left edge. Pointer capture keeps the drag alive over the page
  // underneath, which is what makes a thin handle usable.
  const dragRef = useRef(null)
  const onHandleDown = useCallback(
    (e) => {
      e.preventDefault()
      e.currentTarget.setPointerCapture(e.pointerId)
      dragRef.current = { startX: e.clientX, startWidth: width }
      setDragging(true)
    },
    [width]
  )

  const onHandleMove = useCallback((e) => {
    const drag = dragRef.current
    if (!drag) return
    // Inverted against GuideDrawer: this edge grows the drawer leftwards.
    setWidth(clampWidth(drag.startWidth - (e.clientX - drag.startX)))
  }, [])

  const endDrag = useCallback(
    (e) => {
      if (!dragRef.current) return
      dragRef.current = null
      setDragging(false)
      if (e?.pointerId != null && e.currentTarget.hasPointerCapture?.(e.pointerId)) {
        e.currentTarget.releasePointerCapture(e.pointerId)
      }
      writeStored(storageKey, String(width))
    },
    [storageKey, width]
  )

  const resetWidth = useCallback(() => {
    setWidth(clampWidth(defaultWidth))
    writeStored(storageKey, null)
  }, [storageKey, defaultWidth])

  const toggleMax = useCallback(() => {
    setLeft(contentLeft())
    setMaximized((m) => {
      writeStored(`${storageKey}.max`, m ? null : '1')
      return !m
    })
  }, [storageKey])

  // A shrinking viewport must not leave the drawer wider than the window, and
  // a maximized one follows the content column (the sidebar collapsing, the
  // Guide drawer opening).
  useEffect(() => {
    const onResize = () => {
      setWidth((w) => clampWidth(w))
      setLeft(contentLeft())
    }
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  // Published like ActivityDrawer's --bottom-height, so the page underneath can
  // reserve the column instead of rendering half its content behind the drawer.
  // Maximized, the page is covered, so it keeps its own width rather than
  // re-wrapping behind the drawer.
  const reserveId = useId()
  useEffect(() => {
    reserveWidth(reserveId, maximized ? 0 : width)
    return () => reserveWidth(reserveId, null)
  }, [reserveId, width, maximized])

  // Escape closes, matching every other dismissible surface in the app, unless
  // another control owns the key press (see the header comment).
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape' && !escapeBelongsElsewhere(e)) onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <aside
      ref={asideRef}
      className={`side-drawer${dragging ? ' resizing' : ''}${maximized ? ' side-drawer-max' : ''}${className ? ` ${className}` : ''}`}
      style={maximized ? { left: `${left}px` } : { width: `${width}px` }}
      aria-label={title}
    >
      <div
        className="side-drawer-handle"
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize panel"
        {...tooltipAttrs('Drag to resize — double-click to reset')}
        onPointerDown={onHandleDown}
        onPointerMove={onHandleMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onDoubleClick={resetWidth}
      />
      <div className="side-drawer-header">
        <h2 {...tooltipAttrs(title)}>{title}</h2>
        {badge && <span className={`status-pill status-pill-${badgeTone}`}>{badge}</span>}
        {actions}
        {maximizable && (
          <button
            className="btn btn-icon side-drawer-max-btn"
            type="button"
            aria-label={maximized ? 'Restore the panel width' : 'Maximize the panel'}
            aria-pressed={maximized}
            {...tooltipAttrs(maximized ? 'Restore the panel width' : 'Maximize the panel')}
            onClick={toggleMax}
          >
            {maximized ? <HiOutlineArrowsPointingIn aria-hidden="true" /> : <HiOutlineArrowsPointingOut aria-hidden="true" />}
          </button>
        )}
        <button className="btn btn-icon side-drawer-close" type="button" aria-label="Close" {...tooltipAttrs('Close')} onClick={onClose}>
          <HiOutlineXMark aria-hidden="true" />
        </button>
      </div>
      {subtitle && <p className="side-drawer-subtitle">{subtitle}</p>}
      <div className="side-drawer-body">{children}</div>
    </aside>
  )
}
