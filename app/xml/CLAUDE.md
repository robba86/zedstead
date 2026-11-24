# app/xml: mod XML into the mission

## Where Zedstead writes

- Every merge runs two halves: `pipeline._merge_xml_sync` calls `xml_economy.merge_economy_core` (CE-linked) and `xml_merge.merge_all_direct_configs` (direct).
- CE-linked: a mod file of one of the six `xml_classify.CE_TYPES` is copied to `custom/<kebab-mod>-<file>.xml` and registered in cfgeconomycore.xml. The name comes only from `xml_economy.custom_file_name`, which makes "--" one dash, since a disabled mod's names go into a comment.
- Zedstead writes one `<ce folder="custom">` block and never any other `<ce folder>`. Other folders stay verbatim and are only reported (`xml_report._foreign_ce_entries`).
- `custom/` is wiped and rebuilt on every merge (`xml_economy._clear_custom_folder`). Never treat a file there as user data, never write to it from outside the merge, and keep it read-only in every editor.
- A reader of `custom/` runs beside that wipe with no lock, so a file it just listed can be gone: it reads as missing, never raises (`xml_report._file_size`, `tests/test_xml_report.py::test_a_merge_wiping_custom_mid_report_reads_as_missing`).
- Direct merge: the `ALL_MERGE_CONFIGS` targets get a mod's elements inside `<!-- START MOD: @X -->` / `<!-- END MOD: @X -->`. In `MERGE_MODE` `sections` the markers sit inside the section. An `OPTIONAL` target that is missing is not a failure.
- Which half of the mission is whose follows from that: Zedstead owns `custom/`, the `<ce folder="custom">` block and the START/END MOD blocks. Everything else (`db/types.xml`, `db/events.xml`, `mapgroupproto.xml`, all content outside markers) is the admin's (`app/ops/mission_baseline.py`).
- Read what is loaded from the registrations (`xml_economy.registered_custom_entries`), never from a glob of `custom/`.

## cfgeconomycore.xml

