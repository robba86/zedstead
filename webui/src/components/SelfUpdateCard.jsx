import { useCallback, useEffect, useRef, useState } from 'react'
import BetaBadge from './BetaBadge.jsx'
import { HiOutlineArrowDownTray, HiOutlineMagnifyingGlass } from 'react-icons/hi2'
import { api } from '../api'
import { withBase } from '../instance'
import { useJobs } from '../jobs'
import { useToast } from './Toast.jsx'
import CommandBlock from './CommandBlock.jsx'

// How often the page asks whether the restarted panel answers again, and how
// long it waits before reloading anyway.
const HEALTH_POLL_MS = 2000
const RELOAD_AFTER_MS = 60000

// Updating zsm itself (GET|POST /api/self-update). The check runs `git fetch`
// on the server, so it waits for a click rather than running on every visit.
export default function SelfUpdateCard() {
  const [info, setInfo] = useState(null)
  const [checking, setChecking] = useState(false)
  const [restarting, setRestarting] = useState(false)
  const pushToast = useToast()
  const { startJob } = useJobs()
  const timer = useRef(null)

  const check = useCallback(async () => {
    setChecking(true)
    try {
      setInfo(await api.get('/api/self-update'))
    } catch (err) {
      pushToast(err.message || 'Could not check for updates', 'error')
    } finally {
      setChecking(false)
    }
  }, [pushToast])

  // The panel goes away and comes back as the new version. A restart ends
  // every session, so the reload lands on the sign-in page.
  const waitForRestart = useCallback(() => {
    setRestarting(true)
    const started = Date.now()
    let wentDown = false
    const poll = async () => {
      let up = false
      try {
        up = (await fetch(withBase('/api/health'), { cache: 'no-store' })).ok
      } catch {
        up = false
      }
      if (!up) wentDown = true
      if ((up && wentDown) || Date.now() - started > RELOAD_AFTER_MS) {
        window.location.reload()
        return
      }
      timer.current = setTimeout(poll, HEALTH_POLL_MS)
    }
    timer.current = setTimeout(poll, HEALTH_POLL_MS)
  }, [])

  useEffect(() => () => clearTimeout(timer.current), [])

  async function update() {
    try {
      await startJob({
        endpoint: '/api/self-update',
        name: 'Update Zedstead',
        kind: 'self_update',
        onStatusChange: (status, job) => {
          if (status !== 'success') return
          if (job.result?.restarting) waitForRestart()
          else check()
        },
      })
    } catch (err) {
      pushToast(err.message || 'Could not start the update', 'error')
    }
  }

  const canApply = info?.supported && !info.error && info.behind > 0

  return (
    <div className="card">
      <div className="card-header">
        <h2>
          Zedstead <BetaBadge feature="self-update" />
        </h2>
        <div className="action-bar">
          <button className="btn btn-ghost" disabled={checking || restarting} onClick={check}>
            <HiOutlineMagnifyingGlass aria-hidden="true" />
            {checking ? 'Checking…' : 'Check for updates'}
          </button>
          {canApply && (
            <button className="btn btn-primary" disabled={restarting} onClick={update}>
              <HiOutlineArrowDownTray aria-hidden="true" />
              Update Zedstead
            </button>
          )}
        </div>
      </div>
      {restarting ? (
        <div className="banner banner-info">
          Restarting the app… This page reloads when it is back, and you sign in again.
        </div>
      ) : !info ? (
        <p className="hint">Checks the branch Zedstead was installed from for new changes.</p>
      ) : (
        <SelfUpdateInfo info={info} />
      )}
    </div>
  )
}

function SelfUpdateInfo({ info }) {
  const more = info.behind - info.subjects.length
  return (
    <>
      <p>
        You run Zedstead {info.version}
        {info.current && <> ({info.branch} at <code>{info.current}</code>)</>}.
      </p>
      {info.error && <div className="banner banner-error">{info.error}</div>}
      {!info.error && info.current && info.behind === 0 && <p>Zedstead is up to date.</p>}
      {info.behind > 0 && (
        <>
          <p>
            {info.behind} new change{info.behind === 1 ? '' : 's'} on {info.branch} (up to{' '}
            <code>{info.latest}</code>):
          </p>
          <ul>
            {info.subjects.map((s, i) => (
              <li key={i}>{s}</li>
            ))}
            {more > 0 && <li>… and {more} more</li>}
          </ul>
          {info.supported && (
            <p className="hint">
              Update Zedstead reinstalls it and restarts the app. Everyone signed in is signed out and
              signs in again.
            </p>
          )}
        </>
      )}
      {!info.supported && (
        <>
          <p>{info.message}</p>
          <CommandBlock text={info.command} />
        </>
      )}
    </>
  )
}
