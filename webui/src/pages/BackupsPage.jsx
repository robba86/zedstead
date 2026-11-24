import { Switch } from '../components/FlagSwitch.jsx'
import Select from '../components/Select.jsx'
import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { formatBytes, formatDateTime } from '../format.js'
import { withBase } from '../instance'
import PageIntro from '../components/PageIntro.jsx'
import SteamLoginGate from '../components/SteamLoginGate.jsx'
import Tooltip from '../components/Tooltip.jsx'
import { NEEDS_STEAM_TIP, useSteamLoggedOut } from '../components/NeedsSteam.jsx'
import { useWs } from '../ws'
import { useToast } from '../components/Toast.jsx'
import BetaBadge from '../components/BetaBadge.jsx'
import ConfirmModal from '../components/ConfirmModal.jsx'
import DataTable from '../components/DataTable.jsx'
import OffsiteRemoteCard from '../components/OffsiteRemoteCard.jsx'
import { useJobs } from '../jobs'
import { useUrlTabs } from '../urlTabs.js'
import { BACKUPS_TABS as TABS } from '../pageTabs.js'
import Tabs from '../components/Tabs.jsx'

// The tab that is one untested feature as a whole carries its badge.
const TAB_BETA = { Offsite: 'offsite' }

// The rotation setting, linked from every tab that rotates.
const MAX_COUNT_LINK = <Link to="/settings?q=backup_max_count">backup_max_count</Link>

// What each tab's kind of copy holds and when to use it, as label/text rows.
// The facts come from app/ops/backup_ops.py (quick, full), bundle_ops.py
// (collect_export_manifest, importable_config) and offsite_ops.py: change
// them together.
const TAB_FACTS = {
  'Quick Backups': {
    title: 'What a quick backup is',
    rows: [
      ['Holds', 'The save data of the current mission: players and their gear, vehicles, and the world (bases, tents, stashes). With the Mod data switch on, also what mods save: Expansion, CF, and the TraderPlus bank, garage and trader stock.'],
      ['Leaves out', 'Config files, mission XML, mods and the game files.'],
      ['Use it for', 'A fast, small safety copy before a wipe, a mod change or an update. The Scheduler can make one on a timer.'],
      ['Restore', 'Stop the server first. Restore writes the saved files over the current save data. It does not delete files that the backup does not hold.'],
      ['Kept', <>The newest {MAX_COUNT_LINK} per mission. A new backup deletes the oldest one past that count.</>],
    ],
  },
  'Full Backups': {
    title: 'What a full backup is',
    rows: [
      ['Holds', "The whole server folder: game files, missions, configs and save data. Also this instance's Zedstead settings (config.json) and the mod list."],
      ['Leaves out', 'Mod folders (@…) and log files, unless you turn them on below.'],
      ['Use it for', 'A complete copy before a large change, or to move the server to a new machine. It is large and slow to make.'],
      ['Restore', 'Stop the server first. Restore writes every file back into the server folder. The Zedstead settings and the mods come back only when you turn on those restore options.'],
      ['Kept', <>The newest {MAX_COUNT_LINK}. A new backup deletes the oldest one past that count.</>],
    ],
  },
  Bundles: {
    title: 'What a bundle is',
    rows: [
      ['Holds', "Your setup, not your world: the mod list, serverDZ.cfg, this instance's config.json, the mission XML and JSON files, the BattlEye cfg and the config files in profiles (TraderPlus and Expansion configuration included)."],
      ['Leaves out', 'Save data, player and trader databases, mods, logs and the game files.'],
      ['Use it for', 'Copying a setup to another server of your own. It is small. Before you send one to someone who helps you, remove the passwords and keys that the note under Export bundle lists.'],
      ['Import', 'Writes the bundle files over the current ones, into the mission you pick. Zedstead saves a copy of each overwritten config file first (Analysis → Change history). From config.json it takes only the safe fields, such as the server name and port.'],
      ['Kept', 'Every bundle, until you delete it.'],
    ],
  },
  Offsite: {
    title: 'What offsite does',
    rows: [
      ['Does', 'Copies quick and full backups to another place: an SFTP server, S3 or Backblaze B2. It is not a new kind of backup.'],
      ['Why', 'Backups sit on the same machine as the server. If that machine is lost, the backups are lost with it.'],
      ['When', 'After every new backup when automatic upload is on. If it is off, open the Quick Backups or Full Backups tab and press Push offsite on the backup you want to copy. That button shows after you save a target below.'],
      ['Leaves out', 'Bundles. Zedstead also does not delete old copies on the remote.'],
    ],
  },
}

