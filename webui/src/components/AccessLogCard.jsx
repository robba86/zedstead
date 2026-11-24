import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import DataTable from './DataTable.jsx'
import { formatWhen } from '../format.js'
import RefreshButton from './RefreshButton.jsx'
import PanelNote from './PanelNote.jsx'
import Pager from './Pager.jsx'

// Who reached the panel, from where, when (TODO B6).
//
// The failed attempts are the point. Before this the only trace of one was a
// 401 in uvicorn's log, which `zsm web` runs at `warning`, so it was never
// printed at all.
//
// The "Who" column arrived with accounts (plans/43 step 6) and is blank for
// every line written before that, so it renders a dash rather than assuming
// one. A failed attempt on a name that matches no account deliberately does
// **not** carry the name: it may be a password typed into the username box.
// The backend sends `unknown_user` instead — see app/web/access_log.py.
// One entry per event app/web/access_log.py records (EVENTS). A wrong second
// factor is its own event, kept apart on purpose: the password was right.
// It showed as a grey "Login_totp_failed" and was not counted.
const LABELS = {
  login: 'Signed in',
  login_failed: 'Failed sign-in',
  login_totp_failed: 'Wrong 2FA code',
  logout: 'Signed out',
}
const FAILED_EVENTS = new Set(['login_failed', 'login_totp_failed'])

// The backend keeps at most 500 entries (access_log.MAX_ENTRIES) and caps
// `limit` there, so the card fetches all of them once and pages in the browser.
const FETCH_LIMIT = 500
const PAGE_SIZE = 25

function who(row) {
  if (row.user) return row.user
  if (row.unknown_user) return <span className="hint">no such account</span>
  return <span className="hint">—</span>
}

export default function AccessLogCard() {
  const [entries, setEntries] = useState([])
  const [path, setPath] = useState('')
  const [failed, setFailed] = useState(false)
  const [page, setPage] = useState(1)

  const load = useCallback(() => {
    return api
      .get(`/api/security/access-log?limit=${FETCH_LIMIT}`)
      .then((data) => {
        setEntries(data.entries || [])
        setPage(1)
        setPath(data.path || '')
        setFailed(false)
        return true
      })
      .catch(() => {
        setFailed(true)
        return false
      })
  }, [])

  useEffect(() => {
    load()
  }, [load])

  if (failed) return null

  const failures = entries.filter((e) => FAILED_EVENTS.has(e.event)).length
  const totalPages = Math.max(1, Math.ceil(entries.length / PAGE_SIZE))
  const shown = entries.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE)

  return (
    <div className="card">
      <div className="page-header">
        <h2>Access log</h2>
        <RefreshButton what="Access log" onRefresh={load} />
      </div>
      <PanelNote>
        Sign-ins to this app, newest first. Lines from before accounts existed have no
        name against them.
        {failures > 0 && ` ${failures} failed attempt${failures === 1 ? '' : 's'} listed.`}
      </PanelNote>
      <DataTable
        rowKey={(row) => `${row.timestamp}-${row.event}-${row.ip}-${row.user || ''}`}
        emptyMessage="Nothing recorded yet."
        rows={shown}
        columns={[
          { key: 'timestamp', label: 'When', render: (r) => formatWhen(r.timestamp) },
          {
            key: 'event',
            label: 'What',
            render: (r) => (
              <span
                className={`status-pill status-pill-${
                  FAILED_EVENTS.has(r.event) ? 'failed' : 'neutral'
                }`}
              >
                {LABELS[r.event] || r.event}
              </span>
            ),
          },
          { key: 'user', label: 'Who', render: who },
          { key: 'ip', label: 'From' },
          { key: 'user_agent', label: 'Client' },
        ]}
      />
      {totalPages > 1 && (
        <Pager
          page={page}
          totalPages={totalPages}
          onPrev={() => setPage((p) => Math.max(1, p - 1))}
          onNext={() => setPage((p) => Math.min(totalPages, p + 1))}
          extra={`${entries.length} entries`}
        />
      )}
      {path && <p className="hint">{path}</p>}
    </div>
  )
}
