import Tooltip from './Tooltip.jsx'

// The one tab bar (webui/DESIGN.md "Tabs"). The parent owns the active tab:
// a page passes `useUrlTabs`'s `tab` and `setTab`, an editor its own state.
//
// `tabs` is a list of names, or of `{ id, label?, disabled?, tip? }` when a
// tab has its own label or can be disabled; a disabled tab's `tip` says why.
// `renderLabel(name)` draws a name's label, for a tab that carries a Beta pill
// or a tooltip. `sub` draws the smaller bar under a page's tabs.
// `hideSingle` draws nothing for one tab, so an editor with one view looks
// the same as before it had tabs.
export default function Tabs({ tabs, active, onSelect, sub = false, hideSingle = false, renderLabel }) {
  if (hideSingle && tabs.length <= 1) return null
  const items = tabs.map((t) => (typeof t === 'string' ? { id: t } : t))
  return (
    <div className={sub ? 'tabs subtabs' : 'tabs'} role="tablist">
      {items.map((t) => {
        const button = (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={t.id === active}
            className={`tab${t.id === active ? ' tab-active' : ''}`}
            disabled={t.disabled}
            onClick={() => onSelect(t.id)}
          >
            {t.label ?? (renderLabel ? renderLabel(t.id) : t.id)}
          </button>
        )
        // A disabled button fires no mouseenter, so its reason sits on a wrapper.
        return t.disabled && t.tip ? (
          <Tooltip key={t.id} className="bare" text={t.tip}>
            {button}
          </Tooltip>
        ) : (
          button
        )
      })}
    </div>
  )
}
