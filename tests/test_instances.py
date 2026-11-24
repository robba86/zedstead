"""Tests for app/server/instances.py — named instances (TODO E1, plans/44 step 1)."""

from __future__ import annotations

import json

import pytest

from app.server import instances
from app.server.instances import (
    InstancePorts,
    InstanceRegistry,
    ServerInstance,
    add_instance,
    check_port_conflicts,
    remove_instance,
    service_name,
    set_default,
    suggest_ports,
)
from app.shared.systemd_scope import SERVICE_NAME


# --- names ---------------------------------------------------------------------


@pytest.mark.parametrize("name", ["main", "pvp-2", "a", "9lives", "x" * 32])
def test_valid_names(name):
    assert instances.validate_instance_id(f" {name} ") == name


@pytest.mark.parametrize("name", ["", "Main", "-pvp", "pvp_2", "a b", "../x", "x" * 33, "pvp.2"])
def test_names_that_would_break_a_directory_or_unit_are_refused(name):
    with pytest.raises(ValueError, match="Invalid instance name"):
        instances.validate_instance_id(name)


def test_main_keeps_the_unit_name_an_existing_install_already_runs():
    assert service_name("main") == SERVICE_NAME == "dayz-server.service"
    assert service_name("pvp") == "dayz-server-pvp.service"


# --- the registry -----------------------------------------------------------------


def test_the_first_instance_added_becomes_the_default():
    reg = add_instance(InstanceRegistry(), ServerInstance("main"))
    reg = add_instance(reg, ServerInstance("pvp", "PvP"))

    assert reg.ids() == ["main", "pvp"]
    assert reg.default_id == "main"
    assert reg.get("pvp").display_name == "PvP"
    assert reg.get("main").display_name == "main"


def test_a_duplicate_name_is_refused():
    reg = add_instance(InstanceRegistry(), ServerInstance("main"))
    with pytest.raises(ValueError, match="already exists"):
        add_instance(reg, ServerInstance("main"))


def test_the_last_instance_cannot_be_removed():
    reg = add_instance(InstanceRegistry(), ServerInstance("main"))
    with pytest.raises(ValueError, match="only instance"):
        remove_instance(reg, "main")


def test_removing_the_default_hands_it_to_a_remaining_instance():
    reg = InstanceRegistry((ServerInstance("main"), ServerInstance("pvp")), default="main")

    after = remove_instance(reg, "main")

    assert after.ids() == ["pvp"]
    assert after.default == "pvp"
    assert reg.ids() == ["main", "pvp"], "the registry passed in is not changed"


def test_the_default_must_name_an_instance():
    reg = InstanceRegistry((ServerInstance("main"), ServerInstance("pvp")), default="main")

    assert set_default(reg, "pvp").default_id == "pvp"
    with pytest.raises(ValueError, match="No instance"):
        set_default(reg, "gone")


def test_a_stale_default_falls_back_to_the_first_instance():
    reg = InstanceRegistry((ServerInstance("pvp"),), default="main")
    assert reg.default_id == "pvp"
    assert InstanceRegistry().default_id == ""


def test_the_registry_round_trips(tmp_path):
    path = tmp_path / "instances.json"
    reg = InstanceRegistry((ServerInstance("main"), ServerInstance("pvp", "PvP")), default="pvp")

    instances.save_registry(reg, path)

    assert instances.load_registry(path) == reg
    assert json.loads(path.read_text()) == {
        "default": "pvp",
        "instances": [{"id": "main", "label": ""}, {"id": "pvp", "label": "PvP"}],
    }
    assert [p.name for p in tmp_path.iterdir()] == ["instances.json"], "no temp file left behind"


def test_a_busy_registry_is_renamed_again_on_windows(tmp_path, monkeypatch):
    """Every command reads instances.json. On Windows a rename over it fails
    while another process has it open, and `atomic_write_text` retries;
    `save_registry` had its own copy of the write without that."""
    from app.shared import json_utils

    path = tmp_path / "instances.json"
    real = json_utils.os.replace
    calls = []

    def flaky(src, dst):
        calls.append(dst)
        if len(calls) < 3:
            raise PermissionError("in use")
        real(src, dst)

    monkeypatch.setattr(json_utils.os, "replace", flaky)
    monkeypatch.setattr(json_utils, "_REPLACE_WAIT", 0)
    monkeypatch.setattr(json_utils, "IS_WINDOWS", True)
    reg = InstanceRegistry((ServerInstance("main"),), default="main")
    instances.save_registry(reg, path)
    assert len(calls) == 3 and instances.load_registry(path) == reg


