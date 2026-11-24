import { tooltipAttrs } from './Tooltip.jsx'
import BetaBadge from './BetaBadge.jsx'
import Select from './Select.jsx'
import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import DataTable from './DataTable.jsx'
import RefreshButton from './RefreshButton.jsx'
import PanelNote from './PanelNote.jsx'
import { useConfirm } from './useConfirm.jsx'

// Panel accounts and their roles (TODO B4, plans/43 step 5b).
//
// Admin-only, and it does not police that itself: `GET /api/users` answers 403
// for anybody else, so a non-admin gets `failed` and the card renders nothing.
// Hiding a card is not a permission — the API is what refuses. The same is
// true of the row buttons below.
//
// Your own row has no Role select and no Delete, matching the API. With two
// admins the store's last-admin guard is satisfied by the other one, so an
// admin who misclicked their own row would have locked themselves out of a
// panel that cannot be repaired from inside itself.
const ROLE_HELP = {
  viewer: 'Reads status, players, logs and configs. Changes nothing.',
  moderator: 'Also kicks, bans, broadcasts, uses RCon, and starts/stops/restarts.',
  admin: 'Everything, including mods, backups, settings and these accounts.',
}

const roleOptions = (roles) => roles.map((value) => ({ value, label: value, description: ROLE_HELP[value] }))

export default function UsersCard() {
  // Fetched here rather than threaded down from App: App calls /api/auth/me
  // already but throws the body away, and plans/43 step 5c is where a role
  // context replaces both. One extra request on one page is the cheaper of
  // the two wrong answers until then.
  const [me, setMe] = useState('')
  const [users, setUsers] = useState([])
  const [roles, setRoles] = useState([])
  const [path, setPath] = useState('')
  const [degraded, setDegraded] = useState(false)
  const [failed, setFailed] = useState(false)
  const [error, setError] = useState('')
  const [name, setName] = useState('')
  const [password, setPassword] = useState('')
  const [role, setRole] = useState('viewer')
  const confirm = useConfirm()

  // Delete asks first: it cannot be undone.
  async function remove(name) {
    const ok = await confirm({
      title: `Delete ${name}?`,
      message: 'Their open session stops working on their next request. This cannot be undone.',
      confirmLabel: 'Delete',
      danger: true,
    })
    if (ok) run(api.del(`/api/users/${encodeURIComponent(name)}`))
  }

  const load = useCallback(() => {
    return api
      .get('/api/users')
      .then((data) => {
        setUsers(data.users || [])
        setRoles(data.roles || [])
        setPath(data.path || '')
        setDegraded(!!data.degraded)
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
    api
      .get('/api/auth/me')
      .then((data) => setMe(data.username || ''))
      .catch(() => setMe(''))
  }, [load])

  if (failed) return null

  const isMe = (row) => (row.name || '').toLowerCase() === (me || '').toLowerCase()

  // Resolves true once the write and the reload are done, false when the
  // write was refused (its sentence is shown).
  function run(promise) {
    setError('')
    return promise
      .then(load)
      .then(() => true)
      .catch((err) => {
        setError(err.message || 'Failed')
        return false
      })
  }

  // The boxes are emptied only after the account exists: a refused Add
  // (a short password, a name in use) used to empty them too, so the admin
  // typed everything again.
  function handleCreate(e) {
    e.preventDefault()
    run(api.post('/api/users', { name, password, role })).then((ok) => {
      if (!ok) return
      setName('')
      setPassword('')
      setRole('viewer')
    })
  }

  return (
    <div className="card">
      <div className="page-header">
        <h2>
          Accounts <BetaBadge feature="accounts" />
        </h2>
        <RefreshButton what="Accounts" onRefresh={load} />
      </div>
      <PanelNote>
        Who can sign in, and as what. A role change takes effect on that person&apos;s
        next request — not when their session expires. Two-factor sign-in is each
        person&apos;s own to turn on; if somebody loses their phone,{' '}
        <code>zsm users totp disable &lt;name&gt;</code> is the way back in.
      </PanelNote>

      {degraded && (
        <p className="login-error">
          {path} exists but could not be read. Sign-in has fallen back to the single
          password, and no change here will be accepted until that file is fixed or
          moved.
        </p>
      )}
      {error && <p className="login-error">{error}</p>}

      <DataTable
        rowKey={(row) => row.name}
        emptyMessage="No accounts yet — the app is still on its single password."
        rows={users}
        columns={[
          {
            key: 'name',
            label: 'Name',
            render: (r) => (
              <>
                {r.name}
                {isMe(r) && <span className="hint"> (you)</span>}
              </>
            ),
          },
          {
            key: 'role',
            label: 'Role',
            help: 'viewer reads; moderator also acts on players and the server; admin does everything.',
            render: (r) =>
              isMe(r) ? (
                <span {...tooltipAttrs('You cannot change your own role.')}>{r.role}</span>
              ) : (
                <Select
                  className="text-input text-input-narrow"
                  aria-label={`Role of ${r.name}`}
                  value={r.role}
                  onChange={(role) => run(api.put(`/api/users/${encodeURIComponent(r.name)}/role`, { role }))}
                  options={roleOptions(roles)}
                />
              ),
          },
          {
            // Whether each account has a second factor, never anything about
            // it — the secret *is* the factor, and `User.public()` does not
            // carry it. "Who is not protected" is the question an admin asks
            // here, and this is the only place it is visible at a glance.
            key: 'totp',
            label: '2FA',
            help: 'A code from an authenticator app on top of the password. Each person turns their own on, from Your account.',
            render: (r) =>
              r.totp ? (
                <span className="status-pill status-pill-success">On</span>
              ) : (
                <span className="hint">—</span>
              ),
          },
          {
            key: 'actions',
            label: '',
            render: (r) =>
              isMe(r) ? null : (
                <button
                  className="btn btn-danger btn-small"
                  onClick={() => remove(r.name)}
                >
                  Delete
                </button>
              ),
          },
        ]}
      />

      <form className="inline-form" onSubmit={handleCreate}>
        <input
          className="text-input"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="New account name"
        />
        <input
          className="text-input"
          type="password"
          autoComplete="new-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="Password"
        />
        <Select
          className="text-input text-input-narrow"
          aria-label="Role"
          value={role}
          onChange={setRole}
          options={roleOptions(roles.length ? roles : ['viewer'])}
        />
        <button className="btn btn-primary" type="submit" disabled={!name || !password}>
          Add
        </button>
      </form>
      {path && <p className="hint">{path}</p>}

    </div>
  )
}
