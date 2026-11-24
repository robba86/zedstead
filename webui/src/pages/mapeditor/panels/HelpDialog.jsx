// The editor's quick guide, opened from the sidebar's ? button or the ? key.
//
// Content comes from GET /help — app/data/map_editor_help.json, loaded by
// app/spawn_editor/help_content.py. This renders its three block types
// (paragraph / bullets / definitions) and its inline markup subset.

import { Fragment, useEffect, useRef } from 'react'
import { useFocusTrap } from '../../../components/useFocusTrap.js'
import { createPortal } from 'react-dom'
import { useResource } from '../state/useResource'
import { DocsLinks } from '../../../components/PageIntro.jsx'

// **bold** / *italic* / `code` / [[Key]]. Matched longest-first so ** is never
// consumed as two italics and [[ ]] never as bold. Returns React nodes, so
// unlike the vanilla editor's string builder there is no escaping to get wrong.
const INLINE_RE = /\[\[([^\]]+)\]\]|\*\*([^*]+)\*\*|\*([^*]+)\*|`([^`]+)`/g

function inline(text) {
  const out = []
  let last = 0
  for (const m of String(text).matchAll(INLINE_RE)) {
    if (m.index > last) out.push(text.slice(last, m.index))
    const key = `${m.index}`
    if (m[1] !== undefined) out.push(<kbd key={key}>{m[1]}</kbd>)
    else if (m[2] !== undefined) out.push(<b key={key}>{m[2]}</b>)
    else if (m[3] !== undefined) out.push(<i key={key}>{m[3]}</i>)
    else out.push(<code key={key}>{m[4]}</code>)
    last = m.index + m[0].length
  }
  if (last < text.length) out.push(text.slice(last))
  return out
}

function Block({ block }) {
  if (block.type === 'paragraph') return <p>{inline(block.text)}</p>
  if (block.type === 'bullets') {
    return (
      <ul className="help-bullets">
        {block.items.map((item, i) => (
          <li key={i}>{inline(item)}</li>
        ))}
      </ul>
    )
  }
  if (block.type === 'definitions') {
    return (
      <dl className="help-kbd-list">
        {block.items.map(([term, def], i) => (
          <Fragment key={i}>
            <dt>{inline(term)}</dt>
            <dd>{inline(def)}</dd>
          </Fragment>
        ))}
      </dl>
    )
  }
  return null
}

export default function HelpDialog({ traderplus, onClose }) {
  const help = useResource('/help')
  const boxRef = useRef(null)
  useFocusTrap(boxRef)

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  const data = help.data
  const sections = (data?.sections || []).filter((s) => !s.traderplus_only || traderplus)

  return createPortal(
    <div
      className="dialog-overlay help-dialog-overlay"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div ref={boxRef} className="dialog-box help-dialog-box">
        <div className="help-dialog-header">
          <span className="help-dialog-title">
            {data?.title || 'Map Editor — Quick Guide'}
          </span>
          <button type="button" className="help-close-btn" aria-label="Close" onClick={onClose}>
            ×
          </button>
        </div>

        <div className="help-content">
          {help.error && <p className="help-lede">Failed to load guide: {help.error.message}</p>}
          {!data && !help.error && <p className="help-lede">Loading…</p>}
          {data?.lede && (
            <section className="help-section">
              <p className="help-lede">{inline(data.lede)}</p>
            </section>
          )}
          {sections.map((section) => (
            <section className="help-section" key={section.title}>
              <h3>{section.title}</h3>
              {(section.blocks || []).map((block, i) => (
                <Block key={i} block={block} />
              ))}
            </section>
          ))}
        </div>

        <div className="help-dialog-footer">
          <span className="help-footer-hint">
            Press <kbd>?</kbd> any time to reopen this guide · <kbd>Esc</kbd> to close
          </span>
          <DocsLinks page="/map-editor" className="page-docs help-footer-docs" />
          <button
            type="button"
            className="dialog-btn dialog-button-submit help-got-it-btn"
            onClick={onClose}
          >
            Got it
          </button>
        </div>
      </div>
    </div>,
    document.body
  )
}
