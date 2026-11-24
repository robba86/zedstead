# CLAUDE.md

## Important

**Never** put AI attribution in a commit message: no `Co-Authored-By: Claude` trailer and no "Generated with Claude Code" footer.

**Never** remove existing functionality before you ask the maintainer.

**Always** use DRY code where you can.

**Never** open a new plan or findings file for an idea or a finding that does not break anything: add **one line to `ROADMAP.md`** instead. A plan is written only when work on that item starts.

**`plans/` is not in this repository.** Only `plans/README.md` is tracked. It says what the `plans/NN` and `TODO X0` labels in the code refer to. On the maintainer's machine the folder is a private git repository of its own, so a plan is committed there with `git -C plans …`.

**Always** read `.claude/CONVENTIONS.md` before your first commit in a session and before you change code that looks load-bearing. It holds the commit-staging rule (never `git add -A`) and how to search the history before you change a past fix.

**Keep these files small.** When a session learns a rule that the next session needs, add it to the CLAUDE.md that owns that directory: one rule in one or two lines, with a pointer to the function or test that holds it. The bug's story goes in the commit message. The root file stays under 10,000 bytes and each folder file under 15,000 bytes. If a new rule does not fit, ask the maintainer which rule to shorten or drop. Never drop one on your own: most guard against a real bug.

## Project

- Zedstead manages a DayZ server on Linux and Windows: a Click CLI and a web UI (`zsm web`, FastAPI + React). Entry point: `zsm` → `app.__main__:main`.
- Business logic lives in shared modules. The CLI (`app/commands/`) and the routers (`app/web/routers/`) only wrap them.

## Environment

- Set up with `uv venv .venv --python 3.12` and `uv pip install --python .venv/bin/python -e ".[dev]"`. Never use `python3 -m venv` or `pip`, which Debian ships without.
- Run the suite with `.venv/bin/python -m pytest tests/ -x -q --tb=short`. Never `uv run`: it writes `uv.lock`.
- No linter, no formatter and no CI, by design. Ask the maintainer before you add one. `make web-check` checks that `app/web/static/` matches `webui/src`.
- `python -m app <cmd>` acts on the dev box for real: the live `~/.config/zsm` and the real server dir. Commands and GET routes that read like reports can still write (`bundle export` writes a zip).
- `HOME=$(mktemp -d)` sandboxes the config but not systemd: `service enable/disable/status/enable-linger` and `stop` act on the real `dayz-server.service`. Test those in `tests/` with `_systemctl` patched.

## Config and files

