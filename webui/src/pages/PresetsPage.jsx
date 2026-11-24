import { tooltipAttrs } from '../components/Tooltip.jsx'
import Select from '../components/Select.jsx'
import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import PageIntro from '../components/PageIntro.jsx'
import { useToast } from '../components/Toast.jsx'
import { useUrlTabs } from '../urlTabs.js'
import { PRESETS_TABS as TABS } from '../pageTabs.js'
import PresetPreview from '../components/PresetPreview.jsx'
import WeatherPresets from '../components/WeatherPresets.jsx'
import { editorLink, mapEventLink } from '../crossLinks.js'
import PanelNote from '../components/PanelNote.jsx'
import Tabs from '../components/Tabs.jsx'
import { useConfirm } from '../components/useConfirm.jsx'

// A card starts on the tier the files are on now. Null from the server means
// that no tier matches (Custom), and Apply stays off until the admin picks one.
// A server with no mission to read starts on 1x, wherever the ladder puts it:
// the vehicle ladder has Normal at index 2, not 3.
const CUSTOM_TIER = 'custom'

function defaultTierMultiplier(tweak, variant) {
  const current = tweak.current?.[variant ?? '']
  if (current === null) return CUSTOM_TIER
  if (current !== undefined) return current
  return (tweak.tiers.find((t) => t.multiplier === 1) ?? tweak.tiers[0])?.multiplier ?? 1
}

