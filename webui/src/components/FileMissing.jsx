import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { HiOutlineArrowDownTray, HiOutlineExclamationTriangle } from 'react-icons/hi2'
import { api } from '../api'
import { modLink } from '../crossLinks.js'

// An editor whose file does not exist. It used to be one grey line ("Nothing to
// edit — the underlying file was not found."), which read like an empty file
// and said nothing about the fix. Most of these files belong to a mod that is
// not installed, so the card names the mod and offers the install. The mod
// comes from the editor's group (GET /api/configs/:id/meta) matched against
// Install → Supported (GET /api/mods/supported, app/ops/supported_mods.py), so
// there is no second list of which editor needs which mod.
export default function FileMissing({ editorId }) {
  const [info, setInfo] = useState(null)

  useEffect(() => {
    if (!editorId) return
    let live = true
    Promise.all([api.get(`/api/configs/${editorId}/meta`), api.get('/api/mods/supported')])
      .then(([meta, supported]) => {
        if (!live) return
        const mod = supported.mods.find((m) => m.group === meta.group) || null
        setInfo({ targets: meta.targets || [], mod })
      })
      // The card still says the file is missing; it just cannot name the mod.
      .catch(() => live && setInfo({ targets: [], mod: null }))
    return () => {
      live = false
    }
  }, [editorId])

  // Nothing until the mod is known, or the generic title flashes first.
  if (editorId && !info) return null

  const mod = info?.mod
  const files = info?.targets.length ? (
    <>
      {' '}
      (
      {info.targets.map((t, i) => (
        <span key={t}>
          {i > 0 && ', '}
          <code>{t}</code>
        </span>
      ))}
      )
    </>
  ) : null

  let title = 'This file does not exist yet'
  let body = <>There is nothing to edit: the file{files} was not found on the server.</>
  let action = null

  if (mod && !mod.installed) {
    title = `${mod.group} is not installed`
    body = (
      <>
        This file{files} comes with the {mod.group} mod, and the mod is not on your server.{' '}
        <strong>Install it and start the server once, and the file appears here.</strong>
      </>
    )
    action = mod.picker ? (
      <Link className="btn btn-primary" to="/mods?tab=Install&subtab=Expansion">
        <HiOutlineArrowDownTray aria-hidden="true" />
        Pick {mod.group} modules
      </Link>
    ) : (
      <Link className="btn btn-primary" to={modLink(mod.id)}>
        <HiOutlineArrowDownTray aria-hidden="true" />
        Install {mod.group}
      </Link>
    )
  } else if (mod?.picker) {
    // Expansion writes the settings of its installed modules only, so a
    // restart never creates the file of a module that is not installed.
    title = `${mod.group} has not written this file yet`
    body = (
      <>
        This file{files} does not exist yet. {mod.group} writes it only when the module that uses it is
        installed.{mod.modules?.length ? ` Installed modules: ${mod.modules.join(', ')}.` : ''} If that module is
        installed, start the server once.
      </>
    )
    action = (
      <Link className="btn btn-primary" to="/mods?tab=Install&subtab=Expansion">
        <HiOutlineArrowDownTray aria-hidden="true" />
        Pick {mod.group} modules
      </Link>
    )
  } else if (mod) {
    title = `${mod.group} has not written this file yet`
    body = (
      <>
        {mod.group} is installed, but this file{files} does not exist yet. The mod writes it the
        first time the server starts with it loaded: start the server once, and it appears here.
      </>
    )
  }

  return (
    <div className="card config-missing-mod file-missing" role="alert">
      <HiOutlineExclamationTriangle className="file-missing-icon" aria-hidden="true" />
      <div className="file-missing-text">
        <h2>{title}</h2>
        <p>{body}</p>
      </div>
      {action}
    </div>
  )
}
