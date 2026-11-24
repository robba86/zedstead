---
name: map-editor
description: Rules for the Map Editor (the /map-editor page) — the React page under webui/src/pages/mapeditor/, its FastAPI backend in app/spawn_editor/, Leaflet handling, URL state, and the deep links into and out of the config editors. Use when working on the map editor, spawn positions, cfgeventspawns, traders/garage/safezone map data, or its collision deep links.
---

# Map editor — where the rules live

Split across two auto-loaded files; read whichever your directory does not cover:

| Half | File | Covers |
|---|---|---|
| Frontend | `webui/CLAUDE.md` | the React page, Leaflet rules, `urlState.js`, `?event=` focus, layer toggles, `mapeditor.css` theming, editor↔config deep links |
| Backend | `app/web/CLAUDE.md` | how `routers/map_editor.py` includes `app/spawn_editor/routes.py` under `/api/map-editor`, the `mapeditor` WS channel, `map_editor_svc.py`, `GET /api/maps/thumbnail` |

Frontend rules that bite immediately:

- The page is `/map-editor`, never `/visual-editor`.
- Never change the `className` of `.me-map` after mount: Leaflet adds its own classes to that node. A class that changes goes on `.me-map-wrap`.
- Keep `isolation: isolate` on `.me-editor`.
- Guard every map hook on `isMapAlive(map)`.
- Marker metadata goes in `leaflet/markerRegistry.js`.
- Endpoints take `lat`/`lng`. Never project on the client for a write.
- Never chain two `url.set*` calls in one handler.
- Leaflet sets string tooltips and popups with `innerHTML`. Tooltips go through `leaflet/textTooltip.js::bindTextTooltip`, and markup with a value is `` html`…` `` from `leaflet/html.js`.
