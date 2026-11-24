// The one boxed notice that tells which of two states is live, such as which
// player-spawn system the server uses (webui/DESIGN.md "State notice").
// `active` tints it with the accent when the non-default state is in charge.
// A plain hint is a PanelNote instead, never this box.

export default function StateNotice({ active = false, children }) {
  return <div className={active ? 'state-notice is-active' : 'state-notice'}>{children}</div>
}
