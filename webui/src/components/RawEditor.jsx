import Select from './Select.jsx'
import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { withBase } from '../instance'
import { useToast } from './Toast.jsx'
import StickyActionBar from './StickyActionBar.jsx'
import CodeArea from './CodeArea.jsx'
import XmlTools from './XmlTools.jsx'
import useRawTarget from '../useRawTarget.js'
import { useConfigSync } from '../configSync.js'
import { useConfirm } from './useConfirm.jsx'
import { LEAVE_DIALOG } from '../useUnsavedChanges.js'

// Raw file editor (the "Raw" tab). Self-contained: loads/saves via the
// /api/configs/{id}/raw endpoints, which validate XML/JSON on save. A rejected
// save's message is handed to XmlTools as well as toasted, so the line number
// stays on screen while it is being fixed. When an editor exposes more than one
// file (e.g. BattlEye → beserver + serverDZ.cfg), a target picker switches
// between them. Highlighting keys off the `language` the endpoint reports.
//
// Download / Replace (TODO G1c) are for the admin whose server lives on a
// host panel with nothing but FTP: fetch the file there, drag it in here, use
// the real editor and the economy analysis, take it back. It is deliberately
// NOT seamless — the FTP client does not go away — and the hint under the
// buttons says the one thing that bites: the server may have rewritten the
// file while it was out.
export default function RawEditor({ id, targets }) {
  const [target, setTarget] = useRawTarget(targets)
  const [data, setData] = useState(null) // { name, language, content, targets }
  const [content, setContent] = useState('')
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState(null)
  const pushToast = useToast()
  const confirm = useConfirm()

  const query = target ? `?target=${encodeURIComponent(target)}` : ''
  const path = `/api/configs/${id}/raw${query}`
  const downloadPath = `/api/configs/${id}/download${query}`
  const uploadPath = `/api/configs/${id}/upload${query}`

  // Takes a payload without blanking the editor first, so a background refresh
  // (configSync) is a content swap rather than a flash. `load` adds the blank,
  // which is right for a switch to another file.
  const apply = useCallback((d) => {
    setData(d)
    setContent(d.content)
    setSaveError(null)
  }, [])

  // The file on screen now. An answer for the file just left (a cross-link
  // while this tab is up keeps it mounted) is dropped, or Save wrote that
  // file's text into this one.
  const pathRef = useRef(path)
  pathRef.current = path

  const load = useCallback(async () => {
    setData(null)
    try {
      const d = await api.get(path)
      if (pathRef.current === path) apply(d)
    } catch (err) {
      if (pathRef.current === path) pushToast(err.message || 'Failed to load file', 'error')
    }
  }, [apply, path, pushToast])

  useEffect(() => {
    load()
  }, [load])

  const dirty = !!data && content !== data.content
  const sync = useConfigSync({
    path,
    showing: data,
    dirty,
    busy: saving || data === null,
    apply,
  })

  // The picker reloads the editor with the other file, so unsaved text asks
  // first; it was dropped without a word.
  async function pickTarget(next) {
    if (next === target) return
    if (dirty) {
      const discard = await confirm({
        ...LEAVE_DIALOG,
        title: 'Switch file without saving?',
        message: `${data.name} has unsaved changes. Switching to another file discards them.`,
        confirmLabel: 'Switch',
      })
      if (!discard) return
    }
    setTarget(next)
  }

  const fileInput = useRef(null)
  const [uploading, setUploading] = useState(false)

  async function handleUpload(e) {
    const file = e.target.files?.[0]
    // Cleared straight away, so picking the same file twice in a row still
    // fires a change event.
    e.target.value = ''
    if (!file) return
    setUploading(true)
    try {
      const res = await api.postFile(uploadPath, file)
      pushToast(`Replaced ${res.name}.`, 'info')
      load()
    } catch (err) {
      // A rejected upload wrote nothing — say so, or it reads as a half-done
      // replace.
      setSaveError(err.message || 'Upload failed')
      pushToast(`${err.message || 'Upload failed'} — the file on disk is unchanged.`, 'error')
    } finally {
      setUploading(false)
    }
  }

  async function handleSave() {
    setSaving(true)
    try {
      await api.put(`/api/configs/${id}/raw`, { target, content })
      setSaveError(null)
      pushToast('Saved.', 'info')
      load()
    } catch (err) {
      setSaveError(err.message || 'Save failed')
      pushToast(err.message || 'Save failed', 'error')
    } finally {
      setSaving(false)
    }
  }

  if (!data) return <div className="raw-editor" />

  return (
    <div className="raw-editor">
      {targets.length > 1 && (
        <Select aria-label="File" value={target} onChange={pickTarget} options={targets} />
      )}
      <div className="raw-file-actions">
        {/* A plain anchor, not fetch+blob: the browser streams it to disk and
            names it from the Content-Disposition the backend already sets. */}
        <a className="btn btn-ghost btn-small" href={withBase(downloadPath)} download={data.name}>
          Download {data.name}
        </a>
        <button
          className="btn btn-ghost btn-small"
          disabled={uploading}
          onClick={() => fileInput.current?.click()}
        >
          {uploading ? 'Replacing…' : 'Replace with a file…'}
        </button>
        <input
          ref={fileInput}
          type="file"
          hidden
          onChange={handleUpload}
        />
        <span className="hint">
          An upload is validated and the current version snapshotted before anything is
          written. If the server has been running, it may have rewritten this file since
          you downloaded it.
        </span>
      </div>
      <XmlTools content={content} onChange={setContent} language={data.language} error={saveError}>
        <CodeArea language={data.language} value={content} onChange={setContent} />
      </XmlTools>
      <StickyActionBar
        dirty={dirty}
        saving={saving}
        onSave={handleSave}
        onReset={() => setContent(data.content)}
        {...sync}
      />
    </div>
  )
}
