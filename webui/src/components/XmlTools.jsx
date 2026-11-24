import { useCallback, useRef, useState } from 'react'
import { setSelection } from 'prism-react-editor/utils'
import { api } from '../api'
import { CodeAreaRegistry } from './CodeArea.jsx'
import { tooltipAttrs } from './Tooltip.jsx'

// Format / Check for an XML editor, plus the persistent result strip.
//
// Wraps the editor rather than sitting beside it, so the three call sites (the
// manual XML file content box, the /configs/:id Raw tab, the generated custom/ file
// Raw tab) get bar -> box -> strip in that order from one line of JSX.
//
// The server does the parsing (POST /api/xml/check|format). Nothing here parses
// XML: the frontend has no XML parser, and the editor's Prism grammar is a
// tokenizer that cannot tell valid from broken.
//
// Two deliberate behaviours:
//   * This component NEVER raises a toast. A parse error has to stay on screen
//     while the user fixes it, which is the whole point of the strip.
//   * The strip is not cleared on typing — it is dimmed (`data-stale`) once the
//     text no longer matches what was checked, so it can inform without ever
//     claiming to describe the current content.
export default function XmlTools({
  content,
  onChange,
  language = 'xml',
  fragment = false,
  error = null,
  children,
}) {
  const [result, setResult] = useState(null) // { tone, summary, hint?, message?, at }
  const [busy, setBusy] = useState(false)
  const inputRef = useRef(null)
  const editorRef = useRef(null)
  // Handed to the CodeArea below through CodeAreaRegistry. useRef so the
  // provider value is stable — a new function each render would make the
  // editor detach and re-register its instance on every keystroke.
  const registerEditor = useRef((editor) => {
    editorRef.current = editor
  }).current

  const run = useCallback(
    async (what) => {
      setBusy(true)
      try {
        const body = await api.post(`/api/xml/${what}`, { content, fragment })
        if (!body.ok) {
          setResult({ tone: 'bad', at: content, ...body.error })
          return
        }
        if (what === 'format') {
          if (body.changed) onChange(body.content)
          setResult({
            tone: 'good',
            at: body.content,
            summary: body.changed ? 'Formatted.' : 'Already formatted.',
          })
          return
        }
        const where = body.root_tag ? ` in <${body.root_tag}>` : ''
        setResult({
          tone: 'good',
          at: content,
          // "Well-formed", not "valid": in fragment mode this accepts exactly
          // what a manual XML file accepts, which is looser than a document parse
          // and says nothing about whether DayZ will like the contents.
          summary: `Well-formed — ${body.elements} element${body.elements === 1 ? '' : 's'}${where}`,
        })
      } catch (err) {
        setResult({ tone: 'bad', at: content, summary: err.message || 'Check failed' })
      } finally {
        setBusy(false)
      }
    },
    [content, fragment, onChange],
  )

  // Put the caret on the reported line so "line 412" is a destination, not a
  // scroll hunt. Two paths because CodeArea has two: the prism editor (whose
  // own scroll container has to be told to follow the caret), and the plain
  // textarea it falls back to on very large files.
  function jumpTo(line) {
    const offset = content.split('\n').slice(0, line - 1).join('\n').length + (line > 1 ? 1 : 0)
    const editor = editorRef.current
    if (editor?.textarea) {
      editor.textarea.focus()
      setSelection(editor, offset)
      editor.extensions.cursor?.scrollIntoView()
      return
    }
    const el = inputRef.current?.querySelector('textarea')
    if (!el) return
    el.focus()
    el.setSelectionRange(offset, offset)
  }

  if (language !== 'xml') return children

  const shown = result || (error ? { tone: 'bad', summary: error, at: null } : null)
  const stale = shown && shown.at !== null && shown.at !== content

  return (
    <div className="xml-tools">
      <div className="xml-tools-bar">
        <button
          className="btn btn-ghost btn-small"
          type="button"
          disabled={busy || !content.trim()}
          onClick={() => run('format')}
          {...tooltipAttrs('Re-indent with tabs. Comments and your XML declaration are kept.')}
        >
          Format XML
        </button>
        <button
          className="btn btn-ghost btn-small"
          type="button"
          disabled={busy || !content.trim()}
          onClick={() => run('check')}
          {...tooltipAttrs('Check that this parses, without saving.')}
        >
          Check XML
        </button>
      </div>
      <div ref={inputRef}>
        <CodeAreaRegistry.Provider value={registerEditor}>{children}</CodeAreaRegistry.Provider>
      </div>
      {shown && (
        <div
          className={shown.tone === 'good' ? 'xml-tools-ok' : 'xml-tools-problem'}
          data-stale={stale ? 'true' : undefined}
        >
          {shown.line ? (
            <button className="xml-tools-where" type="button" onClick={() => jumpTo(shown.line)}>
              Line {shown.line}, column {shown.column}
            </button>
          ) : null}
          <span className="xml-tools-said">{shown.hint || shown.summary}</span>
          {shown.message && shown.hint && (
            <span className="xml-tools-raw">{shown.message}</span>
          )}
        </div>
      )}
    </div>
  )
}
