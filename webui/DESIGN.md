# webui/DESIGN.md: one look for each part

This file names the one part to use for each kind of thing on a page. The same kind of thing looks and behaves the same on every page. When you build something new, take the part from this file. Never start a new class family for a list, a header, a count, a row button or a help line.

The parts live in `webui/src/components/`, and their sizes are the "Parts" tokens in `index.css`. The panel page `/design` (`pages/DesignPage.jsx`) shows every part in every state. Its Components tab (`pages/DesignComponents.jsx`) draws every generic component, with the old look-alikes beside the part that replaces them. `tests/test_design_parts.py` fails on a new CSS class that ends in `-row`, `-item`, `-count` or `-badge`, and on a component file that the Components tab neither draws nor lists.

## Page frame

- Use: `.info-bar` and `.action-bar` at the top, `PageIntro` under the title, `.card` for each block.
- Headings: H1 is the page title. H2 is a card title in `.card-header`. H3 is a heading inside a card, at 15px.
- An `.action-bar` button gets a leading icon with `aria-hidden` and no class, and never `.btn-small` (`webui/CLAUDE.md`).
- Never: a page-only heading size, or a page-only card (`.map-card`, `.config-card` are the old exceptions).

## Section header

- Use: `ListSection`. Props: `title`, `lead` (a `RowDot` before the title), `count` (a number or `{value, unit}` parts), `actions`, `link`, `foldable`, `sticky` (the header stays at the top while the list scrolls, below `--list-sticky-top`), `open` and `onOpenChange`, `defaultOpen`.
- Look: small caps, muted, one height. The count sits right after the title. Header actions and the link out sit at the right end.
- Fold: `FoldCaret` points right when closed and down when open. Pass `up` for a panel that opens upward, such as the Activity bar. In a `<details>` summary, leave `open` out and CSS turns it. In the Map Editor, pass `open` and `onOpenChange` from `useStoredOpen(key, defaultOpen)`, never a URL key.
- Never: a `▸` or `▾` text glyph, a darker band behind one tab's headers, or a bold row that stands in for a header.

## List row

- Use: `ListRow`. Slots: `lead`, `name`, `sub` (an optional second line), `meta` (right side), `actions`. Flags: `selected`, `dimmed`, `disabled`, `compact`, `mono` (the name in the code font, for a command or a class name), and `onClick` for a row that is a keyboard target. `nameTip` puts a tooltip on the name. `as="li"` puts the row in a list.
- Wrap: `wrap` lets the name and `sub` wrap for a check, a fact or a finding with a long detail: the label is the `name`, the detail is the `sub`. A severity is `SeverityDot` in `lead`, never a bar on the left.
- Look: one height for a one-line row and one for a two-line row. No divider between rows. A small radius.
- States: hover is `--bg-hover`. Selected is `--bg-hover` with an accent bar on the left, as `.data-table tr.row-active`. Dimmed is muted text (for example, zero points). Disabled is struck through. Focus shows the focus ring.
- Never: a bordered card for one tab's rows, a divider under each row, or a row height per tab.

## Count

- Use: `CountBadge`. Props: `value`, or `parts`, a list of `{value, unit}`.
- Look: a pill on `--bg-hover`, muted text, normal case also inside a small caps header (decision D4 in plan 89).
- Units: parts are joined with ` · `, for example `12 events · 972 pts`.
- Never: a count in brackets `(300)`, a bare number used as a count, a comma between units, or a count inside a filter chip.
- A coordinate, a price or a nominal is not a count. It goes in the row's `meta` slot as muted text.

## Badge

