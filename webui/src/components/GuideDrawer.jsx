// Guided tour of a first-time setup, in the order that actually works.
//
// Mostly a *reader*: a step is explanatory copy plus links to the page that
// does the work. Three steps are not, because for them one button (or one
// form) IS the whole step — Steam login, installing the server, starting it.
// Those render the app's existing controls; the Guide owns no endpoint of its
// own and no second login form. Every other step deliberately keeps only its
// links: picking a map or choosing mods is a decision, and a button that just
// navigates is the link it sits next to. Since 2026 Q3 this replaces the
// old "runs nothing, calls no endpoint that changes state" rule (TODO D2).
//
// It was a page (/wizard) until 2026 Q3. A drawer beside the sidebar means
// its links work as navigation: the user follows one, the app moves underneath,
// and the step they were reading is still open. So it is mounted once in Layout
// (outside the router Outlet) and stays open until the user closes it.
//
// A first visit opens it as a full-page modal instead (guide.jsx
// isFirstVisit). It is the same component in a backdrop, and following any
// of its links docks it as the drawer, so the reader sees the page the link
// opened.
//
// The step list, its copy, the progress record and the link resolver live in
// ../guideSteps.js — shared with the Dashboard's compact Guide card. Open/close
// and progress state live in ../guide.jsx.

import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import PageIntro from './PageIntro.jsx'
import SteamLoginCard from './SteamLoginCard.jsx'
import { NeedsSteamIf } from './NeedsSteam.jsx'
import { useToast } from './Toast.jsx'
import FoldCaret from './FoldCaret.jsx'
import { tooltipAttrs } from './Tooltip.jsx'
import { useFocusTrap } from './useFocusTrap.js'
import { useGuide } from '../guide.jsx'
import { QUICK_GUIDE_URL } from '../routes.js'
import { useJobs } from '../jobs'
import { roleAtLeast, useRole } from '../role.jsx'
import {
  DOCTOR,
  MOD_TABS,
  modTabLabel,
  STEPS,
  WIDTH_KEY,
  countProgress,
  stepLink,
  writeStored,
} from '../guideSteps.js'

const DEFAULT_WIDTH = 360
const MIN_WIDTH = 260
// Leave the content column something usable whatever the window size is.
// At 900px and below the drawer lies over the page (index.css), so it may
// take all but a strip of the screen; the desktop rule left a phone no room
// to drag at all.
const OVERLAY_MAX = 900
const maxWidth = () =>
  window.innerWidth <= OVERLAY_MAX ? window.innerWidth - 24 : Math.max(MIN_WIDTH, window.innerWidth - 420)

function clampWidth(px) {
  return Math.min(Math.max(px, MIN_WIDTH), maxWidth())
}

function loadWidth() {
  try {
    const raw = parseInt(localStorage.getItem(WIDTH_KEY) || '', 10)
    return Number.isFinite(raw) ? clampWidth(raw) : DEFAULT_WIDTH
  } catch {
    return DEFAULT_WIDTH
  }
}

// A link in the modal docks the guide as it navigates: the page it opens
// would otherwise sit behind the backdrop.
function GuideLink({ to, label, tip, className = 'link-chip' }) {
  const { modal, dockGuide } = useGuide()
  return (
    <Link
      className={className}
      to={to}
      onClick={modal ? dockGuide : undefined}
      {...tooltipAttrs(tip)}
    >
      {label}
    </Link>
  )
}

