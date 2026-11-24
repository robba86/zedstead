// The Dashboard's large start card, while the server is not installed. It is
// not a tile in CARDS, so no saved layout key changes. It renders nothing during
// loading and after the install.
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { resolveHelpSlug } from '../helpLinks.js'

export default function QuickSetupStart() {
  const [notInstalled, setNotInstalled] = useState(false)

  useEffect(() => {
    let live = true
    api
      .get('/api/setup-state')
      .then((data) => {
        if (live) setNotInstalled(data.steps?.install?.done === false)
      })
      .catch(() => {
        // The card is a shortcut, and the Guide still covers this step.
      })
    return () => {
      live = false
    }
  }, [])

  if (!notInstalled) return null
  return (
    <div className="card quick-setup-start">
      <h2>Set up your server</h2>
      <p>Read a short text, press Apply, and Zedstead installs a working DayZ server.</p>
      <Link className="btn btn-primary" to={resolveHelpSlug('quick-setup')}>
        Open Quick setup
      </Link>
    </div>
  )
}
