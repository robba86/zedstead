"""Tests for app/shared/paths.py — the per-instance / machine-wide split (plans/44)."""

from __future__ import annotations

import dataclasses

import pytest

from app.shared import paths
from app.shared.config import DayzConfig
from app.shared.paths import InstancePaths


def _members() -> list[str]:
    return [
        name for name, value in vars(InstancePaths).items()
        if isinstance(value, property)
    ]


# Every per-instance file plans/44 §2 Q2 lists, by the name it has today. A
# member added to InstancePaths without a row here fails the first test: that
# is the moment to decide which side of the split it belongs on.
Q2_PER_INSTANCE = {
    "config_file": "config.json",
    "metrics_db": "metrics.sqlite",
    "session_log": "player_sessions.jsonl",
    "chat_log": "chat_log.jsonl",
    "ban_metadata": "ban_metadata.json",
    "start_snapshot": "last_start_snapshot.json",
    "baseline_snapshot": "baseline_snapshot.json",
    "versions_dir": "versions",
    "views_dir": "views",
    "installed_unit_hash": "installed_unit.sha256",
    "custom_xml_dir": "custom-xml",
    "backups_dir": "backups",
    "bundles_dir": "bundles",
    "spawn_test_dir": "spawn-test",
    "presets_dir": "presets",
    "mod_backups_dir": "mod_backups",
    "mod_updates": "mod_updates.json",
    # Not in Q2's table; classified afterwards.
    "site_state": "zsm-app.json",
    "schtasks_dir": "schtasks",
    # plans/67: follows the instance's server directory.
    "mission_stock_dir": "mission-stock",
    # plans/73: the last boot after an update of this instance's server.
    "post_update": "post_update.json",
    # plans/77: the mod list replaced the server directory's mods.txt.
    "mods_json": "mods.json",
    # plans/88: a Quick setup finished on this instance.
    "quick_setup_done": "quick_setup_done",
    "quick_setup_mods_off": "quick_setup_mods_off.json",
    # Which fields the container's environment sets at each start (Docker).
    "docker_env": "docker-env.json",
    # F8: this instance's mission files from before its last pipeline merge.
    "xml_undo_dir": "xml-undo",
    # A merge downloaded TraderPlus_types.xml from GitHub once (trader_common).
    "traderplus_types_fetched": "traderplus-types-fetched",
}

# Q2's machine-wide files (and, last row, the ones classified in 2026 Q3).
# None of them may ever become a member.
Q2_MACHINE_WIDE = {
    "users.json", "access_log.jsonl", "tls", "dev-sessions.json", "maps",
    "version-check.json", "workshop_deps.json", "mod_timestamps.json",
    "expansion-templates", "instances.json", "instances",
    "locations", "rclone.conf", "workshop-ids.json",
}


def test_every_member_is_a_q2_per_instance_file_under_its_current_name(tmp_path):
    p = InstancePaths(tmp_path)

    assert sorted(_members()) == sorted(Q2_PER_INSTANCE)
    for member, name in Q2_PER_INSTANCE.items():
        assert getattr(p, member) == tmp_path / name, member


def test_no_machine_wide_file_is_a_member(tmp_path):
    p = InstancePaths(tmp_path)
    names = {getattr(p, m).name for m in _members()}
    assert not names & Q2_MACHINE_WIDE


def test_two_instances_give_disjoint_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "CONFIG_DIR", tmp_path)
    main = InstancePaths(paths.instance_root("main"))
    pvp = InstancePaths(paths.instance_root("pvp"))

    main_set = {getattr(main, m) for m in _members()}
    pvp_set = {getattr(pvp, m) for m in _members()}

    assert not main_set & pvp_set
    assert main.root == tmp_path / "instances" / "main"


def test_no_instance_is_todays_layout(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "CONFIG_DIR", tmp_path)

    assert paths.instance_root("") == tmp_path
    assert InstancePaths(paths.instance_root("")).metrics_db == tmp_path / "metrics.sqlite"


def test_machine_wide_paths_follow_a_patched_config_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "CONFIG_DIR", tmp_path)

    assert paths.config_dir() == tmp_path
    assert paths.instances_file() == tmp_path / "instances.json"
    assert paths.instances_dir() == tmp_path / "instances"
    assert paths.maps_dir() == tmp_path / "maps"
    assert paths.locations_dir() == tmp_path / "locations"
    assert paths.rclone_conf() == tmp_path / "rclone.conf"


