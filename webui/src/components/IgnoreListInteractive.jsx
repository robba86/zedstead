import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import { useToast } from './Toast.jsx'
import StickyActionBar from './StickyActionBar.jsx'
import MultiSelectList from './MultiSelectList.jsx'
import { useConfigSync } from '../configSync.js'
import { TrashContext, TrashTray, insertAt, useSavedRemovals, useTrashState } from '../trash.jsx'
import PanelNote from './PanelNote.jsx'

// Ignore List's Edit view: the current entries as one editable list plus a
// quick-add pool of common base-building items.
// Reuses the table GET/PUT /api/configs/ignorelist — rows are single-column
// {name}, so the whole edit is one string list.
// Removed names go to the trash (trash.jsx): put back until Save, and after a
// save from the server's removed list, which survives a reload and another
// computer (user test, 2026 Q4: removed entries were gone for good).
// The whole edit is this list, so it is also what a disk change is compared
// against — the pool alongside it is a static catalogue, not part of the file.
const namesOf = (d) => (d.rows || []).map((r) => r.values.name)

// `initialSearch` (a deep link's ?q=, as the table it replaced took) seeds the
// list's filter.
export default function IgnoreListInteractive({ id, initialSearch = '' }) {
  const [names, setNames] = useState([])
  const [pool, setPool] = useState([])
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const pushToast = useToast()
  const saved = useSavedRemovals(id)
  // Puts a name removed since the last save back where it was.
  const trash = useTrashState(({ index, item }) => {
    setNames((prev) => (prev.includes(item) ? prev : insertAt(prev, [], index, item)))
    setDirty(true)
  })
  // A ref: `trash.clear` is a new function every render, and `apply` must
  // stay stable or the load effect below runs again on every render.
  const clearTrash = useRef(trash.clear)
  clearTrash.current = trash.clear

  const path = `/api/configs/${id}`

  const apply = useCallback((d) => {
    setNames(namesOf(d))
    setDirty(false)
    clearTrash.current()
  }, [])

  const load = useCallback(async () => {
    setLoaded(false)
    try {
      const [data, common] = await Promise.all([
        api.get(path),
        api.get('/api/configs/ignorelist/common-items'),
      ])
      apply(data)
      setPool(common.items || [])
      setLoaded(true)
    } catch (err) {
      pushToast(err.message || 'Failed to load', 'error')
    }
  }, [apply, path, pushToast])

  useEffect(() => {
    load()
  }, [load])

  const sync = useConfigSync({
    path,
    showing: names,
    select: namesOf,
    dirty,
    busy: saving || !loaded,
    apply,
  })

  function change(next) {
    setNames(next)
    setDirty(true)
  }

  async function handleSave() {
    setSaving(true)
    try {
      const rows = names.map((n) => ({ id: n, values: { name: n } }))
      await api.put(`/api/configs/${id}`, { rows })
      pushToast('Saved.', 'info')
      load()
      saved.reload()
    } catch (err) {
      pushToast(err.message || 'Save failed', 'error')
    } finally {
      setSaving(false)
    }
  }

  // What earlier saves removed, less what is back in the list or in the
  // unsaved half of the tray already.
  const savedRemoved = useMemo(() => {
    const here = new Set([...names, ...trash.items.map((e) => e.item)])
    return saved.items.filter((e) => !here.has(e.name))
  }, [saved.items, names, trash.items])

  if (!loaded) return <div className="form-editor" />

  return (
    <TrashContext.Provider value={trash.ctx}>
    <div className="form-editor">
      <div className="card">
        <h2>Ignore List</h2>
        <PanelNote>
          Classnames listed here do not spawn, and items of them already on the map are deleted at
          the next restart. Add your own, or quick-add common containers and base-building pieces to
          wipe them from the map.
        </PanelNote>
        <MultiSelectList
          values={names}
          onChange={change}
          addPlaceholder="Item classname…"
          initialFilter={initialSearch}
          pool={pool}
          poolLabel="Add base-building items (deleted from the map on restart)"
          trashPath={['names']}
          name="Ignore list"
        />
      </div>
      <StickyActionBar dirty={dirty} saving={saving} onSave={handleSave} onReset={load} {...sync}>
        <TrashTray trash={trash} saved={savedRemoved} onRestoreSaved={(name) => change([...names, name])} />
      </StickyActionBar>
    </div>
    </TrashContext.Provider>
  )
}
