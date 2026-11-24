import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { highlightText } from 'prism-react-editor/prism'
import { api } from '../api'
import { useWs } from '../ws'
import { SkeletonLines } from './Skeleton.jsx'
import PanelNote from './PanelNote.jsx'

import '../prismSetup.js'
// The `.token.*` colours live in prism-editor.css, which CodeArea also imports
// — but this card renders earlier in the module graph, so importing it here is
// what puts it in the bundle at all if the editor pages ever go lazy.
// layout.css comes with it, in that order: prism-editor.css overrides its
// `.prism-code-editor` line-height, so it must never be hoisted above it.
import 'prism-react-editor/layout.css'
import '../prism-editor.css'

// Shows the generated DayZServer launch command (identical to what the systemd
// unit / start-server.bat / `zsm supervise` run) plus the "Change in:" chips
// linking to the views that own a piece of it (mods → Mods page, paths/flags/
// port → Run Command editor). The per-part breakdown the card used to list
// under the command is gone: every value in it is already on the page that
// owns it, and the command line above spells the same values out verbatim.
// Reused by the Dashboard and the Run Command editor page. Reloads on
// config/mods changes over the WS. `hideSelf` (the Run Command editor) leaves
// out the chip that links to the editor already open.
const SELF = '/configs/runcommand'

export default function RunCommandCard({ showEdit = false, hideSelf = false }) {
  const [info, setInfo] = useState(null)
  const [failed, setFailed] = useState(false)

  const load = useCallback(async () => {
    try {
      setInfo(await api.get('/api/runcommand'))
      setFailed(false)
    } catch {
      // Only a failure hides the card; until then it shows a skeleton, so the
      // dashboard slot doesn't appear late.
      setFailed(true)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const handleWsMessage = useCallback(
    (msg) => {
      if (msg.type === 'configs_changed' || msg.type === 'mods_changed') load()
    },
    [load]
  )
  useWs(handleWsMessage)

  // Highlighted through the same Prism grammars + `.token.*` theme the code
  // editors use (prismSetup.js / prism-editor.css) — the command is read-only
  // display, so it takes the tokenizer alone, not a CodeArea. `highlightText`
  // escapes the text it wraps, which is what makes the innerHTML safe.
  const html = useMemo(
    () => (info ? highlightText(info.command, 'launch') : ''),
    [info]
  )

  if (failed) return null

  const sources = (info?.sources || []).filter((s) => !hideSelf || s.route.split(/[?#]/)[0] !== SELF)

  return (
    <div className="card runcommand-card">
      <div className="card-header">
        <h2>Run Command</h2>
        {showEdit && (
          <Link to="/configs/runcommand" className="card-link">
            Edit →
          </Link>
        )}
      </div>
      <PanelNote>
        The exact command used to launch the server (systemd unit, start-server.bat
        and <code>zsm supervise</code> all run this). Regenerate service files after
        changing it.
      </PanelNote>

      {info === null ? (
        <div className="runcommand-code">
          <SkeletonLines count={3} label="Building the launch command…" />
        </div>
      ) : (
        <pre className="runcommand-code" dangerouslySetInnerHTML={{ __html: html }} />
      )}

      {info !== null && sources.length > 0 && (
        <div className="runcommand-sources">
          <span className="runcommand-sources-label">Change in:</span>
          {sources.map((s) => (
            <Link key={s.route} to={s.route} className="link-chip">
              {s.label}
            </Link>
          ))}
        </div>
      )}
    </div>
  )
}
