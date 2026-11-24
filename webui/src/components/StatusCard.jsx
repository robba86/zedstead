export default function StatusCard({ label, value, detail, tone }) {
  return (
    <div className={`status-card${tone ? ` status-card-${tone}` : ''}`}>
      <div className="status-card-label">{label}</div>
      <div className="status-card-value">{value}</div>
      {detail && <div className="status-card-detail">{detail}</div>}
    </div>
  )
}
