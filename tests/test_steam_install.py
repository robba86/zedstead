"""Tests for the SteamCMD auto-installer (app/shared/steam.py): its dependency step and its download.

Nothing here may run apt, apt-get, dpkg or sudo — `_guard_subprocess` fails the
test if a package-manager argv reaches subprocess. The argv itself is asserted
in tests/test_os_packages.py against the pure `steamcmd_dependency_commands`.
See plans/26.
"""

from __future__ import annotations

import subprocess

import pytest

from app.shared import os_packages, steam

PACKAGE_MANAGERS = {"apt", "apt-get", "dpkg", "sudo"}


def _guard_subprocess(monkeypatch, calls: list[list[str]]):
    """Record every subprocess argv; explode on a package-manager command.

    The download itself is failed deliberately — this module tests the
    dependency step, and `_install_steamcmd_linux` returning False afterwards
    is the expected outcome in a sandbox with no network.
    """

    def check(argv):
        calls.append(list(argv))
        if PACKAGE_MANAGERS & set(argv):
            raise AssertionError(f"a package manager was invoked: {' '.join(argv)}")

    def fake_run(argv, **kwargs):
        check(argv)
        return subprocess.CompletedProcess(argv, 1, "", "")

    def fake_popen(argv, **kwargs):
        check(argv)
        raise OSError("no network in tests")

    monkeypatch.setattr(steam.subprocess, "run", fake_run)
    monkeypatch.setattr(steam.subprocess, "Popen", fake_popen)


def test_install_steamcmd_linux_skips_apt_when_nothing_missing(monkeypatch, tmp_path):
    """A provisioned box must not be asked for a sudo password to do nothing."""
    monkeypatch.setattr(os_packages, "missing_packages", lambda names: [])
    calls: list[list[str]] = []
    _guard_subprocess(monkeypatch, calls)

    lines: list[str] = []
    assert steam._install_steamcmd_linux(tmp_path / "steamcmd", log=lines.append) is False

    assert any("already present" in line for line in lines), lines
    assert not any(PACKAGE_MANAGERS & set(argv) for argv in calls), calls


def test_install_steamcmd_linux_reports_still_missing_packages(monkeypatch, tmp_path):
    """A failed dependency step names packages, not a .so an hour later."""
    monkeypatch.setattr(os_packages, "missing_packages", lambda names: ["libcurl4"])
    monkeypatch.setattr(
        steam, "steamcmd_dependency_commands", lambda missing: [["true", *missing]]
    )
    calls: list[list[str]] = []
    _guard_subprocess(monkeypatch, calls)  # every fake_run returns exit 1

    lines: list[str] = []
    assert steam._install_steamcmd_linux(tmp_path / "steamcmd", log=lines.append) is False

    assert any("Dependency step failed" in line for line in lines), lines
    assert any("Still missing: libcurl4" in line for line in lines), lines
    # The warning alone is not the invariant — a non-zero apt-get must not stop
    # the install either. Without this the branch could `return False` here and
    # every other assertion in this test would still hold.
    assert any(argv[0] == "curl" for argv in calls), (
        "a failed dependency step aborted the install before the download; the "
        "steamcmd.sh check is the real gate (plans/26 STOP condition)"
    )


def test_install_steamcmd_linux_dependency_failure_is_not_fatal(monkeypatch, tmp_path):
    """The steamcmd.sh check is the real gate — apt failing must not stop us.

    Guards the invariant that predates plans/26: a non-Debian host has no apt
    at all, and bailing here would delete a working SteamCMD install.
    """
    monkeypatch.setattr(os_packages, "missing_packages", lambda names: ["libcurl4"])
    monkeypatch.setattr(
        steam, "steamcmd_dependency_commands", lambda missing: [["definitely-not-a-real-binary"]]
    )

    def fake_run(argv, **kwargs):
        raise OSError("No such file or directory")

    reached_download = []

    def fake_popen(argv, **kwargs):
        reached_download.append(list(argv))
        raise OSError("no network in tests")

    monkeypatch.setattr(steam.subprocess, "run", fake_run)
    monkeypatch.setattr(steam.subprocess, "Popen", fake_popen)

    lines: list[str] = []
    assert steam._install_steamcmd_linux(tmp_path / "steamcmd", log=lines.append) is False
    assert reached_download, "an OSError from the dependency step stopped the install"
    assert any("Could not run" in line for line in lines), lines