- Use: `.status-pill` for a state (running, stopped, failed). Add `.status-pill-plain` when the text is a phrase, such as `2 warnings`, so it keeps its own capitals. A pill that filters or opens what it counts is a `<button className="status-pill …">`.
- Use: `RowTag` from `ListRow.jsx`, one small square tag for a label on a row, such as `cooldown` or `disabled`.
- Use: `DefaultPill` (`.default-pill`) for a field that is on its server default, and `atDefault(value, default)` to decide it. `UnitInput`'s `defaultHint` shows the pill at the left of an empty box, with the default after it as the placeholder, and keeps it when the box holds the default itself. `Select`'s `defaultHint` shows it in the closed field and writes `(default)` after that option in the open list. `Switch`'s `badge` takes it after the label. With other text (`<DefaultPill>Before 1.30</DefaultPill>`) it marks a serverDZ.cfg key a newer DayZ replaced (the field's `replaced_in`). A slider (`RangeField`) shows the pill in its scale, under the default's number, set value or not. A field with `default_is_example` (only the value in Bohemia's example file) gets no pill: pass `pillDefault(field)`, and word its tooltip with `notInFileText`. Never write `(default)` into a placeholder or an option label of your own.
- Never: a raw hex colour in a badge. A status colour is `color-mix` on a token.

## Row button

- Use: `RowAction` for an icon action on a row or a section header. Props: `icon`, `label`, `onClick`, `danger`, `active`, `disabled`.
- Look: bare and muted, a border on hover, one size. `danger` turns red on hover.
- `label` is the screen reader name and the tooltip (`tooltipAttrs`). Never a native `title=`.
- Use: `btn btn-ghost btn-small` for a text action on a row or in a card.
- Never: a boxed or a filled icon button on a row, or a button that stays hidden until hover (a touch screen has no hover).

## Row menu

- Use: `RowMenu` (`.menu-list`, `.menu-item`) for a row's ⋯ menu, a framed small ghost button with a large ⋯, when a row has more actions than it can show. The row keeps its state controls (a switch) and any action that waits on the user (an Update).
- Items: `{key, label, onClick, tip, disabled, danger, checked, group, divider, icon}`. `checked` makes a radio set, such as Loads for. `icon` puts a glyph in the check's slot, in line with the ticks, when the row also shows that action as a `RowAction` with the same icon (Auto configuration).
- Never: a second popup menu class. The instance menu shares the look.

## Leading mark

- Use: in the `lead` slot of `ListRow`, one of these:
  - a dot: `RowDot` from `ListRow.jsx`, one 8px circle, with `color` as a prop, or a `className` whose rule sets a Map Editor map-data colour in `mapeditor.css`,
  - an item icon: `ItemIcon`,
  - a checkbox, only to pick rows for a batch action,
  - a number badge: `RowNumber` from `ListRow.jsx`, with `className` and `tip`, for example the trader number.
- Never: a dot class per tab. The colour is the only thing that changes.

## Help line

- Use: `PageIntro` at the top of a page, and `PageIntro sub` under a tab bar or at the top of a form section.
- Use: `PanelNote` everywhere else. Props: `children`, `link` (an optional `{to, label, tip}` at the end), `warn` for a warning colour.
- Look: left-aligned, muted, one size.
- Use: `HelpButton` only for help that is too long for one line.
- Never: centered help text, a filled info box for a plain hint, or a round `?` beside a filter box.

## Form field

- Use: `FormField` for one labelled control in a card form. Props: `label`, `optional`, `hint`, `error`, `action` (a small button at the right of the control, such as Clear), `as="div"` for a group of buttons.
- Look: the label above in the text colour, the control full width, then one muted hint. A wrong value shows a red border and the message in place of the hint.
- Validation: mark the empty required fields when Save is pressed, never while the admin types. The message on a field goes when that field changes. The server makes the same check and answers 422 with `detail.fields`, so the form marks what the server refused too (`OffsiteRemoteCard.jsx`, `offsite_ops.form_problems`).
- Mark the few optional fields with `optional`, never every required one with a star.
- A number with a short closed range is `RangeField`: a slider with a tick per step and a number box. `sliderScale(field)` decides: up to 30 whole steps, or a 0.0-1.0 decimal shown as 0-100 %. A wider range keeps the plain number box.

## Confirm

