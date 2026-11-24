// The Players page: who is online, bans, whitelist, the RCon console and the
// moderator tabs.

import { useCallback, useEffect, useState } from 'react'
import SearchInput from '../components/SearchInput.jsx'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { withBase } from '../instance'
import BetaBadge from '../components/BetaBadge.jsx'
import PageIntro from '../components/PageIntro.jsx'
import { useWs } from '../ws'
import { useToast } from '../components/Toast.jsx'
import DataTable from '../components/DataTable.jsx'
import { tooltipAttrs } from '../components/Tooltip.jsx'
import { useUrlTabs } from '../urlTabs.js'
import { PLAYERS_MODERATOR_TABS, PLAYERS_TABS as TABS } from '../pageTabs.js'
import { useCan } from '../role.jsx'
import { resolveHelpSlug } from '../helpLinks.js'
import { formatUptime, formatWhen } from '../format.js'
import Tooltip from '../components/Tooltip.jsx'
import { useServerStatus } from '../serverStatus'
import RefreshButton from '../components/RefreshButton.jsx'
import { configLink } from '../crossLinks.js'
import PanelNote from '../components/PanelNote.jsx'
import Tabs from '../components/Tabs.jsx'
import { useConfirm } from '../components/useConfirm.jsx'
import CountBadge from '../components/CountBadge.jsx'
import ListSection from '../components/ListSection.jsx'
import { RowTag } from '../components/ListRow.jsx'
import PlayerActionDialog, { BAN_MINUTES_TIP, maxBanMinutes } from '../components/PlayerActionDialog.jsx'
import RconCommandInput from '../components/RconCommandInput.jsx'

// The console's history is what moderators have run (GET /api/rcon/history is
// moderator-only), so a viewer does not get the tab at all.
const VIEWER_TABS = TABS.filter((t) => !PLAYERS_MODERATOR_TABS.includes(t))

// Each of these tabs is one untested moderator feature. A viewer sees them
// read-only, so only a moderator sees the badge.
const TAB_BETA = { Online: 'moderation', Bans: 'moderation', Whitelist: 'whitelist', Console: 'rcon-console' }

// Resolved rather than spelled out, like every other route in the app.
const SERVERDZ_LINK = resolveHelpSlug('serverdz')

function formatDuration(seconds) {
  if (seconds == null) return 'ongoing'
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  return h > 0 ? `${h}h ${m}m` : `${m}m`
}

