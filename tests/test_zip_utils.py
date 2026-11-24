"""Tests for app.shared.zip_utils — zip-slip path containment, owner-only zips."""

from __future__ import annotations

import os
import stat
import zipfile
from pathlib import Path

import pytest

from app.shared.platform import IS_WINDOWS
from app.shared.zip_utils import is_bare_name, private_zip, safe_extract_path


def test_normal_member_resolves_under_dest(tmp_path: Path):
    dest = tmp_path / "out"
    target = safe_extract_path(dest, "data/vehicles.bin")
    assert target == (dest / "data" / "vehicles.bin").resolve()
    # The resolved target is inside dest.
    assert dest.resolve() in target.parents


def test_plain_file_member(tmp_path: Path):
    dest = tmp_path / "out"
    assert safe_extract_path(dest, "players.db") == (dest / "players.db").resolve()


@pytest.mark.parametrize(
    "evil",
    [
        "../escaped.txt",
        "../../etc/cron.d/x",
        "a/b/../../../outside.txt",
        "/etc/passwd",
        "/absolute/evil",
    ],
)
def test_traversal_is_rejected(tmp_path: Path, evil: str):
    dest = tmp_path / "out"
    with pytest.raises(ValueError, match="escapes destination"):
        safe_extract_path(dest, evil)


def test_nested_traversal_that_stays_inside_is_allowed(tmp_path: Path):
    # a/b/../c collapses to a/c — still inside dest, so allowed.
    dest = tmp_path / "out"
    target = safe_extract_path(dest, "a/b/../c.txt")
    assert target == (dest / "a" / "c.txt").resolve()


@pytest.mark.parametrize("name", ["dayzOffline.chernarusplus", "@CF", "a b", "x..y"])
def test_bare_names(name: str):
    assert is_bare_name(name) is True


@pytest.mark.parametrize(
    "name", ["", ".", "..", "a/b", "../x", "/etc", "a\\b", "..\\x", "a\0b"]
)
def test_names_that_are_not_one_component(name: str):
    assert is_bare_name(name) is False


# ── private_zip: archives that carry credentials ────────────────────────


@pytest.fixture()
def umask_022():
    old = os.umask(0o022)
    yield
    os.umask(old)


@pytest.mark.skipif(IS_WINDOWS, reason="POSIX modes; Windows takes the folder's ACL")
def test_private_zip_is_owner_only_under_a_permissive_umask(tmp_path: Path, umask_022):
    target = tmp_path / "out.zip"
    with private_zip(target, compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("config.json", '{"rcon_password": "x"}')
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    with zipfile.ZipFile(target) as zf:
        assert zf.read("config.json") == b'{"rcon_password": "x"}'


@pytest.mark.skipif(IS_WINDOWS, reason="POSIX modes; Windows takes the folder's ACL")
def test_private_zip_tightens_a_file_that_was_already_there(tmp_path: Path, umask_022):
    """`O_CREAT` keeps an existing file's mode; the writer must not."""
    target = tmp_path / "out.zip"
    target.write_bytes(b"old")
    target.chmod(0o644)
    with private_zip(target) as zf:
        zf.writestr("a.txt", "new")
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    with zipfile.ZipFile(target) as zf:
        assert zf.namelist() == ["a.txt"]


@pytest.mark.skipif(IS_WINDOWS, reason="POSIX modes; Windows takes the folder's ACL")
def test_private_zip_still_writes_where_modes_cannot_be_set(tmp_path: Path, monkeypatch):
    """A backup folder on vfat or exFAT refuses chmod; the archive is still made."""
    def _refuse(fd, mode):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(os, "fchmod", _refuse)
    target = tmp_path / "out.zip"
    with private_zip(target) as zf:
        zf.writestr("a.txt", "x")
    with zipfile.ZipFile(target) as zf:
        assert zf.read("a.txt") == b"x"
