// Minimal inline SVG sparkline — no chart library per Stage 7 plan.
export default function Sparkline({ values, width = 200, height = 40, stroke = '#5b9dd9' }) {
  if (!values || values.length === 0) {
    return <span className="text-muted">No data</span>
  }
  const min = Math.min(...values)
  const max = Math.max(...values)
  const range = max - min || 1
  const step = values.length > 1 ? width / (values.length - 1) : 0
  const points = values
    .map((v, i) => {
      const x = i * step
      const y = height - ((v - min) / range) * height
      return `${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')

  return (
    <svg className="sparkline" viewBox={`0 0 ${width} ${height}`} width={width} height={height}>
      <polyline fill="none" stroke={stroke} strokeWidth="1.5" points={points} />
    </svg>
  )
}
