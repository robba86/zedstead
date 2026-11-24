"""Tests for the sudo-availability helpers in app.shared.platform."""

import app.shared.platform as plat


def _set(monkeypatch, *, windows, root, sudo):
    monkeypatch.setattr(plat, "IS_WINDOWS", windows)
    monkeypatch.setattr(plat, "IS_ROOT", root)
    monkeypatch.setattr(
        plat.shutil, "which", lambda name: "/usr/bin/sudo" if sudo else None
    )


def test_sudo_prefix_present_nonroot(monkeypatch):
    """sudo present, not root, Linux -> prefix with sudo, auth needed."""
    _set(monkeypatch, windows=False, root=False, sudo=True)
    assert plat.sudo_prefix() == ["sudo"]
    assert plat.needs_sudo_auth() is True


def test_sudo_prefix_already_root(monkeypatch):
    """Already root -> no sudo prefix, no auth prompt."""
    _set(monkeypatch, windows=False, root=True, sudo=True)
    assert plat.sudo_prefix() == []
    assert plat.needs_sudo_auth() is False


def test_sudo_prefix_no_sudo_installed(monkeypatch):
    """sudo missing (e.g. container) -> run directly, no auth prompt."""
    _set(monkeypatch, windows=False, root=False, sudo=False)
    assert plat.sudo_prefix() == []
    assert plat.needs_sudo_auth() is False


def test_sudo_prefix_windows(monkeypatch):
    """Windows -> never uses sudo."""
    _set(monkeypatch, windows=True, root=False, sudo=True)
    assert plat.sudo_prefix() == []
    assert plat.needs_sudo_auth() is False


def test_has_sudo(monkeypatch):
    monkeypatch.setattr(plat.shutil, "which", lambda name: "/usr/bin/sudo")
    assert plat.has_sudo() is True
    monkeypatch.setattr(plat.shutil, "which", lambda name: None)
    assert plat.has_sudo() is False
