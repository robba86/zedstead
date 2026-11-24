import IgnoreListInteractive from './components/IgnoreListInteractive.jsx'
import MessagesInteractive from './components/MessagesInteractive.jsx'

// Maps an editor id -> a bespoke React component that is that editor's Edit
// tab instead of the generic form/table (ConfigEditorDrawer renders
// <Component id={id} initialSearch={?q=} />; the component owns its own
// load/save through the same /api/configs/{id} GET/PUT).
//
// Until 2026 Q4 these were a separate "Interactive" tab beside "Basic". Two
// tabs that edit the same file were one too many, so each editor now has one
// Edit view and Raw:
//   - ignorelist: one list with a quick-add pool of common base-building
//     items, which the one-column table could not offer.
//   - messages: messages.xml grouped by type (OnConnect / Repeat / Shutdown,
//     and every other message as its own editable row).
// TraderPlus Banking/SafeZone/Garage and weather left this map: their Edit tab
// is the generic form, which shows list fields with `widget: "pick"` as a
// MultiSelectList, and weather's presets are a card above it.
const INTERACTIVE_VIEWS = {
  ignorelist: IgnoreListInteractive,
  messages: MessagesInteractive,
}

export default INTERACTIVE_VIEWS
