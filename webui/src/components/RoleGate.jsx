import { Link, Outlet, useLocation } from 'react-router-dom'
import { minRoleFor, navItemsFor } from '../routes'
import { roleAtLeast, useRole } from '../role.jsx'
import PanelNote from './PanelNote.jsx'

// The page a role may not open (TODO B4, plans/43 step 5c).
//
// Mounted **inside** Layout's route, so a refusal keeps the sidebar and the
// user can go somewhere they can use. Outside it, the whole window would be
// this notice and the only way on would be the Back button.
//
// It is not the permission. `app/web/deps.py::require_session` refuses the
// API whatever the browser thinks, and this gate would let a tampered bundle
// through in a second — what it prevents is a page that loads, renders its
// chrome, and then fills with 403s the reader has to interpret.
function PagesYouCanOpen({ role }) {
  const elsewhere = navItemsFor(role)
  if (elsewhere.length === 0) return null
  return (
    <PanelNote>
      What you can open:{' '}
      {elsewhere.map((item, i) => (
        <span key={item.to}>
          {i > 0 && ', '}
          {item.href ? (
            <a href={item.href} target="_blank" rel="noopener noreferrer">{item.label}</a>
          ) : (
            <Link to={item.to}>{item.label}</Link>
          )}
        </span>
      ))}
      .
    </PanelNote>
  )
}

function NotForYourRole({ role, needed }) {
  return (
    <div className="card">
      <h2>Not for your role</h2>
      <PanelNote>
        This page needs the <strong>{needed}</strong> role. You are signed in as{' '}
        <strong>{role || 'nobody'}</strong>. Ask an admin if you need it — they can
        change your role on the Settings page, and it takes effect on your next
        request.
      </PanelNote>
      <PagesYouCanOpen role={role} />
    </div>
  )
}

// A path no route matches. Without this the router rendered nothing at all —
// not even the sidebar — so a mistyped or outdated link was a blank window.
// Mounted inside Layout for the same reason as the refusal above.
export function NotFound() {
  const { role } = useRole()
  const { pathname } = useLocation()
  return (
    <div className="card">
      <h2>No such page</h2>
      <PanelNote>
        Nothing in the app lives at <code>{pathname}</code>. The link may be mistyped or
        from an older version of zsm.
      </PanelNote>
      <PagesYouCanOpen role={role} />
    </div>
  )
}

export default function RoleGate() {
  const { role } = useRole()
  const { pathname } = useLocation()
  const needed = minRoleFor(pathname)

  if (!roleAtLeast(role, needed)) {
    return <NotForYourRole role={role} needed={needed} />
  }
  return <Outlet />
}