- `xml_economy._rebuild_custom_ce` is the one writer of the custom block, saved through `_save_economycore_dom`. It keeps the block's position: DayZ applies the last definition, so a move changes what wins.
- Remove per `<file>`, never per `<ce>`: one block holds every mod's registrations (`remove_mod_economy_entries`, `unregister_custom_file`). `register_custom_file` adds one manual file.
- `type=` is not always the root tag (globals' root is `<variables>`). `xml_classify.CE_ROOT_TAGS` / `ce_root_tag()` is the one map, and `CE_TYPE_ORDER` is the write order.
- A `type=` outside the six makes DayZ refuse the whole economy. The table editor validates it on save and refuses any row in `custom` with a 400 (`mission_tables._economycore_save`, `editors.SaveElsewhere`, `tests/test_web_configs.py::test_economycore_custom_folder_is_readonly`).
- A disabled mod's `<file>`s are parked in `<!-- DISABLED @Mod: <ce folder="custom"><file …/></ce> -->` comments (`disable_mod_economy_entries`, `enable_mod_economy_entries`). Keep that payload shape, older files and readers match it.
- Any parse of cfgeconomycore.xml that is saved keeps comments (`economycore_xml.parse_economycore_file`). Dropping them silently re-enables a disabled mod (`tests/test_web_configs.py::test_economycore_save_keeps_disabled_wrappers`).
- `_prune_stale_disabled_entries` drops wrappers of mods not in `disabled_mods`. `None` means unknown and prunes nothing, so every caller passes `modlist.disabled(config)`.

## Reading mod files

- Never key a merge on a mod file's root tag. Mods wrap one payload in `<events>`, `<eventposdef>` or a bare `<event>`, so match children on `MOD_XML_CHILD_TAGS` (`xml_merge._collect_children`).
- Content beats filename: `xml_classify.classify_mod_file` parses and decides. `<events>` with `<pos>` is cfgeventspawns, with `<nominal>` economy events. `<variables>` is globals only with `<var>`, `<economy>` only with an init/load/respawn/save flag, and `<globals>` stays unmapped (`test_ce_root_tags_round_trip`).
- The filename catch-all to `types` is for rootless fragments only. A file with a root the CE does not know is skipped and summarised (`xml_economy.ce_bucket`, `tests/test_xml_economy.py::test_non_ce_files_are_not_linked`).
- `messages` is never auto-linked from a mod dir (`AUTO_LINK_KINDS`): mods ship sample messages, and linking one broadcasts it to real players. Take over is the opt-in, with no opt-in list.
- `types_xml.scan_types_files` keys on the `<types>` root, since a spawnabletypes file in `custom/` also holds `<type>`.
- `xml_classify.file_matches_terrain` drops another map's variant. `TERRAIN_FILENAME_TOKENS` derives from `app.maps.registry`, and only a spelling the catalog lacks goes in `registry.EXTRA_TERRAIN_TOKENS`.
- One unreadable mod file never stops a merge: it is skipped with a log line (`validate_xml_file`, `ce_bucket`, `xml_utils.MAX_XML_DEPTH`). A merge that adds nothing logs a warning. Keep both.

## Merging, stripping, backups

- A whole-mission merge never restores from `.backup`. It calls `xml_merge.ensure_backup` (create-only) and `strip_all_mod_blocks`, so admin edits outside markers survive.
- No undo restores `.backup` either: it dates from the first merge, so it holds an older game version. The pipeline undo puts back `xml_undo.keep_before_merge`'s copy and refuses a file changed since the merge, and `run rollback-xml` strips (`xml_undo.strip_mod_xml`, `tests/test_xml_undo.py`).
- A mission file's `.backup` is written once (`ensure_backup`, `keep_first_backup`), so it can hold an older game version. Restore from backup puts back `mission_stock.shipped_copy` (the installed version's file) and uses `.backup` only without one (`xml_utils.restore_file`).
- Per-mod paths never restore from `.backup`, because it wipes every other mod. `mods.reconfigure_mod_xml` is `merge_mod_economy_entries` plus `merge_mod_direct_configs`, each rebuilding only that mod (`mods_only`).
- Per-mod re-configure is refused for a disabled mod (web 400), because its CE entries are parked inside DISABLED wrappers that a scoped merge deletes. Other scoped writes record the change and answer `applied: False, reason: "mod disabled"` (`mod_xml_takeover.apply_source_change`).
- The mission follows a mod's on/off state only through `mods.apply_mod_state_to_mission_xml`: it toggles the CE half, re-copies the mod's `custom/` files on enable, and re-runs `merge_all_direct_configs` (`tests/test_xml_economy_per_mod.py::test_enable_after_a_full_merge_restores_the_mods_custom_files`).
- START and END markers pair by name (`_mod_markers`, `_mod_block_spans`). An unterminated START is left and reported, never paired with another mod's END. Never go back to a regex (`tests/test_xml_merge_mod_files.py`).
- Parse any file a save will write with `load_xml(path, preserve_comments=True)`. Lost markers strand the mod blocks, and every file in the mission root or `db/` can hold them. Skip comment children with `isinstance(child.tag, str)`.
- `xml_utils.restore_file` snapshots before it copies. Restore from backup exists only for the six files `ensure_backup` covers, built by `_paths.restore_from` from the editor's own path getter.
- A strip followed by a re-splice runs inside `xml_utils.one_version_per_file()`, so Versions holds the file as it was, not half merged.
- `apply_direct_files` writes each file once: a target is stripped in memory (`_parse_without_blocks`) and saved with its blocks back, so an apply that changes nothing writes nothing (`tests/test_xml_manual.py::test_an_apply_keeps_one_version_and_a_no_op_apply_none`).
- The step key stays `merge_xml` (a web API contract). Its failure line is `xml_merge.apply_failed_message`.

## Manual XML files (`xml_manual.py`)

- They are the one way to change content inside a mod block or in `custom/`, or to win over a mod's definition. Never add a second mechanism.
- The store is `config.paths.custom_xml_dir`: `files.json`, `<id>.xml`, `excluded-custom.json`, `excluded-sources.json`. Every function that touches it takes a required keyword `manual_dir` with no default, so instances never share one. Tests pass `conftest.manual_dir()`, and a fake merge entry point accepts the keyword.
- A file is applied as pseudo-mod `@user:<id>`: CE kinds to `custom/user-<id>.xml`, direct kinds inside markers. Never rename `@user:`, `user-<id>.xml` or the `link`/`merge` mode names, they are written into mission files.
- They apply inside `merge_economy_core` and `merge_all_direct_configs`, last, so they win. `apply_manual_file` applies one, and `apply_direct_files` also strips files that are now off.
- A file follows its mod: while the mod is off, it stays out of the mission (`mod_is_off`, `follow_mod_state` on a toggle). A mod not installed is not off.
- A free-form `merge` target is the mission root or `db/` only, re-checked on every apply (`is_safe_target`), because `remove_mod_xml_blocks` scans only those. `normalize_kind_target` upgrades a merge into an `ALL_MERGE_CONFIGS` file to that kind. `kind_for_target` scans only those, so a `db/globals.xml` merge stays free-form.
- The webui never holds a kind list or a kind-to-file map. It reads `ce_kinds`, `kind_targets` (`GET /api/modxml/files`) and `capture_target` (`GET /api/configs/{id}/meta`).
- `mod` is a name or a Workshop ID and stays free text, so XML can be prepared for a mod not installed yet (`resolve_mod_value`, `mod_matches`). Join report rows on `mod_name`. `terrain` is checked against `known_terrain_tokens()`, since an unknown value matches every map.
- Capture is a move: `capture_event_positions` and `capture_unowned` delete what they copied and re-apply that file, or each position spawns twice. They take only unowned entries (`unowned_children`). `children_with_owner` is the one marker reader.
- The map editor saves a manual file's position through `edit_merged_event`, and takes a mod's file over first through `app/ops/mod_xml_takeover.take_over_mod_source`, the one Take over path (`tests/test_spawn_editor_owners.py`).
- The three JSON files are user-editable. A reader takes an unreadable one as empty so the merge runs. A writer refuses with `ManualFileError` (`_files_to_add_to`, `_states_to_change`).

