// A drag handle under an open sidebar section (Layers, Locations) that sets the
// section's height; the Edit tabs below take what is left. Rendered right after
// the section, which it sizes as its previous sibling, so ListSection needs no
// ref. Mounted only while the section is open: unmounting (a fold) drops the
// height, and opening again restores it.
//
// The height is per browser in localStorage under `storageKey`, like the
// panel's width (SidebarWidthHandle.jsx): it describes the viewer's screen, not
// the server. Double-click puts the default (the CSS cap) back, and the arrow
// keys move it by 20 px. Wide screen only: on a phone the sheet is one scroller.

import { useCallback, useEffect, useRef, useState } from 'react'
import { MOBILE_QUERY, useMediaQuery } from '../../../useMediaQuery.js'

const MIN_HEIGHT = 60
const STEP = 20

function storedHeight(key) {
  try {
    const value = Number(window.localStorage.getItem(key))
    return value >= MIN_HEIGHT ? Math.round(value) : null
  } catch {
    return null
  }
}

function save(key, height) {
  try {
    if (height == null) window.localStorage.removeItem(key)
    else window.localStorage.setItem(key, String(height))
  } catch {
    /* private mode: the section just forgets */
  }
}

export default function SectionHeightHandle({ storageKey, label }) {
  const isMobile = useMediaQuery(MOBILE_QUERY)
  const [height, setHeight] = useState(() => storedHeight(storageKey))
  const handleRef = useRef(null)
  const dragRef = useRef(null)

  const section = () => handleRef.current?.previousElementSibling

  // Never taller than the sidebar leaves room for, so the tabs stay on screen.
  const clamp = useCallback((h) => {
    const sidebar = handleRef.current?.closest('.me-sidebar')
    const max = sidebar ? sidebar.clientHeight - 120 : 2000
    return Math.round(Math.max(MIN_HEIGHT, Math.min(max, h)))
  }, [])

  useEffect(() => {
    const el = section()
    if (!el || isMobile) return undefined
    if (height != null) {
      el.style.height = `${height}px`
      el.style.maxHeight = 'none'
      el.style.flex = 'none'
    }
    save(storageKey, height)
    return () => {
      el.style.removeProperty('height')
      el.style.removeProperty('max-height')
      el.style.removeProperty('flex')
    }
  }, [height, storageKey, isMobile])

  const onPointerDown = useCallback((e) => {
    if (e.button !== 0) return
    e.preventDefault()
    e.currentTarget.setPointerCapture(e.pointerId)
    dragRef.current = { startY: e.clientY, startHeight: section()?.offsetHeight || 0 }
    handleRef.current?.closest('.me-sidebar')?.classList.add('is-resizing-section')
  }, [])

  const onPointerMove = useCallback(
    (e) => {
      const drag = dragRef.current
      if (drag) setHeight(clamp(drag.startHeight + e.clientY - drag.startY))
    },
    [clamp]
  )

  const onPointerUp = useCallback(() => {
    dragRef.current = null
    handleRef.current?.closest('.me-sidebar')?.classList.remove('is-resizing-section')
  }, [])

  const onKeyDown = useCallback(
    (e) => {
      const step = e.key === 'ArrowUp' ? -STEP : e.key === 'ArrowDown' ? STEP : 0
      if (!step) return
      e.preventDefault()
      setHeight((h) => clamp((h ?? section()?.offsetHeight ?? 0) + step))
    },
    [clamp]
  )

  if (isMobile) return null
  return (
    <div
      ref={handleRef}
      className="me-section-resize"
      role="separator"
      aria-orientation="horizontal"
      aria-label={label}
      tabIndex={0}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
      onDoubleClick={() => setHeight(null)}
      onKeyDown={onKeyDown}
      data-tooltip-id="app-tip"
      data-tooltip-content="Drag to resize. Double-click for the default height."
    />
  )
}
