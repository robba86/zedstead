import { useState } from 'react'
import { api } from '../api'
import { useCan } from '../role.jsx'
import ModReportCard from './ModReportCard.jsx'
import { useToast } from './Toast.jsx'
import { useConfirm } from './useConfirm.jsx'

// Shown above every ToxicZone editor (plans/48, ToxicZone pass B): what looks
// off in its five files (app/ops/toxic_zone.py, the same findings behind the
// Doctor's row). ToxicZone's source is public, so unlike RadZone's these are
// the mod's own rules: a moving zone whose derived path no longer matches its
// ends, one heading away from its end, the mod loaded after Expansion's spawn
// selection. The Moving Zones editor recomputes the path on every save.
//
// D4: while AdminUID still lists only the mod author, the card offers to put
// the admin's own Steam64 ID in its place, behind a confirm. Nothing replaces
// it on its own.
//
// "Fix the paths" recomputes every moving zone's path from its ends. The
// table's Save only enables after an edit, so without it a zone whose file
// values are already wrong (the mod's own default) could not be fixed from
// the editor without a pretend edit.
export default function ToxicZoneCard() {
  return (
    <ModReportCard
      endpoint="/api/toxiczone"
      title="ToxicZone"
      notInstalled="ToxicZone is not installed, or it is disabled. These editors open its files once the mod has written them on a server boot."
      empty="Nothing in these files looks off."
      summary={(report) => (
        <>
          Files in <code>{report.folder}</code>
          {' · '}
          {report.zones.length} static zone{report.zones.length === 1 ? '' : 's'}
          {' · '}
          {report.moving.length} moving
        </>
      )}
    >
      {(report) => {
        const has = (id) => report.findings.some((f) => f.id === id)
        return (
          <>
            {(has('toxiczone_moving_stale') || has('toxiczone_moving_backwards')) && <FixPaths />}
            {has('toxiczone_admin_uid_author') && <ReplaceAdminUid />}
          </>
        )
      }}
    </ModReportCard>
  )
}

function FixPaths() {
  const isAdmin = useCan('admin')
  const toast = useToast()
  const confirm = useConfirm()

  if (!isAdmin) return null

  async function fix() {
    const ok = await confirm({
      title: "Recompute the moving zones' paths?",
      message:
        "Coeff, YtoOrigin, Norme and the sign of Speed are written from each moving zone's start and end in MovingToxicZoneConfigV5.json. The start and end points stay as they are.",
      confirmLabel: 'Recompute',
    })
    if (!ok) return
    try {
      const { fixed } = await api.post('/api/toxiczone/recompute-paths')
      toast(
        fixed.length
          ? `Recomputed the path of ${fixed.join(', ')}. It takes effect on the next server restart.`
          : 'Every moving zone already matched its ends.',
        'info'
      )
    } catch (e) {
      toast(e.message || 'Could not recompute the paths', 'error')
    }
  }

  return (
    <>
      <div className="inline-form">
        <button className="btn btn-primary" onClick={fix}>
          Fix the paths
        </button>
      </div>
    </>
  )
}

function ReplaceAdminUid() {
  const isAdmin = useCan('admin')
  const toast = useToast()
  const [steamId, setSteamId] = useState('')
  const confirm = useConfirm()

  if (!isAdmin) return null

  async function replace() {
    const ok = await confirm({
      title: "Replace the author's ID in AdminUID?",
      message: `The mod author's Steam ID is replaced with ${steamId.trim()} in MovingToxicZoneConfigV5.json. Any other IDs on the list stay.`,
      confirmLabel: 'Replace',
    })
    if (!ok) return
    try {
      await api.post('/api/toxiczone/admin-uid', { steam_id: steamId.trim() })
      toast('AdminUID updated. It takes effect on the next server restart.', 'info')
      setSteamId('')
    } catch (e) {
      toast(e.message || 'Could not update AdminUID', 'error')
    }
  }

  return (
    <>
      <form
        className="inline-form"
        onSubmit={(e) => {
          e.preventDefault()
          if (steamId.trim()) replace()
        }}
      >
        <input
          className="text-input"
          value={steamId}
          onChange={(e) => setSteamId(e.target.value)}
          placeholder="Your Steam64 ID, 76561198…"
          aria-label="Steam64 ID to put in AdminUID"
        />
        <button className="btn btn-primary" type="submit" disabled={!steamId.trim()}>
          Replace the author&apos;s ID
        </button>
      </form>
    </>
  )
}
