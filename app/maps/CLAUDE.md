# app/maps/: maps, switching and world files

The root file holds the rule that every map list derives from `registry.py`.

## Registry

- `registry.py` joins `seed_data`'s `VANILLA_MAPS` and `WORKSHOP_MAPS` (keyed by display name) to the terrain the rest of the app keys on (`dayzOffline.enoch` → `enoch`). Add a new map to that catalog. Tests: `tests/test_map_registry.py`.
- Location labels are read only through `locations.load_locations`. The admin's own names in `locations_<terrain>.json` are written only by `locations.change_own_locations`, which refuses a file it cannot read (`tests/test_own_locations.py`).

## Switching

- A map's `deps` are not map mods: `@CF` and `@Dabs Framework` also serve other mods. `map_ops.partition_old_map_mods` is the one rule. The map's own `mods` go, a `deps` entry goes only when nothing left in the mod list can want it, and the caller reports what it kept.
- The action behind that rule is an `rmtree`, and the registry cannot tell a private dep from a shared framework, so never guess. Tests: `tests/test_maps_switch.py`.
- A map's recommended serverDZ.cfg is a third party's file. A switch copies only `map_ops.MAP_CFG_KEYS` from it and logs each key it changed. The site's `features.md` names the keys, and `tests/test_map_ops.py` keeps them in step.
- Restoring the admin's own `serverDZ.cfg.<map>.backup` replaces the whole file but keeps `PRESERVED_CFG_KEYS` (`password`, `passwordAdmin`).
- `map_ops.switch_map_cfg` decides what a switch does to the cfg. The web job and the CLI both call it, so never write a third copy.
- `map_ops.switch_plan` says what follows, what leaves and what can stay behind. `check_switch_choices` refuses what cannot be done, and `leave_behind` carries out the rest at the top of the mod-list step.
- The web route and `zsm maps switch` hold no rule of their own. With no choices, a switch does what it always did.
- A mod left behind is disabled, never uninstalled. A manual XML file left behind is scoped to the old terrain.

## Images

- No map image ships with zsm: a map's picture is its author's work. The admin's own upload (`map_data.own_image`) comes first. A map in `registry.CLEARED_MAPS` (maker's written yes, quoted in plans/65) gets its image from zedstead.com through `map_data.cleared_image`, and its credit is on screen while its data is drawn (`MapCredit.jsx`). Never commit a map image.

## Downloaded archives

- Mission ZIPs, a map's cfg and the Expansion templates go through `map_ops._download_archive` with `MISSION_ZIP_LIMITS`. `zip_utils.save_capped` caps the download and `zip_utils.check_unpack_size` refuses an oversized archive before anything is unpacked (`ArchiveTooLarge`).
- A new download of a third party's archive uses the same two helpers.
- A mission is unpacked beside its folder and replaces it only once every file is out, and a kept `storage_<n>` never moves (`download_and_extract_mission`, `_replace_folder`). Reset mission must never empty the folder first.
- The folder taken from an archive comes from `resolve_mission_path`: the highest version folder beside a versioned catalog path, else the catalog path, else the same name anywhere. `tests/test_map_ops.py`

## World files are untrusted input

- Gates in `registry.py` answer `not_available` / 404 before any file is looked at: `world_file_allowed` (`world_file`, `world_file_problem`), `heights_allowed` (/terrain-heights), `roads_allowed` (`terrain_roads`, /roads), `terrain_layer_allowed` (`navmesh_file`, `world_names`). A `CLEARED_MAPS` entry opens only what its maker cleared; Banov has no navmesh or names (`tests/test_terrain_map.py`, `tests/test_web_map_editor.py`).
- A `heights_url` makes `maker_heights` the heights' source. Never host a grid a maker sent privately without their yes to a public download (Banov's is not hosted).
- Any installed mod can ship a world file or a navmesh. `terrain_map`, `road_map`, `navmesh_map` and `lzo` check every count before they use it, and a value no real map has raises `ValueError`.
- Every `lzo.decompress` passes `max_out` from the header's grids (`Grid.block_limit`). `LzoTooLarge` is not an `LzoError` on purpose, so `read_prefix` never reads more for it.
- The navmesh rasteriser limits its work per grid point (`MAX_ROWS_PER_POINT`, `MAX_BOX_PER_POINT`). Past those, the navmesh is a `ValueError`.
- A road piece with a point far off the map, NaN or inf is left out and counted in a warning, never the whole road list. Real files have such pieces.
- Anything in `terrain_map.UNREADABLE` from a build means "no terrain": the route's 404, never a 500.
- `cached_build` lower-cases the terrain, remembers a failed build for `terrain_map.FAILURE_BACKOFF` per PBO path, mtime, size and format, and answers hits and failures before it takes `_build_lock`.
- A navmesh version above the known ones is read with the newest layout, and the tile checks refuse a changed one. A failed heights build makes `heights_source` answer no source (`navmesh_unreadable`, `unknown_version`), so `/map-info` turns Terrain off with the reason (`tests/test_web_map_editor.py`).
- The cache is one file per world file, `<terrain>.<sha256(pbo)[:12]>.<kind>.gz`, because every instance shares `maps_dir()`. A build prunes that terrain's stale entries.
- Road ends are indexed by point (`road_map._Ends`). More than `_CROWD` distinct ends in one square is a `ValueError`.
- A lake above sea level is filled only from a point in `ClearedMap.lakes` (webui `terrainLakes.js`). Never fill every dip in the heights: Banov has over 200 dry ones.
- The forest and a world file's missing roads come from the mission's `mapgroupcluster*.xml` (`cluster_map.py`), behind `terrain_layer_allowed`. Never read `areaflags.map` or another undocumented binary for a layer: Bohemia's answer rules out reverse engineering (plans/90).
- Tests build invented world files and navmeshes, never ones made from a real map: `tests/test_terrain_map.py`, `tests/test_road_map.py`, `tests/test_navmesh_map.py`.
