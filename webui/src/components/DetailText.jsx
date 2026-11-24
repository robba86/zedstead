import { Link } from 'react-router-dom'
import { InlineCommand } from './CommandBlock.jsx'

// A message from the backend, shown with its paths as code blocks and its
// URLs as links, and its `backtick-quoted` commands as code with a Copy
// button. The text stays one plain sentence for the CLI and the logs;
// this is the only place the panel reads structure out of it. `phrases`
// (`check.link_phrases` on a Doctor row) turn named phrases into router links.

// A `command`, a URL, a Windows path (C:\...) or an absolute path with at
// least two parts (/home/x/...). The command is first, so a path inside one
// stays part of it; the URL is next, so the slashes inside one are not a path.
const TOKEN = /(`[^`\n]+`|https?:\/\/[^\s<>"]+|[A-Za-z]:\\[^\s;,"']+|(?<![\w.:/])\/(?:[\w.@+-]+\/)+[\w.@+-]*)/g
const TRAILING = /[.,;:)]+$/

const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

function linkPhrases(text, phrases, keyBase) {
  const sorted = (phrases || [])
    .filter((p) => p.phrase)
    .sort((a, b) => b.phrase.length - a.phrase.length)
  if (!text || sorted.length === 0) return text ? [text] : []
  const routes = new Map(sorted.map((p) => [p.phrase, p.to]))
  const re = new RegExp(`(${sorted.map((p) => escapeRe(p.phrase)).join('|')})`, 'g')
  return text.split(re).map((part, i) =>
    routes.has(part) ? (
      <Link key={`${keyBase}-${i}`} className="doctor-link" to={routes.get(part)}>
        {part}
      </Link>
    ) : (
      part
    )
  )
}

export default function DetailText({ text, phrases }) {
  if (!text) return null
  const out = []
  let last = 0
  let pending = ''
  for (const match of text.matchAll(TOKEN)) {
    let token = match[0]
    const tail = token.match(TRAILING)?.[0] || ''
    token = token.slice(0, token.length - tail.length)
    out.push(...linkPhrases(pending + text.slice(last, match.index), phrases, out.length))
    pending = ''
    if (token.startsWith('`')) {
      out.push(<InlineCommand key={out.length} text={token.slice(1, -1)} />)
      pending = tail
    } else if (token.startsWith('http')) {
      out.push(
        <a key={out.length} href={token} target="_blank" rel="noopener noreferrer">
          {token}
        </a>
      )
      pending = tail
    } else {
      // A path is a block of its own, so the full stop that ended its
      // sentence would start the next line; it is left out.
      out.push(
        <pre key={out.length} className="detail-path">
          <code>{token}</code>
        </pre>
      )
      pending = tail.replace(/^\./, '')
    }
    last = match.index + match[0].length
  }
  out.push(...linkPhrases(pending + text.slice(last), phrases, out.length))
  return out
}