- Use: `useConfirm()` for a yes or no question before an action: `if (!(await confirm({ title, message, confirmLabel, danger }))) return` at the top of the handler.
- Use: `ConfirmModal` only when the dialog holds more than a message: an input, a switch, a list of steps, or a Confirm button that waits for something.
- Never: a `confirmX` state and a `<ConfirmModal>` for a plain question.

## State notice

- Use: `StateNotice` for a boxed line that says which of two states is live, for example which player-spawn system the server uses. `active` tints it with the accent when the non-default state is in charge.
- Never: this box for a plain hint. A hint is a `PanelNote`.

## Empty state

- Use: `PanelNote` in a list or a panel. `DataTable`'s `emptyMessage` in a table.
- Look: the same as a help line.
- Never: italic text, or a class per list (`.me-empty`, `.tile-empty`, `.me-collision-empty`, `.me-loot-none` are the old ones), or `.empty-message` (gone: a block is `PanelNote`, and muted text inside a line or a row is `.text-muted`).

## Loading state

- Use: `LoadingState` while a whole page or a lazy chunk loads: an accent ring above a muted label, centered in the free space. `screen` fills the viewport. A loading line inside a panel or a list is a `PanelNote`, and a dashboard tile is a skeleton.
- Never: a bare `Loading…` div with its own class (`.app-loading`, `.page-loading`, `.me-loading` are the old ones).

## Text link

- Use: a plain `Link` or `<a>` inside text, with no colour of its own. The app-wide rule in `index.css` makes it accent blue, visited or not, underlined 2px below the text, and `--accent-hover` (a lighter blue) on hover.
- A class on a link (`.card-link`, `.mod-link`, `.doctor-link`) sets only size and spacing. Inside a warning banner a link takes the banner's colour.
- Never: a link colour per page, `text-decoration: none` on a text link, or an underline only on hover. Buttons, chips, nav rows and breadcrumbs are not text links.

## Link out ("open in editor")

- Use: `LinkOut`. Props: `to` (built by `crossLinks.js`), `label`, and `tip` for a longer tooltip. It has the `RowAction` look. `panels/EditorLink.jsx` is the old Map Editor link until Step 4.
- Place: at the right end of the section header, once per section.
- Use: `.card-link` for a link at the foot of a dashboard card.
- Never: a link out on every row, at the end of a help line, or a URL built by hand (`webui/CLAUDE.md`).

## Link chip

- Use: `.link-chip` on a `Link` (or an `<a>` for an anchor on the same page) for a short link to the page or tab that fixes or explains a line, such as a Doctor check's "Manage mods" or the guide's step links.
- Look: a small accent tag with the button radius (`--btn-radius`), never round, so it does not look like a `.status-pill` badge. After text it keeps a small gap. In a flex row, the row's gap spaces it.
- Never: a chip class per page (`.doctor-chip`, `.runcommand-chip`, `.guide-link` are the old ones), or a chip for "open in editor", which is `LinkOut`.

## Filter box

- Use: `FilterBox` when the list shows "Showing X of Y". `SearchInput` for a box with no counter.
- Place: the box first. Other filter controls (a mod picker, a switch, a chip set) go in one row under it.
- Never: a wrapper class per tab, or a box height per tab.

## Toolbar

- Use: `.action-bar` for page actions. `StickyActionBar` for Save and Discard on a form page.
- Never: a new `*-bar` class for a row of buttons.

## Table

- Use: `DataTable`. `ScrollTable` for a table that scrolls inside a fixed height.
- Never: a list that draws its own table header row (the Loot finder's Building view is the old one).

## Tabs

- Use: `Tabs` (`components/Tabs.jsx`) for every tab bar: a page passes `useUrlTabs`'s `tab` and `setTab`, an editor its own state. `sub` draws the smaller bar under a page's tabs. `Segmented` for a choice inside a section.
- Never: a hand-written `.tabs` loop of buttons, or a new tab bar class.

## Switch

- Use: `Switch` or `FlagSwitch` from `components/FlagSwitch.jsx` for on and off. This is already the one look.
