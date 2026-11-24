"""Tests for app/shared/os_packages.py and the copies of the package list.

The point of these tests is drift: the list lives in app/data/os-packages.txt,
is mirrored inline in install.sh (bootstrap only) and in docker/Dockerfile
(runtime + container extras). See plans/21. The last block guards the copy that
app/shared/steam.py used to keep and must not grow back — see plans/26.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from app.shared import os_packages
from app.shared.os_packages import (
    SO_TO_PACKAGE,
    bootstrap_packages,
    missing_packages,
    missing_shared_libs,
    read_sections,
    runtime_packages,
    steamcmd_packages,
)
from app.shared.steam import steamcmd_dependency_commands

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL_SH = REPO_ROOT / "install.sh"
DOCKERFILE = REPO_ROOT / "docker" / "Dockerfile"

# Packages the container needs that a VPS does not: an init system, the D-Bus
# system bus, passwordless admin, terminfo for the container shell, and
# ssh-keyscan for the SFTP offsite host key (a VPS has it with its sshd).
CONTAINER_ONLY = {"systemd", "systemd-sysv", "dbus", "sudo", "ncurses-term", "openssh-client"}


# --- the shared file ---------------------------------------------------------


def test_sections_parse():
    sections = read_sections()
    assert set(sections) == {"bootstrap", "steamcmd", "runtime"}
    for name, packages in sections.items():
        assert packages, f"[{name}] is empty"
        for pkg in packages:
            assert not pkg.startswith("#"), f"comment leaked into [{name}]: {pkg}"
            assert not pkg.startswith("["), f"header leaked into [{name}]: {pkg}"
            assert pkg == pkg.strip()


# --- copy 2: install.sh's inline bootstrap trio ------------------------------


def test_install_sh_bootstrap_matches_file():
    match = re.search(r'^BOOTSTRAP_PKGS="([^"]*)"', INSTALL_SH.read_text(encoding="utf-8"), re.M)
    assert match, "BOOTSTRAP_PKGS not found in install.sh"
    assert match.group(1).split() == bootstrap_packages()


# --- copy 3: docker/Dockerfile's apt-get install block -----------------------


def _dockerfile_apt_packages() -> set[str]:
    """Package tokens from the Dockerfile's `apt-get install` continuation block."""
    packages: set[str] = set()
    collecting = False
    for raw in DOCKERFILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if "apt-get install" in line:
            collecting = True
            continue
        if not collecting:
            continue
        if not line.endswith("\\"):
            break
        body = line[:-1].strip()
        if body.startswith("&&") or not body:
            break
        packages.update(body.split())
    return packages


def test_dockerfile_covers_runtime():
    dockerfile_pkgs = _dockerfile_apt_packages()
    assert dockerfile_pkgs, "could not parse the Dockerfile apt-get install block"

    runtime = set(runtime_packages())
    assert runtime <= dockerfile_pkgs, (
        f"docker/Dockerfile is missing runtime packages: {sorted(runtime - dockerfile_pkgs)}"
    )

    surplus = dockerfile_pkgs - runtime
    assert surplus <= CONTAINER_ONLY, (
        "docker/Dockerfile installs packages that are neither in the [runtime] "
        f"section nor container-only: {sorted(surplus - CONTAINER_ONLY)}. Decide "
        "whether they belong on a VPS too (add to app/data/os-packages.txt) or "
        "are container-only (add to CONTAINER_ONLY here)."
    )


# --- ldd probing -------------------------------------------------------------


def test_missing_shared_libs_parses_ldd(monkeypatch, tmp_path):
    binary = tmp_path / "DayZServer"
    binary.write_text("")
    stdout = (
        "\tlinux-vdso.so.1 (0x00007ffd1234)\n"
        "\tlibcurl.so.4 => not found\n"
        "\tlibstdc++.so.6 => /usr/lib/x86_64-linux-gnu/libstdc++.so.6 (0x00007f00)\n"
        "\tlibgcc_s.so.1 => not found\n"
    )
    monkeypatch.setattr(
        os_packages.subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(a[0], 0, stdout, ""),
    )
    assert missing_shared_libs(binary) == ["libcurl.so.4", "libgcc_s.so.1"]


