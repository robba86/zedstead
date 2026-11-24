// A square icon-only button (.btn-icon). The label names it for screen
// readers and shows as its tooltip. With `href` it renders a link instead,
// and `danger` turns its hover red (a removal).
import { tooltipAttrs } from './Tooltip.jsx'

export default function IconButton({ icon: Icon, label, tip = label, danger = false, href, ...rest }) {
  const props = {
    className: `btn btn-icon${danger ? ' btn-icon-danger' : ''}`,
    'aria-label': label,
    ...tooltipAttrs(tip),
    ...rest,
  }
  const glyph = <Icon aria-hidden />
  return href ? (
    <a href={href} {...props}>
      {glyph}
    </a>
  ) : (
    <button type="button" {...props}>
      {glyph}
    </button>
  )
}
