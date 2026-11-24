import ModReportCard from './ModReportCard.jsx'

// The editors this card sits above, by id. Not `id.startsWith('expansion-ai')`:
// `expansion-airdrop` starts with that too (plans/64).
export const EXPANSION_AI_EDITORS = new Set([
  'expansion-ai',
  'expansion-ai-patrols',
  'expansion-ai-locations',
  'expansion-ai-patrol-list',
])

// Shown above Expansion AI's own editors (plans/64): what Expansion would skip,
// ignore or quietly change in its patrol file at boot — unknown factions and
// loadouts, patrols with no AI or no waypoints, load-balancing entries that
// never apply. The checks are app/ops/expansion_ai.py; a file as Expansion
// writes it shows nothing to fix.
export default function ExpansionAiCard() {
  return (
    <ModReportCard
      endpoint="/api/expansion-ai"
      title="Expansion AI patrols"
      notInstalled="Expansion AI is not installed, or it is disabled. Its patrols are checked once the AI module is on the mod list."
      empty="Nothing in the patrol file looks off."
      summary={(report) => (
        <>
          {report.file ? (
            <>
              Patrols in <code>{report.file}</code>
            </>
          ) : (
            'No mission folder to look in'
          )}
          {' · '}
          {report.patrols} patrol{report.patrols === 1 ? '' : 's'}
          {report.object_patrols ? `, ${report.object_patrols} on objects` : ''}
          {' · '}
          {report.factions.length
            ? `${report.factions.length} faction${report.factions.length === 1 ? '' : 's'} installed`
            : 'factions unknown'}
        </>
      )}
    />
  )
}
