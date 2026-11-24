// The wide screen's panel width: a handle on the panel's right edge drags it
// between MIN_WIDTH and MAX_WIDTH. The width is per browser in localStorage,
// like the panel's fold (state/useStoredOpen.js): it describes the viewer's
// screen, not the server, so it is never a URL key.
//
// The width is the CSS variable --me-panel-w on `.me-editor`, which sizes the
// panel and --me-panel-space (where Leaflet's left controls start), so the
// map's controls move with the edge. Below the width the tab names need, the
// tabs show only their icons (a container query in mapeditor.css).
// Double-click puts the default back, and the arrow keys move it by 20 px.

import { useCallback, useEffect, useRef, useState } from 'react'

export const DEFAULT_WIDTH = 320
const MIN_WIDTH = 240
const MAX_WIDTH = 640
const KEY = 'zsm.mapeditor.sidebar.width'
const STEP = 20

const clamp = (w) => Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, Math.round(w)))

function storedWidth() {
  try {
    const value = Number(window.localStorage.getItem(KEY))
    return value ? clamp(value) : DEFAULT_WIDTH
  } catch {
    return DEFAULT_WIDTH
  }
}

/** The handle, for the panel *panelRef* inside `.me-editor`. */
export default function SidebarWidthHandle({ panelRef }) {
  const [width, setWidth] = useState(storedWidth)
  const dragRef = useRef(null)

  // The variable lives on .me-editor, which the controls and the panel share.
  useEffect(() => {
    const editor = panelRef.current?.closest('.me-editor')
    if (!editor) return undefined
    editor.style.setProperty('--me-panel-w', `${width}px`)
    try {
      window.localStorage.setItem(KEY, String(width))
    } catch {
      /* private mode: the panel just forgets */
    }
    return () => editor.style.removeProperty('--me-panel-w')
  }, [width, panelRef])

  const onPointerDown = useCallback(
    (e) => {
      if (e.button !== 0) return
      e.preventDefault()
      e.currentTarget.setPointerCapture(e.pointerId)
      dragRef.current = { startX: e.clientX, startWidth: width }
      panelRef.current?.classList.add('is-resizing')
    },
    [width, panelRef]
  )

  const onPointerMove = useCallback((e) => {
    const drag = dragRef.current
    if (drag) setWidth(clamp(drag.startWidth + e.clientX - drag.startX))
  }, [])

  const onPointerUp = useCallback(() => {
    dragRef.current = null
    panelRef.current?.classList.remove('is-resizing')
  }, [panelRef])

  const onKeyDown = useCallback((e) => {
    if (e.key === 'ArrowLeft') setWidth((w) => clamp(w - STEP))
    else if (e.key === 'ArrowRight') setWidth((w) => clamp(w + STEP))
    else return
    e.preventDefault()
  }, [])

  return (
    <div
      className="me-sidebar-resize"
      role="separator"
      aria-orientation="vertical"
      aria-label="Panel width"
      aria-valuemin={MIN_WIDTH}
      aria-valuemax={MAX_WIDTH}
      aria-valuenow={width}
      tabIndex={0}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
      onDoubleClick={() => setWidth(DEFAULT_WIDTH)}
      onKeyDown={onKeyDown}
      data-tooltip-id="app-tip"
      data-tooltip-content="Drag to resize the panel. Double-click for the default width."
    />
  )
}
