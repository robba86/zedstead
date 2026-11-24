// Loading placeholders for the dashboard tiles.
//
// A tile that fetches must render one of these while its first request is in
// flight, and keep `null` for "failed / not configured" only. Rendering null
// while loading is what made the dashboard pop in card by card: SortableCard
// decides a tile is empty by looking for a rendered `.card`, so every tile was
// briefly dropped out of the grid, out of the sort order, and listed as
// "no data" in the Add-tile menu on each visit — then the layout jumped as the
// answers landed.
//
// The bars mimic the real body's shape (a stat row over list rows) so swapping
// in the content moves nothing.

export function SkeletonLine({ width = '100%', height }) {
  return <span className="skeleton skeleton-line" style={{ width, height }} />
}

// A short stack of bars for card bodies that are not a stat row + list
// (RunCommandCard's command block, ServiceSetupCard's buttons).
export function SkeletonLines({ count = 3, className = '', label = 'Loading…' }) {
  return (
    <div className={`skeleton-lines${className ? ` ${className}` : ''}`} role="status" aria-label={label}>
      {Array.from({ length: count }, (_, i) => (
        <SkeletonLine key={i} width={`${92 - i * 14}%`} />
      ))}
    </div>
  )
}

export default function TileSkeleton({ stats = 0, lines = 3, label = 'Loading…' }) {
  return (
    <div className="tile-skeleton" role="status" aria-label={label}>
      {stats > 0 && (
        <div className="tile-stats" aria-hidden="true">
          {Array.from({ length: stats }, (_, i) => (
            <div className="tile-stat" key={i}>
              <SkeletonLine width="32px" height="17px" />
              <SkeletonLine width="48px" height="9px" />
            </div>
          ))}
        </div>
      )}
      {lines > 0 && (
        <ul className="tile-list" aria-hidden="true">
          {Array.from({ length: lines }, (_, i) => (
            <li key={i}>
              <SkeletonLine width={`${68 - i * 9}%`} />
              <SkeletonLine width="40px" />
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