export default function PresetsPage() {
  const { tab, setTab } = useUrlTabs(TABS)
  const [presets, setPresets] = useState([])
  const [tweaks, setTweaks] = useState([])
  const [preview, setPreview] = useState(null)
  const [previewId, setPreviewId] = useState(null)
  const confirm = useConfirm()
  const [tierMultiplier, setTierMultiplier] = useState({})
  const [variantKey, setVariantKey] = useState({})
  const [saveName, setSaveName] = useState('')
  const pushToast = useToast()

  const load = useCallback(async () => {
    try {
      setPresets(await api.get('/api/presets'))
      setTweaks(await api.get('/api/tweaks'))
    } catch (err) {
      pushToast(err.message || 'Failed to load presets', 'error')
    }
  }, [pushToast])

  useEffect(() => {
    load()
  }, [load])

  async function handlePreview(preset) {
    setPreviewId(preset.id)
    setPreview(null)
    try {
      const data = await api.post(`/api/presets/${preset.id}/preview`)
      setPreview(data)
    } catch (err) {
      pushToast(err.message || 'Preview failed', 'error')
    }
  }

  async function handleApply(preset) {
    const ok = await confirm({
      title: `Apply preset ${preset.name}?`,
      message: 'This overwrites related config files (a version snapshot is taken first).',
      confirmLabel: 'Apply',
    })
    if (!ok) return
    try {
      const data = await api.post(`/api/presets/${preset.id}/apply`)
      pushToast(`Applied ${preset.name} — ${data.modified.length} file(s) changed.`, 'info')
      setPreview(null)
      setPreviewId(null)
    } catch (err) {
      pushToast(err.message || 'Apply failed', 'error')
    }
  }

  async function handleSaveCurrent(e) {
    e.preventDefault()
    try {
      const data = await api.post('/api/presets/custom', { name: saveName })
      pushToast(`Saved current config as preset "${data.name}".`, 'info')
      setSaveName('')
      load()
    } catch (err) {
      pushToast(err.message || 'Failed to save preset', 'error')
    }
  }

  async function handleDelete(preset) {
    const ok = await confirm({
      title: `Delete preset ${preset.name}?`,
      message: 'Removes the saved custom preset file. Applied settings are not reverted.',
      confirmLabel: 'Delete',
      danger: true,
    })
    if (!ok) return
    try {
      await api.del(`/api/presets/custom/${preset.id.split(':', 2)[1]}`)
      pushToast(`Deleted preset ${preset.name}.`, 'info')
      load()
    } catch (err) {
      pushToast(err.message || 'Delete failed', 'error')
    }
  }

  function tweakVariant(tweak) {
    return tweak.variants.length > 0 ? variantKey[tweak.id] ?? tweak.variants[0].key : undefined
  }

  async function handleApplyTweak(tweak) {
    const variant = tweakVariant(tweak)
    const body = { multiplier: Number(tierMultiplier[tweak.id] ?? defaultTierMultiplier(tweak, variant)) }
    if (variant) body.variant = variant
    try {
      const data = await api.post(`/api/tweaks/${tweak.id}`, body)
      pushToast(`Applied ${tweak.name} — ${data.modified.length} file(s) changed.`, 'info')
      load()
    } catch (err) {
      pushToast(err.message || 'Tweak failed', 'error')
    }
  }

  async function handleResetTweak(tweak) {
    const variant = tweakVariant(tweak)
    try {
      const data = await api.post(`/api/tweaks/${tweak.id}/reset`, variant ? { variant } : {})
      if (data.modified.length === 0) {
        pushToast(`${tweak.name} is already at its defaults.`, 'info')
      } else {
        pushToast(`Reset ${tweak.name} — ${data.modified.length} file(s) changed.`, 'info')
      }
      setTierMultiplier((prev) => {
        const next = { ...prev }
        delete next[tweak.id]
        return next
      })
      load()
    } catch (err) {
      pushToast(err.message || 'Reset failed', 'error')
    }
  }

  return (
    <div className="page presets-page">
      <div className="page-header">
        <h1>Presets</h1>
      </div>

      <PageIntro page="/presets" />

      <Tabs tabs={TABS} active={tab} onSelect={setTab} />

      {tab === 'Presets' && (
        <>
        <div className="card">
          <h2>Save current config as preset</h2>
          {/* Names what a custom preset holds: it never had the loot and event
              changes, so applying it did not undo a built-in preset's
              types.xml and events.xml edits (browser test 2026-11-17, Q3). */}
          <PanelNote>
            Saves the serverDZ.cfg settings, globals.xml and cfggameplay.json. Loot and event
            changes in types.xml and events.xml are not saved: undo those with Reset to default on
            the Quick Tweaks tab.
          </PanelNote>
          <form className="inline-form" onSubmit={handleSaveCurrent}>
            <input
              className="text-input"
              placeholder="Preset name"
              value={saveName}
              onChange={(e) => setSaveName(e.target.value)}
              required
            />
            <button className="btn btn-primary btn-small" type="submit">
              Save preset
            </button>
          </form>
        </div>
        <div className="maps-grid">
          {presets.map((preset) => (
            <div key={preset.id} className="card">
              <h2>{preset.name}</h2>
              <PanelNote>{preset.description}</PanelNote>
              {preset.recommended_mods.length > 0 && (
                <PanelNote>Recommended mods: {preset.recommended_mods.join(', ')}</PanelNote>
              )}
              <div className="inline-form">
                <button className="btn btn-ghost btn-small" onClick={() => handlePreview(preset)}>
                  Preview
                </button>
                <button className="btn btn-primary btn-small" onClick={() => handleApply(preset)}>
                  Apply
                </button>
                {!preset.is_builtin && (
                  <button className="btn btn-danger btn-small" onClick={() => handleDelete(preset)}>
                    Delete
                  </button>
                )}
              </div>
              {previewId === preset.id && preview && <PresetPreview preview={preview} />}
            </div>
          ))}
        </div>
        </>
      )}

      {/* The weather editor keeps the same card above its per-element form.
          Here it sits with the other presets, so nobody has to find it, with
          the editor's on/off switch and a link to the full form. */}
      {tab === 'Weather' && <WeatherPresets editorTo={editorLink('weather')} />}

      {tab === 'Quick Tweaks' && (
        <div className="maps-grid">
          {tweaks.map((tweak) => {
            const variant = variantKey[tweak.id] ?? tweak.variants[0]?.key
            const multiplier = tierMultiplier[tweak.id] ?? defaultTierMultiplier(tweak, variant)
            // A tier of 0 only stops new event spawns; say so where the stack has events.
            const spawnsEvents =
              tweak.variants.length > 0
                ? tweak.variants.find((v) => v.key === variant)?.has_events
                : tweak.has_events
            // A variant tied to one event (Vehicles → Only motorbikes) links to
            // its spawn points in the Map Editor. The server sends the event
            // only when this mission's events.xml has it.
            const mapEvent = tweak.variants.find((v) => v.key === variant)?.map_event
            return (
              <div key={tweak.id} className="card">
                {mapEvent ? (
                  <div className="card-header">
                    <h2>{tweak.name}</h2>
                    <Link
                      className="card-link"
                      to={mapEventLink(mapEvent)}
                      {...tooltipAttrs(`Open the ${mapEvent} spawn points in the Map Editor`)}
                    >
                      Edit spawns on map →
                    </Link>
                  </div>
                ) : (
                  <h2>{tweak.name}</h2>
                )}
                <PanelNote>{tweak.description}</PanelNote>
                <div className="inline-form">
                  {tweak.variants.length > 0 && (
                    <Select
                      aria-label="Variant"
                      value={variant}
                      onChange={(v) => setVariantKey((prev) => ({ ...prev, [tweak.id]: v }))}
                      options={tweak.variants.map((v) => ({ value: v.key, label: v.name, description: v.description }))}
                    />
                  )}
                  <Select
                    aria-label="Tier"
                    value={multiplier}
                    onChange={(v) => setTierMultiplier((prev) => ({ ...prev, [tweak.id]: v }))}
                    options={[
                      ...(multiplier === CUSTOM_TIER ? [{ value: CUSTOM_TIER, label: 'Custom' }] : []),
                      ...tweak.tiers.map((tier) => ({ value: tier.multiplier, label: `${tier.name} (${tier.multiplier}x)`, description: tier.description })),
                    ]}
                  />
                  <button
                    className="btn btn-primary btn-small"
                    disabled={multiplier === CUSTOM_TIER}
                    onClick={() => handleApplyTweak(tweak)}
                  >
                    Apply
                  </button>
                  <button
                    className="btn btn-ghost btn-small"
                    {...tooltipAttrs('Put these values back to what they were before the first tweak')}
                    onClick={() => handleResetTweak(tweak)}
                  >
                    Reset to default
                  </button>
                </div>
                {tweak.zero_warning && spawnsEvents && Number(multiplier) === 0 && (
                  <div className="banner banner-warning">⚠ {tweak.zero_warning}</div>
                )}
              </div>
            )
          })}
        </div>
      )}

    </div>
  )
}
