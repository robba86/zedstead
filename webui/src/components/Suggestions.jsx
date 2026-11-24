// The dropdown under a text box that suggests values, such as a class name or
// an event name. One compact ListRow per item: the active one is selected, a
// dimmed one is muted. An item with a `group` gets a sticky header when the
// group changes, and `more` is a line at the end ("12 more, keep typing").
// The text box owns the typing and the arrow keys. A mousedown on a row
// chooses it and keeps the focus in the box, so its onBlur does not race.

import { Fragment } from 'react'
import ListRow from './ListRow.jsx'

export default function Suggestions({ items, activeIndex, onChoose, more }) {
  let lastGroup = null
  return (
    <div className="dialog-suggestions">
      {items.map((item, index) => {
        const header = item.group && item.group !== lastGroup ? item.group : null
        lastGroup = item.group
        return (
          <Fragment key={item.key ?? item.name}>
            {header && <div className="dialog-suggestion-group">{header}</div>}
            <ListRow
              compact
              className="dialog-suggestion"
              name={item.name}
              meta={item.meta}
              selected={index === activeIndex}
              dimmed={item.dimmed}
              onMouseDown={(e) => {
                e.preventDefault()
                onChoose(index)
              }}
            />
          </Fragment>
        )
      })}
      {more && <div className="dialog-suggestion-more">{more}</div>}
    </div>
  )
}