// The "What … is" card at the top of a tab: TAB_FACTS[tab] as a label/text grid.
function TabFacts({ tab }) {
  const facts = TAB_FACTS[tab]
  if (!facts) return null
  return (
    <div className="card">
      <h2>{facts.title}</h2>
      <dl className="kv-grid kv-prose">
        {facts.rows.map(([label, text]) => (
          <div className="kv-row" key={label}>
            <dt>{label}</dt>
            <dd>{text}</dd>
          </div>
        ))}
      </dl>
    </div>
  )
}

const CONFIRM_TEXT = {
  restore: { title: 'Restore backup?', verb: 'Restore' },
  // `danger`: the red confirm button. A delete cannot be undone, a bundle's
  // no less than a backup's.
  delete: { title: 'Delete backup?', verb: 'Delete', danger: true },
  'bundle-delete': { title: 'Delete bundle?', verb: 'Delete', danger: true },
  'bundle-import': { title: 'Import bundle?', verb: 'Import', beta: 'bundle-import' },
}


export default function BackupsPage() {
  const { tab, setTab } = useUrlTabs(TABS)
  const [quick, setQuick] = useState([])
  const [quickProblem, setQuickProblem] = useState('')
  const [full, setFull] = useState([])
  const [backupDir, setBackupDir] = useState('')
  const [offsiteReady, setOffsiteReady] = useState(false)
  const [bundles, setBundles] = useState([])
  const [bundleDir, setBundleDir] = useState('')
  const [bundleNote, setBundleNote] = useState('')
  const [missions, setMissions] = useState([])
  const [note, setNote] = useState('')
  const [includeMods, setIncludeMods] = useState(false)
  const [includeLogs, setIncludeLogs] = useState(false)
  const [modData, setModData] = useState(true)
  // { kind: 'restore'|'delete'|'bundle-delete'|'bundle-import', type, name }
  const [confirmTarget, setConfirmTarget] = useState(null)
  const [restoreAppConfig, setRestoreAppConfig] = useState(false)
  const [redownloadMods, setRedownloadMods] = useState(false)
  const steamLoggedOut = useSteamLoggedOut()
  const [importMission, setImportMission] = useState('')
  const pushToast = useToast()
  const { startJob } = useJobs()

  const loadBackups = useCallback(async () => {
    try {
      const data = await api.get('/api/backups')
      setQuick(data.quick)
      setQuickProblem(data.quick_problem || '')
      setFull(data.full)
      setBackupDir(data.dir || '')
    } catch (err) {
      pushToast(err.message || 'Failed to load backups', 'error')
    }
  }, [pushToast])

  // Only to decide whether the per-row Push button renders. The card owns
  // everything else about the target.
  const loadOffsite = useCallback(async () => {
    try {
      const data = await api.get('/api/backups/remote')
      setOffsiteReady(!!data.configured)
    } catch {
      setOffsiteReady(false)
    }
  }, [])

  const loadBundles = useCallback(async () => {
    try {
      const data = await api.get('/api/bundle')
      setBundles(data.bundles || [])
      setBundleDir(data.dir || '')
      setBundleNote(data.sensitive_note || '')
      setMissions(data.missions || [])
    } catch (err) {
      pushToast(err.message || 'Failed to load bundles', 'error')
    }
  }, [pushToast])

  useEffect(() => {
    loadBackups()
    loadBundles()
    loadOffsite()
  }, [loadBackups, loadBundles, loadOffsite])

  const handleWsMessage = useCallback(
    (msg) => {
      if (msg.type !== 'backups_changed') return
      loadBackups()
      loadBundles()
      loadOffsite()
    },
    [loadBackups, loadBundles, loadOffsite]
  )
  useWs(handleWsMessage)

  async function createBackup(type) {
    try {
      await startJob({
        endpoint: '/api/backups',
        body: {
          type,
          note,
          include_mods: includeMods,
          include_logs: includeLogs,
          mod_data: modData,
        },
        name: `Create ${type} backup`,
        kind: 'backup_create',
      })
      setNote('')
    } catch (err) {
      pushToast(err.message || 'Failed to start backup', 'error')
    }
  }

  async function handleConfirm() {
    if (!confirmTarget) return
    const { kind, type, name } = confirmTarget
    setConfirmTarget(null)
    try {
      if (kind === 'restore') {
        await startJob({
          endpoint: '/api/backups/restore',
          body: {
            type,
            name,
            restore_app_config: type === 'full' && restoreAppConfig,
            redownload_mods: type === 'full' && redownloadMods && !steamLoggedOut,
          },
          name: `Restore ${type} backup`,
          kind: 'backup_restore',
        })
      } else if (kind === 'bundle-delete') {
        const data = await api.del('/api/bundle', { names: [name] })
        pushToast(`Deleted ${data.deleted} bundle(s).`, 'info')
        loadBundles()
      } else if (kind === 'bundle-import') {
        await startJob({
          endpoint: '/api/bundle/import',
          body: { filename: name, target_mission: importMission || null },
          name: 'Import bundle',
          kind: 'bundle_import',
        })
      } else {
        await api.del('/api/backups', { type, names: [name] })
        loadBackups()
      }
    } catch (err) {
      pushToast(err.message || 'Action failed', 'error')
    }
  }

  async function pushOffsite(type, name) {
    try {
      await startJob({
        endpoint: '/api/backups/push',
        body: { type, name },
        name: `Push ${name} offsite`,
        kind: 'backup_push',
      })
    } catch (err) {
      pushToast(err.message || 'Failed to start the upload', 'error')
    }
  }

  async function handleExportBundle() {
    try {
      await startJob({
        endpoint: '/api/bundle/export',
        name: 'Export bundle',
        kind: 'bundle_export',
        // No onStatusChange: the job broadcasts `backups_changed` when it
        // finishes, which re-lists the table in every open tab.
      })
    } catch (err) {
      pushToast(err.message || 'Export failed', 'error')
    }
  }

  const backupColumns = (type) => [
    { key: 'name', label: 'Name' },
    { key: 'timestamp', label: 'Created', render: (b) => formatDateTime(b.timestamp) },
    // The zip on disk (what Download sends), not the files before compression.
    { key: 'size', label: 'Size', render: (b) => formatBytes(b.size ?? b.total_size) },
    { key: 'note', label: 'Note' },
    {
      key: 'actions',
      label: '',
      render: (b) => (
        <span className="order-controls">
          {/* A plain anchor, not a fetch+blob: a full backup can be several GB
              and the browser streams this one straight to disk. Same pattern as
              the bans export. The name is in `download` as well as in the
              response's Content-Disposition: on a self-signed HTTPS panel
              Chromium drops the header's name and offers "download". */}
          <a
            className="btn btn-ghost btn-small"
            href={withBase(`/api/backups/download?type=${type}&name=${encodeURIComponent(b.name)}`)}
            download={`${b.name}.zip`}
          >
            Download
          </a>
          {offsiteReady && (
            <button
              className="btn btn-ghost btn-small"
              onClick={() => pushOffsite(type, b.name)}
            >
              Push offsite
            </button>
          )}
          <button
            className="btn btn-ghost btn-small"
            onClick={() => setConfirmTarget({ kind: 'restore', type, name: b.name })}
          >
            Restore
          </button>
          <button
            className="btn btn-danger btn-small"
            onClick={() => setConfirmTarget({ kind: 'delete', type, name: b.name })}
          >
            Delete
          </button>
        </span>
      ),
    },
  ]

  // Download hands over the .zip only. `list_backups` finds a backup by its
  // .meta.json sidecar, so an offsite copy of just the zip restores fine but
  // never shows up in the list — say so rather than let people discover it.
  const sidecarHint = backupDir ? (
    <p className="hint">
      Stored in <code>{backupDir}</code>. Download saves only the <code>.zip</code>. To list the
      backup on another machine, copy the <code>.meta.json</code> beside it too.
    </p>
  ) : null

  return (
    <div className="page backups-page">
      <div className="page-header">
        <h1>Backups</h1>
      </div>

      <PageIntro page="/backups" />

      <SteamLoginGate />

      <Tabs
        tabs={TABS}
        active={tab}
        onSelect={setTab}
        renderLabel={(t) => (
          <>
            {t}
            <BetaBadge feature={TAB_BETA[t]} />
          </>
        )}
      />

      <TabFacts tab={tab} />

      {tab === 'Quick Backups' && (
        <div className="card">
          <h2>Create quick backup</h2>
          <div className="dry-run-toggle">
            <Switch checked={modData} onChange={setModData}>
              Mod data (Expansion, CF, TraderPlus bank, garage and stock)
            </Switch>
          </div>
          <div className="inline-form">
            <input
              className="text-input"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="Note (optional)"
            />
            <Tooltip keep text={quickProblem || undefined} className="bare">
              <button className="btn btn-primary" disabled={!!quickProblem} onClick={() => createBackup('quick')}>
                Create
              </button>
            </Tooltip>
          </div>
          {quickProblem && <p className="hint">{quickProblem}</p>}
          {sidecarHint}
          <DataTable rowKey={(b) => b.name} emptyMessage="No quick backups." rows={quick} columns={backupColumns('quick')} />
        </div>
      )}

      {tab === 'Full Backups' && (
        <div className="card">
          <h2>Create full backup</h2>
          <div className="dry-run-toggle">
            <Switch checked={includeMods} onChange={setIncludeMods}>
              Include mod directories
            </Switch>
          </div>
          <div className="dry-run-toggle">
            <Switch checked={includeLogs} onChange={setIncludeLogs}>
              Include log files
            </Switch>
          </div>
          <div className="inline-form">
            <input
              className="text-input"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="Note (optional)"
            />
            <button className="btn btn-primary" onClick={() => createBackup('full')}>
              Create
            </button>
          </div>
          {full.length > 0 && (
            <>
              <h2>
                Restore options <BetaBadge feature="backup-restore" />
              </h2>
              <div className="dry-run-toggle">
                <Switch checked={restoreAppConfig} onChange={setRestoreAppConfig}>
                  Also restore app config (config.json) from the backup
                </Switch>
              </div>
              <div className="dry-run-toggle">
                {/* Re-downloading runs SteamCMD; the restore itself does not. */}
                <Tooltip text={steamLoggedOut ? NEEDS_STEAM_TIP : undefined} className="bare">
                  <Switch
                    checked={redownloadMods && !steamLoggedOut}
                    disabled={steamLoggedOut}
                    onChange={setRedownloadMods}
                  >
                    Re-download and copy the listed mods after restore
                  </Switch>
                </Tooltip>
              </div>
            </>
          )}
          {sidecarHint}
          <DataTable rowKey={(b) => b.name} emptyMessage="No full backups." rows={full} columns={backupColumns('full')} />
        </div>
      )}

      {tab === 'Bundles' && (
        <div className="card">
          <div className="page-header">
            <h2>Bundles</h2>
            <button className="btn btn-primary" onClick={handleExportBundle}>
              Export bundle
            </button>
          </div>
          {bundleDir && (
            <p className="hint">
              Stored in <code>{bundleDir}</code>. Change the folder with the{' '}
              <Link to="/settings?q=bundle_export_dir">bundle_export_dir</Link> setting.
            </p>
          )}
          {/* A bundle is both a migration artefact and the thing people attach
              to a "can you look at my setup?" post. The second use is the one
              this warns about; Zedstead does not strip the fields, because the
              first use needs them. */}
          {bundleNote && <div className="banner banner-warning">⚠ {bundleNote}</div>}
          <DataTable
            rowKey={(b) => b.name}
            emptyMessage="No bundles."
            rows={bundles}
            columns={[
              { key: 'name', label: 'Filename' },
              { key: 'size', label: 'Size', render: (b) => formatBytes(b.size) },
              {
                key: 'modified',
                label: 'Created',
                render: (b) => (b.modified ? formatDateTime(b.modified * 1000) : '—'),
              },
              {
                key: 'actions',
                label: '',
                render: (b) => (
                  <span className="order-controls">
                    <a
                      className="btn btn-ghost btn-small"
                      href={withBase(`/api/bundle/download?name=${encodeURIComponent(b.name)}`)}
                      download={b.name}
                    >
                      Download
                    </a>
                    <button
                      className="btn btn-ghost btn-small"
                      onClick={() => {
                        setImportMission('')
                        setConfirmTarget({ kind: 'bundle-import', name: b.name })
                      }}
                    >
                      Import
                    </button>
                    <button
                      className="btn btn-danger btn-small"
                      onClick={() => setConfirmTarget({ kind: 'bundle-delete', name: b.name })}
                    >
                      Delete
                    </button>
                  </span>
                ),
              },
            ]}
          />
        </div>
      )}

      {tab === 'Offsite' && <OffsiteRemoteCard />}

      <ConfirmModal
        open={!!confirmTarget}
        title={
          <>
            {CONFIRM_TEXT[confirmTarget?.kind]?.title || 'Delete backup?'}
            <BetaBadge feature={CONFIRM_TEXT[confirmTarget?.kind]?.beta} />
          </>
        }
        message={
          confirmTarget
            ? `${CONFIRM_TEXT[confirmTarget.kind]?.verb || 'Delete'} ${confirmTarget.name}?`
            : ''
        }
        confirmLabel={CONFIRM_TEXT[confirmTarget?.kind]?.verb || 'Delete'}
        danger={!!CONFIRM_TEXT[confirmTarget?.kind]?.danger}
        onConfirm={handleConfirm}
        onCancel={() => setConfirmTarget(null)}
      >
        {confirmTarget?.kind === 'bundle-import' && (
          <>
            <label className="modal-field">
              Target mission
              <Select
                value={importMission}
                onChange={setImportMission}
                options={[{ value: '', label: "Keep the bundle's own mission" }, ...missions]}
              />
            </label>
            <p className="modal-message">
              The bundle's files overwrite the server's current copies. Every overwritten config
              file is snapshotted first (Analysis → Change history).
            </p>
          </>
        )}
      </ConfirmModal>
    </div>
  )
}
