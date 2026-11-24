# app/server/: the server process, its units and the scheduler

## Cron and the scheduler

- `scheduler.cron_now()` is the only clock that decides what a cron expression means, and it is local time. systemd `OnCalendar=` and Windows Task Scheduler both read the fields as local wall-clock, so never anchor croniter at UTC. `tests/test_scheduler.py::TestCronIsLocalTime`
- `cron_problem(expr)` is the one source of a refusal's words and `cron_platform_problem` the one validator. A new rule goes there with a sentence, never as a bare `False`.
- A cron that restricts both day of month and weekday is refused, because cron fires when either matches and `OnCalendar=` only when both do. `_day_or_weekday_problem`
- The Windows converter (`_schtasks_plan`) was never run against a real Task Scheduler.

## Task types and the restart warnings

- A task type that stops the server must be in `scheduler.RESTART_TASK_TYPES` as well as `TASK_COMMANDS`, or it stops the server with nobody warned. `tests/test_update_cycle.py`
- Without systemd `is_server_running` cannot see `zsm supervise`'s DayZServer, so `Supervisor._fire_task` stops it, runs a `RESTART_TASK_TYPES` command and relaunches it. `tests/test_supervise.py`
- `server_status.get_next_restart(config, uptime)` is the one next-restart answer: `next_restart_occurrence(config)` first, `get_shutdown_deadline` as the fallback. The warnings (`notifier._get_remaining_and_deadline`, then `rcon_scheduler.run_warning_cycle`), the Next Restart tile (`collect_status`) and the Discord status all ask it.
- Warnings and the join lock run inside `zsm web` or `zsm supervise`, never in the task's unit, so no Zedstead process means no warnings. A oneshot that sleeps through 15 minutes of warnings restarts late.
- `run_warning_cycle` must keep both resets: `check_restart_detected` (uptime dropped) and `check_cycle_rollover` (a restart window passed with no restart). `tests/test_scheduler.py::TestCycleRollover`
- `_update_join_lock` must send `#unlock` when the countdown leaves the window, or a deferred update leaves the server refusing every join.
- A destructive task type needs a gate, and its exit code is the only record. `zsm run update` exits 0 when it defers for players and 1 on a rollback or failure.
- `SchedulerPage.jsx` (`TASK_TYPES`, `TASK_TYPE_LABELS`, `TASK_TYPE_HINTS`) is a hand-kept copy of the backend dicts. `tests/test_scheduler_page_types.py`
- `ScheduledTask.last_status` is vestigial: nothing writes it. A scheduled run's outcome is only in systemd and the journal, so never write advice that assumes the field works.
- In system scope the `update` task needs passwordless sudo under a timer. Never give it `restart`'s root oneshot, since the pipeline then runs SteamCMD and writes the server dir as root.
- A manual `cleanup wipe-server` refuses a running server. The `wipe` task runs it with `--restart`, which stops, wipes and starts through `update_cycle.run_while_stopped` (needs the same sudo as `update`). `tests/test_cleanup_commands.py`

## Task payloads and task units

- User text (`announce`, `rcon`) never goes into `ExecStart=` or Windows `<Arguments>`, which parse `%` and quotes themselves. Every apply path asks `task_command(task)`, which returns `scheduler run <task id>`. `generate_service_content`, `generate_schtasks_xml`, `supervise._fire_task`
- `validate_task` owns the payload rules: non-empty, one line, under `MAX_PAYLOAD_CHARS`. `options` holds only the one key per type in `PAYLOAD_TASK_TYPES`.
- Decision: no RCon command allowlist for scheduled tasks. Anyone who can add a task already has panel access.
- A task id is checked with `_SAFE_ID_RE.fullmatch`, never `match` with `$`, which accepts a trailing newline.
- Every unit that runs `<python> -m app` starts in `scheduler._working_dir()`. Inside the `app` package, `app/xml/` hides the standard `xml` and the task dies at import. `TestTaskCommandsAreReal`
- Install removes `_stale_unit_names` (disabled tasks, units no task owns) through `_remove_units` / `_remove_units_async`.
- `timer_install_state(config)` reads unit files only, never `systemctl` or sudo. `({}, [])` means unknown, never "not installed".

## The server unit and the launch command