def test_a_missing_file_is_an_empty_registry(tmp_path):
    assert instances.load_registry(tmp_path / "instances.json") == InstanceRegistry()


def test_the_default_location_follows_config_dir(monkeypatch, tmp_path):
    monkeypatch.setattr("app.shared.paths.CONFIG_DIR", tmp_path)

    instances.save_registry(InstanceRegistry((ServerInstance("main"),), default="main"))

    assert (tmp_path / "instances.json").is_file()
    assert instances.load_registry().ids() == ["main"]


def test_a_hand_edited_bad_name_is_refused_on_load(tmp_path):
    path = tmp_path / "instances.json"
    path.write_text(json.dumps({"default": "", "instances": [{"id": "../etc"}]}))
    with pytest.raises(ValueError, match="Invalid instance name"):
        instances.load_registry(path)


# --- ports -----------------------------------------------------------------------


def test_the_port_set_is_what_the_firewall_opens_plus_rcon():
    assert InstancePorts(2302, 27016, 2305).all() == {2302, 2303, 2304, 2305, 27016}
    assert InstancePorts(2402, 27116, 2410).all() == {2402, 2403, 2404, 2405, 27116, 2410}


def test_a_game_port_one_above_another_collides_through_its_voice_ports():
    taken = {"main": InstancePorts(2302, 27016, 2305)}

    conflicts = check_port_conflicts(taken, InstancePorts(2304, 27116, 2405))

    assert conflicts == ["port 2304 is used by 'main'", "port 2305 is used by 'main'"]


def test_separate_sets_do_not_conflict():
    taken = {"main": InstancePorts(2302, 27016, 2305)}
    assert check_port_conflicts(taken, InstancePorts(2402, 27116, 2405)) == []


def test_suggestions_step_by_the_community_offset():
    assert suggest_ports([]) == InstancePorts(2302, 27016, 2305)
    assert suggest_ports([InstancePorts(2302, 27016, 2305)]) == InstancePorts(2402, 27116, 2405)


def test_a_suggestion_steps_past_a_moved_query_port():
    # The second server put its query port where the next game port would land.
    taken = [InstancePorts(2302, 27016, 2305), InstancePorts(2402, 2502, 2405)]

    suggestion = suggest_ports(taken)

    assert suggestion == InstancePorts(2602, 27216, 2605)
    assert not check_port_conflicts({"a": taken[0], "b": taken[1]}, suggestion)


def test_instance_ports_reads_the_query_port_from_serverdz(sample_config):
    sample_config.server_cfg_path.write_text('steamQueryPort = 27200;\n')

    assert instances.instance_ports(sample_config) == InstancePorts(2302, 27200, sample_config.rcon_port)


# --- cloning paths ------------------------------------------------------------------


def test_only_a_whole_segment_is_rewritten():
    assert instances.rewrite_instance_path("/srv/main/mpmissions", "main", "pvp") == "/srv/pvp/mpmissions"
    assert instances.rewrite_instance_path("/srv/maintenance/main", "main", "pvp") == "/srv/maintenance/pvp"
    assert instances.rewrite_instance_path("", "main", "pvp") == ""


def test_a_managed_query_port_wins_over_serverdz(sample_config):
    """An instance `add` made has its query port only in config until the
    server is installed; two new ones would otherwise both read 27016."""
    sample_config.server_cfg_path.write_text('steamQueryPort = 27200;\n')
    sample_config.steam_query_port = 27116

    assert instances.instance_ports(sample_config).query == 27116


# --- add, clone, remove (plans/44 step 5) -------------------------------------------


@pytest.fixture
def main_instance(sample_config, tmp_path):
    """A migrated install: `main` with a server dir and this machine's Steam setup."""
    from app.shared.config import DayzConfig

    config = DayzConfig(
        server_dir=str(tmp_path / "srv" / "main"),
        steamcmd_dir="/opt/steamcmd",
        steam_username="someone",
        server_port=2302,
        rcon_port=2305,
        server_name="Main",
    )
    instances.save_new_config(config)
    return DayzConfig.for_instance("main")


