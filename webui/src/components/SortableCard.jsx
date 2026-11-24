import { useCallback, useEffect, useRef, useState } from 'react'
import { TouchSensor } from '@dnd-kit/core'
import { useSortable } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { MAX_SPAN, MIN_SPAN } from '../dashboardLayout.js'

// One draggable, resizable, hideable slot in the Dashboard grid.
//
// The card components inside are untouched: the controls are absolutely
// positioned in this wrapper and the grid CSS reserves room for them in the
// card's own header (`.dash-item > .card > .card-header`). For the mouse
// and keyboard only the grip carries the drag listeners (setActivatorNodeRef),
// so every button and link inside a card stays clickable; a touch drag starts
// from a long press anywhere on the card (CardTouchSensor below).
//
// WIDTH is a `data-span` attribute, never an inline style: the
// `@media (max-width: 900px)` single-column rule has to be able to beat it, and
// an inline `grid-column` would win over the media query.
//
// EMPTY CARDS: several cards render null when their fetch fails (or when the
// feature is not configured — PlayersCard with no RCON), which would otherwise
// leave controls floating over nothing and a zero-height drop target in the
// sort order. The wrapper checks for a rendered `.card` after every render and
// reports it upward; the page hides those, drops them from the SortableContext
// item list, and marks them unavailable in the Add-tile menu. The check is a
// DOM read rather than a prop because the emptiness belongs to the card, not to
// the page.
// Touch drags start from the whole card after a long press, so a swipe over a
// card still scrolls the page; on the grip they start after a few pixels, as
// with the mouse. The constraint is read when a press starts, which is why it
// is picked here from what was pressed.
export class CardTouchSensor extends TouchSensor {
  constructor(props) {
    const onGrip = props.event?.target?.closest?.('.dash-handle')
    super({
      ...props,
      options: {
        ...props.options,
        activationConstraint: onGrip ? { distance: 4 } : { delay: 300, tolerance: 8 },
      },
    })
  }
}

