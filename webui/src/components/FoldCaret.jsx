import { HiChevronRight } from 'react-icons/hi2'

// The one fold chevron (webui/DESIGN.md "Section header"): points right while
// closed and turns down when open. A "▸" text glyph drew at a different size
// in every font and read as part of the item icon beside it.
// Inside a <details> summary, leave `open` out: CSS turns it with the
// details' own open state.
export default function FoldCaret({ open }) {
  const cls = ['fold-caret', open && 'fold-caret-open'].filter(Boolean).join(' ')
  return <HiChevronRight aria-hidden="true" className={cls} />
}
