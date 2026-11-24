// The credit a cleared map's maker asked for (registry.CLEARED_MAPS), shown
// while their image or their heights are drawn. Never drop it: it is a
// condition of their permission (plans/65).
export default function MapCredit({ credit }) {
  return (
    <div className="me-map-credit">
      {credit.text}
      {credit.url && (
        <>
          {' · '}
          {credit.link_label ? `${credit.link_label}: ` : ''}
          <a href={credit.url} target="_blank" rel="noopener noreferrer">
            {credit.url.replace(/^https?:\/\//, '')}
          </a>
        </>
      )}
    </div>
  )
}
