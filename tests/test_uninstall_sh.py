"""`uninstall.sh` against a throwaway home.

Nothing here reaches the machine's own units: HOME and the system unit
directory point into tmp, and `systemctl`, `sudo` and `uv` are stubs that only
write down what they were asked. The stubs come first on PATH, so the real
ones are never run.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
UNINSTALL_SH = REPO_ROOT / "uninstall.sh"

PANEL = "zsm-web.service"


@pytest.fixture()
def box(tmp_path) -> Path:
    """A home with zsm's source and config dir, and a PATH of stubs."""
    home = tmp_path / "home"
    (home / ".local/share/zsm").mkdir(parents=True)
    (home / ".config/zsm").mkdir(parents=True)
    (home / ".config/zsm/config.json").write_text("{}", encoding="utf-8")
    (home / ".config/systemd/user").mkdir(parents=True)
    (tmp_path / "system").mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("systemctl", "sudo", "uv"):
        stub = bin_dir / name
        stub.write_text(f'#!/bin/sh\necho "{name} $*" >> "{tmp_path}/calls"\n', encoding="utf-8")
        stub.chmod(0o755)
    return tmp_path


def _run(box: Path, answers: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["sh", str(UNINSTALL_SH)],
        input=answers,
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(box),
        env={
            "HOME": str(box / "home"),
            "PATH": f"{box / 'bin'}:/usr/bin:/bin",
            "TERM": "dumb",
            "ZSM_SYSTEM_UNIT_DIR": str(box / "system"),
        },
    )


def _calls(box: Path) -> list[str]:
    calls = box / "calls"
    return calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []


@pytest.mark.parametrize("answer", ["delete", "DELETE", "Delete"])
def test_the_config_dir_goes_only_for_the_typed_word_delete(box, answer):
    result = _run(box, f"y\n{answer}\n")
    assert result.returncode == 0, result.stderr
    assert not (box / "home/.config/zsm").exists()
    assert "Config removed." in result.stdout


@pytest.mark.parametrize("answer", ["", "y", "Y", "yes", "n", "delet", "delete it", "x", " "])
def test_anything_else_keeps_the_config_dir(box, answer):
    """The question deleted the config dir by a slip twice in the first-hour
    test (2026 Q4): once on one unshown keypress, and once on "y" and Enter
    typed straight after the first question, whose answer is also y. So a y
    keeps the folder now. An empty line is also what an Enter left over from
    the first question reads as."""
    result = _run(box, f"y\n{answer}\n")
    assert result.returncode == 0, result.stderr
    assert (box / "home/.config/zsm/config.json").is_file()
    assert "Config kept at" in result.stdout
    assert "Press Enter to keep it. To delete it as well, type delete and press Enter:" in result.stdout
    # Someone who typed something is told why nothing was deleted.
    assert ("only the word delete removes it" in result.stdout) == bool(answer.strip())


def test_no_answer_at_all_keeps_the_config_dir(box):
    result = _run(box, "y\n")  # input ends before the second question
    assert result.returncode == 0, result.stderr
    assert (box / "home/.config/zsm/config.json").is_file()


def test_the_panels_service_is_stopped_and_removed_before_the_tool(box):
    """It starts zsm from the tool being removed: left installed it stayed
    enabled and kept running from the deleted files, answering 404."""
    units = box / "home/.config/systemd/user"
    (units / PANEL).write_text("[Unit]\n", encoding="utf-8")
    wants = units / "default.target.wants"
    wants.mkdir()
    (wants / PANEL).symlink_to(units / PANEL)

    result = _run(box, "y\n\n")

    assert result.returncode == 0, result.stderr
    assert not (units / PANEL).exists()
    assert not (wants / PANEL).is_symlink()
    calls = _calls(box)
    stop = calls.index(f"systemctl --user disable --now {PANEL}")
    assert stop < calls.index("uv tool uninstall zsm")
    assert "systemctl --user daemon-reload" in calls


def test_a_system_scope_panel_service_goes_through_root(box):
    (box / "system" / PANEL).write_text("[Unit]\n", encoding="utf-8")

    result = _run(box, "y\n\n")

    assert result.returncode == 0, result.stderr
    calls = _calls(box)
    # Not root in a test, so each step is handed to sudo (the stub).
    assert f"sudo systemctl disable --now {PANEL}" in calls
    assert f"sudo rm -f {box / 'system' / PANEL}" in calls
    assert "sudo systemctl daemon-reload" in calls


def test_what_stays_is_named_with_the_command_that_removes_it(box):
    """The DayZ server's service starts DayZ directly and is not zsm's to
    delete. Unmentioned, it read as a leftover of a finished uninstall."""
    units = box / "home/.config/systemd/user"
    for name in ("dayz-server.service", "dayz-server-test.service", "dayz-task-restart.timer"):
        (units / name).write_text("[Unit]\n", encoding="utf-8")
    (units / "something-else.service").write_text("[Unit]\n", encoding="utf-8")

    result = _run(box, "y\n\n")

    assert result.returncode == 0, result.stderr
    for name in ("dayz-server.service", "dayz-server-test.service", "dayz-task-restart.timer"):
        assert (units / name).is_file()
    out = result.stdout
    names = "dayz-server.service dayz-server-test.service dayz-task-restart.timer"
    assert f"In {units}: {names}" in out
    assert f"systemctl --user disable --now {names}; cd {units} && rm -f {names}; systemctl --user daemon-reload" in out
    assert "something-else" not in out
    assert "Left alone: the DayZ server folder, SteamCMD and the downloaded mods." in out
    # Nothing was done to them.
    assert not any("dayz-server" in call for call in _calls(box))


def test_a_box_without_units_gets_no_service_lines(box):
    result = _run(box, "y\n\n")
    assert result.returncode == 0, result.stderr
    assert "Still installed" not in result.stdout
    assert _calls(box) == ["uv tool uninstall zsm"]


def test_declining_changes_nothing(box):
    units = box / "home/.config/systemd/user"
    (units / PANEL).write_text("[Unit]\n", encoding="utf-8")
    result = _run(box, "n\n")
    assert result.returncode == 0
    assert "Uninstall cancelled." in result.stdout
    assert (units / PANEL).is_file()
    assert (box / "home/.local/share/zsm").is_dir()
    assert _calls(box) == []