def test_add_writes_a_config_with_free_ports_and_this_machines_steam(main_instance, tmp_path):
    from app.shared.config import DayzConfig

    created = instances.create_instance("pvp", server_dir=str(tmp_path / "srv" / "pvp"), label="PvP")

    pvp = DayzConfig.for_instance("pvp")
    assert created.ports == InstancePorts(2402, 27116, 2405)
    assert (pvp.server_port, pvp.steam_query_port, pvp.rcon_port) == (2402, 27116, 2405)
    assert (pvp.steamcmd_dir, pvp.steam_username) == ("/opt/steamcmd", "someone")
    assert pvp.server_name == DayzConfig().server_name, "settings start from the defaults"
    registry = instances.load_registry()
    assert (registry.ids(), registry.default) == (["main", "pvp"], "main")
    assert registry.get("pvp").label == "PvP"
    assert not check_port_conflicts({"main": instances.instance_ports(main_instance)}, created.ports)


def test_a_third_instance_steps_past_both(main_instance, tmp_path):
    instances.create_instance("pvp", server_dir=str(tmp_path / "pvp"))
    created = instances.create_instance("pve", server_dir=str(tmp_path / "pve"))
    assert created.ports == InstancePorts(2502, 27216, 2505)


@pytest.mark.parametrize("server_dir, message", [
    ("relative/dir", "absolute"),
    ("SAME", "already uses"),
    ("/srv/da'yz", "Server directory contains"),
    ("/srv/dayz\nUnexpected=1", "Server directory contains"),
    ("/srv/%h/dayz", "Server directory contains"),
])
def test_add_refuses_a_bad_server_dir(main_instance, server_dir, message):
    if server_dir == "SAME":
        server_dir = main_instance.server_dir
    with pytest.raises(ValueError, match=message):
        instances.create_instance("pvp", server_dir=server_dir)
    assert instances.load_registry().ids() == ["main"]


