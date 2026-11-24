import { useEffect, useRef, useState } from 'react'

// A long, wide table that scrolls with the page (one vertical scrollbar, the
// page's), for Types and TraderPlus prices. Two things a plain
// .data-table-wrap cannot do, because its sideways scroll makes it a scroll
// box, and a header row inside one cannot stick to the window:
//  - the header row follows the window's top edge while the table is in view
//    (a translateY set from the page scroll, `--head-y`);
//  - a copy of the sideways scrollbar sits in a footer stuck to the window's
//    bottom edge, kept in step with the table, so a wide table scrolls
//    sideways without going to its last row. `footer` (the Pager) shares it.
// On a phone (840px block in index.css) the rows are cards, so the header
// does not move and the bar copy is hidden.
export default function ScrollTable({ className = '', footer = null, children }) {
  const wrapRef = useRef(null)
  const barRef = useRef(null)
  const [width, setWidth] = useState(0) // the table's full width, 0 = no overflow

  // The bar copy's width, and whether there is anything to scroll at all.
  useEffect(() => {
    const wrap = wrapRef.current
    if (!wrap) return undefined
    const measure = () => setWidth(wrap.scrollWidth > wrap.clientWidth + 1 ? wrap.scrollWidth : 0)
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(wrap)
    if (wrap.firstElementChild) ro.observe(wrap.firstElementChild)
    return () => ro.disconnect()
  }, [])

  // Each scroller moves the other. Setting the same value back fires no
  // second scroll event, so the two cannot loop.
  useEffect(() => {
    const wrap = wrapRef.current
    const bar = barRef.current
    if (!wrap || !bar) return undefined
    bar.scrollLeft = wrap.scrollLeft
    const fromWrap = () => {
      if (bar.scrollLeft !== wrap.scrollLeft) bar.scrollLeft = wrap.scrollLeft
    }
    const fromBar = () => {
      if (wrap.scrollLeft !== bar.scrollLeft) wrap.scrollLeft = bar.scrollLeft
    }
    wrap.addEventListener('scroll', fromWrap, { passive: true })
    bar.addEventListener('scroll', fromBar, { passive: true })
    return () => {
      wrap.removeEventListener('scroll', fromWrap)
      bar.removeEventListener('scroll', fromBar)
    }
  }, [width])

  // The header row's offset: how far the table's top has gone above the
  // window's top edge (under the phone's top bar, if one shows), held inside
  // the table so the header never leaves it.
  useEffect(() => {
    const wrap = wrapRef.current
    if (!wrap) return undefined
    let frame = 0
    const update = () => {
      frame = 0
      const head = wrap.querySelector('thead')
      if (!head) return
      const topbar = document.querySelector('.topbar')
      const top = topbar && topbar.offsetParent !== null ? topbar.getBoundingClientRect().bottom : 0
      const rect = wrap.getBoundingClientRect()
      const max = Math.max(0, rect.height - head.offsetHeight)
      const y = Math.min(max, Math.max(0, top - rect.top))
      wrap.style.setProperty('--head-y', `${y}px`)
      // The header row gets its background only while it follows the
      // window (index.css); resting on the table it is as plain as any table's.
      wrap.classList.toggle('head-stuck', y > 0)
    }
    const schedule = () => {
      if (!frame) frame = requestAnimationFrame(update)
    }
    update()
    window.addEventListener('scroll', schedule, { passive: true })
    window.addEventListener('resize', schedule)
    const ro = new ResizeObserver(schedule)
    ro.observe(wrap)
    return () => {
      if (frame) cancelAnimationFrame(frame)
      window.removeEventListener('scroll', schedule)
      window.removeEventListener('resize', schedule)
      ro.disconnect()
    }
  }, [])

  return (
    <>
      <div ref={wrapRef} className={`data-table-wrap scroll-table ${className}`.trim()}>
        {children}
      </div>
      <div className="scroll-table-foot">
        <div ref={barRef} className="scroll-table-bar" hidden={!width} aria-hidden="true">
          <div style={{ width }} />
        </div>
        {footer}
      </div>
    </>
  )
}
