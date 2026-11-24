// A row's ⋯ menu (webui/DESIGN.md "Row menu"): the actions a row needs now
// and then, so the row itself shows only its state. The trigger is a framed
// ghost button with a large ⋯: a bare RowAction icon was too easy to miss. The list is portaled to
// <body> at a fixed position, since a table that scrolls sideways clips
// anything that leaves its box, and it opens upward when the window has no
// room below. It closes on a pick, Escape, a click outside, a scroll or a
// resize. The arrow keys move between the items and stop there, so a page's
// own arrow-key handler (the Mods load order) sees `defaultPrevented`.
//
// `items`: {key, label, onClick, tip, disabled, danger, checked, group, divider, icon}.
// `checked` (true or false) makes the item one of a radio set, `group` puts a
// heading above the item, `divider` a line, and `icon` (a react-icons
// component) a glyph in the check's slot, the same one the row shows for it. A disabled item stays
// focusable (aria-disabled), so its tooltip can still say why.

import { Fragment, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { HiOutlineCheck, HiOutlineEllipsisHorizontal } from 'react-icons/hi2'
import { tooltipAttrs } from './Tooltip.jsx'

const ITEMS = '[role^="menuitem"]'
const GAP = 4
const EDGE = 8

export default function RowMenu({ label, items }) {
  // null while closed. `up` is undefined until the list is measured.
  const [pos, setPos] = useState(null)
  const boxRef = useRef(null)
  const listRef = useRef(null)
  const isOpen = pos !== null

  function toggle() {
    if (isOpen) return setPos(null)
    const r = boxRef.current.getBoundingClientRect()
    setPos({ top: r.bottom + GAP, right: window.innerWidth - r.right, anchorTop: r.top })
  }

  function close(refocus = false) {
    setPos(null)
    if (refocus) boxRef.current?.querySelector('button')?.focus()
  }

  // Measured once per opening, before paint: flip above the button when the
  // list would run past the window's bottom, then focus the first item.
  useLayoutEffect(() => {
    const list = listRef.current
    if (!pos || pos.up !== undefined || !list) return
    const h = list.offsetHeight
    const up = pos.top + h > window.innerHeight - EDGE && pos.anchorTop - h - GAP > EDGE
    setPos({ ...pos, top: up ? pos.anchorTop - h - GAP : pos.top, up })
    list.querySelector(ITEMS)?.focus()
  }, [pos])

  useEffect(() => {
    if (!isOpen) return undefined
    const outside = (e) => !listRef.current?.contains(e.target) && !boxRef.current?.contains(e.target)
    const onDown = (e) => outside(e) && close()
    const onKey = (e) => e.key === 'Escape' && close(true)
    const onScroll = (e) => !listRef.current?.contains(e.target) && close()
    const onResize = () => close()
    document.addEventListener('pointerdown', onDown)
    document.addEventListener('keydown', onKey)
    window.addEventListener('scroll', onScroll, true)
    window.addEventListener('resize', onResize)
    return () => {
      document.removeEventListener('pointerdown', onDown)
      document.removeEventListener('keydown', onKey)
      window.removeEventListener('scroll', onScroll, true)
      window.removeEventListener('resize', onResize)
    }
  }, [isOpen])

  function onListKey(e) {
    const step = { ArrowDown: 1, ArrowUp: -1 }[e.key]
    if (!step) return
    e.preventDefault()
    const all = [...listRef.current.querySelectorAll(ITEMS)]
    const i = all.indexOf(document.activeElement)
    all[(i + step + all.length) % all.length]?.focus()
  }

  function pick(item) {
    if (item.disabled) return
    close(true)
    item.onClick?.()
  }

  return (
    <span className="row-menu" ref={boxRef}>
      <button
        type="button"
        className="btn btn-ghost btn-small row-menu-btn"
        aria-label={label}
        {...(isOpen ? {} : tooltipAttrs('More actions'))}
        aria-haspopup="menu"
        aria-expanded={isOpen}
        onClick={(e) => {
          // Never also the row's own click.
          e.stopPropagation()
          toggle()
        }}
      >
        <HiOutlineEllipsisHorizontal aria-hidden="true" />
      </button>
      {isOpen &&
        createPortal(
          <div
            ref={listRef}
            className="menu-list"
            role="menu"
            aria-label={label}
            style={{ top: pos.top, right: pos.right }}
            onKeyDown={onListKey}
          >
            {items.map((item) => {
              const radio = item.checked !== undefined
              const Icon = item.icon
              return (
                <Fragment key={item.key}>
                  {item.group && (
                    <div className="menu-group" role="presentation">
                      {item.group}
                    </div>
                  )}
                  <button
                    type="button"
                    role={radio ? 'menuitemradio' : 'menuitem'}
                    aria-checked={radio ? item.checked : undefined}
                    aria-disabled={item.disabled || undefined}
                    className={['menu-item', item.danger && 'is-danger', item.divider && 'menu-divider']
                      .filter(Boolean)
                      .join(' ')}
                    {...tooltipAttrs(item.tip, { keep: true })}
                    onClick={() => pick(item)}
                  >
                    {/* An icon takes the check's slot, so it lines up with the ticks. */}
                    <span className="menu-check">
                      {item.checked ? <HiOutlineCheck aria-hidden="true" /> : Icon && <Icon aria-hidden="true" />}
                    </span>
                    {item.label}
                  </button>
                </Fragment>
              )
            })}
          </div>,
          document.body
        )}
    </span>
  )
}
