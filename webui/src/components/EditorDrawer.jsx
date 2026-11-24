// The Configs page's editor drawer frame: one SideDrawer setup and one header
// ("?" intro tip, docs link, the editor's own buttons) for every editor that
// opens on /configs/:id. ConfigEditorDrawer (the registry editors) and the two
// bespoke editors (Types.xml, TraderPlus Prices; plans/91) both render it, so
// the drawers look and size the same.
import SideDrawer from './SideDrawer.jsx'
import { PageIntroTip } from './PageIntro.jsx'

// The Raw and Versions tabs of an editor's drawer, from its /meta answer
// (`raw`, `exists`). Raw is the file itself, passwords included, so it is
// admin-only (app/web/permissions.py). Versions lists the file's saved
// versions, where what a save removed is put back. A file that is not on the
// server yet (its mod is not installed) has neither: both tabs show, turned
// off, with the reason.
export function fileTabs(meta, isAdmin) {
  const off = meta.exists === false ? { disabled: true, tip: 'The file does not exist on the server yet.' } : {}
  const t = []
  if (meta.raw && isAdmin) t.push({ id: 'raw', label: 'Raw', ...off })
  if (meta.raw) t.push({ id: 'versions', label: 'Versions', ...off })
  return t
}

// Props: `title`, `storageKey` (the drawer's width key) and `onClose` (the
// page's navigate back to the grid) come from ConfigsPage. `help` is the intro
// text and `helpPage` the docLinks.json key of its docs line. `actions` are
// the editor's header buttons, shown before the tip. `tabs` is a tab bar shown
// above the body (BespokeEditorDrawer's Edit/Raw/Versions). Without `children`
// the frame is the empty drawer an editor shows while it loads.
export default function EditorDrawer({ title, storageKey, onClose, help, helpPage, actions = null, tabs = null, children }) {
  return (
    <SideDrawer
      storageKey={storageKey}
      onClose={onClose}
      maximizable
      defaultWidth={760}
      className="side-drawer-editor"
      title={title}
      actions={
        (actions || help || helpPage) && (
          <>
            {actions}
            <PageIntroTip text={help} page={helpPage} label={`About ${title}`} />
          </>
        )
      }
    >
      {tabs}
      {children}
    </SideDrawer>
  )
}
