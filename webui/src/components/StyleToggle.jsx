import { useTheme } from '../theme'
import { tooltipAttrs } from './Tooltip.jsx'

// Sidebar compact/classic switch, beside the light/dark one (ThemeToggle.jsx)
// at the bottom of the sidebar and drawn the same way: a pill whose knob
// slides right when the compact style ('slim') is on, with a large dot on the
// knob for classic and a small one for compact. No text: the name is the aria-label,
// and the tooltip says what a click does. The Settings page's Appearance card
// picks the same `zsm.style` (theme.jsx).
const LABEL = 'Compact style'
const TIP = {
  classic: 'Switch to the compact style',
  slim: 'Switch to the classic style',
}

export default function StyleToggle() {
  const { style, setStyle } = useTheme()

  return (
    <button
      className="sidebar-action style-toggle-btn"
      role="switch"
      aria-checked={style === 'slim'}
      aria-label={LABEL}
      onClick={() => setStyle(style === 'slim' ? 'classic' : 'slim')}
      {...tooltipAttrs(TIP[style])}
    >
      <span className="theme-switch style-switch" aria-hidden="true" />
    </button>
  )
}
