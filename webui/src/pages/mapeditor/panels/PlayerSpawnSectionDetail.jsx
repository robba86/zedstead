// Player Spawns tab, detail view: the three parameter blocks of one section.
//
// Field lists and their descriptions come from
// GET /player-spawns/param-metadata rather than being hardcoded here — the
// XML's parameter names are a server-side concern and the tooltips are the
// only documentation an admin gets. Values are edited and sent as strings:
// cfgPlayerSpawnPoints.xml stores everything as text and some fields are
// ranges ("2,5"), not numbers.

import { useEffect, useMemo, useState } from 'react'
import { useEditor } from '../state/MapEditorProvider'
import { SECTION_COLORS, SECTION_LABELS } from '../leaflet/usePlayerSpawnLayer'
import { useResource } from '../state/useResource'
import { meApi } from '../api'
import { useToast } from '../../../components/Toast.jsx'
import { useConfirmDiscard, useUnsavedChanges } from '../../../useUnsavedChanges.js'
import PanelNote from '../../../components/PanelNote.jsx'
import ListSection from '../../../components/ListSection.jsx'

const PS_SECTION_FORM = 'mapeditor-player-spawn-section'

// The fields of one block that differ from what was seeded or last saved.
function changedFields(form, before) {
  return Object.fromEntries(
    Object.entries(form || {}).filter(
      ([field, value]) => String(value ?? '') !== String(before?.[field] ?? '')
    )
  )
}

const PARAM_BLOCKS = [
  ['spawn', 'Spawn Parameters', 'spawn_params'],
  ['generator', 'Generator Parameters', 'generator_params'],
  ['group', 'Group Parameters', 'group_params'],
]

export default function PlayerSpawnSectionDetail({ section: sectionName }) {
  const { playerSpawns, dispatch } = useEditor()
  const pushToast = useToast()
  const metadata = useResource('/player-spawns/param-metadata', { initial: {} })
  const [forms, setForms] = useState(null)
  const [savingBlock, setSavingBlock] = useState(null)
  const [savedBlock, setSavedBlock] = useState(null)

  const section = useMemo(
    () => playerSpawns.find((s) => s.section === sectionName) || null,
    [playerSpawns, sectionName]
  )

  // What each block held when it was seeded or last saved. A save sends only
  // the fields that differ from it: a field the file lacks arrives as null,
  // and sending it back would add it to the file.
  const [loaded, setLoaded] = useState(null)

  // Leaving the page, the tab or the section asks first, as the config
  // editors do.
  const confirmDiscard = useConfirmDiscard()
  const dirty = useMemo(
    () =>
      Boolean(forms && loaded) &&
      PARAM_BLOCKS.some(([blockKey]) => Object.keys(changedFields(forms[blockKey], loaded[blockKey])).length > 0),
    [forms, loaded]
  )
  useUnsavedChanges(dirty, { form: PS_SECTION_FORM })

  // Seed once per section. Unlike the event detail form there is no dirty
  // guard: these are three small forms with explicit per-block saves, and the
  // only thing that reloads them is a change the user made themselves.
  useEffect(() => {
    if (!section) return
    const seed = {
      spawn: { ...section.spawn_params },
      generator: { ...section.generator_params },
      group: { ...section.group_params },
    }
    setForms(seed)
    setLoaded(seed)
  }, [sectionName]) // eslint-disable-line react-hooks/exhaustive-deps

  const back = (
    <button
      type="button"
      className="me-back-btn"
      onClick={async () => {
        if (await confirmDiscard(PS_SECTION_FORM)) {
          dispatch({ type: 'SELECT_PS_SECTION', payload: null })
        }
      }}
    >
      ← Back
    </button>
  )

  if (!section || !forms) {
    return (
      <>
        {back}
        <PanelNote>Loading section…</PanelNote>
      </>
    )
  }

  async function save(blockKey, title) {
    const changed = changedFields(forms[blockKey], loaded?.[blockKey])
    setSavingBlock(blockKey)
    try {
      const saved = await meApi.put(
        `/player-spawns/${encodeURIComponent(sectionName)}/params/${blockKey}`,
        changed
      )
      if (saved && typeof saved === 'object') {
        setLoaded((prev) => ({ ...prev, [blockKey]: { ...saved } }))
        setForms((prev) => ({ ...prev, [blockKey]: { ...saved } }))
      }
      setSavedBlock(blockKey)
      setTimeout(() => setSavedBlock((b) => (b === blockKey ? null : b)), 1500)
    } catch (err) {
      pushToast(`${title} save failed: ${err.message}`, 'error')
    } finally {
      setSavingBlock(null)
    }
  }

  return (
    <>
      {back}
      <div className="me-ps-detail">
        <div className="me-event-detail-header">
          <span
            className="me-event-detail-name"
            style={{ color: SECTION_COLORS[sectionName] || '#888' }}
          >
            {SECTION_LABELS[sectionName] || sectionName}
          </span>
          <span className="me-ps-detail-desc">{section.description}</span>
        </div>

        {PARAM_BLOCKS.map(([blockKey, title, sourceKey]) => {
          const meta = metadata.data?.[blockKey] || {}
          const fields = meta.fields || Object.keys(section[sourceKey] || {})
          const descriptions = meta.descriptions || {}
          return (
            <div key={blockKey}>
              <ListSection title={title} />
              <div className="me-detail-fields">
                {fields.map((field) => (
                  <div key={field} className="me-detail-field-row">
                    <label
                      htmlFor={`me-ps-${blockKey}-${field}`}
                      data-tooltip-id="app-tip"
                      data-tooltip-content={descriptions[field] || field}
                    >
                      {field}
                    </label>
                    <input
                      id={`me-ps-${blockKey}-${field}`}
                      type="text"
                      value={forms[blockKey][field] ?? ''}
                      onChange={(e) =>
                        setForms((prev) => ({
                          ...prev,
                          [blockKey]: { ...prev[blockKey], [field]: e.target.value },
                        }))
                      }
                    />
                  </div>
                ))}
              </div>
              <button
                type="button"
                className="me-detail-save-btn me-ps-param-save"
                disabled={savingBlock === blockKey}
                onClick={() => save(blockKey, title)}
              >
                {savedBlock === blockKey
                  ? 'Saved!'
                  : savingBlock === blockKey
                    ? 'Saving...'
                    : `Save ${title.split(' ')[0]} Params`}
              </button>
            </div>
          )
        })}
      </div>
    </>
  )
}
