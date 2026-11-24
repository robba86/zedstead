// The Configs drawer for an editor with a page-sized UI of its own instead of
// the registry's Edit/Raw/Versions shell (plans/91): Types.xml and TraderPlus
// Prices. The backend lists them with `kind: "custom"` (routers/configs.py
// `_BESPOKE_EDITORS`). Custom config is custom too, but it is a tab of the
// Configs page, so it is not here.
//
// The drawer adds the Raw and Versions tabs every other editor has, over the
// same /api/configs/<id>/meta, raw and versions endpoints. Edit is the
// editor's own body.
import { lazy, Suspense, useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import { useCan } from '../role.jsx'
import { useUrlTabs } from '../urlTabs.js'
import EditorDrawer, { fileTabs } from './EditorDrawer.jsx'
import EditorVersions from './EditorVersions.jsx'
import RawEditor from './RawEditor.jsx'
import Tabs from './Tabs.jsx'
import { useToast } from './Toast.jsx'

// Editor id -> its lazily loaded body. Each body renders EditorDrawer itself,
// so it can put its own buttons in the frame.
const BODIES = {
  types: lazy(() => import('../pages/TypesEditor.jsx')),
  'tp-price': lazy(() => import('../pages/TraderPlusPriceEditor.jsx')),
}

// Is this editor id one that opens here?
export function isBespokeDrawer(id) {
  return Object.hasOwn(BODIES, id)
}

// Props as ConfigEditorDrawer: `id`, the listing's `title`, the width
// `storageKey` and the page's `onClose`. Keyed by id, so a cross-link from one
// bespoke editor to the other starts the second with its own state.
export default function BespokeEditorDrawer({ id, title, storageKey, onClose }) {
  const Body = BODIES[id]
  const isAdmin = useCan('admin')
  const pushToast = useToast()
  const [meta, setMeta] = useState(null) // { raw, exists, targets }

  useEffect(() => {
    let live = true
    setMeta(null)
    api
      .get(`/api/configs/${id}/meta`)
      .then((m) => live && setMeta(m))
      .catch((err) => live && pushToast(err.message || 'Failed to load config', 'error'))
    return () => {
      live = false
    }
  }, [id, pushToast])

  const tabs = useMemo(() => (meta ? [{ id: 'edit', label: 'Edit' }, ...fileTabs(meta, isAdmin)] : []), [meta, isAdmin])
  // A turned-off tab is no choice, so a ?tab=raw link to it lands on Edit.
  const tabIds = useMemo(() => tabs.filter((t) => !t.disabled).map((t) => t.id), [tabs])
  const { tab, setTab } = useUrlTabs(tabIds)

  const bar = <Tabs tabs={tabs} active={tab} onSelect={setTab} hideSingle />
  const drawer = { title, storageKey, onClose, tabs: bar }
  if (!meta || tab === 'edit') {
    return (
      <Suspense fallback={<EditorDrawer {...drawer} />}>
        <Body key={id} drawer={drawer} />
      </Suspense>
    )
  }
  return (
    <EditorDrawer {...drawer}>
      {tab === 'raw' && <RawEditor key={id} id={id} targets={meta.targets} />}
      {tab === 'versions' && <EditorVersions key={id} id={id} targets={meta.targets} />}
    </EditorDrawer>
  )
}
