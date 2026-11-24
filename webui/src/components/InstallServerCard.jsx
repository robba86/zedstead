import { useEffect, useState } from 'react'
import { HiOutlineArrowDownTray, HiOutlineCheckCircle } from 'react-icons/hi2'
import { INSTALL_SERVER_JOB } from '../fixActions.js'
import { useGuide } from '../guide.jsx'
import { useJobs } from '../jobs'
import NeedsSteam from './NeedsSteam.jsx'
import { useToast } from './Toast.jsx'
import { tooltipAttrs } from './Tooltip.jsx'

// The Dashboard's first-run card: shown while GET /api/setup-state says the
// DayZ server is not installed (doctor.server_is_installed), so a new admin
// installs the server files before anything else. Nothing while that answer
// is not in yet: a card that flashes on an installed box reads as a bug.
// The answer is re-fetched on `settings_changed`, which the update job sends
// when it finishes, so the card goes away by itself. An install started here
// that succeeds leaves a green banner in its place until it is closed.
// *job* replaces the install job, so Quick setup can install through its own
// route and keep its setup unfinished. *done* replaces the success line.
export default function InstallServerCard({
  job = INSTALL_SERVER_JOB,
  done = 'The DayZ server is installed. Pick a map and mods next, or start it as it is.',
}) {
  const { detected, ensureDetected } = useGuide()
  const { startJob, jobRunning } = useJobs()
  const pushToast = useToast()
  const [starting, setStarting] = useState(false)
  const [installed, setInstalled] = useState(false)

  useEffect(() => {
    ensureDetected()
  }, [ensureDetected])

  if (installed) {
    return (
      <div className="banner banner-success banner-closable">
        <HiOutlineCheckCircle className="banner-icon" aria-hidden="true" />
        <span>{done}</span>
        <button
          type="button"
          className="banner-close"
          aria-label="Close"
          {...tooltipAttrs('Close')}
          onClick={() => setInstalled(false)}
        >
          ×
        </button>
      </div>
    )
  }
  if (detected?.install?.done !== false) return null

  async function install() {
    setStarting(true)
    try {
      await startJob({
        ...job,
        onStatusChange: (status) => {
          if (status === 'success') setInstalled(true)
        },
      })
    } catch (err) {
      pushToast(err.message || 'Failed to start the server install', 'error')
    } finally {
      setStarting(false)
    }
  }

  return (
    <div className="card install-server-card">
      <h2>No DayZ server installed yet</h2>
      <p>
        Install the server files first: SteamCMD downloads the DayZ server into your server
        directory. It is several GB, so it takes a while; the log is in the Activity drawer.
      </p>
      <div className="action-bar">
        <NeedsSteam>
          <button className="btn btn-primary" disabled={starting || jobRunning} onClick={install}>
            <HiOutlineArrowDownTray aria-hidden="true" />
            {starting ? 'Starting…' : 'Install server'}
          </button>
        </NeedsSteam>
      </div>
    </div>
  )
}
