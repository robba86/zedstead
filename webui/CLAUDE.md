# webui/: React frontend rules

The backend half is `app/web/CLAUDE.md`. The Mod XML pages (`ModXmlPage.jsx`, `ManualXmlFiles.jsx`) are the UI over `app/xml/CLAUDE.md`.

## Build and dev

- The built bundle is committed to `app/web/static/`. Every `webui/src` change must be followed by `npm run build` (or `make web-build`) and the `app/web/static/` diff committed in the same change.
- `make web-check` checks the committed bundle against `webui/src`. Never run it in a worktree whose `webui/node_modules` is a symlink: its `npm ci` empties the target. Run `npm run build`, then `git status --porcelain app/web/static`.
- `make dev` runs the backend and Vite. Open port 5173, never 8433, which serves the committed build.
- The `/api` proxy in `vite.config.js` must stay the object form with `changeOrigin: false`. The string form rewrites Host, and `same_origin.py` then refuses every write, the login included.
- Never write an inline script, an `on*=` attribute, a `javascript:` URL, `eval` or a string timer. The CSP (`app/web/security_headers.py`) refuses them only in the built panel, so they work under `make dev`. `tests/test_web_app.py::test_index_html_has_no_inline_script`
- A new `react-icons/<set>` import needs its licence in `webui/licenses/`, which `thirdPartyLicenses()` in `vite.config.js` writes into the bundle.
- There is no frontend test harness, so check in a browser.
- Hand-kept copies change together: `role.jsx` `ROLES` (`app/web/users.py`), `installFlow.jsx` `TRADERPLUS_WORKSHOP_ID` (`mod_ops`), `SchedulerPage.jsx` `TASK_TYPES` and `PAYLOAD_FIELDS` (`app/server/scheduler.py`), ModPage's spawn warning (`spawn_test_ops.count_warning_text`), the step order in `guideSteps.js` (zedstead-site `src/docs/web-ui.md`).

## Routes, instances and roles

- A new `href` or `src` to `/api/…` needs `withBase` from `instance.js`, or it silently reads the startup instance. `api.js` and `ws.js` add it already.
- Storage that belongs to a server gets `instanceKey`. Storage about the browser (theme, sidebar, dashboard layout) and the job caches do not.
- Switching instance is a full page load (`instanceHref` + `location.assign`), never a router navigation.
- A new page needs a lazy `<Route>` in `App.jsx` and an entry in `routes.js`. A static import pulls the page and its heavy deps into the entry chunk. `LoginPage` and `GuideRoute` stay eager on purpose.
- Every `routes.js` entry must carry `minRole`, with no default. `tests/test_routes_roles.py`
- A write control is hidden below the backend's role through `useCan(minRole)`: `moderator` for a route in `permissions.MODERATOR_WRITES`, `admin` for any other write. Nothing catches a missing check.
- The UI role only decides what is shown, since the API refuses. Never poll it.
- Never add a control that disables another user's two-factor sign-in. Recovery is `zsm users totp disable <name>`.
- `api.js` never swallows a 401 on `/api/auth/login`, so the form sees `totp_required`.

## Unsaved changes

- `useUnsavedChanges(dirty)` records a dirty source, and `<UnsavedChangesGuard />` in `ShellRoute` owns the one `useBlocker`. `StickyActionBar` calls the hook, so a form page needs nothing more.
- Edits that survive a tab switch register `{ pageWide: true }`. A page whose tab holds the form must not pass it.
- A page that reloads on a WS event must keep its unsaved edits. `ModsPage` calls `loadOrder({ keepUnsaved: true })` (`mergeUnsavedOrder`).

## URL state

