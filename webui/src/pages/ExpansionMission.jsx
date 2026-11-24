import Select from '../components/Select.jsx'
import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { HiOutlineArrowDownTray, HiOutlineTrash } from 'react-icons/hi2'
import { api } from '../api'
import { LINKS, configLink } from '../crossLinks'
import { tooltipAttrs } from '../components/Tooltip.jsx'
import { useToast } from '../components/Toast.jsx'
import { useJobs } from '../jobs.jsx'
import PanelNote from '../components/PanelNote.jsx'
import CountBadge from '../components/CountBadge.jsx'
import ListRow from '../components/ListRow.jsx'

// Expansion's mission files — the Install → Expansion sub-tab's second card.
//
// The content is NOT in the mods: it lives in the ExpansionModTeam's separate
// DayZ-Expansion-Missions repo, which ships no LICENSE file, so zsm never
// vendors it. The Download button fetches the GitHub zip into the config dir
// (the same download-and-unpack path the map missions use) and
// `expansion_template_dir` points at a copy the user unpacked themselves.
//
// Everything the preview shows is computed server-side by
// app/ops/expansion_ops.py — which entries survive, which are dropped and why.
// The page must not re-derive any of it: the filter depends on the classes the
// installed mods' PBOs define, which only the backend can read.
export default function ExpansionMission({ onChanged }) {
  const [data, setData] = useState(null)
  const [template, setTemplate] = useState('')
  const [plan, setPlan] = useState(null)
  const [planning, setPlanning] = useState(false)
  const [showDropped, setShowDropped] = useState(false)

  const pushToast = useToast()
  const { startJob } = useJobs()

  const load = useCallback(async () => {
    try {
      const body = await api.get('/api/expansion/templates')
      setData(body)
      setTemplate((prev) => prev || body.match || (body.templates[0]?.name || ''))
    } catch (err) {
      pushToast(err.message || 'Failed to load Expansion templates', 'error')
    }
  }, [pushToast])

  useEffect(() => {
    load()
  }, [load])

  // The preview is a read — it writes nothing — so it refreshes on every
  // template change rather than behind a button.
  useEffect(() => {
    let cancelled = false
    if (!template) {
      setPlan(null)
      return undefined
    }
    setPlanning(true)
    api
      .get(`/api/expansion/mission?template=${encodeURIComponent(template)}`)
      .then((body) => {
        if (!cancelled) setPlan(body)
      })
      .catch((err) => {
        if (!cancelled) {
          setPlan(null)
          pushToast(err.message || 'Failed to read the template', 'error')
        }
      })
      .finally(() => {
        if (!cancelled) setPlanning(false)
      })
    return () => {
      cancelled = true
    }
  }, [template, pushToast])

  async function download() {
    try {
      await startJob({
        endpoint: '/api/expansion/templates/download',
        name: 'Download Expansion templates',
        kind: 'expansion_templates',
        onStatusChange: (status) => {
          if (status === 'success') load()
        },
      })
    } catch (err) {
      pushToast(err.message || 'Failed to start the download', 'error')
    }
  }

  async function apply() {
    try {
      await startJob({
        endpoint: '/api/expansion/mission/apply',
        body: { template },
        name: 'Apply Expansion mission files',
        kind: 'expansion_mission',
        onStatusChange: (status) => {
          if (status !== 'success') return
          load()
          onChanged?.()
        },
      })
    } catch (err) {
      pushToast(err.message || 'Failed to start the apply', 'error')
    }
  }

  async function remove() {
    try {
      const res = await api.post('/api/expansion/mission/remove')
      pushToast(`Removed ${res.removed.length} file(s) from the mission.`, 'success')
      load()
      onChanged?.()
    } catch (err) {
      pushToast(err.message || 'Failed to remove the files', 'error')
    }
  }

  if (!data) return <PanelNote>Loading…</PanelNote>

  const applied = data.applied || []
  // Remove is narrower than it sounds, and the difference matters: it deletes
  // the manual XML records and un-applies them, but the .map files it copied
  // are mission content once copied and nothing records that zsm put them
  // there (app/ops/expansion_ops.py::remove_applied). Say so on the button.
  const removeTip =
    `Deletes the ${applied.length} manual XML file(s) this template created ` +
    `(${applied.map((a) => a.kind).join(', ')}) and undoes what they applied to the mission — ` +
    'the custom/ copies and their cfgeconomycore.xml registration. The trader and object .map ' +
    'files it copied into the mission stay, and so does the downloaded template, so Apply can ' +
    'put the XML back.'

  return (
    <>
      <PanelNote>
        Traders, vehicle spawns, airdrops and the Expansion economy live in the mod team's{' '}
        <a href={data.repo_url} target="_blank" rel="noreferrer">
          DayZ-Expansion-Missions
        </a>{' '}
        repository, not in the mods themselves. Zedstead downloads it, filters it down to the modules
        you have installed, and applies it as{' '}
        <Link to={configLink('manual-xml-links')}>manual XML files</Link> — so every later{' '}
        <code>zsm run</code> re-applies it and removing it is one button.
      </PanelNote>

      <div className="action-bar">
        <button className="btn btn-ghost" onClick={download}>
          <HiOutlineArrowDownTray aria-hidden="true" />
          {data.downloaded ? 'Re-download templates' : 'Download templates'}
        </button>
        <span className="dep-info">{data.dir}</span>
      </div>

      {!data.downloaded ? (
        <PanelNote>
          No templates unpacked yet. Download them, or point{' '}
          <Link to="/settings?q=expansion_template_dir">expansion_template_dir</Link> at a copy you
          unpacked yourself.
        </PanelNote>
      ) : (
        <>
          <label className="expansion-template-field">
            <span>Map template</span>
            <Select
              value={template}
              onChange={setTemplate}
              options={data.templates.map((t) => ({
                value: t.name,
                label: `${t.name}${t.map_name && t.map_name !== t.name ? ` (${t.map_name})` : ''}`,
                description: [t.complete ? '' : 'Unfinished upstream', t.name === data.match ? 'Matches this mission' : '']
                  .filter(Boolean)
                  .join(' · '),
              }))}
            />
          </label>
          {data.match && template !== data.match && (
            <p className="banner banner-warning">
              This mission runs {data.map_name || data.terrain}, whose template is{' '}
              <strong>{data.match}</strong>. Applying another map's template writes its spawn
              positions into your mission.
            </p>
          )}

          {planning && <PanelNote>Reading the template…</PanelNote>}
          {plan && <PlanReport plan={plan} showDropped={showDropped} setShowDropped={setShowDropped} />}

          <div className="action-bar">
            <button
              className="btn btn-primary"
              disabled={!plan || planning || !plan.mission}
              onClick={apply}
            >
              Apply to {plan?.mission || 'the mission'}
            </button>
            {applied.length > 0 && (
              <button className="btn btn-ghost" onClick={remove} {...tooltipAttrs(removeTip)}>
                <HiOutlineTrash aria-hidden="true" />
                Remove <CountBadge value={applied.length} />
              </button>
            )}
          </div>

          {applied.length > 0 && (
            <PanelNote>
              Applied: {applied.map((a) => a.kind).join(', ')}. They are listed on the{' '}
              <Link to={configLink('mod-xml')}>Custom config tab</Link> as{' '}
              <Link to={configLink('manual-xml-links')}>{LINKS['manual-xml-links'].label}</Link> —
              edit or disable them there.
            </PanelNote>
          )}
        </>
      )}
    </>
  )
}

