import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'
import { ToastProvider } from './components/Toast.jsx'
import { ConfirmProvider } from './components/useConfirm.jsx'
import { ThemeProvider } from './theme'

// A tab opened before Zedstead was updated asks for page chunks the update
// replaced, and every page change then failed with "Failed to fetch
// dynamically imported module" until the admin reloaded by hand.
// Vite fires vite:preloadError for exactly that: reload once to
// get the new build. Not again within a minute, so a chunk that is really
// missing shows the error card instead of a reload loop.
// The card tables' breakpoint (index.css, "A phone shows a TableEditor row"):
// a class on <html> instead of a media query, so a table that does not fit
// a wide screen can turn to cards under the same rules (DataTable cards="fit").
const narrowCards = window.matchMedia('(max-width: 640px)')
const setNarrowCards = () => document.documentElement.classList.toggle('cards-narrow', narrowCards.matches)
setNarrowCards()
narrowCards.addEventListener('change', setNarrowCards)

const RELOADED_KEY = 'zsm.reloadedForUpdate'
window.addEventListener('vite:preloadError', (event) => {
  let last = 0
  try {
    last = Number(sessionStorage.getItem(RELOADED_KEY)) || 0
  } catch {
    // no storage: reload anyway, once per page load
  }
  if (Date.now() - last < 60000) return
  try {
    sessionStorage.setItem(RELOADED_KEY, String(Date.now()))
  } catch {
    // ignore
  }
  event.preventDefault()
  window.location.reload()
})

// ThemeProvider is outermost: it owns <html data-theme>, so the login screen
// (rendered before Layout mounts) is themed too.
createRoot(document.getElementById('root')).render(
  <StrictMode>
    <ThemeProvider>
      <ToastProvider>
        <ConfirmProvider>
          <App />
        </ConfirmProvider>
      </ToastProvider>
    </ThemeProvider>
  </StrictMode>,
)
