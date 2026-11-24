// The one full-page loading state: an accent ring turning above a muted
// label, centered in the free space. It fills a flex parent (the map editor's
// .content-bleed column, the .me-editor row) and keeps a minimum height in a
// plain block, so the label never sits at the top edge. `screen` fills the
// whole viewport, for the sign-in check before the app shell exists.
// A loading line inside a panel or a list stays a PanelNote.

export default function LoadingState({ label = 'Loading…', screen = false }) {
  return (
    <div className={screen ? 'loading-state is-screen' : 'loading-state'} role="status" aria-live="polite">
      <span className="loading-state-ring" aria-hidden="true" />
      <span>{label}</span>
    </div>
  )
}
