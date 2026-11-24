import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import { useWs } from '../ws'
import Select from './Select.jsx'
import FileVersions from './FileVersions.jsx'
import useRawTarget from '../useRawTarget.js'
import PanelNote from './PanelNote.jsx'

// A config editor's Versions tab (ConfigEditorDrawer): the saved versions of
// this editor's own file, so what a save removed can be diffed and put back
// without leaving the editor. Every editor save keeps the file it replaced
// (PUT /api/configs/{id}, config_versions.keep_replaced). Mounted only while
// its tab is up, so it loads when the tab becomes active, and re-reads on
// `configs_changed`, since every save adds a version.
export default function EditorVersions({ id, targets = [] }) {
  const [target, setTarget] = useRawTarget(targets)
  const [data, setData] = useState(null) // {file, entries}
  const [error, setError] = useState(null)

  const load = useCallback(async () => {
    try {
      const q = target ? `?target=${encodeURIComponent(target)}` : ''
      setData(await api.get(`/api/configs/${id}/versions${q}`))
      setError(null)
    } catch (err) {
      setError(err.message || 'Failed to load versions')
    }
  }, [id, target])

  useEffect(() => {
    load()
  }, [load])

  useWs(
    useCallback(
      (msg) => {
        if (msg.type === 'configs_changed') load()
      },
      [load]
    )
  )

  return (
    <div className="card">
      <h2>Versions</h2>
      <PanelNote>
        Every save keeps the file as it was before, so a change or a removed entry can be put back
        here. Restore replaces the whole file with that version; the current file is kept as a
        version first.
      </PanelNote>
      {targets.length > 1 && (
        <div className="inline-form">
          <Select aria-label="File" value={target} onChange={setTarget} options={targets} />
        </div>
      )}
      {error ? (
        <p className="banner banner-warning">{error}</p>
      ) : !data ? (
        <PanelNote>Loading…</PanelNote>
      ) : data.entries.length === 0 ? (
        <PanelNote>No saved versions of {data.file} yet. The first save keeps one.</PanelNote>
      ) : (
        <FileVersions key={`${id}:${data.file}`} entries={data.entries} diffIn="inline" onRestored={load} />
      )}
    </div>
  )
}