export default function SortableCard({
  id,
  title,
  span = 1,
  defaultSpan = span,
  hidden = false,
  onEmptyChange,
  onSpanChange,
  onHide,
  children,
}) {
  const { attributes, listeners, setNodeRef, setActivatorNodeRef, transform, transition, isDragging } =
    useSortable({ id, disabled: hidden })
  // The mouse and keyboard drag from the grip only; a touch from anywhere on
  // the card (CardTouchSensor decides how long it has to be held).
  const { onTouchStart, ...gripListeners } = listeners ?? {}
  const contentRef = useRef(null)
  const itemRef = useRef(null)
  const dragRef = useRef(null)
  // Non-null while a resize drag is live: the span the pointer is currently
  // over, rendered instead of the saved one so the drag previews itself.
  const [preview, setPreview] = useState(null)

  // A card that re-renders itself to null (PlayersCard after its fetch 409s)
  // does not re-render this wrapper, so the per-render check alone missed it
  // and left the grip and hide button over an empty slot. The observer catches
  // the card leaving or arriving between this wrapper's own renders.
  const onEmptyRef = useRef(onEmptyChange)
  onEmptyRef.current = onEmptyChange
  const reportEmpty = useCallback(() => {
    onEmptyRef.current?.(id, !contentRef.current?.querySelector('.card'))
  }, [id])

  useEffect(() => {
    reportEmpty()
  })

  useEffect(() => {
    const node = contentRef.current
    if (!node) return undefined
    const observer = new MutationObserver(reportEmpty)
    observer.observe(node, { childList: true })
    return () => observer.disconnect()
  }, [reportEmpty])

  const setRefs = useCallback(
    (node) => {
      itemRef.current = node
      setNodeRef(node)
    },
    [setNodeRef]
  )

  // Column geometry off the live grid rather than a hardcoded 4: below 900px
  // the grid really is one column, which makes resizing a no-op by itself.
  const gridInfo = useCallback(() => {
    const grid = itemRef.current?.parentElement
    if (!grid) return null
    const style = getComputedStyle(grid)
    const columns = style.gridTemplateColumns.split(' ').filter(Boolean).length
    const gap = parseFloat(style.columnGap) || 0
    const width = grid.getBoundingClientRect().width
    if (!columns || width <= 0) return null
    return { columns, colWidth: (width - gap * (columns - 1)) / columns, gap }
  }, [])

  const spanAt = useCallback(
    (clientX) => {
      const info = gridInfo()
      const rect = itemRef.current?.getBoundingClientRect()
      if (!info || !rect) return null
      const wanted = Math.round((clientX - rect.left + info.gap) / (info.colWidth + info.gap))
      return Math.min(info.columns, Math.max(MIN_SPAN, wanted))
    },
    [gridInfo]
  )

  const onResizeDown = useCallback(
    (e) => {
      const info = gridInfo()
      if (!info || info.columns < 2) return // single-column layout: nothing to drag
      e.preventDefault()
      e.currentTarget.setPointerCapture(e.pointerId)
      dragRef.current = { span }
      setPreview(span)
    },
    [gridInfo, span]
  )

  const onResizeMove = useCallback(
    (e) => {
      if (!dragRef.current) return
      const next = spanAt(e.clientX)
      if (next !== null) {
        dragRef.current.span = next
        setPreview(next)
      }
    },
    [spanAt]
  )

  const endResize = useCallback(
    (e) => {
      const drag = dragRef.current
      dragRef.current = null
      setPreview(null)
      if (!drag) return
      if (e.currentTarget.hasPointerCapture?.(e.pointerId)) {
        e.currentTarget.releasePointerCapture(e.pointerId)
      }
      if (drag.span !== span) onSpanChange?.(id, drag.span)
    },
    [id, onSpanChange, span]
  )

  const onResizeKeyDown = useCallback(
    (e) => {
      const step = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0
      if (!step) return
      e.preventDefault()
      const next = Math.min(MAX_SPAN, Math.max(MIN_SPAN, span + step))
      if (next !== span) onSpanChange?.(id, next)
    },
    [id, onSpanChange, span]
  )

  const shown = preview ?? span
  const className = [
    'dash-item',
    isDragging ? 'dash-item-dragging' : '',
    preview !== null ? 'dash-item-resizing resizing' : '',
    hidden ? 'dash-item-empty' : '',
  ]
    .filter(Boolean)
    .join(' ')

  return (
    <div
      ref={setRefs}
      className={className}
      data-span={shown}
      style={{ transform: CSS.Translate.toString(transform), transition }}
      onTouchStart={onTouchStart}
    >
      <div className="dash-controls">
        <button
          type="button"
          ref={setActivatorNodeRef}
          className="dash-handle"
          aria-label={`Reorder ${title}`}
          data-tooltip-id="app-tip"
          data-tooltip-content={`Drag to move the ${title} card. With the keyboard: space, then arrow keys.`}
          {...attributes}
          {...gripListeners}
        >
          ⠿
        </button>
        <button
          type="button"
          className="dash-hide"
          aria-label={`Hide ${title}`}
          data-tooltip-id="app-tip"
          data-tooltip-content={`Hide the ${title} card. Bring it back from "Add tile".`}
          onTouchStart={(e) => e.stopPropagation()}
          onClick={() => onHide?.(id)}
        >
          ✕
        </button>
      </div>
      <div className="dash-item-body" ref={contentRef}>
        {children}
      </div>
      <button
        type="button"
        className="dash-resize"
        aria-label={`Resize ${title}`}
        data-tooltip-id="app-tip"
        data-tooltip-content={`Drag to set how many columns ${title} takes. Arrow keys work too; double-click resets.`}
        onTouchStart={(e) => e.stopPropagation()}
        onPointerDown={onResizeDown}
        onPointerMove={onResizeMove}
        onPointerUp={endResize}
        onPointerCancel={endResize}
        onKeyDown={onResizeKeyDown}
        onDoubleClick={() => span !== defaultSpan && onSpanChange?.(id, defaultSpan)}
      />
    </div>
  )
}