export default function GuideDrawer() {
  const {
    open,
    modal,
    closeGuide,
    dockGuide,
    progress,
    detected,
    refreshDetected,
    mark,
    reset,
    openId,
    setOpenId,
  } = useGuide()
  const { startJob } = useJobs()
  const { role } = useRole()
  const canAdmin = roleAtLeast(role, 'admin')
  const pushToast = useToast()
  const [acting, setActing] = useState('')

  // Degrades open: TraderPlus links stay visible unless we positively know the
  // mod is absent (a failed fetch must not hide half the Configs step).
  const [traderplus, setTraderplus] = useState(true)
  const [modTabHelp, setModTabHelp] = useState({})
  const [width, setWidth] = useState(loadWidth)
  const [dragging, setDragging] = useState(false)

  // The drawer is mounted for the whole session, so its two lookups wait for
  // the first open and then never run again — a guide nobody opens costs no
  // requests. This component stays mounted while closed (it only renders
  // null), so the answers survive close/reopen.
  const fetchedRef = useRef(false)
  useEffect(() => {
    if (!open || fetchedRef.current) return
    fetchedRef.current = true
    api
      .get('/api/mods')
      .then((data) => {
        const mods = (data && data.mods) || []
        setTraderplus(
          mods.some((m) => (m.name || '').toLowerCase().replace(/^@/, '') === 'traderplus')
        )
      })
      .catch(() => {})
    api
      .get('/api/mods/help')
      .then((data) => setModTabHelp((data && data.tabs) || {}))
      .catch(() => {})
  }, [open])

  // The one button a step is allowed. Every endpoint behind this already
  // exists and is reachable from a page; the Guide is only saving the trip.
  //
  // A refusal is surfaced, never worked around: POST /api/update/run answers
  // 409 while the server is running, and that is the right answer — installing
  // over a running server is how you corrupt it. The toast carries the API's
  // own message.
  const runAction = useCallback(
    async (step) => {
      const action = step.action
      if (!action) return
      setActing(step.id)
      try {
        if (action.job) {
          await startJob(action.job)
          pushToast(`${action.job.name} started — watch the Activity drawer.`, 'info')
        } else if (action.post) {
          await api.post(action.post)
          pushToast(`${action.label} requested.`, 'info')
        }
        // The backend broadcasts on both paths and the provider re-fetches on
        // that, but a WS frame can be missed; asking once here means the step
        // ticks even then.
        refreshDetected()
      } catch (err) {
        pushToast(err.message || `${action.label} failed`, 'error')
      } finally {
        setActing('')
      }
    },
    [startJob, pushToast, refreshDetected]
  )

  // Drag the right edge to resize. Pointer capture keeps the drag alive over
  // the content underneath.
  const dragRef = useRef(null)
  const onHandleDown = useCallback(
    (e) => {
      e.preventDefault()
      e.currentTarget.setPointerCapture(e.pointerId)
      dragRef.current = { startX: e.clientX, startWidth: width }
      setDragging(true)
    },
    [width]
  )

  const onHandleMove = useCallback((e) => {
    const drag = dragRef.current
    if (!drag) return
    setWidth(clampWidth(drag.startWidth + (e.clientX - drag.startX)))
  }, [])

  const endDrag = useCallback(
    (e) => {
      if (!dragRef.current) return
      dragRef.current = null
      setDragging(false)
      if (e?.pointerId != null && e.currentTarget.hasPointerCapture?.(e.pointerId)) {
        e.currentTarget.releasePointerCapture(e.pointerId)
      }
      writeStored(WIDTH_KEY, String(width))
    },
    [width]
  )

  const resetWidth = useCallback(() => {
    setWidth(DEFAULT_WIDTH)
    writeStored(WIDTH_KEY, null)
  }, [])

  // A shrinking viewport must not leave the drawer wider than the window.
  useEffect(() => {
    const onResize = () => setWidth((w) => clampWidth(w))
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  // Escape closes the modal, as it closes the app's other dialogs.
  useEffect(() => {
    if (!open || !modal) return
    const onKey = (e) => {
      if (e.key === 'Escape') closeGuide()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, modal, closeGuide])

  // Shown as a modal, the guide holds keyboard focus. Docked beside the page
  // it does not, since the page stays usable.
  const drawerRef = useRef(null)
  useFocusTrap(drawerRef, open && modal)

  if (!open) return null

  const { done: doneCount, handled: handledCount } = countProgress(progress)
  const doctorLink = stepLink(DOCTOR.slug)

  const drawer = (
    <aside
      ref={drawerRef}
      className={`guide-drawer${modal ? ' guide-modal' : ''}${dragging ? ' resizing' : ''}`}
      style={modal ? undefined : { width: `${width}px` }}
      aria-label="Guide"
      {...(modal ? { role: 'dialog', 'aria-modal': true } : {})}
      onClick={modal ? (e) => e.stopPropagation() : undefined}
    >
      <div className="guide-drawer-head">
        <h2>Guide</h2>
        {modal && (
          <button className="btn btn-ghost btn-small" onClick={dockGuide}>
            Show beside the page
          </button>
        )}
        <button
          className="btn btn-ghost btn-small"
          onClick={reset}
          disabled={handledCount === 0}
        >
          Start over
        </button>
        <button className="guide-drawer-close" onClick={closeGuide} aria-label="Close guide">
          ✕
        </button>
      </div>

      <div className="guide-drawer-body">
        <PageIntro page="/wizard" />

        <div className="guide-progress">
          <div className="guide-bar">
            <div
              className="guide-bar-fill"
              style={{ width: `${(handledCount / STEPS.length) * 100}%` }}
            />
          </div>
          <span className="hint guide-progress-text">
            {doneCount} of {STEPS.length} done
            {handledCount > doneCount ? `, ${handledCount - doneCount} skipped` : ''}
          </span>
        </div>

        <p className="hint guide-site-link">
          The{' '}
          <a href={QUICK_GUIDE_URL} target="_blank" rel="noopener noreferrer">
            quick guide on zedstead.com
          </a>{' '}
          shows the whole path in six short steps, from the computer to a running server.
        </p>

        <ol className="guide-steps">
          {STEPS.map((step, i) => {
            const state = progress[step.id] || ''
            const stepOpen = openId === step.id
            // What the server says about this step, when it can say anything
            // (GET /api/setup-state via the provider). `detail` is shown even
            // when the answer is "not yet" — "Server is stopped." is the most
            // useful line on the Start step.
            const server = step.detect ? detected?.[step.detect] : null
            const serverOwns = !!server?.done
            // A link entry is either a slug (resolved through the shared
            // tables) or a URL already built by a crossLinks helper.
            const links = (step.links || [])
              .map((l) =>
                l.to
                  ? { to: l.to, label: l.label, tip: l.tip || '', traderplus: !!l.traderplus }
                  : stepLink(l.slug, l.label)
              )
              .filter((l) => l && (traderplus || !l.traderplus))

            return (
              <li
                key={step.id}
                className={`card guide-step${stepOpen ? ' guide-step-open' : ''}${
                  state ? ` guide-step-${state}` : ''
                }`}
              >
                <button
                  type="button"
                  className="guide-step-header"
                  aria-expanded={stepOpen}
                  onClick={() => setOpenId(stepOpen ? '' : step.id)}
                >
                  <span className="guide-step-num">{state === 'done' ? '✓' : i + 1}</span>
                  <span className="guide-step-title">{step.title}</span>
                  {state && <span className={`guide-tag guide-tag-${state}`}>{state}</span>}
                  <span className="guide-step-caret">
                    <FoldCaret open={stepOpen} />
                  </span>
                </button>

                {stepOpen && (
                  <div className="guide-step-body">
                    {/* A detected answer with a `level` waits on the admin
                        (the stock Chernarus on the map step), so it is a
                        callout at the top of the step, not the muted
                        line further down. */}
                    {server?.level && server.detail && (
                      <p className={`guide-note guide-note-${server.level} guide-callout`}>
                        {server.detail}
                      </p>
                    )}

                    {step.body.filter(Boolean).map((para, pi) => (
                      <p key={pi}>{para}</p>
                    ))}

                    {step.defs && (
                      <dl className="guide-defs">
                        {step.defs.map((d) => {
                          const link = d.slug ? stepLink(d.slug, d.term) : null
                          return (
                            <div className="guide-def" key={d.term}>
                              <dt>
                                {link ? (
                                  <GuideLink {...link} className="guide-inline-link" />
                                ) : (
                                  d.term
                                )}
                              </dt>
                              <dd>{d.text}</dd>
                            </div>
                          )
                        })}
                      </dl>
                    )}

                    {step.modTabs && (
                      <dl className="guide-defs">
                        {MOD_TABS.filter((t) => modTabHelp[t]).map((t) => {
                          const link = stepLink(`mods?tab=${encodeURIComponent(t)}`, modTabLabel(t))
                          return (
                            <div className="guide-def" key={t}>
                              <dt>
                                {link ? (
                                  <GuideLink {...link} className="guide-inline-link" />
                                ) : (
                                  modTabLabel(t)
                                )}
                              </dt>
                              <dd>{modTabHelp[t]}</dd>
                            </div>
                          )
                        })}
                      </dl>
                    )}

                    {step.note && (
                      <p className={`guide-note guide-note-${step.note.level}`}>{step.note.text}</p>
                    )}

                    {links.length > 0 && (
                      <div className="guide-links">
                        {links.map((l) => (
                          <GuideLink key={l.to} {...l} />
                        ))}
                      </div>
                    )}

                    {server?.detail && !server.level && (
                      <p className="hint guide-detected">
                        {serverOwns ? '✓ ' : ''}
                        {server.detail}
                      </p>
                    )}

                    {/* Both are hidden once the server says the step is done:
                        a step reading "Logged in to Steam." must not also
                        offer a login form, and one reading "Server installed."
                        must not offer to install it again.

                        The login card needs a POSITIVE "not logged in" — its
                        banner states that as a fact, and an unanswered fetch
                        is not evidence for it. A button is only an offer, so
                        those degrade the other way and stay visible. */}
                    {step.steamLogin && canAdmin && server && !server.done && (
                      <SteamLoginCard pushToast={pushToast} onLoggedIn={refreshDetected} />
                    )}

                    {step.action &&
                      !serverOwns &&
                      roleAtLeast(role, step.action.minRole || 'admin') && (
                      <div className="guide-actions guide-step-action">
                        <NeedsSteamIf when={step.action.needsSteam}>
                          <button
                            className="btn btn-primary btn-small"
                            disabled={acting === step.id}
                            onClick={() => runAction(step)}
                          >
                            {acting === step.id
                              ? step.action.busyLabel || 'Working…'
                              : step.action.label}
                          </button>
                        </NeedsSteamIf>
                      </div>
                    )}

                    {/* A step the server can see is done is not the user's to
                        un-tick — the tick would come straight back on the next
                        fetch. So the two toggles are replaced by the one thing
                        the click still does: move on. */}
                    <div className="guide-actions">
                      {serverOwns ? (
                        <button
                          className="btn btn-primary btn-small"
                          onClick={() => mark(step.id, 'done')}
                        >
                          Next step
                        </button>
                      ) : (
                        <>
                          <button
                            className="btn btn-primary btn-small"
                            onClick={() => mark(step.id, 'done')}
                          >
                            {state === 'done' ? 'Mark as not done' : 'Mark as done'}
                          </button>
                          <button
                            className="btn btn-ghost btn-small"
                            onClick={() => mark(step.id, 'skipped')}
                          >
                            {state === 'skipped' ? 'Unskip' : 'Skip'}
                          </button>
                        </>
                      )}
                    </div>
                  </div>
                )}
              </li>
            )
          })}
        </ol>

        {handledCount === STEPS.length && (
          <p className="guide-complete">
            Every step handled.{' '}
            {doctorLink && <GuideLink {...doctorLink} className="guide-inline-link" />} is the next
            thing to run if anything misbehaves.
          </p>
        )}

        {doctorLink && (
          <div className="card guide-doctor">
            <p>{DOCTOR.text}</p>
            <GuideLink {...doctorLink} />
          </div>
        )}
      </div>

      {!modal && (
        <div
          className="guide-resize-handle"
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize guide"
          {...tooltipAttrs('Drag to resize — double-click to reset')}
          onPointerDown={onHandleDown}
          onPointerMove={onHandleMove}
          onPointerUp={endDrag}
          onPointerCancel={endDrag}
          onDoubleClick={resetWidth}
        />
      )}
    </aside>
  )

  // A click on the backdrop docks the guide rather than closing it: the
  // reader asked for the page, not for the guide to go away.
  return modal ? (
    <div className="modal-backdrop guide-modal-backdrop" onClick={dockGuide}>
      {drawer}
    </div>
  ) : (
    drawer
  )
}