- `?tab=`, `?subtab=` and `?q=` are the app-wide keys. Never invent a synonym. Tab bars go through `useUrlTabs` (`urlTabs.js`), whose `TABS` and `subtabsFor` must be module constants or a `useMemo`.
- `?q=` only seeds a box. `useSearchPrefill` applies it again when the search palette navigates.
- One URL write per handler. `useSearchParams` resolves against the last render, so a second setter drops the first's change. Combine pure mutators in one `update()` (`urlState.js` `setLayerAndTab`, `setMapLayers`, `setLoot`). Never chain two `url.set*` calls.
- A deep-linked card gets a plain `id`, and `useHashTarget(ready)` (`hashTarget.js`) scrolls to it. No per-page scroll code.
- A page's tab list lives in `pageTabs.js`, imported by the page and by `searchSources.js`. `tests/test_web_search.py` fails on a page with its own copy.
- Every cross-page URL is built by `crossLinks.js` (`configLink`, `editorLink`, `modLink`, `mapEventLink`, `spawnTestMapLink`, `MAP_EDITOR_LINKS`). Never hand-concatenate `/configs/…`, `/mods/…` or `/map-editor?…`.

## Map editor (`webui/src/pages/mapeditor/`)

- The editor is the React page at `/map-editor`, never `/visual-editor`.
- Never change the `className` of `.me-map` after mount: Leaflet adds its own classes. A class that changes goes on `.me-map-wrap`.
- Keep `isolation: isolate` on `.me-editor`. `mapeditor.css`
- Guard every map hook on `isMapAlive(map)`.
- Marker metadata goes in `leaflet/markerRegistry.js` (a `WeakMap`).
- Endpoints take `lat`/`lng`. `coords.js` is display-only, so never project on the client for a write.
- A new layer needs a key in `panels/LayerToggles.jsx` and in `urlState.js`'s `LAYER_KEYS`. `tests/test_map_editor_links.py`
- `?event=<name>` is the inbound focus key. `MapEditorProvider` consumes it once and isolates that event, and `url.consumeFocusEvent()` deletes it in one update. `state/urlState.js`
- `LAYER_TABS` in `LayerToggles.jsx` sends a layer ticked on to the tab that edits it, in the same URL update.
- The collision link keys (`collisions`, `types`, `cross-event-overlaps`, `radius-conflicts`) are built by `app/ops/collision_ops.py::build_collision_query` and parsed in `state/urlState.js`. Renaming one breaks the Analysis page's link.
- Leaflet writes tooltips, popups and `divIcon` HTML as `innerHTML`: use `bindTextTooltip`, `() => popupHost` and `` html`…` `` (`leaflet/`). `tests/test_map_editor_tooltips.py`
- A popup number field sends back the loaded value while untouched, through `coords.js::keepUntouched`. Without it a no-op Save moves the value by its rounding.
- A point drag confirms and snaps back on cancel or failure (`confirmPointDrag`). A zone drag saves at once and reloads its layer on failure.
- An event-spawn write goes through `spawnOwner.js` (`spawnPath`). A position a mod owns asks through `CopyIntoManualFile.jsx` first.
- A previewed terrain (not `isActiveTerrain`) draws no mission data. `useResource` resets when its path goes null.
- The map image is laid over `/map-info`'s `bounds` (`useImageOverlay`), in world coordinates like every write. Terrain, roads and buildings stack over it (`overImagePane`).
- Every canvas layer lists `useMapTheme()` in its effect deps, so it redraws on a theme switch. A canvas GridLayer needs the map's `minZoom` and `maxZoom`. `leaflet/canvasTiles.js`
- A collapsible sidebar section stores its open state through `state/useStoredOpen.js`, never in the URL, which is shared in links.
- A new overlay on the map's left edge must read `--me-panel-space`. `mapeditor.css`
- `mapeditor.css` loads later as a lazy chunk and wins ties. The editor's form-control aliases and any class used outside the editor live in `index.css`.
- `mapeditor.css` uses the app's theme tokens. Its only raw hex values are the map-data colours in its header, duplicated in `leaflet/*.js` and `icons/iconRules.js`, so change both or neither.
- Links to config editors sit in the sidebar (`panels/EditorLink.jsx`, `panels/RelatedLinks.jsx`), since full-window mode hides the breadcrumb. `ConfigEditorDrawer`'s View on map comes from `MAP_EDITOR_LINKS[id]`, or `mapEventLink()` for one event.
- `panels/TraderSystemsNote.jsx` must never say whether running TraderPlus and Expansion Market together works.

