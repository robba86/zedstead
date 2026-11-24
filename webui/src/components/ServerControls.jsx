import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { HiOutlineArrowPath, HiOutlinePlay, HiOutlineStop } from 'react-icons/hi2'
import { api } from '../api'
import { useServerStatus } from '../serverStatus'
import { isStopping } from '../statusFields.js'
import { useCan } from '../role.jsx'
import { useToast } from './Toast.jsx'
import { tooltipAttrs } from './Tooltip.jsx'
import ConfirmModal from './ConfirmModal.jsx'
import DetailText from './DetailText.jsx'

// What each start/stop/restart confirm says. One shared line ("This affects the
// live DayZ server.") told the admin nothing about what the click does — and it
// read as a warning on Start, which is the harmless one of the three.
const ACTION_CONFIRM = {
  start: 'Starts the DayZ server. It takes a minute or two before players can join.',
  stop: 'Stops the DayZ server. Everyone playing is disconnected immediately.',
  restart:
    'Stops and starts the DayZ server. Everyone playing is disconnected, and can rejoin once it is back up.',
}

// Shown under the runtime check's own sentence when a start would fail. The
// check finds a DLL by its file name and can be wrong, so the start stays
// possible: a lock here would keep out a server that runs.
const MISSING_RUNTIME_NOTE =
  'The server exits at start without it. If you know it is installed, you can start the server anyway.'

const RETRY_TIP =
  'systemd is restarting the server by itself. Starting it here would race that, so the button waits.'

function capitalize(s) {
  return s.charAt(0).toUpperCase() + s.slice(1)
}

const formatCountdown = (seconds) =>
  `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`

// Start / Stop / Restart, rendered as action-bar pills by whoever mounts it: the
// Dashboard's header, and the InfoBar on every other page. One button per state
// the server can be in — Start while it is stopped, Stop + Restart while it runs
// — so a disabled button never has to explain itself. Hidden below moderator,
// the backend's classification of the three routes (permissions.MODERATOR_WRITES).
export default function ServerControls() {
  const { status, refresh } = useServerStatus()
  const canModerate = useCan('moderator')
  const pushToast = useToast()
  const [confirmAction, setConfirmAction] = useState(null) // 'start' | 'stop' | 'restart' | null
  const [busy, setBusy] = useState(false)
  // What a start would be refused for (a runtime the server needs looks
  // missing), asked when Start is clicked. null = nothing in the way.
  const [startProblem, setStartProblem] = useState(null)

  // The Start button counts down to systemd's next retry. The number is
  // re-seeded by each status poll (every 5s) and ticks locally in between, so
  // it moves once a second without polling faster just for this.
  const [retrySeconds, setRetrySeconds] = useState(null)
  useEffect(() => {
    setRetrySeconds(status?.restart_pending ? (status.restart_in_seconds ?? null) : null)
  }, [status])
  useEffect(() => {
    if (!status?.restart_pending) return undefined
    const timer = setInterval(
      () => setRetrySeconds((s) => (s == null ? s : Math.max(0, s - 1))),
      1000
    )
    return () => clearInterval(timer)
  }, [status?.restart_pending])

  if (!canModerate) return null

  async function askStart() {
    setBusy(true)
    let check = null
    try {
      check = await api.get('/api/service/start-check')
    } catch {
      // The start route checks again; a failed check only skips the warning.
    }
    setBusy(false)
    // No server, or no unit: nothing to confirm and no Start anyway. The
    // sentence names what installs it.
    if (check?.refusal) {
      pushToast(check.refusal, 'error', 10000)
      return
    }
    setStartProblem(check?.problem || null)
    setConfirmAction('start')
  }

  async function runAction(action) {
    setConfirmAction(null)
    setBusy(true)
    try {
      await api.post(
        `/api/service/${action}`,
        action === 'start' && startProblem ? { anyway: true } : undefined
      )
      pushToast(`Server ${action} requested.`, 'info')
      refresh()
    } catch (err) {
      pushToast(err.message || `Failed to ${action} server`, 'error')
    } finally {
      setBusy(false)
    }
  }

  const running = !!status?.running
  const restartPending = !!status?.restart_pending
  const stopping = isStopping(status)

  return (
    <>
      {stopping ? (
        <button className="btn btn-ghost" disabled>
          <HiOutlineStop aria-hidden="true" />
          Stopping…
        </button>
      ) : running ? (
        <>
          <button className="btn btn-danger" disabled={busy} onClick={() => setConfirmAction('stop')}>
            <HiOutlineStop aria-hidden="true" />
            Stop
          </button>
          <button className="btn btn-ghost" disabled={busy} onClick={() => setConfirmAction('restart')}>
            <HiOutlineArrowPath aria-hidden="true" />
            Restart
          </button>
        </>
      ) : (
        <button
          className="btn btn-primary"
          disabled={busy || restartPending}
          onClick={askStart}
          {...tooltipAttrs(restartPending ? RETRY_TIP : '')}
        >
          {restartPending ? (
            <HiOutlineArrowPath aria-hidden="true" />
          ) : (
            <HiOutlinePlay aria-hidden="true" />
          )}
          {restartPending
            ? retrySeconds > 0
              ? `Retrying in ${formatCountdown(retrySeconds)}`
              : 'Starting…'
            : 'Start'}
        </button>
      )}
      {/* Portalled: the InfoBar is sticky with a z-index (and dims when stale),
          so a backdrop rendered inside it would sit under the sidebar and the
          activity drawer, and fade with the strip. */}
      {createPortal(
        <ConfirmModal
          open={confirmAction !== null}
          title={confirmAction ? `${capitalize(confirmAction)} server?` : ''}
          message={confirmAction === 'start' && startProblem ? '' : ACTION_CONFIRM[confirmAction] || ''}
          confirmLabel={
            confirmAction === 'start' && startProblem
              ? 'Start anyway'
              : confirmAction
                ? capitalize(confirmAction)
                : 'Confirm'
          }
          danger={
            confirmAction === 'stop' ||
            confirmAction === 'restart' ||
            (confirmAction === 'start' && !!startProblem)
          }
          onConfirm={() => runAction(confirmAction)}
          children={
            confirmAction === 'start' && startProblem ? (
              <>
                <div className="modal-message">
                  <DetailText text={startProblem} />
                </div>
                <p className="modal-message">{MISSING_RUNTIME_NOTE}</p>
              </>
            ) : null
          }
          onCancel={() => setConfirmAction(null)}
        />,
        document.body
      )}
    </>
  )
}
