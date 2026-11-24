import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { startPointerGlow } from './pointerGlow'

// Theme state for the whole app. Two palettes, Tactical (dark) and Tactical
// Light, with red/blue glows and glass cards (the "Tactical theme" block at
// the end of index.css). Until the user switches, the OS setting picks one
// and is followed while the app is open; a switch is stored and wins from
// then on. The plain Light and Dark themes were removed in 2026 Q4: a
// stored 'dark' or 'light' reads as its Tactical variant, a stored 'system'
// as nothing stored.
//
// The resolved theme is published as `data-theme` on <html>, which is what
// index.css's `'tactical'` and `'tactical-light'` blocks key off. public/theme-init.js
// (loaded by index.html) sets the same attribute from the same localStorage
// key before the first paint; this provider only takes over afterwards, so
// there is no flash of the wrong theme on load and no second source of truth
// for the initial value.
//
// Preference is per-browser (localStorage), not a DayzConfig field: it is a
// property of who is looking at the page, not of the server being managed.
//
// The style (`zsm.style`, published as `data-style` on <html>) is a second,
// independent axis: how dense the chrome is, not what colour it is. 'classic'
// is the original look and needs no CSS of its own; 'slim' is a block at the
// end of index.css (and mapeditor.css) keyed off `:root[data-style='slim']`.
// Picked on the Settings page's Appearance card; theme-init.js stamps it
// before the first paint the same way it does the theme.

const STORAGE_KEY = 'zsm.theme'
export const THEMES = ['tactical', 'tactical-light']
// What the removed themes stored, read as their Tactical variant.
const RETIRED = { dark: 'tactical', light: 'tactical-light' }
const STYLE_KEY = 'zsm.style'
export const STYLES = ['classic', 'slim']
// A third axis, the effects (2026 Q4): 'low' drops the glass blur, the
// fixed background, the pointer light and the page animations, which a
// machine with no graphics card (a VM) draws on the CPU on every scroll and
// every mouse move. 'auto' (nothing stored) is low where the browser draws in
// software or the OS asks for less transparency or motion. Published as
// `data-effects` on <html>; theme-init.js stamps the stored and the media part
// before the first paint.
const EFFECTS_KEY = 'zsm.effects'
export const EFFECTS = ['auto', 'full', 'low']

const ThemeContext = createContext(null)

function readChoice() {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (THEMES.includes(stored)) return stored
    if (RETIRED[stored]) return RETIRED[stored]
  } catch {
    // private mode / storage disabled — fall through to the OS setting
  }
  return null
}

function readStyle() {
  try {
    const stored = localStorage.getItem(STYLE_KEY)
    if (STYLES.includes(stored)) return stored
  } catch {
    // same fallback as readChoice
  }
  return 'classic'
}

function readEffects() {
  try {
    const stored = localStorage.getItem(EFFECTS_KEY)
    if (EFFECTS.includes(stored)) return stored
  } catch {
    // same fallback as readChoice
  }
  return 'auto'
}

// The renderer names Chromium and Firefox report when no graphics card is
// used: SwiftShader, Mesa's llvmpipe and softpipe, and Windows' Basic Render
// Driver. No WebGL at all also means no acceleration.
const SOFTWARE_RENDERER = /swiftshader|llvmpipe|softpipe|basic render|software/i

function drawsInSoftware() {
  try {
    const gl = document.createElement('canvas').getContext('webgl')
    if (!gl) return true
    const info = gl.getExtension('WEBGL_debug_renderer_info')
    const name = info ? String(gl.getParameter(info.UNMASKED_RENDERER_WEBGL)) : ''
    gl.getExtension('WEBGL_lose_context')?.loseContext()
    return SOFTWARE_RENDERER.test(name)
  } catch {
    return false
  }
}

function autoLowEffects() {
  try {
    if (window.matchMedia('(prefers-reduced-transparency: reduce)').matches) return true
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return true
  } catch {
    // no matchMedia: decide by the renderer alone
  }
  return drawsInSoftware()
}

function prefersDark() {
  try {
    return window.matchMedia('(prefers-color-scheme: dark)').matches
  } catch {
    return true // the app's own default is dark
  }
}

export function ThemeProvider({ children }) {
  const [choice, setChoiceState] = useState(readChoice)
  const [systemDark, setSystemDark] = useState(prefersDark)
  const [style, setStyleState] = useState(readStyle)
  const [effectsChoice, setEffectsState] = useState(readEffects)
  // Measured only while the choice is automatic, and once per such choice.
  const autoLow = useMemo(() => effectsChoice === 'auto' && autoLowEffects(), [effectsChoice])
  const effects = effectsChoice === 'auto' ? (autoLow ? 'low' : 'full') : effectsChoice

  // Track the OS setting; it decides only while nothing is stored.
  useEffect(() => {
    let mq
    try {
      mq = window.matchMedia('(prefers-color-scheme: dark)')
    } catch {
      return undefined
    }
    const onChange = (e) => setSystemDark(e.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])

  const theme = choice || (systemDark ? 'tactical' : 'tactical-light')

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
  }, [theme])

  // The Tactical themes' pointer light, which low effects leaves out.
  useEffect(() => (effects === 'full' ? startPointerGlow() : undefined), [effects])

  useEffect(() => {
    document.documentElement.setAttribute('data-effects', effects)
  }, [effects])

  const setEffects = useCallback((next) => {
    if (!EFFECTS.includes(next)) return
    setEffectsState(next)
    try {
      if (next === 'auto') localStorage.removeItem(EFFECTS_KEY)
      else localStorage.setItem(EFFECTS_KEY, next)
    } catch {
      // best-effort, as setTheme
    }
  }, [])

  useEffect(() => {
    document.documentElement.setAttribute('data-style', style)
  }, [style])

  const setStyle = useCallback((next) => {
    if (!STYLES.includes(next)) return
    setStyleState(next)
    try {
      localStorage.setItem(STYLE_KEY, next)
    } catch {
      // best-effort, as setTheme
    }
  }, [])

  const setTheme = useCallback((next) => {
    if (!THEMES.includes(next)) return
    setChoiceState(next)
    try {
      localStorage.setItem(STORAGE_KEY, next)
    } catch {
      // best-effort, same as jobs.jsx — the session still works unstored
    }
  }, [])

  const toggle = useCallback(() => {
    setTheme(theme === 'tactical' ? 'tactical-light' : 'tactical')
  }, [theme, setTheme])

  const value = useMemo(
    () => ({ theme, setTheme, toggle, style, setStyle, effects, effectsChoice, setEffects }),
    [theme, setTheme, toggle, style, setStyle, effects, effectsChoice, setEffects]
  )

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}

// { theme: 'tactical'|'tactical-light', setTheme, toggle,
//   style: 'classic'|'slim', setStyle,
//   effects: 'full'|'low' (resolved), effectsChoice: 'auto'|'full'|'low', setEffects }
export function useTheme() {
  const ctx = useContext(ThemeContext)
  if (!ctx) throw new Error('useTheme must be used inside <ThemeProvider>')
  return ctx
}
