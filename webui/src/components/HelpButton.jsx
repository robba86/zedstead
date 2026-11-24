import { HiOutlineQuestionMarkCircle } from 'react-icons/hi2'
import { tooltipAttrs } from './Tooltip.jsx'

// A "?" beside a control whose tap is its action (a switch), on a touch
// screen: a tap here shows the control's tip, so reading it never changes the
// value (user test 2026-11-03: a switch's help only showed after a toggle).
// The tip is a `keep` one, which the touch rules in Layout.jsx still open.
export default function HelpButton({ text, label = 'Help' }) {
  if (!text) return null
  return (
    <button type="button" className="help-button" aria-label={label} {...tooltipAttrs(text, { keep: true })}>
      <HiOutlineQuestionMarkCircle aria-hidden="true" />
    </button>
  )
}
