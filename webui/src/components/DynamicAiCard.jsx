import ModReportCard from './ModReportCard.jsx'
import FoldCaret from './FoldCaret.jsx'

// Shown above every Dynamic AI editor (plans/47 C). It holds two things the
// mod never tells the admin anywhere else:
//   - what zsm finds in the files: the mod's own load rules restated
//     (app/ops/dynamic_ai.py, the same findings behind the Doctor's row), in the
//     log analyzer's shape so LogFindings renders them;
//   - what the mod said on its last boot: the [Spatial …] lines of the newest
//     Expansion log (D5). A line printed on every first boot is marked expected.
// Loading, reloading and the findings list are ModReportCard's; this adds the
// factions line and the last boot.
export default function DynamicAiCard() {
  return (
    <ModReportCard
      endpoint="/api/dynamic-ai"
      title="Dynamic AI"
      notInstalled="Dynamic AI is not installed, or it is disabled. These editors open its files once the mod has written them on a server boot."
      empty="Nothing in these files makes the mod skip or change a value."
      summary={(report) => (
        <>
          Files in <code>{report.folder}</code>
          {' · '}
          {report.factions.length
            ? `${report.factions.length} factions registered by the installed mods`
            : 'no factions could be read from the installed mods'}
          {report.ai_log === false && ' · Expansion AIGeneral logging is off'}
        </>
      )}
    >
      {(report) => <LastBoot report={report} />}
    </ModReportCard>
  )
}

function LastBoot({ report }) {
  const boot = report.last_boot
  const problems = report.findings.filter((f) => f.severity !== 'notice')
  return (
    <>
      {problems.length === 0 && report.findings.length > 0 && (
        <p className="text-muted">Only notes above; nothing here stops AI from spawning.</p>
      )}
      <details className="dynamic-ai-boot">
        <summary>
          <FoldCaret />{' '}
          {boot
            ? `Last boot (${boot.file}): ${
                boot.enabled === true
                  ? 'the mod enabled itself'
                  : boot.enabled === false
                    ? 'the mod switched itself off'
                    : 'the mod did not say whether it enabled itself'
              }${boot.first_boot ? ', first boot' : ''}`
            : 'No Expansion log yet — start the server once'}
        </summary>
        {boot && boot.lines.length === 0 && (
          <p className="text-muted">
            That log has no [Spatial …] lines. The mod writes them only while Expansion&apos;s
            AIGeneral logging is on.
          </p>
        )}
        {boot && boot.lines.length > 0 && (
          <>
            {boot.total > boot.lines.length && (
              <p className="text-muted">
                The last {boot.lines.length} of {boot.total} lines.
              </p>
            )}
            <div className="data-table-wrap">
            <table className="data-table">
              <tbody>
                {boot.lines.map((ln, i) => (
                  <tr key={i}>
                    <td className="btn-nowrap">{ln.time}</td>
                    <td className="btn-nowrap">{ln.tag}</td>
                    <td>
                      {ln.message}
                      {ln.expected && <span className="text-muted"> (expected on a first boot)</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            </div>
          </>
        )}
      </details>
    </>
  )
}
