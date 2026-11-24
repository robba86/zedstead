import { useEffect } from 'react'
import { useGuide } from '../guide.jsx'
import { countProgress, firstPending } from '../guideSteps.js'
import PanelNote from './PanelNote.jsx'

// Compact Guide beside the Dashboard's Doctor card: the same step list and the
// same progress record the drawer reads (../guide.jsx merges what the user
// ticked in localStorage `zsm.wizard.progress` with what GET /api/setup-state
// detected), never a second source of truth. Read-only — ticking a step done
// stays in the drawer; the buttons here only open it, so the card and the
// drawer can be on screen together without ever disagreeing.
//
// Mounting the card asks the provider for the detected half (once per page
// load, shared with the drawer), so the count is right without opening the
// drawer first (test pass 1, F3).
export default function GuideCard() {
  const { progress, openGuide, open, ensureDetected } = useGuide()
  useEffect(() => {
    ensureDetected()
  }, [ensureDetected])

  const { done, handled, total } = countProgress(progress)
  const next = handled === total ? null : firstPending(progress)

  return (
    <div className="card guide-card">
      <div className="card-header">
        <h2>Guide</h2>
        <button type="button" className="btn-link card-link" onClick={() => openGuide()}>
          {open ? 'Open ↖' : 'Open →'}
        </button>
      </div>

      <div className="guide-progress guide-card-progress">
        <div className="guide-bar">
          <div className="guide-bar-fill" style={{ width: `${(handled / total) * 100}%` }} />
        </div>
        <span className="hint guide-progress-text">
          {done} of {total} done
          {handled > done ? `, ${handled - done} skipped` : ''}
        </span>
      </div>

      {next ? (
        <p className="guide-card-next">
          <span className="hint">Next up</span>
          <button
            type="button"
            className="btn-link guide-inline-link"
            onClick={() => openGuide(next.id)}
          >
            {next.title}
          </button>
        </p>
      ) : (
        <PanelNote>Every step handled. Re-open it any time for a refresher.</PanelNote>
      )}
    </div>
  )
}