## Config editors

- Anything in the `ConfigEditorDrawer` shell that holds file data must load when its tab becomes active, never once on mount.
- `configs_changed` is a hint to look. `useConfigSync` (`configSync.js`) does nothing on an identical payload, applies a change silently when clean, and shows "Changed on disk" when dirty. `apply` must not blank the view first.
- `ConfigEditorDrawer` passes `key={id}` to FormEditor and TableEditor, or a new link's params are ignored.
- The Raw target comes from `useRawTarget.js`, or a previous file's target name answers 400.
- Filtering is display-only (`fuzzy.js`, `FilterBox`). It must never touch values, rows or the dirty flag.
- A closed list keeps a hand-edited bad value through `withCurrent(choices, current)`. `components/Select.jsx`
- A flag switch saves `0`/`1`, never `true`/`false`. The key name decides (`format.isFlagKey`, `NOT_FLAG`), never the value alone.
- An untouched float is never sent, so the file keeps its own text. `format.displayFloat`
- `TableEditor` must keep `row.choices` when it copies rows into state.
- An editor that reshapes rows must send every value it loaded back on save. `MessagesInteractive`
- `EDITORS` copy in `pageHelp.js` comes from `dayz-docs/`. Check the spec before changing a number. Expansion entries carry no defaults, units or field names.

## Jobs, drawer and downloads

- A page never holds a job id. It calls `startJob` or `trackJob` from `jobs.jsx`, which toasts on completion.
- Only finished jobs are cached (`persist.js`, `ACTIVITY_TTL_MS`). Never declare a second TTL.
- A job's Cancel becomes Kill on the second click (`isCancelPending`, `killJob`). `ActivityDrawer.jsx`
- Every Activity drawer pane needs `flex-shrink: 0`, or collapsed panes squash to zero height.
- A new job kind gets its icon in `JOB_ICONS`. `ActivityDrawer.jsx`
- A file download is `<a download="<name>">`, never fetch-to-blob and never a bare `download`.
- `OffsiteRemoteCard.jsx` never reveals or caches stored credentials.

## Mods and findings

- Update re-posts `POST /api/mods/install`. Never add an update endpoint, and never add `loadUpdates()` to the `mods_changed` handler.
- An update check that did not run is never shown as "no updates". `updatesEmptyMessage`
- `InstallOptionsModal` (`installFlow.jsx`) asks one question per install. A set with TraderPlus asks none and keeps the XML merge on, since the merge copies its default configs.
- Fix advice is built on the server (`app/ops/fix_hints.py`) and rendered by `FixCell`. Never special-case a finding in a page.
- `stepMods` is the one load-order move rule. Never add a KeyboardSensor, because the checkbox and arrow keys are the keyboard path.
- `LogFindings.jsx` decides nothing. To change a finding, fix the rule in `app/ops/log_analysis.py`.

## Theme and CSS

