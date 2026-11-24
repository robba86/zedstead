"""Tests for app/server/instance_migration.py (plans/44 step 4, §4a)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from app.server import instance_migration as im
from app.server.instances import load_registry
from app.shared import paths
from app.shared.config import DayzConfig
from tests.test_paths import Q2_PER_INSTANCE

# conftest stubs this for every test so the host's processes decide nothing;
# the tests of the check itself need the real one.
_real_other_panel_running = im.other_panel_running


def _no_panel() -> bool:
    return False


def _tree(root: Path) -> dict[str, tuple[bytes, float]]:
    """Every file under *root*: content and mtime, to prove nothing changed."""
    return {
        str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime)
        for p in sorted(root.rglob("*"))
        if p.is_file() and not p.is_symlink()
        and not {"instances", "instances.json"} & set(p.relative_to(root).parts)
    }


@pytest.fixture
def legacy(sample_config) -> Path:
    """A pre-instances install with every Q2 file, plus machine-wide and
    unknown ones that must stay where they are."""
    sample_config.server_name = "Legacy"
    sample_config.save()
    root = paths.config_dir()
    for name in Q2_PER_INSTANCE.values():
        if name == "config.json":
            continue
        path = root / name
        if "." in name:
            path.write_text(f"{name}\n")
        else:
            path.mkdir()
            (path / "sub").mkdir()
            (path / "sub" / f"{name}.bin").write_bytes(b"x" * 64)
    (root / "users.json").write_text("{}")
    (root / "maps").mkdir()
    (root / "maps" / "chernarusplus.jpg").write_bytes(b"jpg")
    (root / "config.json.backup").write_text("{}")
    return root


def test_every_per_instance_file_lands_in_main_and_the_originals_stay(legacy):
    before = _tree(legacy)

    outcome = im.migrate(panel_running=_no_panel)

    assert outcome.status == "done"
    main = legacy / "instances" / "main"
    for name in Q2_PER_INSTANCE.values():
        assert (main / name).exists(), name
    assert _tree(legacy) == before, "the originals are only read"
    assert DayzConfig.load().server_name == "Legacy"
    assert DayzConfig.load().instance_id == "main"
    registry = load_registry()
    assert (registry.ids(), registry.default) == (["main"], "main")
    assert "originals" in outcome.message and str(legacy) in outcome.message


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes")
def test_a_config_json_left_at_0644_ends_up_0600_in_both_places(legacy):
    """A file last saved before c081c521 is 0644, copy2 kept that mode, and
    nothing saves the original again (plans/68 SEC-11)."""
    import stat

    (legacy / "config.json").chmod(0o644)
    before = _tree(legacy)

    assert im.migrate(panel_running=_no_panel).status == "done"

    for path in (legacy / "config.json", legacy / "instances" / "main" / "config.json"):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600, path
    assert _tree(legacy) == before, "only the mode changed"


def test_machine_wide_and_unknown_files_are_not_copied(legacy):
    im.migrate(panel_running=_no_panel)

    main = legacy / "instances" / "main"
    assert sorted(p.name for p in main.iterdir()) == sorted(Q2_PER_INSTANCE.values())
    assert not (main / "users.json").exists()
    assert not (main / "maps").exists()
    assert not (main / "config.json.backup").exists()


def test_a_backup_shares_its_inode_and_metrics_do_not(legacy):
    im.migrate(panel_running=_no_panel)

    main = legacy / "instances" / "main"
    backup = Path("backups") / "sub" / "backups.bin"
    assert (main / backup).stat().st_ino == (legacy / backup).stat().st_ino
    assert (main / "metrics.sqlite").stat().st_ino != (legacy / "metrics.sqlite").stat().st_ino

    # Written in place by the new instance: the original must not see it.
    (main / "metrics.sqlite").write_text("changed")
    assert (legacy / "metrics.sqlite").read_text() == "metrics.sqlite\n"


def test_a_filesystem_without_hardlinks_gets_a_copy(legacy, monkeypatch):
    def no_links(*_a, **_k):
        raise OSError("links not supported")

    monkeypatch.setattr(im.os, "link", no_links)

    assert im.migrate(panel_running=_no_panel).status == "done"
    backup = Path("backups") / "sub" / "backups.bin"
    main = legacy / "instances" / "main"
    assert (main / backup).read_bytes() == (legacy / backup).read_bytes()
    assert (main / backup).stat().st_ino != (legacy / backup).stat().st_ino


def test_a_symlinked_member_stays_a_symlink(legacy, tmp_path):
    elsewhere = tmp_path / "big-disk" / "backups"
    elsewhere.mkdir(parents=True)
    (elsewhere / "a.zip").write_bytes(b"zip")
    import shutil

    shutil.rmtree(legacy / "backups")
    (legacy / "backups").symlink_to(elsewhere)

    im.migrate(panel_running=_no_panel)

    moved = legacy / "instances" / "main" / "backups"
    assert moved.is_symlink()
    assert moved.resolve() == elsewhere.resolve()


def test_a_failure_half_way_leaves_nothing_and_the_legacy_config_still_loads(legacy, monkeypatch):
    real_place = im._place
    calls = []

    def fail_second(src, dst, link):
        calls.append(src)
        if len(calls) == 2:
            raise PermissionError("denied")
        real_place(src, dst, link)

    monkeypatch.setattr(im, "_place", fail_second)
    before = _tree(legacy)

    outcome = im.migrate(panel_running=_no_panel)

    assert outcome.status == "failed"
    assert "denied" in outcome.message and "next start tries again" in outcome.message
    instances = legacy / "instances"
    assert not (instances / "main").exists()
    assert not (instances / im.STAGING_NAME).exists()
    assert not paths.instances_file().exists()
    assert not (instances / im.LOCK_NAME).exists()
    assert _tree(legacy) == before
    config = DayzConfig.load()
    assert (config.instance_id, config.server_name) == ("", "Legacy")


def test_a_config_dir_it_cannot_write_is_not_fatal(legacy, monkeypatch):
    def read_only(*_a, **_k):
        raise PermissionError("read-only file system")

    monkeypatch.setattr(im.os, "open", read_only)

    outcome = im.migrate(panel_running=_no_panel)

    assert outcome.status == "failed"
    assert "read-only" in outcome.message
    assert DayzConfig.load().instance_id == ""


def test_a_failed_registry_write_takes_main_back_out(legacy, monkeypatch):
    def broken(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(im, "save_registry", broken)

    assert im.migrate(panel_running=_no_panel).status == "failed"
    assert not (legacy / "instances" / "main").exists()


def test_an_interrupted_run_is_cleaned_up_and_done_again(legacy):
    instances = legacy / "instances"
    (instances / im.STAGING_NAME / "junk").mkdir(parents=True)
    (instances / "main").mkdir()
    (instances / "main" / "config.json").write_text('{"server_name": "half"}')

    assert im.migrate(panel_running=_no_panel).status == "done"

    assert not (instances / im.STAGING_NAME).exists()
    assert DayzConfig.load().server_name == "Legacy"


def test_a_second_run_does_nothing(legacy):
    im.migrate(panel_running=_no_panel)
    (legacy / "instances" / "main" / "chat_log.jsonl").write_text("kept\n")

    assert im.migrate(panel_running=_no_panel) == im.NOT_NEEDED
    assert (legacy / "instances" / "main" / "chat_log.jsonl").read_text() == "kept\n"


def test_nothing_to_migrate_on_a_fresh_box(sample_config):
    assert im.migrate(panel_running=_no_panel) == im.NOT_NEEDED
    assert not paths.instances_dir().exists()


def test_a_held_lock_runs_on_the_legacy_layout(legacy):
    lock = legacy / "instances" / im.LOCK_NAME
    lock.parent.mkdir()
    lock.write_text("999\n")

    outcome = im.migrate(panel_running=_no_panel)

    assert outcome.status == "busy"
    assert lock.exists(), "someone else's lock is left alone"
    assert not paths.instances_file().exists()
    assert DayzConfig.load().instance_id == ""


def test_a_stale_lock_is_taken_over(legacy):
    lock = legacy / "instances" / im.LOCK_NAME
    lock.parent.mkdir()
    lock.write_text("999\n")
    old = time.time() - im.STALE_LOCK_SECONDS - 5
    os.utime(lock, (old, old))

    assert im.migrate(panel_running=_no_panel).status == "done"
    assert not lock.exists()


def test_a_running_panel_refuses_and_says_restart(legacy):
    outcome = im.migrate(panel_running=lambda: True)

    assert outcome.status == "refused"
    assert "Restart the app" in outcome.message
    assert not (legacy / "instances" / "main").exists()
    assert not paths.instances_file().exists()


def test_not_enough_space_refuses_before_copying(legacy, monkeypatch):
    class Usage:
        free = 10

    monkeypatch.setattr(im.shutil, "disk_usage", lambda _p: Usage)

    outcome = im.migrate(panel_running=_no_panel)

    assert outcome.status == "failed"
    assert "MB needed" in outcome.message
    assert not (legacy / "instances" / "main").exists()
    assert not (legacy / "instances" / im.STAGING_NAME).exists()


def test_linked_bytes_do_not_count_against_the_space(legacy, monkeypatch):
    (legacy / "backups" / "big.zip").write_bytes(b"z" * 4 * 1024 * 1024)

    class Usage:
        free = 1024 * 1024

    monkeypatch.setattr(im.shutil, "disk_usage", lambda _p: Usage)

    assert im.migrate(panel_running=_no_panel).status == "done"


# --- an old panel -------------------------------------------------------------


@pytest.mark.parametrize("argv, panel", [
    (["/home/u/.local/bin/zsm", "web"], True),
    (["/home/u/.local/bin/zsm"], True),
    (["/usr/bin/python3", "/home/u/.local/bin/zsm", "--instance", "pvp", "web", "--dev"], True),
    (["/opt/venv/bin/python", "-m", "app", "web"], True),
    (["/opt/venv/bin/python3.12", "-u", "-m", "app"], True),
    (["C:\\Users\\u\\.local\\bin\\zsm.exe", "-y", "web"], True),
    (["zsm", "--instance=pvp", "web"], True),
    (["zsm", "mods", "list"], False),
    (["zsm", "--instance", "web", "run"], False),
    (["zsm", "--version"], False),
    (["code", "/home/user/Code/zsm"], False),
    (["tail", "-f", "zsm"], False),
    (["python", "-m", "pytest"], False),
    (["python", "-c", "import app"], False),
    ([], False),
])
def test_which_processes_are_a_panel(argv, panel):
    assert im.is_panel_argv(argv) is panel


def test_our_own_process_and_its_parents_are_not_another_panel(monkeypatch):
    me = os.getpid()
    table = [
        (1, 0, ["/sbin/init"]),
        (500, 1, ["C:\\bin\\zsm.exe", "web"]),  # the launcher that started us
        (me, 500, ["python", "zsm-script.py", "web"]),
    ]
    monkeypatch.setattr(im, "_process_table", lambda: table)
    assert _real_other_panel_running() is False

    table.append((900, 1, ["/home/u/.local/bin/zsm", "web"]))
    assert _real_other_panel_running() is True


def test_an_unreadable_process_table_does_not_block_the_upgrade(monkeypatch):
    def broken():
        raise OSError("no /proc")

    monkeypatch.setattr(im, "_process_table", broken)
    assert _real_other_panel_running() is False


def test_the_linux_process_table_is_readable():
    if not Path("/proc/self/stat").exists():
        pytest.skip("no /proc")
    rows = {pid: (ppid, argv) for pid, ppid, argv in im._linux_processes()}
    assert rows[os.getpid()][0] == os.getppid()


def test_the_registry_it_writes_is_the_one_load_registry_reads(legacy):
    im.migrate(panel_running=_no_panel)
    data = json.loads(paths.instances_file().read_text())
    assert data == {"default": "main", "instances": [{"id": "main", "label": ""}]}
