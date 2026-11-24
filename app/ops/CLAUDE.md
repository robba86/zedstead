# `app/ops/`: shared operations

Logic that the CLI and the web routers both call. Keep Click and FastAPI out of it.

## Firewall `--apply` (`firewall_ops.py`)

- `--apply` sets deny-by-default, so a mistake locks the operator out of their only VPS. A test never runs a real `ufw`, `firewall-cmd`, `ss`, `sshd`, `systemctl` or `sudo`. `tests/test_firewall_ops.py`
- The allow rules and the default policy run as two batches with a verification gate between them, never as one loop. An exit code proves nothing: `ufw allow` exits 0 when it skips a rule. `split_at_policy`
- The gate reads the staged rules (`ufw show added`, `firewall-cmd --permanent --list-all`), never `firewall_status`, which misses a rule until `ufw enable` or `--reload`. `staged_rules`
- "Could not tell" never renders as "closed", for firewall status and for SSH detection. A source with no answer goes into `SshDetection.incomplete`, and `--apply` refuses when nothing observed the running system.
- SSH detection is the union of `ss`, the `ssh.socket`/`sshd.socket` unit, `sshd -T` and `sshd_config` with its `Include`s, never the first that answers. Never drop `sshd_config` from it: the over-allow from a stale `Port` is the chosen direction. `ssh_detect`
- Every read retries under `sudo -n` only, never bare `sudo`, which hangs a tty-less web poll. Never gate that retry on `sudo_available_noninteractive()`.
- A systemd-owned TCP listener never becomes an SSH allow rule, since systemd owns other services' sockets too. `_socket_unit_ports()` skips a unit whose `ActiveState` is not `active`, because a disabled `sshd.socket` still reports a port.
- The gate proves the rule landed, not that the port is reachable. Ubuntu's `ssh.socket` binds IPv6 only, so a port drop-in must list both address families.
- `FirewallStatus.error` means "do not trust this for a decision". The firewalld non-default-zone case sets `zone_conflict` as well. Never branch on `zone_conflict` instead of `error`, and a surface that renders `error` also renders `zone_conflict`.
- One port in a non-default zone makes the whole port table `unknown`, by decision, since `_firewalld_other_zone_with` finds only the first such zone. A finer table needs a union over every zone, with `error` still set. `tests/test_service_cli.py`
- `firewall-cmd --state` is read as text by exact match, never by exit code or substring: the exit code mixes "stopped" with "polkit refused", and that refusal contains "running". `_parse_firewalld_state`
- A surface never prints a command list it cannot stand behind. With no SSH port detected, the dry run shows no commands, never a fallback port 22.
- With no firewall backend, doctor's Firewall row is a WARN, by decision. The text comes from `NO_BACKEND_SUMMARY` and `no_backend_advice()`.
- `--set-target=DROP` leaves the zone's existing services open, and `_zone_services_note()` names them. Never turn it into a `--remove-service`.
- `firewall_revert.py` is not dead code. It is built and not yet called from `apply_rules`.

## RCon exposure

- BattlEye binds RCon on `0.0.0.0` unless `RConIP` is set in `beserver_x64.cfg`. The default `rcon_port` 2305 is in the game's port range, so opening the game ports publishes RCon. `firewall_ops.rcon_collision`, `rcon_ops.local_rcon_bind`
- Zedstead never writes `RConIP`, by decision, since a write silences remote RCon tools: the warning with the fix is the whole intervention. Never drop the warning or the port from `required_ports()`. `tests/test_web_configs.py::test_battleye_form_does_not_promise_a_loopback_rcon`

## VPS readiness (`vps_readiness.py`)

- `MIN_RAM_MB` means a box sold as that size. Every RAM comparison goes through `advertised_ram_mb()`, which rounds `MemTotal` up the `RAM_SIZES_MB` ladder. Never lower a threshold instead. `MIN_MHZ` stays as it is, by decision.
- `preflight.sh` mirrors `app/data/vps-thresholds.env`. Add a threshold to both, or `tests/test_vps_readiness.py` fails.

## Stock mission files through an update (`mission_stock.py`)

- Every server download goes through it: a runner through `run_download`, a sync caller through `before_download`, `download_failed` and `after_download`. A caller of `build_server_update_cmd` that skips it resets every edited stock file silently.
- The Steam-owned list covers the stock missions' top level, `db/`, `env/` and `pra/`. A folder Bohemia adds goes on the list.
- `stock/` mirrors `stock.json`: a recorded file has a copy with the recorded hash, or none. `base_for` takes a candidate only when its hash matches. `before_download` empties `pending/` first.
- A file both sides changed is merged value by value and by key, never by position (`app/shared/merge3.py`, `app/xml/xml_merge3.py`). A value both changed follows `mission_update_conflict` and is listed. An entry DayZ removed always goes, and anything unsure is `Unmergeable`.
- `ask` keeps DayZ's file live until the admin chooses. `settle` and `settle_values` are the one path for a choice, and a name is filtered against the list, never joined onto a path.
- The mods go back in exactly once: the pipeline passes `remerge_mods=not ctx.should_run("merge_xml")`. Reset-mission passes `resetting=<mission>`, and a community mission's reset calls `cleanup_ops.remerge_reset_mission`.
- A map switch ends with the merge (`map_ops.apply_mod_xml_to_new_mission`) unless told not to. Plain Start does not merge.
- Tests: `tests/test_mission_stock.py` (fake downloads, `_forget_stock` reaches the whole-file path), `tests/test_xml_merge3.py`.

