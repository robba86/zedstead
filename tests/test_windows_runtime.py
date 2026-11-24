"""The Windows runtimes DayZServer_x64.exe needs (`app/server/windows_runtime.py`).

Measured on a fresh Windows 11 (first-hour test, 2026 Q4): neither is
there, Windows names MSVCP140.dll and then XAPOFX1_5.dll, and the server runs
once both are installed.
"""

from __future__ import annotations

import dataclasses
import os

import pytest

from app.server import windows_runtime


@pytest.fixture
def windows(monkeypatch, sample_config, tmp_path):
    """A config whose server folder, system folders and PATH are all under
    tmp_path, with the module told it is on Windows."""
    monkeypatch.setattr(windows_runtime, "IS_WINDOWS", True)
    root = tmp_path / "Windows"
    (root / "System32").mkdir(parents=True)
    on_path = tmp_path / "tools"
    on_path.mkdir()
    monkeypatch.setenv("SystemRoot", str(root))
    monkeypatch.setenv("PATH", os.pathsep.join([str(on_path), str(tmp_path / "gone")]))
    server = tmp_path / "dayz-server"
    server.mkdir()
    config = dataclasses.replace(sample_config, server_dir=str(server))
    return config, root, on_path


def test_a_fresh_windows_has_neither_runtime(windows):
    config, _root, _on_path = windows

    assert [r.dll for r in windows_runtime.missing_runtimes(config)] == ["MSVCP140.dll", "XAPOFX1_5.dll"]
    problem = windows_runtime.runtime_problem(config)
    assert "MSVCP140.dll and XAPOFX1_5.dll are missing" in problem
    # Each runtime with the one step that installs it, as the install docs page has them.
    assert str(windows_runtime.VC_REDIST) in problem
    assert config.server_dir in problem
    assert windows_runtime.DIRECTX_URL in problem


def test_one_missing_runtime_is_named_alone(windows):
    config, root, _on_path = windows
    (root / "System32" / "MSVCP140.dll").write_bytes(b"")

    problem = windows_runtime.runtime_problem(config)

    assert "XAPOFX1_5.dll is missing" in problem
    assert "MSVCP140" not in problem
    assert "VC_redist" not in problem


def test_both_runtimes_installed_is_no_problem(windows):
    config, root, _on_path = windows
    for dll in ("MSVCP140.dll", "XAPOFX1_5.dll"):
        (root / "System32" / dll).write_bytes(b"")

    assert windows_runtime.missing_runtimes(config) == []
    assert windows_runtime.runtime_problem(config) is None


@pytest.mark.parametrize("where", ["server", "windows", "system", "path"])
def test_a_dll_counts_wherever_the_loader_looks(windows, where):
    """The check may miss a broken install, but must not call a working one
    broken: a start is ended on its word."""
    config, root, on_path = windows
    (root / "System").mkdir()
    folder = {
        "server": config.server_dir_path,
        "windows": root,
        "system": root / "System",
        "path": on_path,
    }[where]
    for dll in ("MSVCP140.dll", "XAPOFX1_5.dll"):
        (folder / dll).write_bytes(b"")

    assert windows_runtime.runtime_problem(config) is None


def test_nothing_is_missing_off_windows(monkeypatch, sample_config):
    monkeypatch.setattr(windows_runtime, "IS_WINDOWS", False)

    assert windows_runtime.missing_runtimes(sample_config) == []
    assert windows_runtime.runtime_problem(sample_config) is None