- The launch command runs through bash or cmd, so `_validate_launch_command` runs at the top of `_render_service` and `_render_start_bat`. A new template placeholder needs a line there.
- A value inside the template's double quotes faces the quoted set, a bare value the full shell set (`_check_launch_value`). Never `shlex.quote`, because of the nested quoting.
- Anything that stores `server_dir` asks `server_dir_problem`. `launch_field_problem` is the per-field check.
- On Windows no configured value reaches cmd's command line. `_windows_launch_start_bat` is the one launcher, and `_path_arg` drops trailing backslashes.
- `service_control.breaks_a_unit_line` is the line-break rule for every value the server, panel and task units render, written through `unit_value` / `unit_exec_word`.
- Zedstead replaces only a unit it wrote: no `ZSM_UNIT_MARKER` is foreign (only `zsm service install --replace`), and a hand edit needs an interactive yes. `unit_guard.py`, `tests/test_unit_guard.py`
- `generate_service_files_async` logs a `unit_guard` refusal and returns True, so an update does not roll back.
- In user scope it reloads with `scope.subprocess_env()`, never through the runner, and records the unit before the reload. A failed reload returns False. `tests/test_unit_guard.py`
- A setting that changes the unit does nothing until reinstall, so `unit_is_stale(config)` must report it. Any change to rendered text marks every install stale.
- `Restart=` values are made safe at render time (`resolve_restart_policy`, `resolve_restart_seconds`), never refused at save.
- `_get_server_mod_parts(config)` is the only `-serverMod=` split. With no server-side mods it returns `config.server_args` untouched, and it never emits an empty flag.
- Launch order comes from `mods.get_resolved_mod_order`, written only through `mods.apply_load_order`. Turning manual order on must not change `ExecStart`.

## Start, stop and status

- `StartLimitBurst=0` does not disable the start limit. `_get_template_replacements` writes `StartLimitIntervalSec=0` for it, which is how systemd spells no limit.
- `start_server` and `restart_server` call `clear_start_limit`, `stop_server` never does. Start limit keys live in `[Unit]`, where systemd honours them. `tests/test_crash_recovery.py`
- A stop Zedstead asks for runs `clear_stop_failure` (DayZ exits 255 on SIGTERM), except after systemd restarted the server in that run. The restart task restarts an `active` or `failed` unit and skips an `inactive` one, so the two must change together. That covers `stop_server`, the CLI `stop` and `service update`, and no message may tell the admin to stop with `systemctl stop`. `tests/test_crash_recovery.py`, `TestTaskUnitPaths`, `tests/test_service_cli.py`
- `restart_stats` reads "gave up" from timing (`_gave_up`), never from `Result=start-limit-hit`, which only a requested start gets.
- The retry countdown uses `time.monotonic()`, never `/proc/uptime`, which counts suspended time. `parse_timespan`, `tests/test_restart_policy.py`
- `get_server_start_time` returns None unless the unit is `active` or `reloading`.
- Every start Zedstead makes runs `changeset_ops.take_start_snapshot`, which never raises. A new start path goes through `start_server`.
- `is_server_running` asks "is it up". Use `is_server_process_alive` in a guard that must wait until the process has exited, since it counts `deactivating`. `tests/test_battleye_cfg.py::TestRefuseWhileRunning`

## Windows

- A `.bat` that runs with delayed expansion must print no lone `!`, which swallows the rest of the line. `tests/test_windows_scripts.py`
- None of the Windows code runs in the suite, because tests fake `IS_WINDOWS`. A change here needs a run on a Windows machine.
- `windows_stop_server` ends the watchdog first, whether or not the server is up.
- `start_server(config, anyway=False)` refuses with `windows_runtime.runtime_problem`. Automatic starts (update cycle, post-update) pass `anyway=True`.
- `collect_status` reads uptime, memory and CPU on Windows only while the server runs, in one `_windows_process_snapshot` call. Never use `wmic`.

## Panel unit and instances

- `zsm service web install` refuses with no login (`users.has_no_login`) or when `panel_port_taken`, since either gives a restart loop. Move the check if `zsm web`'s password prompt moves.
- The panel unit runs `scheduler._python_exe()` in `_working_dir()` and binds `panel_bind_host`, which asks `default_bind_host(config, env={})`.
- Instances on Windows are not separate yet: a stop kills every `start-server.bat` watchdog and `DayZServer_x64.exe` by name, so it reaches every instance.
- `instance_migration.migrate()` runs before every subcommand and refuses while another `zsm web` runs. `tests/conftest.py` stubs `other_panel_running`, so the dev box's own panel never decides a test.
- The root group settles the instance once (`enter_instance`) and pins `$ZSM_INSTANCE`. A test that invokes the root group twice pops it in between.
- The web never calls `DayzConfig.load()` after startup. It uses `config.reloaded()`. `tests/test_paths.py::test_the_web_never_reloads_through_load`
- A config built from nothing goes through `instances.save_new_config`.
- A new setting that names this server to the outside goes in `CLONE_CLEARED`, and one that says where it writes data in `OWN_DATA_PATHS`.
- The CLI and the web Instances page share `instance_rows`, `removal_problem`, `set_label` and `clean_label`. A new add or remove check goes there.
