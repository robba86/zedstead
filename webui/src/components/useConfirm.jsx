// App-wide confirmation dialog, as an awaitable call.
//
// Every page that needs a confirm currently hand-rolls the same three pieces:
// a `pending` state holding what was clicked, a `<ConfirmModal>` at the bottom
// of the render, and an onConfirm that re-reads that state. That works when the
// call site is a button handler, but not when it is buried in an imperative
// callback (a Leaflet marker drag) that has to *await* the answer before
// deciding whether to save.
//
//     const confirm = useConfirm()
//     if (!(await confirm({ title: 'Move spawn?', danger: true }))) return
//
// `body` is an element shown under the message, for a confirm that also
// needs a choice (the Change history's Restore picks a version with it).
//
// Rendering stays with the provider, so there is one modal in the tree
// regardless of how many callers there are.

import { createContext, useCallback, useContext, useRef, useState } from 'react'
import ConfirmModal from './ConfirmModal.jsx'

const ConfirmContext = createContext(null)

export function ConfirmProvider({ children }) {
  const [request, setRequest] = useState(null)
  const resolveRef = useRef(null)

  const confirm = useCallback(
    (options) =>
      new Promise((resolve) => {
        // A second confirm while one is open resolves the first as "cancelled"
        // rather than dropping it — an abandoned promise would leave its caller
        // awaiting forever, and in the map editor that caller is holding a
        // half-finished drag.
        resolveRef.current?.(false)
        resolveRef.current = resolve
        setRequest(typeof options === 'string' ? { message: options } : options || {})
      }),
    []
  )

  const settle = useCallback((answer) => {
    const resolve = resolveRef.current
    resolveRef.current = null
    setRequest(null)
    resolve?.(answer)
  }, [])

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      <ConfirmModal
        open={Boolean(request)}
        title={request?.title}
        message={request?.message}
        confirmLabel={request?.confirmLabel}
        cancelLabel={request?.cancelLabel}
        danger={request?.danger}
        onConfirm={() => settle(true)}
        onCancel={() => settle(false)}
      >
        {request?.body}
      </ConfirmModal>
    </ConfirmContext.Provider>
  )
}

export function useConfirm() {
  const confirm = useContext(ConfirmContext)
  if (!confirm) throw new Error('useConfirm must be used within a ConfirmProvider')
  return confirm
}
