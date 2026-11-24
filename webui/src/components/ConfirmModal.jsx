import { useRef } from 'react'
import { useFocusTrap } from './useFocusTrap.js'

// The app's modal dialog: a title, a message and Cancel/Confirm buttons, with
// keyboard focus held inside while it is open (useFocusTrap).
// `children` render between the message and the buttons — for the few confirms
// that also need an input (e.g. the bundle import's target mission).
export default function ConfirmModal({
  open,
  title,
  message,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  danger = false,
  confirmDisabled = false,
  onConfirm,
  onCancel,
  children,
}) {
  const boxRef = useRef(null)
  useFocusTrap(boxRef, open)
  if (!open) return null
  return (
    <div className="modal-backdrop" onClick={onCancel}>
      <div className="modal" ref={boxRef} onClick={(e) => e.stopPropagation()}>
        {title && <h3 className="modal-title">{title}</h3>}
        {message && <p className="modal-message">{message}</p>}
        {children}
        <div className="modal-actions">
          <button className="btn btn-ghost" onClick={onCancel}>
            {cancelLabel}
          </button>
          <button
            className={danger ? 'btn btn-danger' : 'btn btn-primary'}
            disabled={confirmDisabled}
            onClick={onConfirm}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
