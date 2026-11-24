import { useTheme } from '../theme'
import { tooltipAttrs } from './Tooltip.jsx'

// Sidebar light/dark switch. Until it is clicked the OS setting decides
// (theme.jsx); a click flips to the other Tactical variant and is remembered
// in this browser.
//
// A switch, not an icon (2026 Q4, after
// codepen.io/web-strategist/pen/WbNGPZL): a pill whose knob slides right when
// dark mode is on, alone and right-aligned at the bottom of the sidebar
// (.sidebar-theme), with no text: the name is the aria-label, and the tooltip
// says what a click does. role="switch" + aria-checked say the state to a
// screen reader, so the name itself stays the same. The site's
// menu has the same switch (zedstead-site src/theme.js, .theme-toggle), kept in
// step by hand like the tokens.
const LABEL = 'Dark mode'
const TIP = {
  tactical: 'Switch to light mode',
  'tactical-light': 'Switch to dark mode',
}

// A click fades every colour over instead of cutting (.theme-fading in
// index.css). Only for the length of the fade, so no other transition in the
// panel is touched, and not at all under reduced motion.
let fadeTimer
function fade() {
  if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return
  const html = document.documentElement
  html.classList.add('theme-fading')
  clearTimeout(fadeTimer)
  fadeTimer = setTimeout(() => html.classList.remove('theme-fading'), 350)
}

export default function ThemeToggle() {
  const { theme, toggle } = useTheme()

  return (
    <button
      className="sidebar-action theme-toggle-btn"
      role="switch"
      aria-checked={theme === 'tactical'}
      aria-label={LABEL}
      onClick={() => {
        fade()
        toggle()
      }}
      {...tooltipAttrs(TIP[theme])}
    >
      <span className="theme-switch" aria-hidden="true" />
    </button>
  )
}
