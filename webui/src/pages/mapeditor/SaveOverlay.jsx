// Blocking progress overlay for bulk operations.
//
// A group move of 300 markers is 300 requests; a bulk delete is 300 sequential
// ones. Half-finished, that leaves the XML inconsistent with what is on
// screen — hence the warning, kept verbatim from the vanilla editor.

import { createPortal } from 'react-dom'

export default function SaveOverlay({ progress }) {
  if (!progress) return null
  const { action, done, total } = progress
  return createPortal(
    <div className="me-save-overlay">
      <div className="me-save-overlay-content">
        <div className="me-save-spinner" />
        <span>
          {action} positions… {done}/{total}
        </span>
        <span className="me-save-overlay-warn">
          Do not close the Zedstead app or update/close the browser!
        </span>
      </div>
    </div>,
    document.body
  )
}