def test_no_module_binds_a_config_dir_path_at_import():
    """plans/44 §6. A `NAME = CONFIG_DIR / ...` constant is fixed for the life
    of the process, so it can follow neither an instance nor a test's patched
    config dir; the ten there were converted in step 3. Crude, and the only
    thing that stops an eleventh."""
    import re
    from pathlib import Path

    app_dir = Path(paths.__file__).resolve().parent.parent
    pattern = re.compile(r"^[A-Z_]+\s*(:[^=]+)?=.*\bCONFIG_DIR\b", re.MULTILINE)
    offenders = [
        f"{py.relative_to(app_dir)}: {m.group(0).strip()}"
        for py in app_dir.rglob("*.py")
        if py.name != "paths.py"
        for m in pattern.finditer(py.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_no_per_instance_file_is_joined_onto_the_config_dir():
    """plans/44 step 3b. Resolved at call time is not enough for a per-instance
    file: `CONFIG_DIR / "backups"` is right only while every config is the
    pre-instances one, and after step 4 it is the same directory whichever
    instance asks. Those come from `config.paths`. Crude like the one above:
    it sees a join written on one line, which is how `xml_manual.manual_dir`
    wrote `custom-xml` before 3b, and it fails on that version."""
    from pathlib import Path

    app_dir = Path(paths.__file__).resolve().parent.parent
    names = {f'"{name}"' for name in Q2_PER_INSTANCE.values()}
    offenders = [
        f"{py.relative_to(app_dir)}:{n}: {line.strip()}"
        for py in app_dir.rglob("*.py")
        if py.name != "paths.py"
        for n, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1)
        if ("CONFIG_DIR" in line or "config_dir()" in line) and any(q in line for q in names)
    ]
    assert offenders == []


# --- DayzConfig.paths / for_instance ------------------------------------------------


def test_a_config_without_an_instance_resolves_todays_paths(sample_config, tmp_path):
    assert sample_config.instance_id == ""
    assert sample_config.paths.root == tmp_path / "config"
    assert sample_config.paths.metrics_db == tmp_path / "config" / "metrics.sqlite"


def test_for_instance_loads_that_instances_file_and_knows_where_it_came_from(sample_config, tmp_path):
    root = tmp_path / "config" / "instances" / "pvp"
    root.mkdir(parents=True)
    (root / "config.json").write_text('{"server_name": "PvP", "server_port": 2402}')

    cfg = DayzConfig.for_instance("pvp")

    assert (cfg.instance_id, cfg.server_name, cfg.server_port) == ("pvp", "PvP", 2402)
    assert cfg.paths.root == root
    assert cfg.paths.metrics_db != sample_config.paths.metrics_db


def test_save_writes_back_to_the_instance_and_never_records_its_id(sample_config, tmp_path):
    root = tmp_path / "config" / "instances" / "pvp"
    root.mkdir(parents=True)
    (root / "config.json").write_text("{}")
    legacy = tmp_path / "config" / "config.json"

    cfg = DayzConfig.for_instance("pvp")
    cfg.server_name = "Renamed"
    cfg.save()

    assert DayzConfig.for_instance("pvp").server_name == "Renamed"
    assert "instance_id" not in (root / "config.json").read_text()
    assert not legacy.exists(), "the legacy config.json is not touched"


def test_the_legacy_save_does_not_record_an_instance_id_either(sample_config, tmp_path):
    sample_config.save()
    assert "instance_id" not in (tmp_path / "config" / "config.json").read_text()


def test_a_hand_edited_instance_id_is_ignored_on_load(sample_config, tmp_path):
    (tmp_path / "config" / "config.json").write_text('{"instance_id": "pvp", "server_name": "X"}')

    cfg = DayzConfig.load()

    assert cfg.instance_id == ""
    assert cfg.server_name == "X"


def test_replace_keeps_the_instance(sample_config):
    """`dataclasses.replace` is how `service migrate-scope` builds a variant;
    losing the instance there would write it into the legacy file."""
    cfg = dataclasses.replace(sample_config, instance_id="pvp")
    assert dataclasses.replace(cfg, service_scope="user").instance_id == "pvp"


def test_a_missing_instance_is_config_not_found(sample_config):
    from app.shared.config import ConfigNotFound

    with pytest.raises(ConfigNotFound, match="instance 'gone'"):
        DayzConfig.for_instance("gone")


def test_a_bad_instance_name_never_reaches_the_filesystem(sample_config):
    with pytest.raises(ValueError, match="Invalid instance name"):
        DayzConfig.for_instance("../config")


# --- which config a process acts on (plans/44 step 4) ---------------------------


def _register(name: str, server_name: str) -> None:
    from app.server.instances import ServerInstance, add_instance, load_registry, save_registry

    cfg = DayzConfig(server_name=server_name)
    cfg.instance_id = name
    cfg.save()
    save_registry(add_instance(load_registry(), ServerInstance(name)))


def test_load_reads_the_legacy_file_until_an_instance_exists(sample_config):
    sample_config.server_name = "Legacy"
    sample_config.save()

    cfg = DayzConfig.load()

    assert (cfg.instance_id, cfg.server_name) == ("", "Legacy")


def test_load_reads_the_registrys_default_over_the_legacy_file(sample_config):
    sample_config.server_name = "Legacy"
    sample_config.save()
    _register("main", "Main")

    assert DayzConfig.load().server_name == "Main"


def test_load_reads_the_instance_the_environment_names(sample_config, monkeypatch):
    _register("main", "Main")
    _register("pvp", "PvP")
    monkeypatch.setenv("ZSM_INSTANCE", "pvp")

    cfg = DayzConfig.load()

    assert (cfg.instance_id, cfg.server_name) == ("pvp", "PvP")


def test_load_with_nothing_is_config_not_found(sample_config):
    from app.shared.config import ConfigNotFound

    with pytest.raises(ConfigNotFound, match="zsm init"):
        DayzConfig.load()


def test_reloaded_reads_its_own_file_not_the_default(sample_config, monkeypatch):
    _register("main", "Main")
    _register("pvp", "PvP")
    pvp = DayzConfig.for_instance("pvp")
    on_disk = DayzConfig.for_instance("pvp")
    on_disk.server_name = "Edited"
    on_disk.save()

    fresh = pvp.reloaded()

    assert (fresh.instance_id, fresh.server_name) == ("pvp", "Edited")


def test_reloaded_legacy_stays_legacy_and_a_missing_file_is_none(sample_config):
    assert sample_config.reloaded() is None
    sample_config.server_name = "Legacy"
    sample_config.save()
    _register("main", "Main")

    assert sample_config.reloaded().server_name == "Legacy"


def test_a_new_config_on_a_fresh_box_becomes_main(sample_config, tmp_path):
    from app.server.instances import load_registry, save_new_config

    save_new_config(DayzConfig(server_name="Fresh"))

    assert not (tmp_path / "config" / "config.json").exists(), "nothing at the legacy path"
    assert DayzConfig.for_instance("main").server_name == "Fresh"
    assert load_registry().default == "main"
    assert DayzConfig.load().server_name == "Fresh"


def test_a_new_config_for_a_named_instance_joins_the_registry(sample_config, monkeypatch):
    from app.server.instances import load_registry, save_new_config

    _register("main", "Main")
    monkeypatch.setenv("ZSM_INSTANCE", "pvp")

    save_new_config(DayzConfig(server_name="PvP"))

    registry = load_registry()
    assert (registry.ids(), registry.default) == (["main", "pvp"], "main")
    assert DayzConfig.for_instance("pvp").server_name == "PvP"


def test_the_web_never_reloads_through_load():
    """plans/44 §4a item 4. The panel keeps the instance it started with, so it
    re-reads with `config.reloaded()`. The one `load()` allowed is the dev
    reload worker's in `server.create_dev_app`, which starts a process the way
    `zsm web` does."""
    import ast
    from pathlib import Path

    web = Path(paths.__file__).resolve().parent.parent / "web"
    offenders = []
    for py in web.rglob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        allowed = {
            id(node)
            for fn in ast.walk(tree)
            if isinstance(fn, ast.FunctionDef) and fn.name == "create_dev_app"
            for node in ast.walk(fn)
        }
        offenders += [
            f"{py.relative_to(web)}:{node.lineno}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and node.attr in ("load", "load_or_none")
            and isinstance(node.value, ast.Name) and node.value.id == "DayzConfig"
            and id(node) not in allowed
        ]
    assert offenders == []


# Implicit loads outside app/web/ that a panel serving several instances could
# reach, each with why it cannot (plans/62 §2.9). A new one fails here until
# someone writes down its reason.
_IMPLICIT_LOADS_ALLOWED = {
    "ops/doctor_report.py": "only when no config is passed; the web always passes one",
    "shared/systemd_scope.py": "only for config=None; service_scope is a machine field",
    "shared/container.py": "Docker bootstrap, CLI only",
}


def test_no_new_implicit_config_load_outside_the_web():
    import ast
    from pathlib import Path

    root = Path(paths.__file__).resolve().parent.parent
    found = set()
    for area in ("ops", "server", "shared", "modmgr", "rcon", "xml", "parsers",
                 "maps", "spawn_editor", "traderplus", "monitoring"):
        for py in (root / area).rglob("*.py"):
            tree = ast.parse(py.read_text(encoding="utf-8"))
            if any(
                isinstance(node, ast.Attribute)
                and node.attr in ("load", "load_or_none")
                and isinstance(node.value, ast.Name) and node.value.id == "DayzConfig"
                for node in ast.walk(tree)
            ):
                found.add(py.relative_to(root).as_posix())
    assert found == set(_IMPLICIT_LOADS_ALLOWED)


def _config_optional_functions(root) -> set[str]:
    """Functions in app/server, app/shared and app/ops whose ``config``
    argument defaults to None, i.e. that silently act on the default
    instance when it is left out."""
    import ast

    names = set()
    for area in ("server", "shared", "ops"):
        for py in (root / area).rglob("*.py"):
            for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
                if not isinstance(node, ast.FunctionDef):
                    continue
                args = node.args.args + node.args.kwonlyargs
                defaults = ([None] * (len(node.args.args) - len(node.args.defaults))
                            + node.args.defaults + node.args.kw_defaults)
                for arg, default in zip(args, defaults):
                    if (arg.arg == "config" and isinstance(default, ast.Constant)
                            and default.value is None):
                        names.add(node.name)
    return names


def test_the_web_never_leaves_out_the_config_of_a_per_instance_call():
    """plans/62: a panel serving several instances must say which one. Left
    out, `is_server_running()` asks about main's unit while the page is
    pvp's; five routes did exactly that until step 6."""
    import ast
    from pathlib import Path

    root = Path(paths.__file__).resolve().parent.parent
    optional = _config_optional_functions(root)
    assert "is_server_running" in optional  # the scan still sees them

    def name_of(node) -> str:
        return node.attr if isinstance(node, ast.Attribute) else getattr(node, "id", "")

    offenders = []
    for area in ("web", "spawn_editor", "rcon"):
        for py in (root / area).rglob("*.py"):
            for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
                if not isinstance(node, ast.Call):
                    continue
                called = name_of(node.func)
                bare = called in optional and not node.args and not node.keywords
                handed = (called == "to_thread" and len(node.args) == 1
                          and name_of(node.args[0]) in optional)
                if bare or handed:
                    offenders.append(f"{py.relative_to(root)}:{node.lineno}")
    assert offenders == []


# --- the config dir is owner-only (SEC-6) ------------------------------------


def _mode(path) -> int:
    import stat

    return stat.S_IMODE(path.stat().st_mode)


@pytest.fixture()
def umask_022():
    import os

    old = os.umask(0o022)
    yield
    os.umask(old)


@pytest.mark.skipif(paths.IS_WINDOWS, reason="POSIX modes")
def test_a_missing_config_dir_is_created_owner_only(monkeypatch, tmp_path, umask_022):
    target = tmp_path / "home" / ".config" / "zsm"
    monkeypatch.setattr(paths, "CONFIG_DIR", target)

    paths.secure_config_dir()

    assert _mode(target) == 0o700
    assert _mode(target.parent) == 0o755  # ~/.config keeps the usual mode


@pytest.mark.skipif(paths.IS_WINDOWS, reason="POSIX modes")
def test_an_existing_config_dir_is_tightened_and_its_files_left_alone(monkeypatch, tmp_path, umask_022):
    import os

    target = tmp_path / "zsm"
    target.mkdir()
    os.chmod(target, 0o755)
    (target / "config.json").write_text("{}")
    os.chmod(target / "config.json", 0o600)
    monkeypatch.setattr(paths, "CONFIG_DIR", target)

    paths.secure_config_dir()

    assert _mode(target) == 0o700
    assert _mode(target / "config.json") == 0o600
    assert (target / "config.json").read_text() == "{}"


@pytest.mark.skipif(paths.IS_WINDOWS, reason="POSIX modes")
def test_a_dir_that_cannot_be_tightened_is_left_quietly(monkeypatch, tmp_path):
    import os

    target = tmp_path / "zsm"
    target.mkdir()
    os.chmod(target, 0o755)
    monkeypatch.setattr(paths, "CONFIG_DIR", target)

    def _refuse(*_a, **_k):
        raise PermissionError("not the owner")

    monkeypatch.setattr(paths.os, "chmod", _refuse)

    paths.secure_config_dir()  # no exception

    assert _mode(target) == 0o755


def test_windows_is_left_to_its_acl(monkeypatch, tmp_path):
    target = tmp_path / "zsm"
    monkeypatch.setattr(paths, "CONFIG_DIR", target)
    monkeypatch.setattr(paths, "IS_WINDOWS", True)

    paths.secure_config_dir()

    assert not target.exists()