- Parts: `webui/DESIGN.md`. Never a new class family.
- `theme.jsx` owns the theme (Tactical and Tactical Light). `public/theme-init.js` repeats its keys and rules before first paint and must stay a file. Change both.
- Every surface, border, text and accent colour is a token. Raw hex is for data colours only, and a status tint is `color-mix` on a token.
- Never write a colour into a shared Tactical rule. A colour that differs is a `--tac-*` token in each variant's block.
- A button or card colour goes through a shared variant (`.btn-primary`, `.btn-danger`, `.btn-good`, `.btn-ghost`, `.btn-state`) with a Tactical block, never a one-off page rule.
- Never use `cursor: help` anywhere.
- A banner is `banner banner-<variant>`, never the variant alone. An error banner is `<ErrorBanner dismissKey=…>`.
- A form control gets `.field` and changes `--field-*` tokens. Never restate its background, border, radius or colour. `index.css` "Form controls"
- A `SideDrawer` body never scrolls above 900px (below, the drawer scrolls and a code view grows). Drawer content that scrolls a new kind of element must join the `.side-drawer-body … :has(…)` selectors in `index.css`, or it is clipped.
- Every picker is `Select.jsx`, never a native `<select>` or a second library. An option's meaning is on its row (`description`/`optionHelp`), not only in `help`. `tests/test_choice_help.py`
- On/off is `Switch` from `components/FlagSwitch.jsx`. A checkbox only picks rows or items for a batch action.
- Padding and radius read the density tokens, so the Slim style follows.
- The Tactical glass blur sits on a `::before`, never a `backdrop-filter` on the card, and the page transition fill mode stays `backwards`. Modals are not portaled, and a filter or transform on an ancestor traps their `position: fixed`.
- A new always-running blur, fixed background or animation gets a line in the "Low effects" block of `index.css`.
- The app background stays on `:root`. A background on `body`, `#root`, `.shell` or `.content` hides the watermark.
- The `@media (max-width: 840px)` block stays last in `index.css`, and `840px` is repeated in every query. Field text stays 16px there, or iOS zooms.
- Icons come from `react-icons/hi2`, never `react-icons/hi`. An `.action-bar` button gets a leading glyph with `aria-hidden` and no class, and never `.btn-small`.
- A new sidebar row needs the `.nav-label` wrapper and `labelTip`. Width reads `--sidebar-w`.

## Tooltips

- Nothing in `webui/src` uses a native `title=` on a DOM element. Spread `{...tooltipAttrs(text)}`. A capitalised component's `title=` is a prop.
- Wrap in `<Tooltip className="bare">` when the tip explains why the element is disabled, since a disabled button fires no `mouseenter`.
- A tip worth reading to the end is `tooltipAttrs(text, { keep: true })`, and a tip with links is `richTooltipAttrs`. `closeEvents` in `Layout.jsx` must keep `mouseleave`.
- On a touch screen a plain tip never opens on a control (`isTouchControl`, `Layout.jsx`).

## CodeArea

- Every code editor is `components/CodeArea.jsx`. It is not controlled: it seeds again only when the parent's value differs from what it last emitted. Never turn it into `value={value}`.
- Grammars live in `prismSetup.js`. `prism-editor.css` is the theme, uses tokens only, and is imported after `layout.css`.
- A `CodeAreaRegistry` callback must be stable, or the editor registers again on every keystroke.
- Keep `insertSpaces={false}`, because mission XML is tab-indented.

## Dashboard and guide

- `CARDS` in `DashboardPage.jsx` is the tile registry. A tile `id` and a `statusFields.js` `key` are saved layout keys, so never rename one.
- A loading tile renders a skeleton (`components/Skeleton.jsx`), never `null`. `null` means failed or not configured.
- A tile refreshes on an existing WS event, never a timer. A heavy endpoint goes on a `defaultHidden` tile. Economy is the maintainer's exception (2026 Q4).
- A tile `render` must read only `{pushToast}`, or `memo` stops it updating.
- Width is a `data-span` attribute, never an inline `grid-column`. `SortableCard.jsx`
- A `useSyncExternalStore` snapshot must be cached, or it loops. `dashboardLayout.js`
- Guide progress meets the server only in `guideSteps.js::mergeProgress`. The `zsm.wizard.*` keys keep their names.
- The guide owns no endpoint and no second login form. A step gets a button only when one existing control is the whole step.
- A control that downloads through SteamCMD goes inside `NeedsSteam`, and its page renders `<SteamLoginGate />`. `components/NeedsSteam.jsx`
