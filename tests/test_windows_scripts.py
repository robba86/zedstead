"""The Windows install and uninstall scripts, read as text.

Nothing here runs them: there is no cmd or PowerShell where the suite runs.
These are the mistakes that can be seen without running, each found by the
first-hour test on a fresh Windows 11 (2026 Q4). What the scripts do is
checked by hand on a Windows machine.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(name: str) -> str:
    return (REPO_ROOT / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("name", ["install.bat", "uninstall.bat"])
def test_no_printed_line_holds_a_lone_exclamation_mark(name):
    """Both files run with delayed expansion, where a lone "!" swallows the
    rest of the line: uninstall.bat's "[!] Config dir holds ..." printed as
    "[" and the question below it had lost its subject."""
    text = _read(name)
    assert "EnableDelayedExpansion" in text
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.upper().startswith("REM"):
            continue
        rest = re.sub(r"![A-Za-z_][A-Za-z0-9_]*!", "", stripped)
        assert "!" not in rest, f"{name}:{number}: {stripped}"


def test_the_bat_uninstaller_stops_before_it_removes_anything_while_zsm_runs():
    """With the panel running, uv failed half way with "Access is denied" and
    the script went on to remove the shortcut and to blame the terminal."""
    text = _read("uninstall.bat")
    running = text.index("zsm is still running")
    uninstall = text.index("\n    uv tool uninstall zsm\n")
    failed = text.index("uv could not remove zsm")
    step_2 = text.index("Step 2: Remove the cloned source")

    assert running < uninstall < failed < step_2
    for start in (running, failed):
        assert "exit /b 1" in text[start:step_2].split(")", 1)[0]
    # The folder reaches PowerShell through the environment, never the command line.
    assert "$env:ZSM_TOOL_DIR" in text and "%TOOL_DIR%" not in text.split("powershell", 1)[1].split("\n", 1)[0]


def test_the_bat_uninstaller_has_no_label_to_jump_to():
    """The scripts are stored with LF line endings, and cmd is known to miss a
    label in such a file; uninstall.bat is written without one."""
    text = _read("uninstall.bat")
    assert "\r\n" not in text, "with CRLF endings this rule can go"
    lines = [line.strip().lower() for line in text.splitlines()]
    assert not any(line.startswith(":") for line in lines)
    assert not any(re.search(r"\bgoto\b", line) for line in lines if not line.startswith("rem"))


def test_the_powershell_uninstaller_does_the_same():
    text = _read("uninstall.ps1")
    running = text.index("zsm is still running")
    uninstall = text.index("\n        uv tool uninstall zsm")
    failed = text.index("uv could not remove zsm")
    step_2 = text.index("Step 2: Remove the cloned source")

    assert running < uninstall < failed < step_2
    assert "return" in text[running:uninstall]
    assert "return" in text[failed:step_2]
    # An "Access is denied" from uv used to be reported as this.
    assert "was not installed as a uv tool" not in text


def test_both_uninstallers_delete_the_config_only_for_the_typed_word():
    assert 'if /i "!ANSWER!"=="delete" (' in _read("uninstall.bat")
    assert "-eq 'delete'" in _read("uninstall.ps1")
