# app/web/: FastAPI backend

The React half is in `webui/CLAUDE.md`. Read both when a change crosses the API.

## Web app (app/web/)

- `zsm web` (port 8433) is FastAPI plus one WebSocket. `server.create_app` builds it, and its lifespan starts and stops `WebNotifier` and a persistent `RconManager`.
- `WebState` (`state.py`) is one instance's server side, never a module global. Sessions, users, the WS hub, the `JobManager` and Steam state live on `PanelShared`. `Panel` (`panel.py`) holds every bundle.
- `JobManager` (`jobs.py`) runs every server-mutating operation as a job, one at a time across all instances.
- `WsHub` (`ws.py`) is the single `/ws` endpoint with channel subscriptions. A new channel must be added to `KNOWN_CHANNELS`.
- `routers/` holds one `APIRouter` per domain, listed in `routers/__init__.py::ALL_ROUTERS`. Request bodies are pydantic models in `schemas.py`, and responses are plain dicts and lists.
- `editors/` is the `EditorSpec` registry (`list_editors()`) of config-file editors, served by `routers/configs.py`.
- `permissions.py` gives each route a role, and `deps.require_session` is the only thing that enforces it.
- The built React bundle is committed to `app/web/static/`, so `uv tool install` from git works without node. A `webui/src` change ships with its `make web-build` output in the same commit, and `make web-check` verifies it.
- The build output must never be named `dist`, because the root `.gitignore` ignores `dist/` at any depth (`webui/vite.config.js`).
- Files from `webui/public/` are served by the SPA catch-all, not a second mount. There is no `/docs`, `/redoc` or `/openapi.json`, on purpose.
- Two `zsm web` processes on one config run the notifier loops twice and open two RCon connections. Turn `web_notifier_enabled` off on the extra one.
- `ZSM_DEV_RELOAD` (set by `zsm web --reload`) is the only switch for persisted sessions. A restart logging everyone out is intended.

## Config and state

- Every web save of the config goes through `WebState.mutate_config`, and `grep -rn "\.save()" app/web/` must find only it. An editor that changes `DayzConfig` sets `EditorSpec.mutates_app_config=True`.
- A service that keeps a `DayzConfig` registers with `state.on_config_change`, because `mutate_config` swaps the object and a stale copy that saves rolls `config.json` back.
- The web re-reads its own instance with `config.reloaded()`, never `DayzConfig.load()` (`tests/test_paths.py::test_the_web_never_reloads_through_load`).
- A CLI edit made while `zsm web` runs stays invisible to the web process until its next own write. Code that must see it reads the file each time: the zedstead.com heartbeat (`site_heartbeat.py`), `paths_locked()`, `GET /api/settings`.
- Every per-instance helper gets the page's config. One whose `config` defaults to `None` acts on the default instance (`tests/test_paths.py::test_the_web_never_leaves_out_the_config_of_a_per_instance_call`).
- `deps.get_state` is the page's bundle, `get_panel_state` the startup bundle for panel things (login, users, access log, jobs). Code without a `Request` uses `app.state.panel.bundle_for`, never `app.state.ctx`.
- Never bind a config-dir path at module level. A `CONFIG_DIR / …` constant ignores the conftest monkeypatch and writes into the real `~/.config/zsm` in tests, so resolve paths at call time (`config.paths`, `app.shared.paths`).
- A persistent `RconManager` swap stops the old connection before it starts the new one (`panel._sync_rcon` under `state.rcon_swap_lock`), or the session log says every player left.
- A task nobody stores goes through `app.web.tasks.spawn`. A bare `asyncio.create_task` can be collected mid-run and leave a job holding the exclusive slot.

## Jobs and SteamCMD

- Cancel is cooperative (`job.cancel_event`), so a loop over mods checks the runner's `cancelled` before each item. `JobManager.kill` cancels the task of a job that never checks.
- `stream_subprocess` signals the whole process tree, because `steamcmd.sh` runs SteamCMD as a child. Never go back to `proc.terminate()`.
- Only `state.refresh_steam()` measures the Steam login (startup, after a job with `ran_steamcmd`, `?check=1`), and `JobManager.before_exclusive` makes a job wait for it. A login route records success with `state.note_steam_ok(True)`.
- A SteamCMD login failure is classified from its output by `steam.diagnose_login_output`, never from the exit code.
- `GET /api/setup-state` reuses the function that already decides each step and adds no detector of its own. `presets` has none on purpose.

## Files in and out

