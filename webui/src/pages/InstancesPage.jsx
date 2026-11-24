import Tooltip, { tooltipAttrs } from '../components/Tooltip.jsx'
import Select from '../components/Select.jsx'
import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import ErrorBanner from '../components/ErrorBanner.jsx'
import { useWs } from '../ws'
import { useRole } from '../role.jsx'
import { instanceHref } from '../instance'
import BranchBadge from '../components/BranchBadge.jsx'
import ConfirmModal from '../components/ConfirmModal.jsx'
import DataTable from '../components/DataTable.jsx'
import BetaBadge from '../components/BetaBadge.jsx'
import PageIntro from '../components/PageIntro.jsx'
import { useToast } from '../components/Toast.jsx'
import { useUrlTabs } from '../urlTabs.js'
import RefreshButton from '../components/RefreshButton.jsx'
import { INSTANCES_TABS as TABS } from '../pageTabs.js'
import PanelNote from '../components/PanelNote.jsx'
import Tabs from '../components/Tabs.jsx'
import { useConfirm } from '../components/useConfirm.jsx'
import IconButton from '../components/IconButton.jsx'
import {
  HiOutlineArrowTopRightOnSquare,
  HiOutlinePencilSquare,
  HiOutlineStar,
  HiOutlineTrash,
} from 'react-icons/hi2'

// Instances: several DayZ servers on one machine (TODO E1/E2). The rules are
// `app/server/instances.py`'s, the same ones `zsm instances` uses; see
// `app/web/routers/instances.py` for the three this page adds.
//
// One panel serves every instance (plans/62): this page, like every other, is
// about one of them, and Open (or the sidebar's picker) loads another's. The
// panel runs on the settings of the instance it was started with, which is
// why that one cannot be removed from here.

const INTRO =
  'Each instance is one DayZ server on this machine, with its own settings, data, ports ' +
  'and systemd unit. Adding one copies nothing from the server you have; cloning copies ' +
  'its settings, custom XML and presets, never its backups or server files.'

// The Add form's field tips: what each field means, moved off the card so
// the card keeps only what is true of the whole form.
const ADD_HELP = {
  name:
    'A short ID, such as pvp. It becomes a directory and part of the systemd unit\'s name, ' +
    'so it cannot change later.',
  label: 'The name the panel shows for this instance. You can change it later.',
  serverDir:
    'The folder the DayZ server is installed in, such as ~/servers/pvp. It must be a folder of ' +
    'its own that this panel\'s user can write to: beside the other servers, never inside one.',
  settings:
    'Default settings start empty. Copy settings clones another instance\'s settings, custom XML ' +
    'and presets, never its backups or server files.',
}

function displayName(row) {
  return row.label || row.id
}

function Ports({ ports }) {
  if (!ports) return <span className="hint">—</span>
  return (
    <span {...tooltipAttrs('Game, Steam query and RCon ports')}>
      {ports.game} / {ports.query} / {ports.rcon}
    </span>
  )
}

function Status({ row }) {
  if (row.missing) return <span className="status-pill status-pill-warning">config.json missing</span>
  if (row.running) return <span className="status-pill status-pill-success">Running</span>
  return <span className="status-pill status-pill-neutral">Stopped</span>
}

