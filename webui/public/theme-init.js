// Stamp the theme and the style before the first paint, or every load flashes
// dark (or classic) before React mounts. Same localStorage keys and same
// resolution rules as src/theme.jsx, which takes over once it mounts — keep
// the two in sync.
//
// A file, not an inline <script>: the panel's Content-Security-Policy allows
// scripts only from the panel itself (app/web/security_headers.py). index.html
// loads it in <head> without async or defer, so it runs before the body is
// rendered, exactly as the inline script did.
;(function () {
  try {
    var choice = localStorage.getItem('zsm.theme')
    if (choice === 'dark') choice = 'tactical'
    if (choice === 'light') choice = 'tactical-light'
    if (choice !== 'tactical' && choice !== 'tactical-light') {
      choice = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'tactical' : 'tactical-light'
    }
    document.documentElement.setAttribute('data-theme', choice)
  } catch (e) {
    document.documentElement.setAttribute('data-theme', 'tactical')
  }
  // The effects: a stored choice, else low when the OS asks for less
  // transparency or motion. theme.jsx adds the software-renderer check after
  // it mounts, which needs a WebGL context and so is not done here.
  try {
    var effects = localStorage.getItem('zsm.effects')
    if (effects !== 'full' && effects !== 'low') {
      effects =
        window.matchMedia('(prefers-reduced-transparency: reduce)').matches ||
        window.matchMedia('(prefers-reduced-motion: reduce)').matches
          ? 'low'
          : 'full'
    }
    document.documentElement.setAttribute('data-effects', effects)
  } catch (e) {
    document.documentElement.setAttribute('data-effects', 'full')
  }
  try {
    var style = localStorage.getItem('zsm.style')
    document.documentElement.setAttribute('data-style', style === 'slim' ? 'slim' : 'classic')
  } catch (e) {
    document.documentElement.setAttribute('data-style', 'classic')
  }
})()
