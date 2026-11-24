// The editor for the status bar's fields: reorder by drag or keyboard,
// hide a field, or add one back (statusFields.js holds the keys).

import { DndContext, KeyboardSensor, MouseSensor, TouchSensor, closestCenter, useSensor, useSensors } from '@dnd-kit/core'
import { SortableContext, arrayMove, rectSortingStrategy, sortableKeyboardCoordinates, useSortable } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { Link } from 'react-router-dom'
import { HiOutlineCheck } from 'react-icons/hi2'
import { useServerStatus } from '../serverStatus'
import useDismissDetails from '../useDismissDetails.js'
import { useStatusLayout, writeStatusLayout } from '../dashboardLayout.js'
import { hiddenStatusFields, resolveStatusFields } from '../statusFields.js'
import StatusCard from './StatusCard.jsx'
import BranchBadge from './BranchBadge.jsx'
import PanelNote from './PanelNote.jsx'
import CountBadge from './CountBadge.jsx'

// The status panel's edit mode (InfoBar.jsx, on the Dashboard at
// STATUS_EDIT_LINK): drag a field to move it, hide it, or add it back. The
// panel shows the result on every page. Lazy, so dnd-kit stays out of the
// entry chunk.
function SortableStatusTile({ field, status, onHide }) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: field.key,
  })

  return (
    <div
      ref={setNodeRef}
      className={`status-tile${isDragging ? ' status-tile-dragging' : ''}`}
      style={{ transform: CSS.Translate.toString(transform), transition }}
      {...attributes}
      {...listeners}
    >
      <StatusCard
        label={field.label}
        value={field.value(status)}
        detail={field.detail?.(status)}
        tone={field.tone?.(status)}
      />
      <button
        type="button"
        className="status-tile-hide"
        aria-label={`Hide ${field.label}`}
        // The whole tile is the drag handle, so keep the button's press away
        // from the sensors or every click starts a drag instead.
        onMouseDown={(e) => e.stopPropagation()}
        onTouchStart={(e) => e.stopPropagation()}
        onClick={() => onHide(field.key)}
      >
        ✕
      </button>
    </div>
  )
}

export default function StatusFieldEditor() {
  // Telemetry comes from the shared WS-backed store: no polling of its own.
  const { status } = useServerStatus()
  const layout = useStatusLayout()
  const addMenuRef = useDismissDetails()
  const fields = resolveStatusFields(layout)
  const hidden = hiddenStatusFields(layout)

  const sensors = useSensors(
    // Mouse and touch apart, because the whole tile is the handle: a touch
    // drag would otherwise fight the page's scroll, which the browser wins.
    // On a phone a drag starts with a long press, so a swipe still scrolls.
    useSensor(MouseSensor, { activationConstraint: { distance: 4 } }),
    useSensor(TouchSensor, { activationConstraint: { delay: 300, tolerance: 8 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates })
  )

  function onDragEnd({ active, over }) {
    if (!over || active.id === over.id) return
    const from = layout.order.indexOf(active.id)
    const to = layout.order.indexOf(over.id)
    if (from < 0 || to < 0) return
    writeStatusLayout({ ...layout, order: arrayMove(layout.order, from, to) })
  }

  function hide(key) {
    writeStatusLayout({ ...layout, hidden: [...new Set([...layout.hidden, key])] })
  }

  function show(key) {
    writeStatusLayout({ ...layout, hidden: layout.hidden.filter((k) => k !== key) })
  }

  return (
    <div className="status-editor">
      <div className="status-editor-header">
        <BranchBadge branch={status?.server_branch} />
        <span className="status-editor-hint">Drag a field to move it. ✕ hides it.</span>
        <div className="action-bar">
          {hidden.length > 0 && (
            <details className="dash-add" ref={addMenuRef}>
              <summary className="btn btn-ghost">
                Add field <CountBadge value={hidden.length} />
              </summary>
              <div className="dash-add-menu">
                {hidden.map((f) => (
                  <button key={f.key} type="button" className="dash-add-item" onClick={() => show(f.key)}>
                    <span>{f.label}</span>
                    <span className="dash-add-plus">+</span>
                  </button>
                ))}
              </div>
            </details>
          )}
          <Link to="/" replace className="btn btn-primary">
            <HiOutlineCheck aria-hidden="true" />
            Done
          </Link>
        </div>
      </div>
      {fields.length === 0 ? (
        <PanelNote>Every field is hidden. Add one back with Add field.</PanelNote>
      ) : (
        <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
          <SortableContext items={fields.map((f) => f.key)} strategy={rectSortingStrategy}>
            <div className="status-grid">
              {fields.map((f) => (
                <SortableStatusTile key={f.key} field={f} status={status} onHide={hide} />
              ))}
            </div>
          </SortableContext>
        </DndContext>
      )}
    </div>
  )
}