def test_missing_shared_libs_survives_no_ldd(monkeypatch, tmp_path):
    binary = tmp_path / "DayZServer"
    binary.write_text("")

    def boom(*a, **kw):
        raise FileNotFoundError("ldd")

    monkeypatch.setattr(os_packages.subprocess, "run", boom)
    assert missing_shared_libs(binary) == []


def test_missing_shared_libs_absent_binary(tmp_path):
    assert missing_shared_libs(tmp_path / "nope") == []


# --- dpkg probing ------------------------------------------------------------


def test_missing_packages_no_dpkg(monkeypatch):
    """No dpkg-query -> report nothing, rather than claim everything is missing."""

    def boom(*a, **kw):
        raise FileNotFoundError("dpkg-query")

    monkeypatch.setattr(os_packages.subprocess, "run", boom)
    assert missing_packages(["libcurl4", "tar"]) == []


def test_missing_packages_reads_status(monkeypatch):
    def fake_run(argv, **kw):
        status = "install ok installed" if argv[-1] == "tar" else "unknown ok not-installed"
        return subprocess.CompletedProcess(argv, 0, status, "")

    monkeypatch.setattr(os_packages.subprocess, "run", fake_run)
    assert missing_packages(["tar", "libcurl4"]) == ["libcurl4"]


def test_missing_packages_empty_input():
    assert missing_packages([]) == []


# --- the soname -> package map ----------------------------------------------


def test_so_map_entries_are_in_runtime_list():
    runtime = set(runtime_packages())
    for soname, pkg in SO_TO_PACKAGE.items():
        assert pkg in runtime, (
            f"SO_TO_PACKAGE maps {soname} -> {pkg}, which the installer would "
            "never install because it is not in the [runtime] section"
        )


# --- copy 4 that must never come back: app/shared/steam.py -------------------
#
# steam.py used to carry its own inline apt list (`lib32gcc-s1 lib32stdc++6
# curl tar`) — a fourth copy, on the lib32 route while everything else is on
# multiarch, installed by a privileged call whose failure is deliberately
# non-fatal and therefore silent. See plans/26.

STEAM_PY = REPO_ROOT / "app" / "shared" / "steam.py"

# `lib32gcc-s1` is the documented exception: there is no `libgcc-s1:i386`.
LIB32_NOT_ON_THE_MULTIARCH_ROUTE = re.compile(r"^lib32(?!gcc-s1$)")


def _program(cmd: list[str]) -> str:
    """The command being run, with any sudo prefix stripped."""
    tokens = list(cmd)
    while tokens and (tokens[0] == "sudo" or tokens[0].startswith("-")):
        tokens.pop(0)
    return tokens[0] if tokens else ""


def test_steamcmd_section_is_subset_of_runtime():
    """SteamCMD's needs are a subset of the box's — never a separate list."""
    assert set(steamcmd_packages()) <= set(runtime_packages())


def test_steamcmd_packages_not_empty():
    """A section rename would return [] and make steam.py install nothing."""
    assert steamcmd_packages()


def test_no_lib32_route_packages_anywhere():
    """One route, verified once: multiarch. See os-packages.txt's header."""
    for section, packages in read_sections().items():
        for pkg in packages:
            assert not LIB32_NOT_ON_THE_MULTIARCH_ROUTE.match(pkg), (
                f"[{section}] has {pkg}, an amd64 lib32* package. This file is "
                "on the multiarch (<pkg>:i386) route — see its header."
            )


def test_dependency_commands_empty_when_nothing_missing():
    """Nothing missing -> no privileged command at all, so no sudo prompt."""
    assert steamcmd_dependency_commands([]) == []


def test_dependency_commands_add_architecture_only_for_i386():
    with_i386 = steamcmd_dependency_commands(["libstdc++6:i386"])
    assert _program(with_i386[0]) == "dpkg"
    assert with_i386[0][-2:] == ["--add-architecture", "i386"]

    without = steamcmd_dependency_commands(["curl"])
    assert not any(_program(cmd) == "dpkg" for cmd in without)


def test_dependency_commands_use_apt_get_not_apt():
    """`apt` warns it has no stable CLI interface and means it."""
    for cmd in steamcmd_dependency_commands(["curl"]):
        assert _program(cmd) != "apt", cmd