export default function PlayersPage() {
  // Kick, ban, broadcast and the three lists are moderator writes; importing
  // a bans.txt is not on that list, so it stays admin (app/web/permissions.py).
  const canModerate = useCan('moderator')
  const isAdmin = useCan('admin')
  const tabs = canModerate ? TABS : VIEWER_TABS
  const { tab, setTab } = useUrlTabs(tabs)
  const pushToast = useToast()

  // Online
  const [players, setPlayers] = useState([])
  const [onlineError, setOnlineError] = useState(null)
  // Not an error: the endpoint answers 200 with a reason when the server is
  // simply stopped, and an empty table alone doesn't say which it is.
  const [onlineNotice, setOnlineNotice] = useState(null)
  const confirm = useConfirm()
  const [messageText, setMessageText] = useState('')

  // Sessions: one row per player, and a click on a name lists that player's
  // single sessions under the table.
  const [summaries, setSummaries] = useState([])
  const [sessions, setSessions] = useState([])
  const [sessionGuid, setSessionGuid] = useState(null)

  // The Ban / Priority / Whitelist dialog of an Online or Sessions row:
  // null, or { kind, player }.
  const [playerAction, setPlayerAction] = useState(null)

  // Bans
  const [bans, setBans] = useState([])
  const [banStats, setBanStats] = useState(null)
  const [serverBanlist, setServerBanlist] = useState(null)
  const [importText, setImportText] = useState('')
  const [banIdentifier, setBanIdentifier] = useState('')
  const [banReason, setBanReason] = useState('')
  const [banDuration, setBanDuration] = useState('')

  // Priority
  const [priorityIds, setPriorityIds] = useState([])
  const [newPriorityId, setNewPriorityId] = useState('')

  // Whitelist
  const [whitelist, setWhitelist] = useState({ ids: [], comments: {}, enabled: false, path: '' })
  const [newWhitelistId, setNewWhitelistId] = useState('')

  // Stats (TODO D3) — the .ADM's combat events joined to the session
  // tracker's playtime. No heatmap: adm_parser only extracts a position from
  // connect lines, and a map fed by nothing is worse than no map.
  const [stats, setStats] = useState(null)
  const [watchlist, setWatchlist] = useState([])
  const [newWatch, setNewWatch] = useState('')

  // Console (TODO D1) — every BattlEye command the server understands,
  // including the ones zsm has no button for and the ones a mod adds, which
  // is exactly what a fixed button list can never cover.
  const [rconHistory, setRconHistory] = useState([])
  const [rconCommand, setRconCommand] = useState('')
  const [rconBusy, setRconBusy] = useState(false)
  const [messageBusy, setMessageBusy] = useState(false)
  // Which of the two boxes last got an empty Send, so it can say why nothing
  // happened instead of doing nothing.
  const [emptyHint, setEmptyHint] = useState(null)
  // The backend refuses RCon at once on a stopped local server (409); say so
  // before the click. `running === false` only — undefined is "not known yet".
  const { status } = useServerStatus()
  const rconStopped = status?.running === false && !status?.rcon_remote
  const stoppedTip = rconStopped ? 'The server is stopped — start it first.' : null

  // Chat
  const [chatEvents, setChatEvents] = useState([])
  const [chatQuery, setChatQuery] = useState('')

  const loadOnline = useCallback(async () => {
    try {
      const data = await api.get('/api/players/online')
      setPlayers(data.players)
      setOnlineNotice(data.available === false ? data.detail : null)
      setOnlineError(null)
    } catch (err) {
      // 409 is "RCON is not set up" (routers/players.py): the banner then
      // links to the RCon settings instead of only naming them.
      setOnlineError(
        err.status === 409
          ? { rcon: true }
          : err.message || 'Failed to load online players'
      )
    }
  }, [])

  // The Sessions tab: the per-player totals and the single sessions behind them.
  const loadSummaries = useCallback(async () => {
    try {
      const [data, detail] = await Promise.all([
        api.get('/api/players/summaries'),
        api.get('/api/players/sessions'),
      ])
      setSummaries(data.summaries)
      setSessions(detail.sessions)
    } catch (err) {
      pushToast(err.message || 'Failed to load sessions', 'error')
    }
  }, [pushToast])

  const loadBans = useCallback(async () => {
    try {
      const data = await api.get('/api/bans')
      setBans(data.bans)
      setBanStats(data.stats)
      setServerBanlist(data.server_banlist || null)
    } catch (err) {
      pushToast(err.message || 'Failed to load bans', 'error')
    }
  }, [pushToast])

  const loadPriority = useCallback(async () => {
    try {
      const data = await api.get('/api/priority')
      setPriorityIds(data.ids)
    } catch (err) {
      pushToast(err.message || 'Failed to load priority list', 'error')
    }
  }, [pushToast])

  const loadWhitelist = useCallback(async () => {
    try {
      setWhitelist(await api.get('/api/whitelist'))
    } catch (err) {
      pushToast(err.message || 'Failed to load whitelist', 'error')
    }
  }, [pushToast])

  const loadStats = useCallback(async () => {
    try {
      const [s, w] = await Promise.all([
        api.get('/api/players/stats?days=30'),
        api.get('/api/players/watchlist'),
      ])
      setStats(s)
      setWatchlist(w.entries || [])
      return true
    } catch (err) {
      pushToast(err.message || 'Failed to load player stats', 'error')
      return false
    }
  }, [pushToast])

  const loadRconHistory = useCallback(async () => {
    try {
      const data = await api.get('/api/rcon/history')
      setRconHistory(data.entries || [])
    } catch {
      setRconHistory([])
    }
  }, [])

  const loadChat = useCallback(async () => {
    try {
      const q = chatQuery ? `&q=${encodeURIComponent(chatQuery)}` : ''
      const data = await api.get(`/api/chat?hours=24${q}`)
      setChatEvents(data.events)
    } catch (err) {
      pushToast(err.message || 'Failed to load chat', 'error')
    }
  }, [chatQuery, pushToast])

  useEffect(() => {
    if (tab === 'Online') loadOnline()
    if (tab === 'Sessions') {
      loadSummaries()
      // For the Banned tag on each row.
      loadBans()
    }
    if (tab === 'Bans') loadBans()
    if (tab === 'Priority') loadPriority()
    if (tab === 'Whitelist') loadWhitelist()
    if (tab === 'Stats') loadStats()
    if (tab === 'Console') loadRconHistory()
    if (tab === 'Chat') loadChat()
  }, [
    tab,
    loadOnline,
    loadSummaries,
    loadBans,
    loadPriority,
    loadWhitelist,
    loadStats,
    loadRconHistory,
    loadChat,
  ])

  useEffect(() => {
    if (tab !== 'Online') return
    const interval = setInterval(loadOnline, 10000)
    return () => clearInterval(interval)
  }, [tab, loadOnline])

  const handleWsMessage = useCallback(
    (msg) => {
      if (msg.type === 'bans_changed') {
        loadBans()
        loadPriority()
        loadWhitelist()
      }
      // A serverDZ.cfg save can flip enableWhitelist, which changes what the
      // card has to warn about even though the list itself did not move.
      if (msg.type === 'settings_changed') loadWhitelist()
      if (msg.type === 'rcon_event') loadOnline()
    },
    [loadBans, loadPriority, loadWhitelist, loadOnline]
  )
  useWs(handleWsMessage)

  async function handleKick(target) {
    const ok = await confirm({ title: 'Kick player?', message: `Kick ${target.name} from the server?`, confirmLabel: 'Kick', danger: true })
    if (!ok) return
    try {
      await api.post('/api/players/kick', { player_id: target.index, reason: 'Kicked by admin' })
      pushToast(`Kicked ${target.name}`, 'info')
      loadOnline()
    } catch (err) {
      pushToast(err.message || 'Kick failed', 'error')
    }
  }

  // GUIDs with a ban that still applies, for the Sessions tab's Banned tag.
  const bannedGuids = new Set(
    bans.filter((b) => b.ban_type === 'guid' && b.is_active).map((b) => b.identifier.toLowerCase())
  )

  // A player row's moderator buttons, shared by the Online and Sessions tabs.
  // Kick needs the player number, so only an online row (`index`) gets it.
  function playerActionsColumn() {
    if (!canModerate) return []
    return [
      {
        key: 'actions',
        label: '',
        className: 'cell-actions',
        render: (p) => (
          <span className="inline-form">
            {p.index != null && (
              <button className="btn btn-danger btn-small" onClick={() => handleKick(p)}>
                Kick
              </button>
            )}
            <button className="btn btn-danger btn-small" onClick={() => setPlayerAction({ kind: 'ban', player: p })}>
              Ban
            </button>
            <button className="btn btn-ghost btn-small" onClick={() => setPlayerAction({ kind: 'priority', player: p })}>
              Priority
            </button>
            <button className="btn btn-ghost btn-small" onClick={() => setPlayerAction({ kind: 'whitelist', player: p })}>
              Whitelist
            </button>
          </span>
        ),
      },
    ]
  }

  async function handleSendMessage(e) {
    e.preventDefault()
    if (messageBusy) return
    if (!messageText.trim()) {
      setEmptyHint('message')
      return
    }
    setEmptyHint(null)
    setMessageBusy(true)
    try {
      await api.post('/api/players/message', { message: messageText })
      setMessageText('')
      pushToast('Message sent.', 'info')
    } catch (err) {
      pushToast(err.message || 'Failed to send message', 'error')
    } finally {
      setMessageBusy(false)
    }
  }

  async function handleAddBan(e) {
    e.preventDefault()
    if (!banIdentifier.trim()) return
    try {
      await api.post('/api/bans', {
        identifier: banIdentifier.trim(),
        reason: banReason,
        duration_minutes: Number(banDuration) || 0,
      })
      setBanIdentifier('')
      setBanReason('')
      setBanDuration('')
      loadBans()
    } catch (err) {
      pushToast(err.message || 'Failed to add ban', 'error')
    }
  }

  async function handleRemoveBan(identifier) {
    try {
      await api.del(`/api/bans?identifier=${encodeURIComponent(identifier)}`)
      loadBans()
    } catch (err) {
      pushToast(err.message || 'Failed to remove ban', 'error')
    }
  }

  async function handleAddPriority(e) {
    e.preventDefault()
    if (!newPriorityId.trim()) return
    try {
      await api.post('/api/priority', { steam_id: newPriorityId.trim() })
      setNewPriorityId('')
      loadPriority()
    } catch (err) {
      pushToast(err.message || 'Failed to add priority ID', 'error')
    }
  }

  async function handleRemovePriority(steamId) {
    try {
      await api.del(`/api/priority?steam_id=${encodeURIComponent(steamId)}`)
      loadPriority()
    } catch (err) {
      pushToast(err.message || 'Failed to remove priority ID', 'error')
    }
  }

  async function handleAddWhitelist(e) {
    e.preventDefault()
    if (!newWhitelistId.trim()) return
    try {
      await api.post('/api/whitelist', { steam_id: newWhitelistId.trim() })
      setNewWhitelistId('')
      loadWhitelist()
    } catch (err) {
      pushToast(err.message || 'Failed to add whitelist ID', 'error')
    }
  }

  async function handleRemoveWhitelist(steamId) {
    try {
      await api.del(`/api/whitelist?steam_id=${encodeURIComponent(steamId)}`)
      loadWhitelist()
    } catch (err) {
      pushToast(err.message || 'Failed to remove whitelist ID', 'error')
    }
  }

  async function handleAddWatch(e) {
    e.preventDefault()
    if (!newWatch.trim()) return
    try {
      const res = await api.post('/api/players/watchlist', { entry: newWatch.trim() })
      setWatchlist(res.entries || [])
      setNewWatch('')
      loadStats()
    } catch (err) {
      pushToast(err.message || 'Failed to add to the watchlist', 'error')
    }
  }

  async function handleRemoveWatch(entry) {
    try {
      const res = await api.del(`/api/players/watchlist?entry=${encodeURIComponent(entry)}`)
      setWatchlist(res.entries || [])
      loadStats()
    } catch (err) {
      pushToast(err.message || 'Failed to remove from the watchlist', 'error')
    }
  }

  async function handleRconSubmit(e) {
    e.preventDefault()
    const command = rconCommand.trim()
    if (rconBusy) return
    if (!command) {
      setEmptyHint('command')
      return
    }
    setEmptyHint(null)
    setRconBusy(true)
    try {
      await api.post('/api/rcon/command', { command })
      setRconCommand('')
    } catch (err) {
      // The failure is already in the server-side history; the toast is so it
      // is noticed without reading the list.
      pushToast(err.message || 'RCon command failed', 'error')
    } finally {
      setRconBusy(false)
      loadRconHistory()
    }
  }

  return (
    <div className="page players-page">
      <div className="page-header">
        <h1>Players</h1>
      </div>

      <PageIntro page="/players" />

      <Tabs
        tabs={tabs}
        active={tab}
        onSelect={setTab}
        renderLabel={(t) => (
          <>
            {t}
            {canModerate && <BetaBadge feature={TAB_BETA[t]} />}
          </>
        )}
      />

      {tab === 'Online' && (
        <div className="card">
          {onlineError && (
            <div className="banner banner-warning">
              {onlineError.rcon ? (
                <>
                  Set the RCon password and port in <Link to={configLink('battleye')}>BattlEye</Link>
                </>
              ) : (
                onlineError
              )}
            </div>
          )}
          {onlineNotice && !onlineError && (
            <div className="banner banner-info">{onlineNotice}</div>
          )}
          {canModerate && (
          <form className="inline-form" onSubmit={handleSendMessage}>
            <input
              className="text-input"
              value={messageText}
              onChange={(e) => {
                setMessageText(e.target.value)
                setEmptyHint(null)
              }}
              placeholder="Message to all players"
            />
            <Tooltip text={stoppedTip} className="bare">
              <button
                className="btn btn-primary"
                type="submit"
                disabled={messageBusy || rconStopped}
              >
                {messageBusy ? 'Sending…' : 'Send'}
              </button>
            </Tooltip>
          </form>
          )}
          {canModerate && emptyHint === 'message' && (
            <p className="hint">Type a message to broadcast first.</p>
          )}
          <DataTable
            rowKey={(p) => p.index}
            emptyMessage="No players online."
            rows={players}
            columns={[
              { key: 'name', label: 'Name' },
              { key: 'guid', label: 'GUID' },
              { key: 'ping', label: 'Ping' },
              ...playerActionsColumn(),
            ]}
          />
        </div>
      )}

      {tab === 'Sessions' && (
        <div className="card">
          <DataTable
            rowKey={(s) => s.guid}
            emptyMessage="No session history."
            rows={summaries}
            columns={[
              {
                key: 'name',
                label: 'Name',
                className: 'cell-name',
                render: (s) => (
                  <button
                    type="button"
                    className="btn-link"
                    aria-expanded={sessionGuid === s.guid}
                    onClick={() => setSessionGuid(sessionGuid === s.guid ? null : s.guid)}
                    {...tooltipAttrs('Show this player\'s sessions under the table')}
                  >
                    {s.name}
                  </button>
                ),
              },
              {
                key: 'status',
                label: 'Status',
                render: (s) => (
                  <>
                    {s.is_online && <RowTag>online</RowTag>}{' '}
                    {bannedGuids.has(s.guid.toLowerCase()) && (
                      <RowTag tip="This GUID has a ban in battleye/bans.txt that still applies.">banned</RowTag>
                    )}
                  </>
                ),
              },
              { key: 'total_sessions', label: 'Sessions' },
              {
                key: 'total_playtime_seconds',
                label: 'Playtime',
                // A session no disconnect closed counts as 6 hours at most
                // (player_ops.MAX_UNCLOSED); say so rather than print a
                // precise-looking total.
                render: (s) =>
                  s.capped_sessions > 0 ? (
                    <span
                      {...tooltipAttrs(
                        `${s.capped_sessions} session(s) were never closed, so each counts as 6 hours at most.`
                      )}
                    >
                      {formatDuration(s.total_playtime_seconds)}+ (not closed)
                    </span>
                  ) : (
                    formatDuration(s.total_playtime_seconds)
                  ),
              },
              { key: 'first_seen', label: 'First seen', render: (s) => formatWhen(s.first_seen) },
              { key: 'last_seen', label: 'Last seen', render: (s) => formatWhen(s.last_seen) },
              ...playerActionsColumn(),
            ]}
          />
          {sessionGuid && (
            <ListSection
              title={`Sessions of ${summaries.find((s) => s.guid === sessionGuid)?.name ?? sessionGuid}`}
              actions={
                <button type="button" className="btn btn-ghost btn-small" onClick={() => setSessionGuid(null)}>
                  Close
                </button>
              }
            >
              <DataTable
                rowKey={(s) => s.connect_time}
                emptyMessage="No sessions in the last 30 days."
                rows={sessions.filter((s) => s.guid === sessionGuid).reverse()}
                columns={[
                  { key: 'connect_time', label: 'Joined', render: (s) => formatWhen(s.connect_time) },
                  {
                    key: 'disconnect_time',
                    label: 'Left',
                    render: (s) => (s.disconnect_time ? formatWhen(s.disconnect_time) : 'not closed'),
                  },
                  {
                    key: 'duration_seconds',
                    label: 'Length',
                    render: (s) => `${formatDuration(s.duration_seconds)}${s.capped ? '+' : ''}`,
                  },
                ]}
              />
            </ListSection>
          )}
        </div>
      )}

      {/* Two ban lists, and both apply at once (not either/or): BattlEye's
          battleye/bans.txt (GUID or IP; the Online tab's RCon ban writes here
          too) and DayZ's own ban.txt in the server root (player IDs). Each
          gets its own card so nobody reads one as part of the other. */}
      {tab === 'Bans' && (
        <>
          <div className="card">
            <div className="card-header">
              <h2>
                BattlEye bans <CountBadge value={banStats?.total ?? bans.length} />
              </h2>
              {canModerate && (
                <span className="inline-form">
                  {/* bans.txt holds player IP addresses, which a viewer is not
                      served (SEC-7): the download is moderator in permissions.py. */}
                  <a className="btn btn-ghost btn-small" href={withBase('/api/bans/export')} download="bans.txt">
                    Export
                  </a>
                  <button
                    className="btn btn-ghost btn-small"
                    disabled={!banStats || banStats.expired === 0}
                    onClick={async () => {
                      try {
                        const data = await api.post('/api/bans/cleanup-expired')
                        pushToast(`Removed ${data.removed} expired ban(s).`, 'info')
                        loadBans()
                      } catch (err) {
                        pushToast(err.message || 'Cleanup failed', 'error')
                      }
                    }}
                  >
                    Clean up expired
                  </button>
                </span>
              )}
            </div>
            <p className="hint">
              The list in <code>battleye/bans.txt</code>, by GUID or IP. A ban from the Online tab lands here too.
            </p>
            {banStats && banStats.total > 0 && (
              <PanelNote>
                {banStats.permanent} permanent, {banStats.temporary} temporary, {banStats.expired} expired ·{' '}
                {banStats.guid_bans} GUID / {banStats.ip_bans} IP
              </PanelNote>
            )}
            <DataTable
              rowKey={(b) => b.identifier}
              emptyMessage="No BattlEye bans."
              rows={bans}
              columns={[
                {
                  key: 'display_id',
                  label: 'Player',
                  render: (b) => (b.player_name ? `${b.player_name} (${b.display_id})` : b.display_id),
                },
                { key: 'reason', label: 'Reason' },
                { key: 'time_remaining', label: 'Remaining' },
                ...(canModerate
                  ? [
                      {
                        key: 'actions',
                        label: '',
                        render: (b) => (
                          <button
                            className="btn btn-ghost btn-small"
                            onClick={() => handleRemoveBan(b.identifier)}
                          >
                            Remove
                          </button>
                        ),
                      },
                    ]
                  : []),
              ]}
            />
            {canModerate && (
              <ListSection title="Add a ban">
                <form className="inline-form" onSubmit={handleAddBan}>
                  <input
                    className="text-input"
                    value={banIdentifier}
                    onChange={(e) => setBanIdentifier(e.target.value)}
                    placeholder="GUID or IP"
                  />
                  <input
                    className="text-input"
                    value={banReason}
                    onChange={(e) => setBanReason(e.target.value)}
                    placeholder="Reason"
                  />
                  <input
                    className="text-input text-input-narrow"
                    type="number"
                    min="0"
                    max={maxBanMinutes()}
                    value={banDuration}
                    onChange={(e) => setBanDuration(e.target.value)}
                    placeholder="Minutes"
                    {...tooltipAttrs(BAN_MINUTES_TIP)}
                  />
                  <button className="btn btn-primary" type="submit">
                    Ban
                  </button>
                </form>
              </ListSection>
            )}
            {isAdmin && (
              <ListSection title="Import from a bans.txt" foldable defaultOpen={false}>
                <form
                  className="inline-form"
                  onSubmit={async (e) => {
                    e.preventDefault()
                    try {
                      const data = await api.post('/api/bans/import', { content: importText })
                      pushToast(
                        `Imported ${data.added} ban(s), skipped ${data.skipped_duplicates} duplicate(s).`,
                        'info'
                      )
                      setImportText('')
                      loadBans()
                    } catch (err) {
                      pushToast(err.message || 'Import failed', 'error')
                    }
                  }}
                >
                  <textarea
                    className="text-input"
                    rows={3}
                    spellCheck={false}
                    autoComplete="off"
                    autoCapitalize="off"
                    autoCorrect="off"
                    placeholder="Paste the lines of another bans.txt. Bans already listed are skipped."
                    value={importText}
                    onChange={(e) => setImportText(e.target.value)}
                  />
                  <button className="btn btn-ghost btn-small" type="submit" disabled={!importText.trim()}>
                    Import
                  </button>
                </form>
              </ListSection>
            )}
          </div>
          {(serverBanlist?.ids?.length > 0 || serverBanlist?.disabled) && (
            <div className="card">
              <div className="card-header">
                <h2>
                  DayZ ban list <CountBadge value={serverBanlist.ids?.length || 0} />
                </h2>
              </div>
              {serverBanlist.disabled && (
                <div className="banner banner-info">
                  <code>disableBanlist</code> is on in serverDZ.cfg, so DayZ ignores this file.
                </div>
              )}
              <p className="hint">
                The game&apos;s own list in <code>{serverBanlist.path}</code> in the server folder, by player ID. It
                applies next to the BattlEye bans. The panel only shows it: edit the file on the server to change it.
              </p>
              <DataTable
                rowKey={(r) => r.id}
                emptyMessage="No IDs in ban.txt."
                rows={(serverBanlist.ids || []).map((id) => ({ id }))}
                columns={[{ key: 'id', label: 'Player ID' }]}
              />
            </div>
          )}
        </>
      )}
      {tab === 'Priority' && (
        <div className="card">
          <h2>Priority queue (Steam64 IDs)</h2>
          {canModerate && (
          <form className="inline-form" onSubmit={handleAddPriority}>
            <input
              className="text-input"
              value={newPriorityId}
              onChange={(e) => setNewPriorityId(e.target.value)}
              placeholder="76561198000000000"
            />
            <button className="btn btn-primary" type="submit">
              Add
            </button>
          </form>
          )}
          <DataTable
            rowKey={(id) => id}
            emptyMessage="No priority entries."
            rows={priorityIds.map((id) => ({ id }))}
            columns={[
              { key: 'id', label: 'Steam64 ID' },
              ...(canModerate
                ? [
                    {
                      key: 'actions',
                      label: '',
                      render: (row) => (
                        <button className="btn btn-ghost btn-small" onClick={() => handleRemovePriority(row.id)}>
                          Remove
                        </button>
                      ),
                    },
                  ]
                : []),
            ]}
          />
        </div>
      )}

      {tab === 'Whitelist' && (
        <div className="card">
          <h2>Whitelist (Steam64 or 44-character player IDs)</h2>
          {/* The switch and the file are only dangerous together. "On and
              empty" refuses every player with kick 0x0004000C, and that state
              was reachable from the serverDZ.cfg editor before this tab
              existed — which is the whole reason it does. */}
          {whitelist.enabled && whitelist.ids.length === 0 && (
            <p className="banner banner-warning">
              <strong>enableWhitelist is on and this list is empty</strong> — the server
              is refusing every player with "not present in whitelist.txt". Add an ID
              below, or turn the switch off in{' '}
              <Link to={SERVERDZ_LINK}>serverDZ.cfg</Link>.
            </p>
          )}
          {!whitelist.enabled && (
            <p className="hint">
              <code>enableWhitelist</code> is off, so this list is not being read. Turn
              it on in <Link to={SERVERDZ_LINK}>serverDZ.cfg</Link> once the people
              who should get in are listed here.
            </p>
          )}
          {canModerate && (
          <form className="inline-form" onSubmit={handleAddWhitelist}>
            <input
              className="text-input"
              value={newWhitelistId}
              onChange={(e) => setNewWhitelistId(e.target.value)}
              placeholder="Steam64 ID, or the player ID from the .ADM"
            />
            <button className="btn btn-primary" type="submit">
              Add
            </button>
          </form>
          )}
          <DataTable
            rowKey={(id) => id}
            emptyMessage="Nobody is whitelisted."
            rows={whitelist.ids.map((id) => ({ id }))}
            columns={[
              { key: 'id', label: 'Player ID' },
              // The comment on the ID's line: the stock file's example ID says so only there.
              { key: 'note', label: 'Note', render: (row) => whitelist.comments?.[row.id] || '' },
              ...(canModerate
                ? [
                    {
                      key: 'actions',
                      label: '',
                      render: (row) => (
                        <button
                          className="btn btn-ghost btn-small"
                          onClick={() => handleRemoveWhitelist(row.id)}
                        >
                          Remove
                        </button>
                      ),
                    },
                  ]
                : []),
            ]}
          />
          {whitelist.path && <p className="hint">{whitelist.path}</p>}
        </div>
      )}

      {tab === 'Stats' && (
        <>
          <div className="card">
            <h2>Watchlist</h2>
            <PanelNote>
              Names or UIDs to flag wherever they appear below. Either kind — an admin
              adds whichever they have to hand.
            </PanelNote>
            {canModerate && (
            <form className="inline-form" onSubmit={handleAddWatch}>
              <input
                className="text-input"
                value={newWatch}
                onChange={(e) => setNewWatch(e.target.value)}
                placeholder="Player name or UID"
              />
              <button className="btn btn-primary" type="submit">
                Watch
              </button>
            </form>
            )}
            {watchlist.length > 0 && (
              <div className="guide-links">
                {watchlist.map((entry) =>
                  canModerate ? (
                    <button
                      key={entry}
                      className="btn btn-ghost btn-small"
                      onClick={() => handleRemoveWatch(entry)}
                      {...tooltipAttrs('Remove from the watchlist')}
                    >
                      {entry} ✕
                    </button>
                  ) : (
                    <span key={entry} className="status-pill">
                      {entry}
                    </span>
                  )
                )}
              </div>
            )}
          </div>

          <div className="card">
            <div className="page-header">
              <h2>Leaderboard</h2>
              <RefreshButton what="Leaderboard" onRefresh={loadStats} />
            </div>
            <PanelNote>
              Playtime is the last 30 days from the session tracker; kills and deaths
              come from{' '}
              {stats?.adm_found ? <code>{stats.adm_file}</code> : 'the .ADM log'}
              {!stats?.adm_found &&
                ', which was not found — add -adminLog to the launch flags to get one.'}
            </PanelNote>
            <DataTable
              rowKey={(r) => r.uid || r.name}
              emptyMessage="Nothing recorded yet."
              rows={stats?.leaderboard || []}
              columns={[
                {
                  key: 'name',
                  label: 'Player',
                  render: (r) => (
                    <span className={r.watched ? 'watched-player' : undefined}>
                      {r.watched ? '★ ' : ''}
                      {r.name || r.uid}
                    </span>
                  ),
                },
                { key: 'kills', label: 'Kills' },
                { key: 'deaths', label: 'Deaths' },
                { key: 'kd', label: 'K/D' },
                {
                  key: 'playtime_seconds',
                  label: 'Playtime',
                  render: (r) => formatUptime(r.playtime_seconds),
                },
                { key: 'sessions', label: 'Sessions' },
              ]}
            />
          </div>

          <div className="card">
            <h2>Kill feed</h2>
            <DataTable
              rowKey={(r, i) => `${r.at}-${r.killer}-${r.victim}-${i}`}
              emptyMessage="No player kills in the current .ADM log."
              rows={stats?.killfeed || []}
              columns={[
                { key: 'at', label: 'When' },
                {
                  key: 'summary',
                  label: 'What happened',
                  render: (r) => (
                    <span className={r.watched ? 'watched-player' : undefined}>
                      {r.watched ? '★ ' : ''}
                      {r.summary}
                    </span>
                  ),
                },
              ]}
            />
          </div>
        </>
      )}

      {tab === 'Console' && (
        <div className="card">
          <h2>RCon console</h2>
          <PanelNote>
            Sent straight to BattlEye — every command the server understands, including
            the ones this app has no button for. It can kick, ban and shut the server
            down; there is no confirmation step. Kept in memory only, so the list is
            empty again after Zedstead restarts.
          </PanelNote>
          <RconCommandInput
            value={rconCommand}
            onChange={(text) => {
              setRconCommand(text)
              setEmptyHint(null)
            }}
            onSubmit={handleRconSubmit}
            busy={rconBusy}
            disabled={rconStopped}
            disabledTip={stoppedTip}
          />
          {emptyHint === 'command' && <p className="hint">Type a command first.</p>}
          {rconHistory.length === 0 ? (
            <PanelNote>Nothing sent yet.</PanelNote>
          ) : (
            <ol className="rcon-history">
              {rconHistory
                .slice()
                .reverse()
                .map((entry, i) => (
                  <li key={`${entry.at}-${i}`} className={entry.ok ? '' : 'rcon-failed'}>
                    <div className="rcon-command">
                      <code>{entry.command}</code>
                      <span className="hint">{formatWhen(entry.at)}</span>
                    </div>
                    {entry.response && <pre className="rcon-response">{entry.response}</pre>}
                  </li>
                ))}
            </ol>
          )}
        </div>
      )}

      {tab === 'Chat' && (
        <div className="card">
          <form
            className="inline-form"
            onSubmit={(e) => {
              e.preventDefault()
              loadChat()
            }}
          >
            <SearchInput
              value={chatQuery}
              onChange={(e) => setChatQuery(e.target.value)}
              placeholder="Search chat…"
            />
            <button className="btn btn-ghost" type="submit">
              Search
            </button>
          </form>
          <DataTable
            rowKey={(e, i) => `${e.timestamp}-${i}`}
            emptyMessage="No chat history."
            rows={chatEvents.map((e, i) => ({ ...e, i }))}
            columns={[
              { key: 'timestamp', label: 'Time' },
              { key: 'name', label: 'Name' },
              { key: 'channel', label: 'Channel' },
              { key: 'message', label: 'Message' },
            ]}
          />
        </div>
      )}

      <PlayerActionDialog
        action={playerAction}
        onClose={() => setPlayerAction(null)}
        onDone={(kind) => {
          if (kind === 'ban') {
            loadBans()
            loadOnline()
          }
        }}
        pushToast={pushToast}
      />
    </div>
  )
}