- An upload is the raw request body, not multipart, read through `uploads.read_bounded`. Multipart needs `python-multipart` on every install, and no client-supplied filename ever reaches a path.
- A config upload goes through `save_raw`, which validates and snapshots before it writes. A new `.cfg` raw target needs its own `RawTarget.validate`.
- A download goes through `downloads.file_attachment`, and its path comes from a listing, never from the query string.
- A URL value that names a file is matched against a discovered list or checked with `zip_utils.is_bare_name` before any join.
- A quick backup is a zip plus a `.meta.json` sidecar. `list_backups` finds a backup by its sidecar and `restore_backup` reads only the zip, so both travel together (`offsite_ops.upload_candidates`).
- `config_versions.snapshot()` fires inside `save_xml`/`save_json`. Any other save calls it after every check, so a refused save leaves no version.
- Strings from `app/ops/*` carry Rich markup. A route returns them through `editors/_help.py::strip_markup`.

## BattlEye cfg

- While the server runs, BattlEye renames its cfg to `beserver_x64_active_<hex>.cfg` and back at stop, so a write made meanwhile is lost. Readers use `battleye_cfg.live_beserver_cfg(config)` or `live_file`.
- Writers call `battleye_cfg.refuse_while_running(config)` before any snapshot, and `BeserverCfgInUse` is 409. It asks `is_server_process_alive`, since the rename back waits for the process to exit. Tests use the `battleye_running` fixture.
- The BattlEye editor's save, its Raw save and its Upload copy the RCon pair into the config through `rcon_ops.adopt_beserver_rcon` inside `mutate_config` (`RawTarget.adopt`), except for a remote `rcon_host`.

## Editors

- An XML save mutates elements in place, never rebuilds the tree. `xml_merge` matches `START MOD` markers as text, so load with `load_xml(..., preserve_comments=True)`.
- Every save of a mod's JSON or a map layer goes through `app/shared/json_patch.py` (`save_json_patched`, `versioning.write_json`). A save with no edits writes nothing (`tests/test_mod_saves_lossless.py`).
- `editors/_mod_json.py` holds the mod editor rules: a save patches the entry it read, never invents a key, keeps whole numbers whole and addresses rows by index or `dup-<n>`.
- Nested values travel as `$nested` with `$nested_base`. `editors/_nested.apply_nested` refuses a stale base and never adds or drops a key, and XML rows use `editors/_xml_nested.apply_parts`.
- A key the game reads as a decimal is named through `apply_nested(..., floats=)`, citing Bohemia's wiki.
- Expansion's `m_Version` is never a field and is refused on save. Expansion flags stay ints.
- A saver resolves an added row (`new-<n>-<time>`) through `ce_tables._row_name`, never `row.get("id")`.
- The Events and Economy tables send every row, and `ce_tables._sync_rows` removes any entry no row names.
- A file Zedstead cannot read is kept, never replaced. TraderPlus map layers use `tp_document.read_document` and `refuse_unreadable`.
- serverDZ.cfg ranges come only from Bohemia's wiki (`config_cmd._CFG_RANGES` via `cfg_range`). The save checks every field before it writes.
- A new editor needs a line in `routers/search.py::FIELD_SOURCES` (`tests/test_web_search.py`).
- A finding's advice must name an action the UI can take, with its button enabled.
- `globals.xml` stays one flat section. Ask the maintainer before you group it.

## Auth and roles

- `permissions.py` is default-deny. A write is admin unless in `MODERATOR_WRITES`, a read is viewer unless in `NON_VIEWER_READS`, and `SESSION_WRITES` holds what every role needs (logout, own password, own TOTP).
- GET is not automatically safe. A read that shows a credential goes into `NON_VIEWER_READS`, and a read that downloads or builds a cache goes into `permissions.SIDE_EFFECTING_READS` (`tests/test_permissions.py::test_a_read_that_downloads_or_builds_a_cache_is_listed`).
- Walk the route table only through `permissions.enumerate_routes`. A plain `app.routes` walk misses included routers.
- `tests/test_permissions.py::PUBLIC_ROUTES` lists every route that answers without a session.
- A new credential field goes into `redact.SECRET_KEYS`, and a route that prints cfg values redacts for non-admins. A player IP goes out only through `players.shows_player_ips`.
- The role is read from `users.json` per request (`deps.current_user`). A failure inside the gate refuses the request.
- Accounts live in `users.json` (0600), never in `config.json`, which a bundle carries. With `users.json` unreadable, only a session opened with the fallback password is admin.
- The last admin can be neither removed nor demoted, and no account write runs while `users.json` cannot be read. The router refuses a change to your own role or account.
- A password change writes the password `UserStore.authenticate` reads next. Its test signs in with the new password.
- A route that sets a password uses `auth.check_new_password`, `SessionStore.revoke_all_for` and `sync_linked_fallback`. A re-check of the caller's password goes through `deps.confirm_password`, and a wrong one is 400, never 401.
- `SessionStore` holds `_lock` around every `_sessions` access, because `require_session` validates in the thread pool.
- A login goes through `state.login_limiter.begin()` and its `LoginAttempt`. The limiter and the access log take the verified name from `_attempted_identity`, never the typed one.
- The password is checked before the TOTP code. The secret is never served, the TOTP routes act only on the caller's row, and there is no QR code (decision).
- `deps.cookie_is_secure` sets the cookie's Secure flag and name for login and logout, and `deps.session_token(conn)` is its only reader. Secure must never default to true, or a loopback HTTP install cannot log in.
- `start_server` passes `forwarded_allow_ips=None` when `web_trusted_proxies` is empty, so uvicorn's `FORWARDED_ALLOW_IPS` still works.
- `access_log.record()` never raises, and `client_ip` reads `request.client`, never `X-Forwarded-For`.
- The WebSocket has no role gate and accepts no write action. It closes when its session is revoked. An unauthenticated handshake answers 403, not 401.
- Never add a CORS layer without revisiting `same_origin.SameOriginWrites`. A route whose body can pass 32 MB goes into `body_limit._route_caps`.
- A web route that writes `web_allowed_hosts` calls `allowed_hosts.lockout_problem` first.
- `app/commands/web.py::_resolve_tls` picks the certificate, and nothing falls back to HTTP. The self-signed key is unencrypted, because uvicorn cannot answer a passphrase prompt.
- A `web_tls_cert` pinned at the generated pair is never auto-renewed, since a real certificate copied there gets replaced. `web_self_signed` is the persistent way.