## Persistence folder (`storage_<instanceId>`)

- serverDZ.cfg's `instanceId` names the folder. Name it only through `servercfg.storage_folder_name` or `cleanup_ops.storage_dir`, `find_storage`, `storage_name`, never the literal `storage_1`. `servercfg.is_storage_folder` matches any id.
- The fixtures' cfg has no `instanceId`. A test adds it with `conftest.set_instance_id`.

## Spawn test (`spawn_test_ops.py`)

- `active.json` is written last and means "something to restore". Nothing else creates it.
- Restore replaces the storage folder whole, through a `.zsm-restore` sibling and `os.replace`. Never route it through `backup_ops.restore_backup`, which never deletes a file. `tests/test_spawn_test_ops.py`
- The `init.c` block is global functions plus one call line in `void main()`. A script error stops every client at the loading screen, so read `_block_lines`' docstring first.
- Restore strips the block byte for byte for LF and CRLF (`strip_spawn_test_block`), so an edit made during the test survives.
- The count has no maximum, by decision. `SPAWN_WARN_ABOVE` only warns.
- "Only X" reads events through `_event_files` (registered `custom/` files too), and `_plan_only_x` writes nothing until the plan is complete.

## Reading a server log (`log_analysis.py`)

- `build_log_report` builds the one dict that `zsm log-analyzer`, Doctor and Analysis → Server logs render. Classification lives here, never in a surface.
- `RULES` hold named messages. `CATCH_ALLS` report any unclaimed `!!! [CE]`, `SCRIPT (E)` or `(E):` line verbatim. `NOISE` is labelled, counted and checked first, so never broad enough to swallow a rule's line.
- A rule's `hint` must be a substring of everything its pattern can match, or the rule stops firing.
- What every stock boot prints is noise. What the admin controls is a finding. What the stock mission ships is a finding with a `VANILLA_BASELINE` entry, not counted against the admin.
- `MOD_BASELINE` does the same for a pre-configured mod, only while the run loads it, and names the Workshop build measured. `first_boot` applies only when the previous run lacked the mod (`previous_run_mods`), and `every_subject` requires it.
- Every `Baseline` cites its stock `source`, and an unknown terrain matches only the map-independent entries. A new DayZ version gets a `versions`-scoped entry, and old fixtures stay unedited.
- An RPT hour before 10:00 has one digit, so patterns take `\d{1,2}`.
- `parse_mods` drops empty names, because `server_dir / ""` is the server dir and reads as an installed mod.
- Keep every test RPT fixture verbatim. `tests/test_log_analysis.py::test_a_stock_server_owns_none_of_its_own_findings`

## Bundles (`bundle_ops.py`)

- `export_bundle` is the one export path. It logs markup-free lines, and `should_cancel` deletes the partial zip.
- A bundle carries `config.json` verbatim, credentials included, plus the cfg passwords. By design: warn, never redact, no `--redact` flag. `sensitive_warning` names the set fields, never values. `tests/test_bundle_ops.py::test_the_exported_bundle_really_does_contain_the_credentials`
- `users.json` stays out of a bundle. The old `config.web_password_hash` travels as the fallback login, by decision.
- An archive with `config.json` or player data is made by `zip_utils.private_zip`, never `ZipFile(path, "w")`. A new credential field goes on `SENSITIVE_CONFIG_FIELDS`.
- A bundle is third-party content. `mission_entry_dest` and `profiles_entry_dest` confine entries, and `importable_config` runs each field through its setter's check.
- Read and write the mission only through `servercfg.read_mission_template` and `set_mission_template`. A test's serverDZ.cfg needs the `class Missions { class DayZ { … } }` shape.

## Uninstall keeps mod data

- `mod_ops.uninstall_mods` keeps the mod's `<profiles>/<Name>/` folder (live trader and garage data) and reports it.
- `cleanup_ops.find_leftover_mod_data` matches only a known mod name, never a guess. `remove_mod_data_dirs` re-derives that list and never joins a raw name onto a path.
- A monkeypatched `uninstall_mods` needs `log=None` in its lambda. `tests/test_cleanup_ops.py::TestLeftoverModData`

## Expansion templates (`expansion_ops.py`)

- The templates are never vendored. A download empties a folder only when `template_dir_refusal` allows it.
- The XML goes in as manual XML files with fixed record ids, never as an `expansion_ce` folder. Known classes come from the PBOs and `db/types.xml` only, never `custom/`. `apply_plan` refuses a mission with no `cfgeconomycore.xml`.

