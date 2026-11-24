import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import { useWs } from '../ws'
import LogFindings from './LogFindings.jsx'
import { SkeletonLines } from './Skeleton.jsx'

// The card above a pre-configured mod's editors (Dynamic AI, RadZone,
// ToxicZone): what zsm finds in the mod's files, in the log analyzer's finding
// shape so LogFindings renders them. The same findings are behind the mod's Doctor row. Reloads
// when a config is saved or the mod list changes, so fixing a finding makes it
// go away.
//
// `summary(report)` is the one-line context under the title; `children(report)`
// is anything a mod adds below the findings (Dynamic AI: its last boot).
export default function ModReportCard({ endpoint, title, notInstalled, empty, summary, children }) {
  const [report, setReport] = useState(null)
  const [failed, setFailed] = useState(false)

  const load = useCallback(async () => {
    try {
      setReport(await api.get(endpoint))
      setFailed(false)
    } catch {
      setFailed(true)
    }
  }, [endpoint])

  useEffect(() => {
    load()
  }, [load])

  useWs(
    useCallback(
      (msg) => {
        if (msg.type === 'configs_changed' || msg.type === 'mods_changed') load()
      },
      [load]
    )
  )

  if (failed) return null
  if (!report) {
    return (
      <div className="card">
        <SkeletonLines count={3} />
      </div>
    )
  }
  if (!report.installed) {
    return <p className="banner banner-info">{notInstalled}</p>
  }

  return (
    <div className="card">
      <h2>{title}</h2>
      {summary && <p className="text-muted">{summary(report)}</p>}
      {report.findings.length ? (
        <LogFindings findings={report.findings} showSamples={false} />
      ) : (
        <p className="text-muted">{empty}</p>
      )}
      {children && children(report)}
    </div>
  )
}
