// Colorized unified diff: + green, - red, @@ accent.
//
// `+++`/`---` file headers must be classified BEFORE the plain `+`/`-` cases,
// otherwise the header lines get painted as added/removed content.
function lineClass(line) {
  if (line.startsWith('+++') || line.startsWith('---') || line.startsWith('\\')) return 'diff-meta'
  if (line.startsWith('@@')) return 'diff-hunk'
  if (line.startsWith('+')) return 'diff-add'
  if (line.startsWith('-')) return 'diff-del'
  return 'diff-ctx'
}

export default function DiffView({ text }) {
  const lines = String(text ?? '').split('\n')

  return (
    <pre className="diff-view">
      {lines.map((line, i) => (
        <span className={`diff-line ${lineClass(line)}`} key={i}>
          {line || ' '}
        </span>
      ))}
    </pre>
  )
}
