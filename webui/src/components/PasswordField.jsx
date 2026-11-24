import { useState } from 'react'
import { HiOutlineEye, HiOutlineEyeSlash } from 'react-icons/hi2'
import { tooltipAttrs } from './Tooltip.jsx'

// A password in a config form (serverDZ.cfg's join and admin passwords,
// BattlEye's RCon password; the backend marks them `secret` from
// redact.SECRET_KEYS): dots until the eye button shows it in clear text. The
// Raw tab shows the file anyway, so this only keeps it off a shared screen.
// The rest of the props go on the <input> (value, onChange, aria-label, …).
export default function PasswordField({ className = 'text-input text-input-narrow', disabled, ...input }) {
  const [shown, setShown] = useState(false)
  const tip = shown ? 'Hide the password' : 'Show the password'
  return (
    <span className="password-field">
      <input
        className={className}
        type={shown ? 'text' : 'password'}
        autoComplete="new-password"
        spellCheck={false}
        disabled={disabled}
        {...input}
      />
      <button
        type="button"
        className="btn btn-icon"
        aria-label={tip}
        aria-pressed={shown}
        {...tooltipAttrs(tip)}
        onClick={() => setShown((v) => !v)}
      >
        {shown ? <HiOutlineEyeSlash aria-hidden="true" /> : <HiOutlineEye aria-hidden="true" />}
      </button>
    </span>
  )
}
