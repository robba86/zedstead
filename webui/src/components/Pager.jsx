import { HiOutlineChevronLeft, HiOutlineChevronRight } from 'react-icons/hi2'

// Previous / "Page X of Y" / Next row under a paged table (Types, TraderPlus
// prices). The parent owns the offset; `extra` is appended to the label.
// Under a ScrollTable (its `footer`), it stays at the bottom of the window
// while the table is in view. The buttons are bare chevrons with an
// aria-label, centred with the label (index.css .pager).
export default function Pager({ page, totalPages, onPrev, onNext, extra = null }) {
  return (
    <div className="action-bar pager">
      <button className="btn btn-ghost" disabled={page <= 1} onClick={onPrev} aria-label="Previous page">
        <HiOutlineChevronLeft aria-hidden="true" />
      </button>
      <span className="pager-label">
        Page {page} of {totalPages}
        {extra && <> ({extra})</>}
      </span>
      <button className="btn btn-ghost" disabled={page >= totalPages} onClick={onNext} aria-label="Next page">
        <HiOutlineChevronRight aria-hidden="true" />
      </button>
    </div>
  )
}
