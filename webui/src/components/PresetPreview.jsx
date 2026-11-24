// What a preset would change, grouped by file: each row names the setting
// (its last path part stands out, the parents are muted) and shows the
// current value struck through beside the new one. On a phone the values
// drop under the name.

import PanelNote from './PanelNote.jsx'

export default function PresetPreview({ preview }) {
  const groups = []
  for (const c of preview.changes) {
    const last = groups[groups.length - 1]
    if (last?.file === c.file) last.changes.push(c)
    else groups.push({ file: c.file, changes: [c] })
  }
  return (
    <div className="preset-preview">
      {preview.warnings.map((w) => (
        <div key={w} className="banner banner-warning">
          {w}
        </div>
      ))}
      {preview.changes.length === 0 ? (
        <PanelNote>No changes: the server already matches this preset.</PanelNote>
      ) : (
        <p className="hint">
          {preview.changes.length} change{preview.changes.length === 1 ? '' : 's'} in {groups.length} file
          {groups.length === 1 ? '' : 's'}
        </p>
      )}
      {groups.map((g) => (
        <section key={g.file} className="preset-preview-file">
          <h3>
            <code>{g.file}</code>
            <span className="hint">{g.changes.length}</span>
          </h3>
          <ul>
            {g.changes.map((c, i) => {
              const dot = c.parameter.lastIndexOf('.')
              return (
                <li key={i}>
                  <span className="preset-preview-param">
                    {dot > 0 && <span className="preset-preview-path">{c.parameter.slice(0, dot + 1)}</span>}
                    {c.parameter.slice(dot + 1)}
                  </span>
                  <span className="preset-preview-values">
                    <span className="preset-preview-old">{c.old_value}</span>
                    <span aria-hidden="true">→</span>
                    <span className="preset-preview-new">{c.new_value}</span>
                  </span>
                </li>
              )
            })}
          </ul>
        </section>
      ))}
    </div>
  )
}
