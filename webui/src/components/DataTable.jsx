import { useLayoutEffect, useRef } from 'react'
import Tooltip from './Tooltip.jsx'
import PanelNote from './PanelNote.jsx'
import ScrollTable from './ScrollTable.jsx'

// Minimal generic table: columns = [{ key, label, help?, header?, render?(row), className? }], rows = [...].
// `header` is a node that replaces the label in the column head (a tick-all checkbox).
// No sorting/paging — callers filter/sort rows before passing them in.
// `cards` shows each row as a card on a phone (index.css .table-cards): every
// cell carries its column's name (data-label) for the hidden header, a
// column with className 'cell-name' heads the card, and one with
// 'cell-actions' (the row's buttons) sits beside it. Neither class changes
// the wide table.
// `Row` swaps the plain <tr> for a component taking { row, children } (the
// cells) — the Mods page's sortable load-order rows. Pass a module-level
// component: a new one per render would remount every row.
// `cards="fit"` also shows the cards on a wide screen whenever the table is
// wider than its box (useFitCards), so no column hides off the right edge.
// `cardsBelow` (px, with cards="fit") shows the cards on every screen up to
// that width too, fit or not: the Mods page's Installed cards read better
// than a squeezed table up to 1024px.
// `scroll` wraps the table in ScrollTable: a long, wide table keeps its
// sideways scrollbar at the window's bottom edge (the Mods page's Installed).
export default function DataTable({ columns, rows, rowKey, emptyMessage = 'Nothing to show.', Row, cards = false, scroll = false, cardsBelow = 0 }) {
  const tableRef = useRef(null)
  useFitCards(cards === 'fit' && rows.length > 0, tableRef, rows, cardsBelow)
  if (!rows.length) return <PanelNote>{emptyMessage}</PanelNote>
  return (
    <TableWrap scroll={scroll} className={cards ? 'table-cards' : ''}>
      <table ref={tableRef} className="data-table">
        <thead>
          <tr>
            {columns.map((col) => (
              <th key={col.key}>
                {col.header ?? <Tooltip text={col.help}>{col.label}</Tooltip>}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const cells = columns.map((col) => (
              <td key={col.key} className={col.className} data-label={col.label || undefined}>
                {col.render ? col.render(row) : row[col.key]}
              </td>
            ))
            return Row ? (
              <Row key={rowKey(row)} row={row}>
                {cells}
              </Row>
            ) : (
              <tr key={rowKey(row)}>{cells}</tr>
            )
          })}
        </tbody>
      </table>
    </TableWrap>
  )
}

// Puts .cards-fit on the table's box while the table, laid out as a table,
// is wider than the box. Each check drops the class first, so it measures the
// table and not the cards, and sets it again before the browser paints. The
// class is the DOM's, not React's: React never re-renders the box's
// className, so it never wipes it. It runs again when the box or the table
// changes size, and when the rows change (a longer name, a new column value).
// With `below` (px) a window up to that width always gets the cards.
export function useFitCards(on, tableRef, rows, below = 0) {
  useLayoutEffect(() => {
    const table = tableRef.current
    const wrap = table?.parentElement
    if (!on || !wrap) return undefined
    const narrow = below ? window.matchMedia(`(max-width: ${below}px)`) : null
    const check = () => {
      wrap.classList.remove('cards-fit')
      wrap.classList.toggle('cards-fit', !!narrow?.matches || wrap.scrollWidth > wrap.clientWidth + 1)
    }
    check()
    // From the observer, the check waits for the next frame: a switch between
    // table and cards resizes the observed boxes, and doing it inside the
    // callback raised "ResizeObserver loop completed with undelivered
    // notifications".
    let frame = 0
    const ro = new ResizeObserver(() => {
      if (!frame) frame = requestAnimationFrame(() => {
        frame = 0
        check()
      })
    })
    ro.observe(wrap)
    ro.observe(table)
    narrow?.addEventListener('change', check)
    return () => {
      if (frame) cancelAnimationFrame(frame)
      ro.disconnect()
      narrow?.removeEventListener('change', check)
      wrap.classList.remove('cards-fit')
    }
  }, [on, tableRef, rows, below])
}

// The table's sideways scroll box: a plain .data-table-wrap, or ScrollTable.
// Module level, so a re-render never remounts the rows.
function TableWrap({ scroll, className, children }) {
  if (scroll) return <ScrollTable className={className}>{children}</ScrollTable>
  return <div className={`data-table-wrap ${className}`.trim()}>{children}</div>
}
