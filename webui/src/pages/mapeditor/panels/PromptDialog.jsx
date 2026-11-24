// A single-text-field dialog, replacing the one native prompt() the editor
// used ("Enter group name:").
//
// Deliberately not generalised into components/ the way useConfirm was: this
// is the editor's dialog chrome (.dialog-* from the ported stylesheet), and
// there is exactly one caller.

import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useFocusTrap } from '../../../components/useFocusTrap.js'

export default function PromptDialog({
  title,
  label,
  placeholder,
  hint,
  submitLabel = 'Create',
  onSubmit,
  onClose,
}) {
  const [value, setValue] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const inputRef = useRef(null)
  const boxRef = useRef(null)
  useFocusTrap(boxRef)

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  async function submit() {
    const trimmed = value.trim()
    if (!trimmed) {
      setError(`${label} is required.`)
      return
    }
    setBusy(true)
    setError('')
    try {
      await onSubmit(trimmed)
      onClose()
    } catch (err) {
      setError(err.message || 'Failed.')
      setBusy(false)
    }
  }

  return createPortal(
    <div
      className="dialog-overlay"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div ref={boxRef} className="dialog-box">
        <div className="dialog-title">{title}</div>
        <div className="dialog-body">
          <label className="dialog-label" htmlFor="me-prompt-input">
            {label}
          </label>
          <input
            id="me-prompt-input"
            ref={inputRef}
            className="dialog-input"
            type="text"
            placeholder={placeholder}
            spellCheck={false}
            autoComplete="off"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') submit()
              else if (e.key === 'Escape') onClose()
            }}
          />
          {hint && <div className="dialog-hint">{hint}</div>}
          {error && <div className="dialog-error">{error}</div>}
        </div>
        <div className="dialog-actions">
          <button type="button" className="dialog-btn dialog-button-cancel" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="dialog-btn dialog-button-submit"
            disabled={busy}
            onClick={submit}
          >
            {busy ? 'Working…' : submitLabel}
          </button>
        </div>
      </div>
    </div>,
    document.body
  )
}
