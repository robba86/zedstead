// Whether this server still offers Quick setup (GET /api/quick-setup/state):
// until a Quick setup finishes here, and again when the server is gone.
// The sidebar hides Quick setup and the page locks its cards when it is not offered.
import { useEffect, useState } from 'react'
import { api } from './api'
import { TERMINAL_STATUSES, useJobs } from './jobs'
import { roleAtLeast, useRole } from './role.jsx'

// `{ offered, reason }`, or null while unknown. It asks again when a Quick setup job ends.
export function useQuickSetupState() {
  const { role } = useRole()
  const { jobs } = useJobs()
  const isAdmin = roleAtLeast(role, 'admin')
  const finished = jobs.filter((j) => j.kind === 'quick_setup' && TERMINAL_STATUSES.has(j.status)).length
  const [state, setState] = useState(null)

  useEffect(() => {
    if (!isAdmin) return undefined
    let live = true
    api
      .get('/api/quick-setup/state')
      .then((data) => live && setState(data))
      .catch(() => {
        // Unknown: the sidebar keeps Quick setup and the Apply route still refuses.
      })
    return () => {
      live = false
    }
  }, [isAdmin, finished])

  return state
}