## Exclusions and take over

- `excluded-custom.json` is keyed by generated name and read in `find_and_copy_mods_xml`. `excluded-sources.json` is keyed by the server-dir-relative mod file (`normalize_source`) and read by both halves.
- `excluded` and `deleted` both suppress the file. A delete is recorded, never only unlinked, or the next merge brings it back.
- `owning_mod()` matches the kebab prefix longest first, since `@Trader` also prefixes `@TraderPlus`.
- Deleting one mod file is a scoped re-merge, never a block strip: a mod's files share one block per target.
- Take over (`fork_custom_file`, `fork_mod_source`) creates an enabled, applied manual file and excludes the original in the same step. `set_baseline` runs only after `create_file` succeeds. An `upstream_status` of `unknown` never reads as unchanged.
- Restoring a mod's turned-off files is `clear_mod_file_states` plus `reconfigure_mod_xml`, never part of `POST /api/mods/{name}/xml/merge`, which also runs on install and toggle.
- A mod file path from a request goes through `xml_report.resolve_mod_source`. Mod files are read-only, SteamCMD overwrites them.
- Never `rglob(filename)` with a name from a mod or a request, because `[` or `*` in it acts as a pattern. `mod_file_for_custom` walks the folder and compares names.

## Every editor and mod save is lossless

- A save with no edits leaves a file, Bohemia's stock files included, byte-identical, and an edit changes only the edited lines. `tests/test_mod_saves_lossless.py` holds mod files to the same standard.
- `xml_utils.save_xml` keeps the file's own layout (`_file_style`) and writes nothing when the document is unchanged (`_same_document`). Never call `ET.indent` or `ET.tostring(..., xml_declaration=True)` on a mission file (`tests/test_xml_nested.py`, `tests/test_xml_utils.py` `STOCK_LAYOUTS`).
- Write a value only when it changed, compared as a number (`spawnabletypes_xml._set_number`). A setter writes into the tree, not only the dataclass, or the save answers 200 and writes nothing.
- Tree edits change elements in place and keep unknown attributes and children (`app/web/editors/_xml_nested.py`).
- A file that exists and cannot be read raises, only a missing file is empty (`messages_xml.read_messages`). A bad number attribute reads as `None` or 0, but a position stays strict.
- `save_xml` refuses a character XML 1.0 cannot hold with `XmlCharError`. Never use stdlib `ET.parse` or `ET.fromstring`: use `load_xml` or `xml_text.parse_xml_text` (defusedxml).
- cfgrandompresets.xml rows are addressed by position, since a mod preset can repeat a stock name.

## Other rules

- `ce_tables.py` is the one table adapter for the six CE kinds. `CE_TABLE_KINDS` derives from `_LOADERS`. globals is a form for `db/globals.xml` and a table for `custom/` copies. Do not unify them.
- `economy_xml.add_system` validates the name: a system is an element tag, and a bad one breaks the whole economy.
- A `.backup` is not a baseline for presets or tweaks. They keep `<mission>/.zsm-presets.json` and `.zsm-tweaks.json` (`app/presets/field_baselines.py`) and write only `db/types.xml` and `db/events.xml`, never `custom/` (`tests/test_quick_tweaks.py`).
- Tests: `tests/test_xml_classify.py`, `test_xml_economy.py`, `test_xml_economy_per_mod.py`, `test_xml_merge.py`, `test_xml_merge_mod_files.py`, `test_xml_manual.py`, `test_xml_report.py`, `test_web_modxml.py`, `test_web_mods.py`.
