import ModReportCard from './ModReportCard.jsx'

// Shown above every RadZone editor (plans/48 B): what looks off in its three
// files — zones off the map or still at Chernarus' airfields on another map,
// square zones with unset or swapped corners, crates outside their zone or
// naming no loot list. The checks are app/ops/zone_mods.py; RadZone's source
// is not public, so they read the files' shape and say "probably" where the
// conclusion is inference.
export default function RadZoneCard() {
  return (
    <ModReportCard
      endpoint="/api/radzone"
      title="RadZone"
      notInstalled="RadZone is not installed, or it is disabled. These editors open its files once the mod has written them on a server boot."
      empty="Nothing in these files looks off."
      summary={(report) => (
        <>
          Files in <code>{report.folder}</code>
          {' · '}
          {report.zones.length} zone{report.zones.length === 1 ? '' : 's'}
        </>
      )}
    />
  )
}
