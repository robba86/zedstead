import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import { useWs } from '../ws'
import { useToast } from './Toast.jsx'
import { SkeletonLines } from './Skeleton.jsx'
import BetaBadge from './BetaBadge.jsx'
import Tooltip, { tooltipAttrs } from './Tooltip.jsx'
import PanelNote from './PanelNote.jsx'
import { useConfirm } from './useConfirm.jsx'

// The service setup: regenerate the systemd unit or the start/stop .bat files,
// enable or disable start at boot, and remove the unit (Linux only).
//
// Two places draw it. `inline` is the buttons alone, under the Doctor tile's
// failing "Service" row (DoctorCard). Without it, it is the Service Setup
// dashboard tile, hidden by default since 2026 Q4, which keeps the buttons in
// reach once the service is installed and the Doctor row is gone. Both callers
// show it to an admin only, since every button is an admin write.
export default function ServiceSetupCard({ inline = false }) {
  const [setup, setSetup] = useState(null)
  const [failed, setFailed] = useState(false)
  const [busy, setBusy] = useState(false)
  const confirm = useConfirm()
  const pushToast = useToast()

  const load = useCallback(async () => {
    try {
      setSetup(await api.get('/api/service/setup'))
      setFailed(false)
    } catch {
      // Only a failed fetch hides the buttons. See the skeleton branch below.
      setFailed(true)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const handleWsMessage = useCallback(
    (msg) => {
      if (msg.type === 'service_changed') load()
    },
    [load]
  )
  useWs(handleWsMessage)

  // Regenerate and Remove ask first; the others run at once.
  async function ask(options, action, successMsg) {
    if (await confirm(options)) run(action, undefined, successMsg)
  }

  async function run(action, payload, successMsg) {
    setBusy(true)
    try {
      await api.post(`/api/service/${action}`, payload)
      pushToast(successMsg, 'info')
      load()
    } catch (err) {
      pushToast(err.message || `Service ${action} failed`, 'error')
    } finally {
      setBusy(false)
    }
  }

  if (failed) return null

  // The tile's header, minus the state pills while the first fetch runs.
  const title = (
    <h2>
      Service Setup <BetaBadge feature="service-setup" />
    </h2>
  )

  if (!setup) {
    if (inline) return <SkeletonLines count={1} label="Loading service setup…" />
    return (
      <div className="card">
        <div className="page-header">{title}</div>
        <SkeletonLines count={3} label="Loading service setup…" />
      </div>
    )
  }

  const linux = !setup.is_windows
  // Prefer the scope-aware privileged-ops gate; fall back to the legacy
  // sudo_available flag for older backends.
  const privilegedOk =
    setup.privileged_ops_ok !== undefined ? setup.privileged_ops_ok : setup.sudo_available
  const privilegedWarning = linux && setup.has_systemd && !privilegedOk
  const privilegedReason =
    setup.privileged_ops_reason ||
    'sudo is not available non-interactively — service changes will fail. Configure ' +
      'passwordless sudo or use zsm service from a terminal.'

  const controls = (
    <div className="service-setup-controls">
      {privilegedWarning && (
        <div className="banner banner-warning">{privilegedReason}</div>
      )}
      {/* A setting that silently does nothing until someone regenerates is
          worse than no setting. Restart policy, restart delay and the
          crash-loop limit all live in a file systemd read at install time. */}
      {setup.stale && (
        <div className="banner banner-warning">
          The installed service file no longer matches your settings — regenerate it
          for the change to reach systemd.
        </div>
      )}
      <div className="inline-form">
        <Tooltip text={setup.running ? 'Stop the server first' : ''} className="bare">
          <button
            className="btn btn-primary btn-small"
            disabled={busy || setup.running}
            onClick={() =>
              ask(
                {
                  title: 'Regenerate service files?',
                  message:
                    'The server is stopped if running, then the service files are regenerated and installed from the current settings.',
                  confirmLabel: 'Regenerate',
                },
                'regenerate',
                'Service files regenerated.',
              )
            }
          >
            {setup.installed ? 'Regenerate service files' : 'Install service'}
          </button>
        </Tooltip>
        {linux && setup.has_systemd && (
          <>
            <button
              className="btn btn-ghost btn-small"
              disabled={busy || !setup.installed}
              onClick={() =>
                run(
                  'boot',
                  { enabled: !setup.enabled_at_boot },
                  setup.enabled_at_boot ? 'Start at boot disabled.' : 'Start at boot enabled.'
                )
              }
            >
              {setup.enabled_at_boot ? 'Disable start at boot' : 'Enable start at boot'}
            </button>
            <button
              className="btn btn-danger btn-small"
              disabled={busy || !setup.installed || setup.running}
              onClick={() =>
                ask(
                  {
                    title: 'Remove service?',
                    message: 'Stops, disables, and removes the systemd unit. Server files are not touched.',
                    confirmLabel: 'Remove',
                    danger: true,
                  },
                  'remove',
                  'Service removed.',
                )
              }
            >
              Remove service
            </button>
          </>
        )}
        {inline && <BetaBadge feature="service-setup" />}
      </div>
    </div>
  )

  if (inline) return controls

  return (
    <div className="card">
      <div className="page-header">
        {title}
        {linux && setup.has_systemd && setup.scope && (
          <span
            className="status-pill status-pill-neutral"
            {...tooltipAttrs('systemd service scope')}
          >
            {setup.scope} scope
          </span>
        )}
        <span className={`status-pill status-pill-${setup.installed ? 'success' : 'pending'}`}>
          {setup.installed ? 'Installed' : 'Not installed'}
        </span>
      </div>
      <PanelNote>
        {linux
          ? 'Systemd unit for the DayZ server. Regenerate after changing mods, launch args, or RCON config.'
          : 'start-server.bat / stop-server.bat scripts. Regenerate after changing mods, launch args, or RCON config.'}
      </PanelNote>
      {controls}
    </div>
  )
}