// After Add, when the server answered with notes: the notes, then the way to its Quick setup.
function NextSteps({ created, startup, onClose }) {
  const id = created.id
  return (
    <div className="banner banner-info">
      <p>
        <strong>Added {id}</strong> with ports {created.ports.game} / {created.ports.query} /{' '}
        {created.ports.rcon} and server directory <code>{created.server_dir}</code>.
      </p>
      {created.notes.length > 0 && (
        <ul className="banner-list">
          {created.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      )}
      <p>
        Next, install its DayZ server with Quick setup. If its server directory already holds a
        server that ran before Zedstead, adopt that server instead.
      </p>
      <div className="inline-form">
        <a className="btn btn-primary btn-small" href={instanceHref(id, startup, '/quick-setup')}>
          Open Quick setup for {id}
        </a>
        <a className="btn btn-ghost btn-small" href={instanceHref(id, startup, '/adopt')}>
          Adopt the server in {id}
        </a>
        <button className="btn btn-ghost btn-small" onClick={onClose}>
          Stay here
        </button>
      </div>
    </div>
  )
}

// The form that adds an instance: name, settings source, branch and folder.
function AddForm({ rows, locked, branchHelp, branchChoiceHelp, onCreated }) {
  const pushToast = useToast()
  const [name, setName] = useState('')
  const [label, setLabel] = useState('')
  // The directory follows the name (~/servers/<name>) until the admin types
  // one, so Add or Clone is never greyed out for a field that has a default.
  const [typedDir, setTypedDir] = useState(null)
  const serverDir = typedDir ?? (name ? `~/servers/${name}` : '')
  const [source, setSource] = useState('')
  // '' keeps the copied instance's branch, or stable for a new one (plans/66).
  const [branch, setBranch] = useState('')
  const sourceRow = rows.find((row) => row.id === source)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function handleSubmit(e) {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      const created = await api.post('/api/instances', {
        name,
        label,
        server_dir: serverDir,
        source,
        branch: branch || null,
      })
      setName('')
      setLabel('')
      setTypedDir(null)
      setSource('')
      setBranch('')
      pushToast(`Added instance ${created.id}.`, 'info')
      onCreated(created)
    } catch (err) {
      setError(err.message || 'Failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="card">
      <h2>
        Add an instance <BetaBadge feature="instances" />
      </h2>
      {locked ? (
        <PanelNote>
          The path lock (web_lock_paths) is on, and a new instance needs a server directory. Use{' '}
          <code>zsm instances add</code> or <code>zsm instances clone</code> on the server, or
          lift the lock there.
        </PanelNote>
      ) : (
        <>
          {/* What the form does as a whole stays on the card; what one field
              means is its label's tip. */}
          <PanelNote>
            A new instance gets the next free ports, 100 above the highest in use. If its server
            started before Zedstead, <Link to="/adopt">Adopt a server</Link> reads its launch command
            and mods: add the instance with that server directory, then adopt it there.
          </PanelNote>
          {error && <p className="login-error">{error}</p>}
          <form onSubmit={handleSubmit}>
            {/* data-tip-at-label: a text field's tip opens on its label
                (Layout.jsx's fieldTipLabel), from hover or focus alike. */}
            <ul className="step-list">
              <li data-tip-at-label="">
                <Tooltip keep text={ADD_HELP.name}>Name</Tooltip>
                <input
                  className="text-input"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="pvp"
                  aria-label="Name"
                  {...tooltipAttrs(ADD_HELP.name, { keep: true })}
                />
              </li>
              <li data-tip-at-label="">
                <Tooltip keep text={ADD_HELP.label}>Label (optional)</Tooltip>
                <input
                  className="text-input"
                  value={label}
                  onChange={(e) => setLabel(e.target.value)}
                  placeholder="PvP server"
                  aria-label="Label"
                  {...tooltipAttrs(ADD_HELP.label, { keep: true })}
                />
              </li>
              <li data-tip-at-label="">
                <Tooltip keep text={ADD_HELP.serverDir}>Server directory</Tooltip>
                <input
                  className="text-input"
                  value={serverDir}
                  onChange={(e) => setTypedDir(e.target.value)}
                  placeholder="~/servers/pvp"
                  aria-label="Server directory"
                  {...tooltipAttrs(ADD_HELP.serverDir, { keep: true })}
                />
              </li>
              <li>
                <Tooltip keep text={ADD_HELP.settings}>Settings</Tooltip>
                <Select
                  className="text-input"
                  value={source}
                  onChange={setSource}
                  aria-label="Settings"
                  options={[
                    { value: '', label: 'Default settings', description: 'Starts with Zedstead\'s defaults and copies nothing.' },
                    ...rows.filter((row) => !row.missing).map((row) => ({
                      value: row.id,
                      label: `Copy settings from ${displayName(row)}`,
                      description: 'Copies its settings, custom XML and presets, never its backups or server files.',
                    })),
                  ]}
                />
              </li>
              <li>
                <Tooltip keep text={branchHelp}>Server branch</Tooltip>
                <Select
                  className="text-input"
                  value={branch}
                  onChange={setBranch}
                  aria-label="Server branch"
                  optionHelp={branchChoiceHelp}
                  options={[
                    {
                      value: '',
                      label: `${sourceRow?.branch === 'experimental' ? 'Experimental server' : 'Stable server'}${sourceRow ? ` (as ${displayName(sourceRow)})` : ''}`,
                      // '' is the branch it names, so it reads that branch's line.
                      description: branchChoiceHelp?.[sourceRow?.branch === 'experimental' ? 'experimental' : 'stable'],
                    },
                    ...(sourceRow && sourceRow.branch !== 'stable' ? [{ value: 'stable', label: 'Stable server' }] : []),
                    ...(sourceRow?.branch !== 'experimental' ? [{ value: 'experimental', label: 'Experimental server' }] : []),
                  ]}
                />
              </li>
            </ul>
            <button className="btn btn-primary" type="submit" disabled={busy || !name || !serverDir}>
              {source ? 'Clone' : 'Add'}
            </button>
          </form>
        </>
      )}
    </div>
  )
}

export default function InstancesPage() {
  const pushToast = useToast()
  const { setInstance } = useRole()
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [created, setCreated] = useState(null)
  const { tab, setTab } = useUrlTabs(TABS)
  const [renaming, setRenaming] = useState(null)
  const [newLabel, setNewLabel] = useState('')
  const confirm = useConfirm()

  // Remove asks first: the instance leaves the list, its files stay.
  async function remove(target) {
    const ok = await confirm({
      title: `Remove ${displayName(target)}?`,
      message:
        'It leaves the list, and no Zedstead command or app acts on it any more. Its settings ' +
        'and data stay in its instances/ directory, and its server directory is not touched.',
      body: (
        <p>
          <code>zsm instances remove {target.id} --purge</code> deletes the settings and frees the name.
        </p>
      ),
      confirmLabel: 'Remove',
      danger: true,
    })
    if (ok) act(api.del(`/api/instances/${encodeURIComponent(target.id)}`), `Removed ${displayName(target)}.`)
  }

  const load = useCallback(() => {
    return api
      .get('/api/instances')
      .then((next) => {
        setData(next)
        setError('')
        const mine = next.instances.find((row) => row.current)
        if (mine) setInstance({ label: displayName(mine), count: next.instances.length })
        return true
      })
      .catch((err) => {
        setError(err.message || 'Failed to load instances')
        return false
      })
  }, [setInstance])

  useEffect(() => {
    load()
  }, [load])

  useWs(
    useCallback(
      (msg) => {
        if (msg.type === 'instances_changed') load()
      },
      [load],
    ),
  )

  function act(promise, message) {
    return promise
      .then(() => {
        if (message) pushToast(message, 'info')
        load()
      })
      .catch((err) => pushToast(err.message || 'Failed', 'error'))
  }

  const rows = data?.instances || []
  const current = rows.find((row) => row.current)

  return (
    <div className="page">
      <div className="page-header">
        <h1>Instances</h1>
        {/* Kept: the list reloads by itself only on instances_changed, so
            another instance's Server column goes stale on a start or stop. */}
        <RefreshButton what="Instances" onRefresh={load} />
      </div>
      <PageIntro text={INTRO} page="/instances" />

      {error && <ErrorBanner as="p" dismissKey={`instances:${error}`}>{error}</ErrorBanner>}
      <Tabs tabs={TABS} active={tab} onSelect={setTab} />

      {created && (
        <NextSteps created={created} startup={data?.startup} onClose={() => setCreated(null)} />
      )}

      {tab === 'On this machine' && (
      <div className="card">
        <h2>On this machine</h2>
        <PanelNote>
          {current
            ? `This page is about ${displayName(current)}. `
            : 'This app runs on the layout from before instances. '}
          The default is what CLI commands act on without --instance, and what zsm web starts
          with: its pages are the ones without /i/ in the address, and its settings run the app.
        </PanelNote>
        <DataTable
          cards
          rowKey={(row) => row.id}
          emptyMessage={data ? 'No instances yet. The command zsm init creates the first one, main.' : 'Loading…'}
          rows={rows}
          columns={[
            {
              key: 'name',
              label: 'Instance',
              className: 'cell-name',
              // The name on its line, its tags on one muted line under it:
              // inline, a phone card broke each tag onto a line of its own.
              render: (row) => {
                const tags = [
                  row.current && 'this page',
                  row.id === data?.startup && 'runs the app',
                  row.default && 'default',
                ].filter(Boolean)
                return (
                  <div className="instance-name-cell">
                    <div>
                      <strong>{displayName(row)}</strong>
                      {row.label && <span className="hint"> ({row.id})</span>}{' '}
                      <BranchBadge branch={row.branch} />
                    </div>
                    {tags.length > 0 && <div className="hint">{tags.join(' · ')}</div>}
                  </div>
                )
              },
            },
            {
              key: 'server_dir',
              label: 'Server directory',
              render: (row) => <div>{row.server_dir ? <code>{row.server_dir}</code> : <span className="hint">—</span>}</div>,
            },
            {
              key: 'ports',
              label: 'Ports',
              help: 'Game / Steam query / RCon. The game port also holds the three after it.',
              render: (row) => (
                <>
                  <Ports ports={row.ports} />
                  {row.conflicts.length > 0 && (
                    <div className="login-error">{row.conflicts.join('; ')}</div>
                  )}
                </>
              ),
            },
            {
              key: 'status',
              label: 'Server',
              // In a div, so a phone card does not stretch the pill to its width.
              render: (row) => (
                <div>
                  <Status row={row} />
                </div>
              ),
            },
            {
              key: 'actions',
              label: '',
              className: 'cell-actions',
              render: (row) => (
                <span className="order-controls">
                  {row.served && !row.current && (
                    <IconButton
                      icon={HiOutlineArrowTopRightOnSquare}
                      label="Open"
                      tip={`Open ${displayName(row)}`}
                      href={instanceHref(row.id, data.startup, '/instances')}
                    />
                  )}
                  <IconButton
                    icon={HiOutlinePencilSquare}
                    label="Rename"
                    tip={`Rename ${displayName(row)}`}
                    onClick={() => {
                      setNewLabel(row.label)
                      setRenaming(row)
                    }}
                  />
                  {!row.default && (
                    <IconButton
                      icon={HiOutlineStar}
                      label="Make default"
                      tip={`Make ${displayName(row)} the default`}
                      onClick={() =>
                        act(
                          api.post(`/api/instances/${encodeURIComponent(row.id)}/default`),
                          `${displayName(row)} is the default.`,
                        )
                      }
                    />
                  )}
                  {!row.current && row.id !== data?.startup && (
                    <IconButton
                      icon={HiOutlineTrash}
                      label="Remove"
                      tip={`Remove ${displayName(row)}`}
                      danger
                      onClick={() => remove(row)}
                    />
                  )}
                </span>
              ),
            },
          ]}
        />
      </div>
      )}

      {tab === 'Add an instance' && data && (
        <AddForm
          rows={rows}
          locked={data.locked}
          branchHelp={data.branch_help}
          branchChoiceHelp={data.branch_choice_help}
          onCreated={(next) => {
            // A new instance has no server yet, so the admin goes straight to its
            // Quick setup. A note (such as a folder Docker deletes) stays on screen
            // first, with the same way in.
            if (next.notes.length === 0) {
              window.location.assign(instanceHref(next.id, data.startup, '/quick-setup'))
              return
            }
            // The next steps sit above the list, so the new row shows with them.
            setCreated(next)
            setTab('On this machine')
            load()
          }}
        />
      )}

      <ConfirmModal
        open={!!renaming}
        title={renaming ? `Rename ${displayName(renaming)}` : ''}
        message="The label names it in the app. Its id stays, since it names its directory and units."
        confirmLabel="Save"
        onCancel={() => setRenaming(null)}
        onConfirm={() => {
          const target = renaming
          setRenaming(null)
          act(
            api.put(`/api/instances/${encodeURIComponent(target.id)}`, { label: newLabel }),
            'Label saved.',
          )
        }}
      >
        <label className="modal-field">
          Label
          <input
            className="text-input"
            value={newLabel}
            maxLength={data?.max_label || 64}
            onChange={(e) => setNewLabel(e.target.value)}
            placeholder={renaming?.id}
            autoFocus
          />
        </label>
      </ConfirmModal>

    </div>
  )
}
