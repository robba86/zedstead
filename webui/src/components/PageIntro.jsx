// Page help: the intro under a page title, its ? tip, and the page's docs line
// (the zedstead.com sections docLinks.json names for it).
import { useEffect, useRef, useState } from 'react'
import { HiOutlineQuestionMarkCircle, HiOutlineBookOpen } from 'react-icons/hi2'
import { tooltipAttrs } from './Tooltip.jsx'
import { PAGE_HELP } from '../pageHelp.js'
import { DOCS_URL } from '../routes.js'
import DOC_LINKS from '../docLinks.json'

// The zedstead.com doc sections for a PAGE_HELP key, as [{ href, label }].
// An unknown key gives [], so a page with no entry renders no docs line.
export function pageDocs(page) {
  return (DOC_LINKS.pages[page] || []).map(([doc, anchor, label]) => ({
    href: `${DOCS_URL}${doc}/${anchor ? `#${anchor}` : ''}`,
    label,
  }))
}

// "Docs: Maps · …" — the page's sections on zedstead.com, each in a new tab so
// the panel stays where it was. Renders nothing for a page with no entry.
export function DocsLinks({ page, className = 'page-docs' }) {
  const links = pageDocs(page)
  if (!links.length) return null
  return (
    <p className={className}>
      <HiOutlineBookOpen aria-hidden="true" />
      <span>Docs:</span>
      {links.map((link, i) => (
        <span key={link.href}>
          {i > 0 && ' · '}
          <a href={link.href} target="_blank" rel="noopener noreferrer">{link.label}</a>
        </span>
      ))}
    </p>
  )
}

// A text cut to three lines, with "Read more" under it while it is cut and
// "Read less" once opened. The button shows only when the text is longer
// than three lines; a resize measures again.
export function ClampedText({ as: Tag = 'p', className = '', children }) {
  const ref = useRef(null)
  const [open, setOpen] = useState(false)
  const [cut, setCut] = useState(false)
  useEffect(() => {
    const el = ref.current
    if (!el || open) return undefined
    const measure = () => setCut(el.scrollHeight > el.clientHeight + 1)
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [open, children])
  return (
    <div className="clamped">
      <Tag ref={ref} className={`${className}${open ? '' : ' clamped-text'}`}>
        {children}
      </Tag>
      {(cut || open) && (
        <button type="button" className="btn-link clamped-toggle" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
          {open ? 'Read less' : 'Read more'}
        </button>
      )}
    </div>
  )
}

// Muted description block shown under a page header or config-section heading.
// `page` (a PAGE_HELP key) supplies the text when `text` is not given, and
// adds the page's docs line below it. Renders nothing when there is neither,
// so callers can pass a possibly-missing help lookup unconditionally. A long
// text is cut to three lines with a Read more (ClampedText).
// `sub` is the smaller intro under a tab bar or at the top of a form section.
export default function PageIntro({ text, page, sub = false }) {
  const className = sub ? 'page-intro page-intro-sub' : 'page-intro'
  const body = text ?? (page ? PAGE_HELP[page] : '')
  if (!body && !page) return null
  return (
    <>
      {/* Keyed by the text: a tab switch starts the new text cut, with no "Read less" left open. */}
      {body && <ClampedText key={body} className={className}>{body}</ClampedText>}
      {page && <DocsLinks page={page} />}
    </>
  )
}

// The same help text as a "?" beside the page title, for a page whose intro
// would otherwise push the content it describes below the fold. Same affordance
// as a /configs card's help button: it opens on focus as well as hover (the
// shared <ReactTooltip> in Layout.jsx wires both), which a paragraph never did.
// `label` names the page because the button has no visible text. `page` adds
// a book icon beside it that opens the page's first doc section.
export function PageIntroTip({ text, page, label = 'About this page' }) {
  const doc = page ? pageDocs(page)[0] : null
  if (!text && !doc) return null
  return (
    <>
      {text && (
        <button type="button" className="btn btn-icon page-intro-tip" aria-label={label} {...tooltipAttrs(text, { keep: true })}>
          <HiOutlineQuestionMarkCircle aria-hidden="true" />
        </button>
      )}
      {doc && (
        <a
          href={doc.href}
          target="_blank"
          rel="noopener noreferrer"
          className="btn btn-icon page-intro-tip"
          aria-label={`Docs: ${doc.label}`}
          {...tooltipAttrs(`Docs on zedstead.com: ${doc.label}`)}
        >
          <HiOutlineBookOpen aria-hidden="true" />
        </a>
      )}
    </>
  )
}
