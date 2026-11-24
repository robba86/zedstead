// The headline numbers on a dashboard tile: one row of label/value pairs.
//
// Shared by the Players/Bans/Backups/Mods/Schedule/Economy tiles so they read
// as one family instead of six hand-rolled layouts. `tone` is the same
// vocabulary the status cards use (good/warning/danger/muted).
export default function TileStats({ items }) {
  return (
    <div className="tile-stats">
      {items.map((item) => (
        <div key={item.label} className="tile-stat">
          <span className={`tile-stat-value${item.tone ? ` tile-stat-${item.tone}` : ''}`}>
            {item.value}
          </span>
          <span className="tile-stat-label">{item.label}</span>
        </div>
      ))}
    </div>
  )
}