def test_dependency_commands_update_precedes_install():
    """Without an update, a pruned image cannot resolve the names at all."""
    cmds = steamcmd_dependency_commands(["libstdc++6:i386"])
    update = next(i for i, c in enumerate(cmds) if c[-1] == "update")
    install = next(i for i, c in enumerate(cmds) if "install" in c)
    assert update < install


def test_dependency_commands_use_no_install_recommends():
    cmds = steamcmd_dependency_commands(["curl"])
    install = next(c for c in cmds if "install" in c)
    assert "--no-install-recommends" in install


def test_steam_py_has_no_inline_package_list():
    """The anti-regression test for plans/26: the fourth copy stays gone."""
    source = STEAM_PY.read_text(encoding="utf-8")
    for token in ("lib32", ":i386"):
        assert token not in source, (
            f"app/shared/steam.py mentions {token!r} — package names belong in "
            "app/data/os-packages.txt's [steamcmd] section, not here."
        )


# --- virtual packages (libcurl4 -> libcurl4t64 on Ubuntu 24.04) -------------

_PROVIDES_LISTING = (
    "install ok installed|libcurl4 (= 8.18.0-1ubuntu2.4)\n"
    "install ok installed|\n"
    "deinstall ok config-files|libfoo\n"
    "install ok installed|libssl3, libssl3t64 (= 3.0.13)\n"
)


def _dpkg_fake(installed, listing=_PROVIDES_LISTING):
    """Fake dpkg-query: 4 args = per-package status, 3 args = the Provides dump."""

    def fake_run(argv, **kw):
        if len(argv) == 4:
            status = "install ok installed" if argv[-1] in installed else "unknown ok not-installed"
            return subprocess.CompletedProcess(argv, 0, status, "")
        return subprocess.CompletedProcess(argv, 0, listing, "")

    return fake_run


def test_missing_packages_accepts_a_virtual_name(monkeypatch):
    """Ubuntu 24.04 renamed libcurl4 to libcurl4t64, which only *Provides* the
    old name. `dpkg-query -W libcurl4` says not-installed while `apt-get install
    libcurl4` says already installed — so a direct-name check alone left the
    user in a loop with no way out."""
    monkeypatch.setattr(os_packages.subprocess, "run", _dpkg_fake({"tar"}))
    assert missing_packages(["tar", "libcurl4"]) == []


def test_missing_packages_still_reports_a_genuinely_absent_name(monkeypatch):
    monkeypatch.setattr(os_packages.subprocess, "run", _dpkg_fake({"tar"}))
    assert missing_packages(["tar", "libcurl4", "locales"]) == ["locales"]


def test_missing_packages_ignores_provides_for_arch_qualified(monkeypatch):
    """${Provides} carries no architecture, so a match there cannot tell i386
    from amd64 — and a false "present" for a 32-bit dep is the exact failure
    this check exists to prevent."""
    monkeypatch.setattr(
        os_packages.subprocess,
        "run",
        _dpkg_fake(set(), listing="install ok installed|libssl3\n"),
    )
    assert missing_packages(["libssl3:i386"]) == ["libssl3:i386"]


def test_provided_names_parses_versions_and_lists(monkeypatch):
    monkeypatch.setattr(os_packages.subprocess, "run", _dpkg_fake(set()))
    provided = os_packages._provided_names()
    assert "libcurl4" in provided          # version parenthetical stripped
    assert "libssl3" in provided           # comma-separated
    assert "libssl3t64" in provided
    assert "libfoo" not in provided        # not an *installed* package


def test_provided_names_empty_without_dpkg(monkeypatch):
    def boom(*a, **kw):
        raise FileNotFoundError("dpkg-query")

    monkeypatch.setattr(os_packages.subprocess, "run", boom)
    assert os_packages._provided_names() == set()


def test_install_sh_has_the_same_provides_fallback():
    """install.sh mirrors _provided_names for the piped-from-curl path. If one
    grows the fallback and the other does not, the installer and doctor
    disagree about whether the box is ready."""
    text = INSTALL_SH.read_text(encoding="utf-8")
    assert "${Provides}" in text
    assert "query_provides" in text
