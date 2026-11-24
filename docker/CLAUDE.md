# docker/: container deployment

## Layout

- The image runs systemd as PID 1, so the app's own systemd paths run unchanged. Never add Docker branches to `app/`. The glue lives in `docker/` (`Dockerfile`, `bootstrap.sh`, `zsm-bootstrap.service`).
- `docker-compose.yml` and `.dockerignore` must stay at the repo root, where Compose and the build context read them. Editing only those two does not load this file.
- `app/shared/container.py` and `app/commands/container.py` are hidden `zsm` commands, not Docker glue. They stay in `app/`.
- Host data lives outside the repo in `../zsm-data/{server,steam-cache,config,backups,bundles}`. Never remove the root-level `server/` and `config/` entries in `.gitignore` and `.dockerignore`. They keep a misplaced data folder out of git and the image.
- `../zsm-data` must be on an exec mount. `DayZServer` runs from the bind mount, so on a noexec filesystem every start fails with status 126 while `ls -l` shows it executable.
- Fixing noexec needs `make up-fresh` (`--force-recreate`). An existing bind mount keeps its mount flags through a restart.
- Only the bind mounts outlive a recreate. `container.server_dir_volume_problem` (reads `/proc/self/mountinfo`) warns on `instances add`/`clone` and in doctor's "Server volume" row (`tests/test_container.py`). The bootstrap reinstalls only the default instance's unit and timers.

## systemd and network

- The container is pinned to system scope with NOPASSWD sudo (`sync_config_from_env` sets `service_scope="system"`).
- Service gating is `has_systemd()`, never `IS_DOCKER`. `zsm supervise` is the no-systemd fallback and stays.
- `dbus` and `openssh-client` are required packages in the `Dockerfile`. Without `dbus` every non-root systemd read fails, so a running server reads as stopped.
- systemd does not pass the container env to services. `bootstrap.sh` re-exports a whitelist from `/proc/1/environ` and runs zsm as the `zsm` user.
- Compose uses `network_mode: host`. `IS_DOCKER` makes `zsm web` bind `0.0.0.0`, so it serves HTTPS with the self-signed certificate unless `web_bind_host` is set. A curl against it needs `-k https://`.

## Boot

- `bootstrap.sh` runs `zsm container-init`, `zsm setup`, `zsm sync-server-cfg`, then installs the unit and timers from config. It starts the server only with `START_SERVER=1`.
- The first boot installs nothing without a cached Steam token. Cache it with `make login`, then run `make up-fresh` so bootstrap runs `zsm setup` again.
- The install gate keeps two cases apart. A missing binary (`-e` false) exits 0. A present binary that is not executable (`-x` false) is noexec, so it warns and still installs the unit.
- The `-e` check must match `zsm setup`'s `(config.server_dir_path / binary).exists()` in `app/commands/setup.py`.

## Config from the env

- `sync_config_from_env` merges env fields onto `config.json` and must never clobber fields the app set. `ZSM_WRITE_CONFIG=0` skips it and `sync-server-cfg`.
- A new env variable needs an entry in both `_ENV_MAP` in `app/shared/container.py` and the whitelist in `bootstrap.sh`.
- An empty or `changeme` `RCON_PASSWORD` gets a random password that is kept and never printed (`settle_rcon_password`).
- A field the env set is locked in the web UI, because the next boot overwrites an edit. It is recorded in `docker-env.json` (`record_env_fields`), read with `env_locked_fields` and refused with `env_lock_problem` (409).
- `sync_server_cfg` and `sync_battleye_cfg` push the env into serverDZ.cfg and the BattlEye cfg. A refused value is skipped with a note, never raised, so the container still starts.
- The web reinstall (`app/web/routers/cleanup.py::reinstall_server`) calls the same two functions. Never write a second copy.

## Refresh and test

- A backend change needs `make refresh` (down, web-build, build, up-fresh, web).
- A frontend-only change needs no rebuild: `docker compose cp app/web/static/. dayz:/home/zsm/.local/share/uv/tools/zsm/lib/python3.12/site-packages/app/web/static/`, then a hard refresh. The next `up-fresh` drops the copy.
- Tests: `tests/test_container.py` and `bash -n docker/bootstrap.sh`.