const KIND_LABELS = {
  types: 'types',
  spawnabletypes: 'spawnable types',
  events: 'events',
  cfgeventspawns: 'event spawn positions',
}

function PlanReport({ plan, showDropped, setShowDropped }) {
  const kinds = Object.keys(plan.total)
  return (
    <>
      {plan.warnings.map((w) => (
        <p className="banner banner-warning" key={w}>
          {w}
        </p>
      ))}
      {plan.overrides.length > 0 && (
        <p className="banner banner-warning">
          These events replace the mission's own: <strong>{plan.overrides.join(', ')}</strong>. DayZ
          takes the last definition, so the template's counts win over{' '}
          <Link to="/configs/events">db/events.xml</Link> — including anything the Vehicles quick
          tweak set there.
        </p>
      )}

      <PanelNote>
        Filtered for {plan.modules.length} installed module(s): {plan.modules.join(', ') || 'none'}.
      </PanelNote>
      <ul className="expansion-picker">
        {kinds.map((kind) => (
          <ListRow
            as="li"
            key={kind}
            name={KIND_LABELS[kind] || kind}
            meta={<CountBadge value={`${plan.kept[kind].toLocaleString()} of ${plan.total[kind].toLocaleString()} kept`} />}
          />
        ))}
        {plan.map_files.length > 0 && (
          <ListRow
            as="li"
            name="trader & object maps"
            sub="Copied as they are"
            meta={<CountBadge parts={[{ value: plan.map_files.length, unit: plan.map_files.length === 1 ? 'file' : 'files' }]} />}
          />
        )}
      </ul>

      {plan.dropped.length > 0 && (
        <>
          <button className="btn btn-ghost btn-small" onClick={() => setShowDropped(!showDropped)}>
            {showDropped ? 'Hide' : 'Show'} the {plan.dropped.length} dropped entr
            {plan.dropped.length === 1 ? 'y' : 'ies'}
          </button>
          {showDropped && (
            <ul className="expansion-picker">
              {/* The index is part of the key: one item can be dropped from
                  several parent types (M4Suppressor three times on Chernarus),
                  so kind/element/name alone collides. */}
              {plan.dropped.map((d, i) => (
                <ListRow
                  as="li"
                  key={`${d.kind}-${d.element}-${d.name}-${i}`}
                  wrap
                  name={d.name}
                  sub={`${KIND_LABELS[d.kind] || d.kind} / ${d.element} · ${d.reason}`}
                />
              ))}
            </ul>
          )}
        </>
      )}
    </>
  )
}