# ---------------------------------------------------------------------------
# The download itself (plans/68 NET-12)
# ---------------------------------------------------------------------------


def _fake_download(monkeypatch, *, curl_rc: int, curl_err: bytes, tar_rc: int):
    """Fake curl (Popen) and tar (run); record curl's argv. Nothing is fetched."""
    import io

    seen: dict[str, list[str]] = {}

    class _Curl:
        def __init__(self, argv, **_kwargs):
            seen["curl"] = list(argv)
            self.stdout = io.BytesIO(b"")
            self.stderr = io.BytesIO(curl_err)

        def wait(self):
            return curl_rc

    def fake_run(argv, **_kwargs):
        seen["tar"] = list(argv)
        return subprocess.CompletedProcess(argv, tar_rc, "gzip: stdin: unexpected end of file", "")

    monkeypatch.setattr(os_packages, "missing_packages", lambda names: [])
    monkeypatch.setattr(steam.subprocess, "Popen", _Curl)
    monkeypatch.setattr(steam.subprocess, "run", fake_run)
    return seen


def test_the_steamcmd_download_ignores_curlrc_and_fails_on_http_errors(monkeypatch, tmp_path):
    """curl reads ~/.curlrc unless `-q` is the FIRST argument; `-sqL` was not."""
    seen = _fake_download(monkeypatch, curl_rc=0, curl_err=b"", tar_rc=0)
    steam._install_steamcmd_linux(tmp_path / "steamcmd", log=lambda _s: None)

    argv = seen["curl"]
    assert argv[0] == "curl"
    assert argv[1] == "-q"
    short = "".join(a[1:] for a in argv[1:] if a.startswith("-") and not a.startswith("--"))
    assert "f" in short and "S" in short and "L" in short
    assert argv[argv.index("--proto") + 1] == "=https"
    assert argv[argv.index("--proto-redir") + 1] == "=https"
    assert "--tlsv1.2" in argv
    assert float(argv[argv.index("--max-time") + 1]) > 0
    assert argv[-1] == steam.STEAMCMD_TAR_URL


def test_a_failed_steamcmd_download_is_reported_as_curls_failure(monkeypatch, tmp_path):
    """With `-f` curl writes no body, so tar fails too; the log names curl."""
    _fake_download(
        monkeypatch,
        curl_rc=22,
        curl_err=b"curl: (22) The requested URL returned error: 404",
        tar_rc=2,
    )
    lines: list[str] = []
    assert steam._install_steamcmd_linux(tmp_path / "steamcmd", log=lines.append) is False
    assert any("Download failed (curl exit 22)" in line and "404" in line for line in lines), lines
    assert not any("Extract failed" in line for line in lines), lines


def test_the_dockerfile_downloads_steamcmd_with_the_same_curl_flags():
    """The image fetches the same tarball; the two copies must not drift."""
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "docker" / "Dockerfile").read_text()
    start = text.index("# --- SteamCMD")
    block = text[start : text.index("# ---", start + 1)]
    command = " ".join(
        line.strip().rstrip("\\").strip()
        for line in block.splitlines()
        if not line.lstrip().startswith("#")
    )
    words = command.split()
    curl = words.index("curl")
    flags = steam.STEAMCMD_CURL_ARGV[1:-1]
    assert words[curl + 1 : curl + 1 + len(flags)] == flags, command
    assert steam.STEAMCMD_TAR_URL in words
    # Not piped: under /bin/sh a pipe reports only tar's status.
    assert "|" not in words, command


def test_the_windows_steamcmd_download_has_a_timeout(monkeypatch, tmp_path):
    """urlopen without a timeout waits forever on a stalled connection."""
    import urllib.request

    seen: dict = {}

    def fake_urlopen(url, *args, **kwargs):
        seen["url"] = url
        seen["timeout"] = kwargs.get("timeout", args[1] if len(args) > 1 else None)
        raise OSError("no network in tests")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    lines: list[str] = []
    assert steam._install_steamcmd_windows(tmp_path / "steamcmd", log=lines.append) is False
    assert seen["url"] == steam.STEAMCMD_ZIP_URL
    assert seen["timeout"] and seen["timeout"] > 0
    assert any("Failed to download SteamCMD" in line for line in lines), lines