@pytest.mark.parametrize("where, message", [
    ("inside", "inside it or holds it"),
    ("around", "inside it or holds it"),
    ("config", "holds Zedstead's own settings"),
    ("home", "home folder"),
])
def test_add_refuses_a_server_dir_a_reinstall_would_empty_with_more(
    main_instance, tmp_path, monkeypatch, where, message
):
    """/home/zsm was accepted as a server dir,
    and Reinstall server deletes the whole dir before it downloads, so every
    server, the Steam cache and Zedstead's config with the accounts would go."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    server_dir = {
        "inside": main_instance.server_dir_path / "exp",
        "around": main_instance.server_dir_path.parent,
        "config": tmp_path,  # sample_config's config dir is tmp_path / "config"
        "home": home,
    }[where]
    with pytest.raises(ValueError, match=message):
        instances.create_instance("exp", server_dir=str(server_dir))
    assert instances.load_registry().ids() == ["main"]


def test_a_sibling_whose_name_starts_like_another_is_not_inside_it(main_instance):
    sibling = f"{main_instance.server_dir}-exp"
    assert instances.create_instance("exp", server_dir=sibling).config.server_dir == sibling


def test_clone_refuses_a_server_dir_the_unit_could_not_render(main_instance):
    with pytest.raises(ValueError, match="Server directory contains"):
        instances.create_instance("pvp", server_dir="/srv/da$yz", source=main_instance)
    assert instances.load_registry().ids() == ["main"]


def test_a_server_dir_with_a_space_or_parens_is_still_fine(main_instance, tmp_path):
    created = instances.create_instance("pvp", server_dir=str(tmp_path / "DayZ (pvp) server"))
    assert created.config.server_dir.endswith("DayZ (pvp) server")


def test_add_refuses_a_taken_name_and_a_leftover_directory(main_instance, tmp_path):
    from app.shared.paths import instance_root

    with pytest.raises(ValueError, match="already exists"):
        instances.create_instance("main", server_dir=str(tmp_path / "x"))
    instance_root("old").mkdir(parents=True)
    with pytest.raises(ValueError, match="zsm instances remove old --purge"):
        instances.create_instance("old", server_dir=str(tmp_path / "y"))


def test_add_refuses_before_the_install_has_moved(sample_config, tmp_path):
    """A first registered instance would become the default and hide the
    legacy config the migration has not copied yet."""
    sample_config.save()

    with pytest.raises(ValueError, match="Restart the app"):
        instances.create_instance("pvp", server_dir=str(tmp_path / "pvp"))
    assert not instances.load_registry().instances


def test_clone_copies_settings_not_data_and_not_identity(main_instance, tmp_path):
    from app.shared.config import DayzConfig

    main_instance.server_name = "Main"
    main_instance.discord_webhook_url = "https://discord/x"
    main_instance.site_listing = True
    main_instance.site_install_id = "abc"
    main_instance.site_secret = "s3cret"
    main_instance.discord_status_message_id = "42"
    main_instance.backup_dir = "/data/main/backups"
    main_instance.bundle_export_dir = "/data/bundles"
    main_instance.save()
    for name in ("custom_xml_dir", "presets_dir", "backups_dir", "versions_dir"):
        d = getattr(main_instance.paths, name)
        d.mkdir(parents=True)
        (d / "f.txt").write_text(name)
    main_instance.paths.metrics_db.write_text("db")

    created = instances.create_instance(
        "pvp", server_dir=str(tmp_path / "pvp"), source=DayzConfig.for_instance("main"),
    )

    pvp = DayzConfig.for_instance("pvp")
    assert (pvp.server_name, pvp.discord_webhook_url) == ("Main", "https://discord/x")
    assert (pvp.server_port, pvp.rcon_port) == (2402, 2405)
    assert pvp.server_dir == str(tmp_path / "pvp")
    assert (pvp.site_listing, pvp.site_install_id, pvp.site_secret) == (False, "", "")
    assert pvp.discord_status_message_id == ""
    assert pvp.backup_dir == "/data/pvp/backups", "the instance's name in a path is rewritten"
    assert pvp.bundle_export_dir == "", "a shared path falls back to the instance's own dir"
    assert any("bundle_export_dir" in note for note in created.notes)
    assert (pvp.paths.custom_xml_dir / "f.txt").read_text() == "custom_xml_dir"
    assert (pvp.paths.presets_dir / "f.txt").exists()
    assert not pvp.paths.backups_dir.exists()
    assert not pvp.paths.versions_dir.exists()
    assert not pvp.paths.metrics_db.exists()
    assert DayzConfig.for_instance("main").site_secret == "s3cret", "the source is untouched"


def test_a_clone_made_while_accounts_exist_starts_without_the_fallback(main_instance, tmp_path):
    """2P-W2: the accounts are its login, as they are an added instance's, so
    a copy of the fallback would only be one more place a retired password
    can survive."""
    from app.shared.config import DayzConfig
    from app.web.auth import hash_password
    from app.web.users import UserStore, has_no_login

    main_instance.web_password_hash = hash_password("shared-pass")
    main_instance.save()
    UserStore().migrate_from_config(main_instance)

    instances.create_instance(
        "pvp", server_dir=str(tmp_path / "pvp"), source=DayzConfig.for_instance("main"))

    pvp = DayzConfig.for_instance("pvp")
    assert pvp.web_password_hash == ""
    assert not has_no_login(pvp), "the accounts still sign in on it"
    assert DayzConfig.for_instance("main").web_password_hash == main_instance.web_password_hash


@pytest.mark.parametrize("users_json", [None, "{ truncated"], ids=["missing", "unreadable"])
def test_a_clone_keeps_the_fallback_while_no_account_can_sign_in(main_instance, tmp_path, users_json):
    """Then the fallback is the clone's only login, as it is its source's."""
    from app.shared.config import DayzConfig
    from app.web.auth import hash_password
    from app.web.users import has_no_login, users_path

    main_instance.web_password_hash = hash_password("shared-pass")
    main_instance.save()
    if users_json is not None:
        users_path().write_text(users_json, encoding="utf-8")

    instances.create_instance(
        "pvp", server_dir=str(tmp_path / "pvp"), source=DayzConfig.for_instance("main"))

    pvp = DayzConfig.for_instance("pvp")
    assert pvp.web_password_hash == main_instance.web_password_hash
    assert not has_no_login(pvp)


def test_remove_keeps_the_files_and_purge_deletes_only_the_instance_dir(main_instance, tmp_path):
    from app.shared.paths import instance_root

    server = tmp_path / "pvp-server"
    server.mkdir()
    (server / "keep.txt").write_text("server files")
    instances.create_instance("pvp", server_dir=str(server))
    instances.create_instance("pve", server_dir=str(tmp_path / "pve"))

    kept = instances.delete_instance("pvp")
    assert kept == instance_root("pvp") and kept.is_dir()
    purged = instances.delete_instance("pve", purge=True)
    assert not purged.exists()

    assert instances.load_registry().ids() == ["main"]
    assert (server / "keep.txt").exists(), "never the server directory"
    with pytest.raises(ValueError, match="only instance"):
        instances.delete_instance("main", purge=True)
    assert instance_root("main").is_dir()


def test_add_in_a_container_warns_about_a_server_dir_on_no_volume(main_instance, container_mounts):
    """night-srv-03: exp's /home/zsm/server-exp was inside the container and
    would be gone at the next recreate, with no warning anywhere."""
    created = instances.create_instance("exp", server_dir=str(container_mounts / "server-exp"))
    assert "is on no volume" in created.notes[0]
    assert instances.load_registry().get("exp"), "a warning, not a refusal"


def test_add_in_a_container_says_nothing_about_a_mounted_server_dir(main_instance, container_mounts):
    created = instances.create_instance("exp", server_dir=str(container_mounts / "server" / "x"))
    assert not any("no volume" in note for note in created.notes)


def test_a_bundle_import_keeps_this_instances_port_when_another_uses_it(main_instance, tmp_path):
    """Main's bundle imported into a second instance
    put it on main's game port 2302, and four ports clashed with no word."""
    from app.ops import bundle_ops
    from app.shared.config import DayzConfig

    instances.create_instance("exp", server_dir=str(tmp_path / "srv" / "exp"))
    exp = DayzConfig.for_instance("exp")

    updates, refused = bundle_ops.importable_config({"server_port": 2302, "server_name": "x"}, exp)
    assert "server_port" not in updates and updates["server_name"] == "x"
    assert refused == [
        f"server_port was not imported: port 2302 is used by 'main' on this machine, "
        f"so this instance keeps {exp.server_port}."
    ]
    # A free port, and main importing its own bundle, still go through.
    assert bundle_ops.importable_config({"server_port": 2902}, exp)[0] == {"server_port": 2902}
    assert bundle_ops.importable_config({"server_port": 2302}, main_instance)[0] == {"server_port": 2302}


