"""A systemd unit per instance (plans/44 step 6).

`main` and a config on the pre-instances layout keep `dayz-server.service` and
`dayz-task-<id>`; every other instance gets its own names, and nothing done
for one instance reaches another's units.
"""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.server import scheduler, service_control
from app.server.scheduler import ScheduledTask, install_timers, timer_install_state
from app.shared.systemd_scope import SERVICE_NAME, server_unit, task_unit_prefix


def _cfg(ident: str):
    return SimpleNamespace(instance_id=ident)


# --- names ------------------------------------------------------------------------


@pytest.mark.parametrize("config, unit", [
    (None, SERVICE_NAME),
    (_cfg(""), SERVICE_NAME),
    (_cfg("main"), SERVICE_NAME),
    (MagicMock(), SERVICE_NAME),
    (_cfg("pvp"), "dayz-server-pvp.service"),
])
def test_main_keeps_the_unit_an_existing_install_runs(config, unit):
    assert server_unit(config) == unit


def test_no_instances_task_prefix_starts_another(tmp_path):
    names = ["", "main", "pvp", "pvp-task", "p"]
    prefixes = {n: task_unit_prefix(_cfg(n)) for n in names}
    assert prefixes["main"] == prefixes[""] == "dayz-task-"
    assert prefixes["pvp"] == "dayz-task.pvp."
    distinct = {n: p for n, p in prefixes.items() if n != ""}
    for a, pa in distinct.items():
        for b, pb in distinct.items():
            if a != b:
                assert not pb.startswith(pa), (a, b)


def test_service_name_by_instance_agrees():
    from app.server.instances import service_name

    assert service_name("main") == SERVICE_NAME
    assert service_name("pvp") == server_unit(_cfg("pvp"))


# --- the server's unit --------------------------------------------------------------


@pytest.fixture
def pvp(sample_config):
    return dataclasses.replace(sample_config, instance_id="pvp")


def test_systemctl_acts_on_the_instances_unit(pvp, monkeypatch):
    seen = []
    monkeypatch.setattr(service_control, "has_systemd", lambda: True)
    monkeypatch.setattr(service_control.subprocess, "run", lambda cmd, **k: seen.append(cmd))

    service_control.systemctl("stop", config=pvp)
    service_control.systemctl("stop", config=dataclasses.replace(pvp, instance_id="main"))

    assert seen[0][-1] == "dayz-server-pvp.service"
    assert seen[1][-1] == SERVICE_NAME


def test_the_unit_says_whose_it_is_and_mains_is_unchanged(sample_config, monkeypatch):
    monkeypatch.setattr(service_control, "_validate_unit_user", lambda user: None)
    render = service_control._render_service

    main = render(dataclasses.replace(sample_config, instance_id="main"))
    legacy = render(sample_config)
    pvp = render(dataclasses.replace(sample_config, instance_id="pvp"))

    assert "Description=DayZ Dedicated Server\n" in main
    assert legacy == main, "an installed main unit must not read as stale"
    assert "Description=DayZ Dedicated Server (pvp)\n" in pvp
    assert "{{" not in pvp


def test_the_panel_unit_is_still_named_explicitly(pvp, monkeypatch):
    seen = []
    monkeypatch.setattr(service_control, "has_systemd", lambda: True)
    monkeypatch.setattr(service_control.subprocess, "run", lambda cmd, **k: seen.append(cmd))

    service_control.systemctl("restart", config=pvp, name="zsm-web.service")

    assert seen[0][-1] == "zsm-web.service"


def test_status_reads_the_instances_unit(pvp, monkeypatch):
    seen = []

    class Done:
        returncode, stdout, stderr = 0, "ActiveState=active\nSubState=running\n", ""

    monkeypatch.setattr(service_control, "IS_WINDOWS", False)
    monkeypatch.setattr(service_control.subprocess, "run", lambda cmd, **k: seen.append(cmd) or Done())

    assert service_control.is_server_running(pvp) is True
    assert "dayz-server-pvp.service" in seen[0]