## Offsite backup (`offsite_ops.py`)

- The transport is one `rclone` binary, by decision, pinned as `RCLONE_VERSION` and checked against `RCLONE_SHA256`. Never swap it for a Python library. `find_rclone` never falls through a bad `rclone_path`.
- Secrets live only in `rclone.conf`, and no route returns one. Zedstead owns only `[zsm-offsite]` and parses by hand (`_split_sections`), never with `configparser`.
- `write_remote` filters to `REMOTE_FIELDS[kind]` and refuses a line break or NUL in a value. A blank field keeps the stored value.
- An SFTP host key is saved only after the admin confirms it. `--yes` never confirms.
- `auto_push` never raises and never stays quiet. No test runs rclone.

## Backups (`backup_ops.py`)

- `_MOD_PROFILE_DIRS` is a decision, since restore overwrites. `backup_part` is the one classifier, and `_restore_target` confines a `profiles/` entry. Restore is refused while the server runs.

## VPS bootstrap (`bootstrap_ops.py`)

- `zsm bootstrap-vps` is the only place provisioning lives. `user-data.yaml` calls it and runs no `apt`, `sysctl` or swap command. `tests/test_user_data.py`
- `plan()` never calls `_run_command` or `_write_file`. `UNKNOWN` never counts as `NEEDED`.
- Root's `authorized_keys` is copied to the new user, or disabling root SSH locks the box.
- The firewall step forwards `assume_yes` and `confirm` to `firewall_ops.apply_rules`, never hardcodes them.
- A printed command goes through `display_command`, so it pastes as the argv `--apply` runs.

## Whitelist (`whitelist_ops.py`)

- Edit lines, never regenerate the file: a regenerated file loses comments and 44-character IDs. Line endings survive.

## Mod rollback (`mod_rollback.py`)

- The stash is a move in `fileops.remove_server_mods`, one generation. `pipeline._undo_copy_mods` passes `stash=False`, or the undo stashes the broken version.
- The undos put the stash back, or a failed update boots with no mods. `_undo_remove_old_mods` skips a run that set nothing aside (`ctx.stashed_mods == []`). `_undo_copy_mods` removes the copies only within one run. A web rollback just restores what the stash holds (`tests/test_mod_rollback.py`).
- `rollback_pipeline` returns the undos that failed. Every caller reports a failure, and `run_update_cycle` leaves the server stopped (`tests/test_update_cycle.py`, `tests/test_web_jobs.py`).
- A loop over `copy_mod_to_server` passes one `generation=` per operation.
- A local mod is never moved, stashed or copied.
- After `restore()`, never advise re-running the update. It sets the restored mods aside again.

## Player stats (`adm_parser.py`)

- `parse_line` tries `_parse_prefixed` first. Never put an older pattern in front, and end a new one with `$`. `tests/test_adm_parser.py::TestANameShapedLikeAKill`

## Pre-configured mods (`dynamic_ai.py`, `expansion_ai.py`, `zone_mods.py`, `toxic_zone.py`, `zone_trader.py`)

- A mod author's terms forbid copying their files, so tests use invented files in the layout of the mod's defaults.
- These modules never write a mod's files. `test_the_report_never_writes`
- The one write into a mod folder is a file the mod stopped shipping, fetched from the author's repo into the server copy, never the Workshop cache (`upstream_defaults.restore_missing_mod_files`, `tests/test_upstream_defaults.py`).
- Shared helpers live in `mod_files.py`. `read_json` returns a failure as a string.
- An empty faction scan means unknown. `RANDOM` and an empty `Loadout` are valid. `ai_refs.py`
- `zone_trader` writes classes unpriced, except ToxicZone's own `Trader.txt` prices on request.
- A new zone mod is a new `ZoneMod` spec. `toxic_zone.apply_derived` is the one writer of derived values.

## After an update (`post_update.py`)

- Booting without a mod deletes its saved objects, so `PERSISTENCE_WARNING` stays blunt and a set-aside takes a backup first.
- No unattended path disables or re-enables a mod. `run_update_cycle` never calls `set_aside`.
- A script error names the PBO prefix, never the `@folder`. `suspects` matches `mod_conflict.mod_pbo_prefixes`.
- A crash log alone is no crash while the process runs: DayZ writes one for every script error it survives. Only a minidump, a systemd restart or a gone process end the boot watch (`_crash_evidence`).
- `dependents` needs `every_provider=True`, or setting CF aside leaves its users enabled. `set_aside` takes `since` after `stop_server`.

## Collision scans (`collision_ops.py`)

- Unreadable input goes into `CollisionReport.skipped`, never an exception. Readers catch `_UNREADABLE`.

## Adopting a server (`adopt.py`)

- `scan` only reads, and `apply` writes only its own state. Every fix runs on a click. `test_apply_alone_fixes_nothing`
- `_pasted_copies` is the one rule for pasted types. A name alone is not a paste.
- `findings` runs against `preview_config`, which is never saved. The web saves through `apply_to`, so the path lock refuses before any write.
