// A phone sheet's top edge: drag it to make the sheet taller or shorter, or
// tap it to switch between its shortest and its tallest. Phone only. Two
// sheets use it: the Tools sheet (LeftPanel) and the Loot finder (LootDrawer).
// The height is per browser in localStorage, like the Activity drawer's: it
// describes the viewer's screen, not the server.

import { useCallback, useEffect, useRef, useState } from 'react'

// One entry per sheet: its localStorage key (px string) and its shortest
// height, a number or a function of the sheet node.
export const TOOLS_SHEET = { key: 'zsm.mapeditor.sheet.height', min: 120 }
// The Loot finder shrinks to its header row, so the map shows what it marks.
export const LOOT_SHEET = {
  key: 'zsm.mapeditor.loot.height',
  min: (sheet) => {
    const head = sheet.querySelector('.me-loot-head')
    return head ? Math.ceil(head.getBoundingClientRect().bottom - sheet.getBoundingClientRect().top) + 1 : 120
  },
}
// Space kept between the sheet and the top of the editor, so the map and the
// Tools button stay in view at the tallest drag.
const TOP_GAP = 72
// A pointer that moves less than this is a tap, not a drag.
const TAP_PX = 6

function minHeight(sheet, cfg) {
  return typeof cfg.min === 'function' ? (sheet ? cfg.min(sheet) : 120) : cfg.min
}

// The sheet sits on the Activity bar; the editor's top is the info bar's
// bottom. The tallest sheet fills what lies between, less TOP_GAP. The sheet's
// bottom comes from its fixed `bottom`, not its box: shut, it is translated
// below the screen.
function clampHeight(px, sheet, cfg) {
  const min = minHeight(sheet, cfg)
  const editor = sheet?.closest('.me-editor')
  const max = editor
    ? window.innerHeight -
      (parseFloat(getComputedStyle(sheet).bottom) || 0) -
      editor.getBoundingClientRect().top -
      TOP_GAP
    : window.innerHeight / 2
  return Math.round(Math.min(Math.max(px, min), Math.max(min, max)))
}

function loadHeight(cfg) {
  try {
    const raw = parseInt(window.localStorage.getItem(cfg.key) || '', 10)
    return Number.isFinite(raw) ? raw : null
  } catch {
    return null
  }
}

function saveHeight(cfg, px) {
  try {
    window.localStorage.setItem(cfg.key, String(px))
  } catch {
    /* private mode — the height applies for this visit only */
  }
}

// null = the CSS default height (mapeditor.css mobile block).
export function useSheetHeight(sheetRef, cfg) {
  const [height, setHeight] = useState(() => loadHeight(cfg))
  // A turned phone or a closed keyboard can leave a stored height too tall.
  useEffect(() => {
    const onResize = () => setHeight((h) => (h == null ? h : clampHeight(h, sheetRef.current, cfg)))
    onResize()
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [sheetRef, cfg])
  return [height, setHeight]
}

export default function SheetResizeHandle({ sheetRef, setHeight, sheet: cfg, label }) {
  const dragRef = useRef(null)
  const [dragging, setDragging] = useState(false)

  const onDown = useCallback(
    (e) => {
      const sheet = sheetRef.current
      if (!sheet) return
      e.preventDefault()
      e.currentTarget.setPointerCapture(e.pointerId)
      dragRef.current = { startY: e.clientY, startHeight: sheet.offsetHeight, moved: false }
      setDragging(true)
    },
    [sheetRef]
  )

  const onMove = useCallback(
    (e) => {
      const drag = dragRef.current
      if (!drag) return
      if (!drag.moved && Math.abs(drag.startY - e.clientY) < TAP_PX) return
      drag.moved = true
      // Up (a smaller clientY) makes the bottom-docked sheet taller.
      drag.height = clampHeight(drag.startHeight + (drag.startY - e.clientY), sheetRef.current, cfg)
      setHeight(drag.height)
    },
    [sheetRef, setHeight, cfg]
  )

  const onEnd = useCallback(
    (e) => {
      const drag = dragRef.current
      if (!drag) return
      dragRef.current = null
      setDragging(false)
      if (e.currentTarget.hasPointerCapture?.(e.pointerId)) {
        e.currentTarget.releasePointerCapture(e.pointerId)
      }
      // A tap: a sheet above half way goes shortest, any other tallest.
      if (!drag.moved && e.type === 'pointerup') {
        const sheet = sheetRef.current
        const min = clampHeight(0, sheet, cfg)
        const max = clampHeight(Infinity, sheet, cfg)
        drag.height = drag.startHeight > (min + max) / 2 ? min : max
        setHeight(drag.height)
      }
      if (drag.height != null) saveHeight(cfg, drag.height)
    },
    [sheetRef, setHeight, cfg]
  )

  return (
    <div
      className={`me-sheet-handle${dragging ? ' dragging' : ''}`}
      role="separator"
      aria-orientation="horizontal"
      aria-label={label}
      onPointerDown={onDown}
      onPointerMove={onMove}
      onPointerUp={onEnd}
      onPointerCancel={onEnd}
    >
      <span className="me-sheet-grip" aria-hidden="true" />
    </div>
  )
}