## Path lock

- `web_lock_paths` is enforced only in `WebState.mutate_config`, never in a router. Its `fn` returns file writes as a function, so a refusal writes nothing.
- The locked set is `settings_registry.web_locked_fields()`. A new check built on `_absolute_path` goes into `_PATH_CHECKS`, and `tests/test_web_path_lock.py` fails on an unlisted path field.
- Every lock read goes through `WebState.paths_locked()`, which reads the file.
- A restore or bundle import keeps the locked fields and logs them, never fails. The CLI is never locked.

## Instances

- `/i/<id>/` names the instance (`instance_prefix.py`), and no prefix is the startup instance. A bundle broadcasts through `InstanceHub` to its own sockets only.
- Removing an instance never purges, and refuses the startup instance and the page's own (409). Add and clone are 403 under the path lock.

## Map editor

- `app/spawn_editor/routes.py` is one `APIRouter` without the `/api` prefix, included by `routers/map_editor.py` under `/api/map-editor`. A route that reads a raw body lives in `routers/map_editor.py`, because `_check_write_body` reads every write body as JSON.
- `map_editor_svc.py` builds `WebState.map_editor` lazily and keeps it. `ensure_map_editor_ready` rebuilds it when serverDZ.cfg names another mission.
- The watcher runs only while `mapeditor` has subscribers. The first one, and a write with none, reload all (`map_editor._ready`).
- A new broadcast type needs a line in `spawn_editor/state._WEB_MESSAGE_TYPES`, or it is dropped silently (`tests/test_map_editor_broadcasts.py`).
- `_check_write_body` refuses a non-finite, `null` or off-map number before any state mutates. A breadcrumb radius of `-1` is exempt.
- An update path keeps an untouched value through `converters.same_as_shown` or `keep_or_round`, never `round(float(...), 2)`.
- A write patches the document it read and keeps heights, except a moved Expansion spawn point, which gets `y = 0`. An entry key is matched against what `load` found, never joined onto a path.
- `spawn_editor/versioning.py` takes one snapshot per JSON file per editor session, never per drag.
- Never read or write Dynamic AI's `SpatialPlayerSettings.json` or its mod folder (the author's terms).
- `GET /api/maps/thumbnail` resolves the terrain from serverDZ.cfg, never map-editor state, and serves `own_thumbnail` before the own image. `/api/map-editor/map-image` serves `own_image`, then `cleared_image`.
- `/map-info`'s `bounds` is the world, which places markers and the image. Never move it. Measure a misplaced marker against the building map first, which draws the mission's own `mapgrouppos.xml`.
- `POST /api/maps/download` keeps map mods in the Workshop cache, never the server dir, since the XML merge reads every `@` folder there.

## Doctor and players

- A doctor or adopt row for something merely off passes, with a detail that starts "Off:". Off is not wrong, and `ok=False` makes `zsm doctor` exit 1.
- Link phrases in `app/ops/doctor_report.py` match `app/commands/doctor.py` verbatim, as `log_analysis.EXECSTART_NO_MODS` does. A reworded check loses its link silently.
- The doctor's `install_server` action runs `/api/update/run`, never `/api/maintenance/reinstall-server`, which deletes the server dir.
- A mutating job pauses every doctor check (`WebNotifier.busy`).
- `player_ops.fetch_online` is the single path for the live player list. `is_server_running` only explains a failure, never gates a connection.
- Test helpers: `tests/conftest.py::unreleased_map`, `_cheap_scrypt` (mark `real_scrypt_cost` to test the cost) and `tests/test_doctor_report.py::_install_server_binary`.