def test_doctor_and_adopt_name_a_port_another_instance_holds(main_instance, tmp_path, monkeypatch):
    """Adopt said "Port 2402 is free" for exp's game
    port, since the socket test passes while exp is stopped."""
    from app.commands import doctor
    from app.shared.config import DayzConfig

    instances.create_instance("exp", server_dir=str(tmp_path / "srv" / "exp"))
    monkeypatch.setattr(doctor, "_udp_port_bindable", lambda port: True)
    exp = DayzConfig.for_instance("exp")
    assert doctor._check_port(exp)[1] is True

    exp.server_port = main_instance.server_port
    label, ok, detail = doctor._check_port(exp)
    assert (label, ok) == ("Server port", False)
    assert "Port 2302 is used by 'main'" in detail


def test_a_server_dir_needs_a_writable_folder(tmp_path, monkeypatch):
    assert instances.server_dir_unwritable(tmp_path / "new" / "deep") is None
    assert not (tmp_path / "new").exists()  # the check creates nothing

    monkeypatch.setattr(instances.os, "access", lambda path, mode: False)
    problem = instances.server_dir_unwritable(tmp_path / "new")
    assert str(tmp_path) in problem
    with pytest.raises(ValueError, match="cannot create or write"):
        instances.create_instance("pvp", server_dir=str(tmp_path / "new"))


def test_a_file_in_the_path_is_not_writable(tmp_path):
    (tmp_path / "file").write_text("x")
    assert "cannot create or write" in instances.server_dir_unwritable(tmp_path / "file" / "srv")