def test_the_unit_file_is_written_under_the_instances_name(pvp, monkeypatch, tmp_path):
    from app.server import service_ops

    unit_dir = tmp_path / "user-units"
    user = dataclasses.replace(pvp, service_scope="user")
    monkeypatch.setattr(service_ops, "IS_WINDOWS", False)
    monkeypatch.setattr(service_ops, "has_systemd", lambda: True)
    monkeypatch.setattr(service_ops.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(
        "app.shared.systemd_scope.get_scope",
        lambda config=None: SimpleNamespace(
            user_mode=True, unit_dir=unit_dir,
            systemctl_argv=lambda *a, **k: ["systemctl", "--user", *a],
            subprocess_env=lambda: None,
        ),
    )

    service_ops.generate_service_files(user)

    assert (user.server_dir_path / "dayz-server-pvp.service").is_file()
    assert (unit_dir / "dayz-server-pvp.service").is_file()
    assert not (unit_dir / SERVICE_NAME).exists(), "main's unit is not touched"


# --- timers ---------------------------------------------------------------------------


def _with_tasks(config, *ids):
    tasks = [ScheduledTask(id=i, task_type="backup", cron="0 5 * * *").to_dict() for i in ids]
    return dataclasses.replace(config, scheduled_tasks=tasks)


def test_one_instances_timer_install_leaves_anothers_alone(sample_config, tmp_path):
    """Before step 6, installing pvp's timers removed main's as units no task
    owned: the same prefix, and pvp has no task called `nightly`."""
    dest = tmp_path / "units"
    main = _with_tasks(dataclasses.replace(sample_config, instance_id="main"), "nightly")
    pvp = _with_tasks(dataclasses.replace(sample_config, instance_id="pvp"), "hourly")

    install_timers(main, dest_dir=dest)
    install_timers(pvp, dest_dir=dest)

    assert (dest / "dayz-task-nightly.timer").is_file()
    assert (dest / "dayz-task.pvp.hourly.timer").is_file()
    assert timer_install_state(main, unit_dir=dest) == ({"nightly": "installed"}, [])
    assert timer_install_state(pvp, unit_dir=dest) == ({"hourly": "installed"}, [])

    scheduler.remove_timers(pvp, dest_dir=dest)
    assert (dest / "dayz-task-nightly.timer").is_file()
    assert not (dest / "dayz-task.pvp.hourly.timer").exists()


def test_a_restart_task_restarts_its_own_instance(sample_config):
    task = ScheduledTask(id="r", task_type="restart", cron="0 4 * * *")
    content = scheduler.generate_service_content(
        task, dataclasses.replace(sample_config, instance_id="pvp", service_scope="user"),
    )
    assert "restart dayz-server-pvp.service" in content


# --- the CLI ---------------------------------------------------------------------------


def test_service_stop_with_an_instance_stops_that_unit(sample_config, tmp_path, monkeypatch):
    import os

    from click.testing import CliRunner

    from app.__main__ import main
    from app.server import instances
    from app.shared.config import DayzConfig

    instances.save_new_config(DayzConfig(server_dir=str(tmp_path / "main")))
    instances.create_instance("pvp", server_dir=str(tmp_path / "pvp"))
    seen = []
    monkeypatch.setattr("app.commands.service.IS_WINDOWS", False)
    monkeypatch.setattr("app.commands.service.is_server_running", lambda config=None: True)
    monkeypatch.setattr(
        "app.commands.service.systemctl",
        lambda action, **k: seen.append((action, server_unit(k.get("config")))) or SimpleNamespace(ok=True, message=""),
    )
    os.environ.pop("ZSM_INSTANCE", None)

    result = CliRunner().invoke(main, ["--instance", "pvp", "service", "stop"])

    assert result.exit_code == 0, result.output
    assert seen == [("stop", "dayz-server-pvp.service")]


def test_the_doctor_reads_the_instances_unit(pvp, monkeypatch, tmp_path):
    from app.commands import doctor

    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    (unit_dir / SERVICE_NAME).write_text("[Unit]\n")
    monkeypatch.setattr(doctor, "IS_WINDOWS", False, raising=False)
    monkeypatch.setattr(
        "app.shared.systemd_scope.get_scope",
        lambda config=None: SimpleNamespace(unit_dir=unit_dir, query=lambda *a: "", name="user"),
    )

    label, ok, detail = doctor._check_service(pvp)

    assert not ok and "dayz-server-pvp.service" in detail, "main's unit is not pvp's"
