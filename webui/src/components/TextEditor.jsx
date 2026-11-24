import { useEffect, useState } from 'react'
import StickyActionBar from './StickyActionBar.jsx'
import CodeArea from './CodeArea.jsx'
import { useEditorSync } from '../configSync.js'

// Renders {kind:"text", content, language}. Raw editing with server-side
// validation on save (defusedxml/json) — errors surface via the caller's
// error handling (e.g. a toast).
export default function TextEditor({ data, onSave, saving }) {
  const [content, setContent] = useState('')

  useEffect(() => {
    setContent(data.content)
  }, [data])

  const dirty = content !== data.content
  // Reports up to ConfigEditorDrawer, which owns `data` and so owns the refresh.
  const sync = useEditorSync(dirty)

  return (
    <div className="text-editor">
      <CodeArea language={data.language} value={content} onChange={setContent} />
      <StickyActionBar
        dirty={dirty}
        saving={saving}
        onSave={() => onSave(content)}
        onReset={() => setContent(data.content)}
        {...sync}
      />
    </div>
  )
}
