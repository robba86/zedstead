import { useEffect, useState } from 'react'
import { api } from '../api'
import { Switch } from './FlagSwitch.jsx'
import DiffView from './DiffView.jsx'
import SideDrawer from './SideDrawer.jsx'
import { useConfirm } from './useConfirm.jsx'
import { useToast } from './Toast.jsx'
import { useCan } from '../role.jsx'
import { formatWhen } from '../format.js'
import PanelNote from './PanelNote.jsx'

const DIFF_DRAWER_WIDTH_KEY = 'zsm.versions.drawer.width'

// What started a version, in words (config_versions.VersionEntry.trigger).
export const TRIGGERS = {
  auto_save: 'Before a save',
  editor: 'Before an editor save',
  manual: 'Snapshot',
  pre_restore: 'Before a restore',
  restore_backup: 'Before restore from backup',
  pre_merge: 'Before a merge',
  pre_tweak: 'Before a tweak',
  preset_apply: 'Before a preset',
  tweak_apply: 'Before a tweak',
  tweak_reset: 'Before a tweak reset',
  bundle_import: 'Before a bundle import',
  merge_undo: 'Before a merge undo',
  server_update: 'DayZ update',
  update_conflict: 'Before an update choice',
  upstream_adopt: 'Before the mod author\'s file',
  map_editor: 'Before a Map Editor save',
  outside_edit: 'Changed outside Zedstead',
}

// A trigger in words. `preset_apply:<id>` and `tweak_apply:<id>` carry the
// preset's id after the colon, so the id is shown after the words.
export function triggerLabel(trigger) {
  if (!trigger) return ''
  const [kind, id] = trigger.split(/:(.*)/s)
  const words = TRIGGERS[kind]
  if (!words) return trigger
  return id ? `${words}: ${id}` : words
}

// One file's stored versions: the list, "Diff vs current" and Restore, on a
// config editor's own Versions tab (ConfigEditorDrawer, that file only), so a
// removed entry can be put back from the editor it was removed in. Every
// file's versions are on Analysis → Change history (ChangeTimeline.jsx).
// `entries` come from GET /api/versions?file= or /api/configs/{id}/versions;
// diff and restore go through /api/versions/* with each entry's file_name and
// source_path. `onRestored` reloads the caller's list (the live file is kept
// as a pre_restore version first, so the reload shows the way back).
// `diffIn`: 'drawer' opens the diff in a side drawer (and reports it through
// `onDiffOpen`, for a page that reserves a column for it); 'inline' shows it
// under the list, for a caller that is a drawer already.
// The caller keys this by file, so a new file starts with no diff open.
export default function FileVersions({ entries, onRestored, diffIn = 'drawer', onDiffOpen }) {
  const confirm = useConfirm()
  const pushToast = useToast()
  // Restore is an admin write; a diff is a read, and the backend hides
  // passwords in it below admin (app/web/redact.py).
  const isAdmin = useCan('admin')
  // {version, file, trigger, timestamp, text} — the entry whose diff is open.
  const [diff, setDiff] = useState(null)
  // Entries whose diff vs the current file is empty are noise (an XML snapshot
  // can differ in bytes but not in normalized content). Hidden by default; the
  // `identical` flag comes from the server. Nothing is deleted.
  const [showIdentical, setShowIdentical] = useState(false)

  // Unmounting (a tab switch) must not leave the page's column reserved.
  useEffect(() => {
    if (!onDiffOpen) return undefined
    onDiffOpen(diff !== null)
    return () => onDiffOpen(false)
  }, [diff, onDiffOpen])

  async function handleRestore(entry) {
    const ok = await confirm({
      title: `Restore ${entry.file_name} to version ${entry.version}?`,
      message:
        `This replaces the live file with version ${entry.version}. The current file is ` +
        'kept as a new version first, so you can restore it from this list.',
      confirmLabel: 'Restore',
      danger: true,
    })
    if (!ok) return
    try {
      const result = await api.post('/api/versions/restore', {
        file: entry.file_name,
        version: entry.version,
        source_path: entry.source_path,
      })
      if (result.restored) {
        pushToast(`Restored version ${entry.version}.`, 'info')
      } else {
        pushToast(`Version ${entry.version} is no longer stored. Nothing was restored.`, 'error')
      }
      setDiff(null)
      onRestored?.()
    } catch (err) {
      pushToast(err.message || 'Restore failed', 'error')
    }
  }

  async function handleDiff(entry) {
    // The open row's button closes the diff again.
    if (diff?.version === entry.version) {
      setDiff(null)
      return
    }
    try {
      const params = new URLSearchParams({
        file: entry.file_name,
        version: entry.version,
        source_path: entry.source_path || '',
      })
      const data = await api.get(`/api/versions/diff?${params}`)
      setDiff({
        version: entry.version,
        file: entry.file_name,
        trigger: entry.trigger,
        timestamp: entry.timestamp,
        text: data.diff || '(no differences)',
      })
    } catch (err) {
      pushToast(err.message || 'Diff failed', 'error')
    }
  }

  const identicalCount = entries.filter((e) => e.identical).length
  const visible = showIdentical ? entries : entries.filter((e) => !e.identical)
  const diffTitle = diff && `${diff.file} — v${diff.version}`
  const diffSubtitle = diff && `${triggerLabel(diff.trigger)} · ${formatWhen(diff.timestamp)}`

  return (
    <>
      {identicalCount > 0 && (
        <div className="inline-check">
          <Switch
            checked={showIdentical}
            onChange={setShowIdentical}
            help={
              'A version the file is back at: it held this content before a change that was later undone ' +
              '(a preset reset, a restore). Only the newest copy of each content is kept. A version that ' +
              'differs only in comments or indentation also counts as identical.'
            }
          >
            Show {identicalCount} identical version{identicalCount === 1 ? '' : 's'} (no changes vs
            the current file)
          </Switch>
        </div>
      )}
      {entries.length > 0 && visible.length === 0 && (
        <PanelNote>All {entries.length} versions match the current file.</PanelNote>
      )}
      {visible.length > 0 && (
        <div className="data-table-wrap file-versions-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Version</th>
                <th>Saved</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {visible.map((e) => (
                <tr key={`${e.source_path}:${e.version}`} className={diff?.version === e.version ? 'row-active' : undefined}>
                  <td>v{e.version}</td>
                  <td>
                    {formatWhen(e.timestamp)}
                    <span className="file-versions-trigger">{triggerLabel(e.trigger)}</span>
                  </td>
                  <td className="file-versions-actions">
                    <button className="btn btn-ghost btn-small" onClick={() => handleDiff(e)}>
                      {diff?.version === e.version && diffIn === 'inline' ? 'Hide diff' : 'Diff vs current'}
                    </button>
                    {isAdmin && (
                      <button className="btn btn-ghost btn-small" onClick={() => handleRestore(e)}>
                        Restore
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {diff && diffIn === 'inline' && (
        <div className="file-versions-diff">
          <PanelNote>
            <strong>{diffTitle}</strong> against the current file · {diffSubtitle}
          </PanelNote>
          <DiffView text={diff.text} />
        </div>
      )}
      {diff && diffIn === 'drawer' && (
        <SideDrawer
          title={diffTitle}
          badge="diff vs current"
          subtitle={diffSubtitle}
          storageKey={DIFF_DRAWER_WIDTH_KEY}
          onClose={() => setDiff(null)}
        >
          <DiffView text={diff.text} />
        </SideDrawer>
      )}
    </>
  )
}