- Config is `~/.config/zsm/instances/<name>/config.json` (Windows: `%LOCALAPPDATA%\zsm\instances\<name>\`), `main` unless `--instance` names another.
- A per-instance file comes from `config.paths`, a machine-wide one from a function in `app.shared.paths`. Never bind a config-dir path at import (`tests/test_paths.py`).
- A secret is written 0600 inside a 0700 config dir. The root group calls `paths.secure_config_dir()`, and an entry point outside `app.__main__:main` must call it too. Write through `atomic_write_text` or `os.open(..., 0o600)`, never `write_text`.
- A text file a person also edits goes through `app/shared/json_utils.py`: write with `atomic_write_text`, read for display with `read_text_lossy`, read to change with `read_text_to_rewrite` (`NotUtf8Error`, 400).
- The mod list is read and changed only through `app/modmgr/modlist.py`. An unreadable list raises `ModListError`, never an empty list, since an update keeps only listed mods.
- User docs are zedstead.com pages from the `zedstead-site` repo. Link them by URL, never a path. Tests read them through `tests/conftest.py::site_doc` and skip without it. The site build copies `install.sh`, `preflight.sh` and `user-data.yaml`, so a rename breaks it.

## `app/` layout

- `commands/`: Click commands, flag-driven only.
- `ops/`: operations the CLI and the web share.
- `xml/`: one module per mission XML file, and the mod XML merge (`xml_configs.ALL_MERGE_CONFIGS`).
- `modmgr/`: the mod list, installs and conflict scans.
- `parsers/`: serverDZ.cfg, the BattlEye cfg, ACF and ADM files.
- `presets/` and `traderplus/`: presets and quick tweaks, TraderPlus files.
- `rcon/`: the BattlEye RCon client and scheduler.
- `maps/`: the map registry, map switching, images and world files.
- `monitoring/`: Discord webhooks and the A2S, chat, metrics and session trackers.
- `server/`: the server process, units, scheduler and instances.
- `shared/`: config, paths, platform, Steam, systemd scope, CLI helpers (`cli_ui.py`).
- `spawn_editor/`: the map editor backend for `webui/src/pages/mapeditor/`.
- `web/`: the FastAPI backend. The React source is `webui/`, and its build is committed in `app/web/static/`.

## SteamCMD and the pipeline

- `zsm run` and the web Update run `app/ops/pipeline.py::run_pipeline`: login, server update, mods, bikeys, XML merge, start.
- SteamCMD exits 0 on a download that fetched nothing. `steam.is_mod_downloaded` is the shared check (`tests/test_steam_download_guards.py`).
- Never `rmtree` a workshop item without dropping it from `appworkshop_221100.acf`, or every later download fails. Use `cleanup_ops.remove_workshop_mods` (`tests/test_workshop_manifest.py`).
- An anonymous login cannot download DayZ (`steam.no_subscription_error`).
- A username goes to SteamCMD only from `steam.steamcmd_username(config)`. A Workshop ID is checked with `mods.is_workshop_id`.
- The server app is `steam.server_app_id(config)`, never a literal. `build_server_update_cmd` refuses a branch mismatch (`tests/test_server_branch.py`).
- Every server download resets stock mission files, so it runs between `mission_stock.before_download` and `after_download` (or `run_download`). See `app/ops/CLAUDE.md`.
- Two SteamCMD processes on one STEAMROOT corrupt the login token and the workshop manifest. Never spawn SteamCMD from a read-only endpoint while a job can run. Reads serve `WebState.steam_known()`, and `zsm doctor` probes only with `--steam`. Never shorten `check_steam_login`'s 90 s timeout: a timeout kills SteamCMD, and a kill mid-write destroys the cached login.
- Every SteamCMD run holds `app/shared/steamcmd_lock.py`. Spawn it only through `stream_subprocess`, `run_passthrough` or `steamcmd_lock.hold`, never a bare `subprocess`.
- Workshop titles and files are third-party input: `mod_ops.title_mod_name` names a mod and `zip_utils.copytree_without_links` copies it.

## Cross-cutting rules

- A new kind of outgoing request needs TLS verification, a timeout and a URL that no request body chooses.
- Every Discord request goes through `discord_hooks._discord_request`, with outside values escaped by `_escape_discord`.
- A check that explains an RCon problem from this machine's cfg files must skip itself when `rcon_ops.is_remote_rcon(config)` is true.
- BattlEye renames its cfg while the server runs. Read it with `battleye_cfg.live_beserver_cfg(config)` and refuse writes with `refuse_while_running` (409).
- Every map list derives from `app/maps/registry.py`. Never hardcode a second list of maps.

## systemd

- Every systemd action goes through `app/shared/systemd_scope.py`: `get_scope(config)`, then `scope.systemctl_argv`, `scope.unit_dir`. Never hardcode `sudo systemctl` or `/etc/systemd/system`.
- Every unit-state read goes through `scope.query(*args)`, which retries with `sudo -n`. A plain read can fail and look like "inactive".
- Hot read paths (`is_server_running`, `server_status`, `service_setup_state`) take the caller's loaded config, so `get_scope` does not re-read config.json per poll.
- The server's unit is `server_unit(config)`, never `SERVICE_NAME`. Docker is pinned to system scope (`container.py::sync_config_from_env`).

## Testing

- The autouse `tests/conftest.py::_isolate_config_dir` gives every test a throwaway config dir. Use `tmp_server_dir`, `tmp_mission_dir` and `sample_config`.
- The autouse `_default_system_scope` pins system scope, so a stray unit write needs sudo and never lands on the dev box. Patch a function that takes `config` with `lambda config=None: ...`.
- `sample_config` does not sandbox `workshop_dir`, which falls back to the host's real Workshop cache. Pin `config.workshop_content_dir` to tmp first.
- Patch the binding the caller uses (`monkeypatch.setattr(<caller_module>, ...)`), not the module it came from. A missed patch runs the real pipeline: SteamCMD, the server dir and systemd units.
- In a git worktree, a loose `python script.py` imports the main checkout's `app`. Run `python -m pytest` from the worktree root.
- Web job tests use `with TestClient(app) as client:` and poll the job to its end before leaving the block (`tests/test_web_jobs.py::_poll_job`).

## Platform

- `app.shared.platform.IS_WINDOWS` is the platform check, never `sys.platform`.
- Import `resource`, `fcntl`, `pwd`, `grp`, `termios` or `pty` inside the function or a `try`, never at module top (`tests/test_windows_startup.py`).

## Where the rest lives

| File | Subject |
|---|---|
| `app/xml/CLAUDE.md` | Mod XML: `custom/` files, cfgeconomycore, merges, manual XML, exclusions |
| `app/web/CLAUDE.md` | FastAPI backend: `WebState`, jobs, WebSocket, editors, roles |
| `app/ops/CLAUDE.md` | Shared operations: firewall, mission stock, bundles, uninstall's kept mod data, the log analyzer |
| `app/server/CLAUDE.md` | The server process, its units and the scheduler |
| `app/rcon/CLAUDE.md` | The RCon client: berconpy traps, deadlines, sessions |
| `app/maps/CLAUDE.md` | Map registry, switching, images, world files |
| `webui/CLAUDE.md` | React frontend: pages, theme, URL state, map editor UI |
| `docker/CLAUDE.md` | Container deployment |
| `tests/CLAUDE.md` | Test fixtures: no copied game files, when a run is not green |
| `dayz-docs/CLAUDE.md` | Local only, not in the repository: Bohemia's specs, the source for a correct DayZ config |
