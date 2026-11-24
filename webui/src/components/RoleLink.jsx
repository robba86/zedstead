import { Link } from 'react-router-dom'
import { roleAtLeast, useRole } from '../role.jsx'
import { minRoleFor } from '../routes'

// A link only for a role the page it opens is for (routes.js `minRole`), and
// `fallback` otherwise: a viewer's Dashboard linked to /mods, /scheduler and
// /backups, which answer "Not for your role".
// Courtesy, like every role check in the UI: the API refuses regardless.
export default function RoleLink({ to, fallback = null, children, ...rest }) {
  const { role } = useRole()
  const path = String(to).split(/[?#]/)[0]
  if (!roleAtLeast(role, minRoleFor(path))) return fallback
  return (
    <Link to={to} {...rest}>
      {children}
    </Link>
  )
}
