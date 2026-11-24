// One plain question with its control: a row of My server and of Quick setup's
// "Your server" card. `to` adds a link to the page that holds the full setting.
import { Link } from 'react-router-dom'

export default function SettingQuestion({ label, hint, to, linkLabel, children }) {
  return (
    <li className="my-server-question">
      <div className="my-server-label">{label}</div>
      <div className="my-server-control">
        {children}
        {hint && <p className="hint">{hint}</p>}
      </div>
      {to && (
        <Link className="my-server-link" to={to}>
          {linkLabel}
        </Link>
      )}
    </li>
  )
}
