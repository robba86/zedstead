// The dialog behind a player row's Ban, Priority and Whitelist buttons, on the
// Players page's Online and Sessions tabs. It makes the API call itself, so
// both tabs share one copy.
//
// Ban: a player who is online is banned through RCon by their player number
// (BattlEye kicks them and writes bans.txt). A player from the session
// history is offline, so the ban goes into bans.txt by GUID.
// Priority and Whitelist: BattlEye only knows a player's GUID, a one-way hash
// of the Steam64 ID, so the ID cannot be found from the row and is typed in.

import { useEffect, useState } from 'react'
import { api } from '../api'
import ConfirmModal from './ConfirmModal.jsx'
import { tooltipAttrs } from './Tooltip.jsx'

// ban_ops.MAX_BAN_END: BattlEye can read the end as 32-bit (auth-02).
export function maxBanMinutes() {
  return Math.floor((2147483647 - Date.now() / 1000) / 60)
}

export const BAN_MINUTES_TIP =
  'Duration in minutes. Empty or 0 = permanent. A temporary ban must end by 19 January 2038.'

const LIST_TEXT = {
  priority: {
    title: 'Add to the priority queue',
    path: '/api/priority',
    placeholder: '76561198000000000',
    label: 'Steam64 ID',
    done: 'Added to the priority queue',
  },
  whitelist: {
    title: 'Add to the whitelist',
    path: '/api/whitelist',
    placeholder: 'Steam64 ID, or the player ID from the .ADM',
    label: 'Steam64 ID or 44-character player ID',
    done: 'Added to the whitelist',
  },
}

/** `action` is null (closed) or `{ kind: 'ban'|'priority'|'whitelist', player }`.
 *  `player` carries `name` and `guid`, and `index` when the player is online. */
export default function PlayerActionDialog({ action, onClose, onDone, pushToast }) {
  const [reason, setReason] = useState('')
  const [minutes, setMinutes] = useState('')
  const [id, setId] = useState('')
  const [busy, setBusy] = useState(false)

  // A new dialog starts empty, never with the last player's text.
  useEffect(() => {
    setReason('')
    setMinutes('')
    setId('')
  }, [action])

  if (!action) return null
  const { kind, player } = action
  const isBan = kind === 'ban'
  const list = LIST_TEXT[kind]
  const online = player.index != null

  async function submit(e) {
    e?.preventDefault()
    if (busy || (!isBan && !id.trim())) return
    setBusy(true)
    try {
      if (isBan && online) {
        await api.post('/api/players/ban', {
          player_id: player.index,
          duration_minutes: Number(minutes) || 0,
          reason,
        })
      } else if (isBan) {
        await api.post('/api/bans', { identifier: player.guid, reason, duration_minutes: Number(minutes) || 0 })
      } else {
        await api.post(list.path, { steam_id: id.trim() })
      }
      pushToast(isBan ? `Banned ${player.name}` : `${list.done}: ${player.name}`, 'info')
      onDone?.(kind)
      onClose()
    } catch (err) {
      pushToast(err.message || (isBan ? 'Ban failed' : 'Failed to add the ID'), 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <ConfirmModal
      open
      title={isBan ? `Ban ${player.name}?` : list.title}
      message={
        isBan
          ? online
            ? 'BattlEye kicks the player now and writes the ban to battleye/bans.txt.'
            : `The player is offline, so the ban goes into battleye/bans.txt by GUID (${player.guid}).`
          : `BattlEye only knows ${player.name} by GUID, and the Steam64 ID cannot be worked out from it. Type the ID: the player's Steam profile shows it.`
      }
      confirmLabel={busy ? 'Saving…' : isBan ? 'Ban' : 'Add'}
      danger={isBan}
      confirmDisabled={busy || (!isBan && !id.trim())}
      onConfirm={submit}
      onCancel={onClose}
    >
      <form onSubmit={submit}>
        {isBan ? (
          <>
            <label className="modal-field">
              Reason
              <input className="text-input" value={reason} onChange={(e) => setReason(e.target.value)} autoFocus />
            </label>
            <label className="modal-field">
              Minutes
              <input
                className="text-input text-input-narrow"
                type="number"
                min="0"
                max={maxBanMinutes()}
                value={minutes}
                onChange={(e) => setMinutes(e.target.value)}
                placeholder="0 = permanent"
                {...tooltipAttrs(BAN_MINUTES_TIP)}
              />
            </label>
          </>
        ) : (
          <label className="modal-field">
            {list.label}
            <input
              className="text-input"
              value={id}
              onChange={(e) => setId(e.target.value)}
              placeholder={list.placeholder}
              spellCheck={false}
              autoComplete="off"
              autoFocus
            />
          </label>
        )}
        {/* Enter in a field submits; the modal's own button does the same. */}
        <button type="submit" hidden />
      </form>
    </ConfirmModal>
  )
}
