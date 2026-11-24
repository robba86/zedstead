"""Tests for default_bind_host resolution in app.shared.platform."""

import types

import app.shared.platform as plat

SSH_VARS = ("SSH_CLIENT", "SSH_TTY", "SSH_CONNECTION")


def _clear_ssh(monkeypatch):
    for var in SSH_VARS:
        monkeypatch.delenv(var, raising=False)


def _cfg(web_bind_host=""):
    return types.SimpleNamespace(web_bind_host=web_bind_host)


def test_config_wins_over_autodetect(monkeypatch):
    """Explicit config.web_bind_host beats env/Docker auto-detect."""
    _clear_ssh(monkeypatch)
    monkeypatch.setattr(plat, "IS_DOCKER", False)
    assert plat.default_bind_host(_cfg("0.0.0.0")) == "0.0.0.0"


def test_ssh_connection_env_binds_all(monkeypatch):
    """SSH_CONNECTION alone (SSH_CLIENT/TTY absent) still binds 0.0.0.0."""
    _clear_ssh(monkeypatch)
    monkeypatch.setattr(plat, "IS_DOCKER", False)
    monkeypatch.setenv("SSH_CONNECTION", "1.2.3.4 5 6.7.8.9 22")
    assert plat.default_bind_host() == "0.0.0.0"
    assert plat.default_bind_host(_cfg("")) == "0.0.0.0"


def test_no_ssh_no_docker_binds_loopback(monkeypatch):
    """Empty config, no SSH env, not Docker -> loopback."""
    _clear_ssh(monkeypatch)
    monkeypatch.setattr(plat, "IS_DOCKER", False)
    assert plat.default_bind_host() == "127.0.0.1"
    assert plat.default_bind_host(_cfg("")) == "127.0.0.1"


def test_docker_binds_all(monkeypatch):
    """Docker binds 0.0.0.0 even without SSH or config override."""
    _clear_ssh(monkeypatch)
    monkeypatch.setattr(plat, "IS_DOCKER", True)
    assert plat.default_bind_host() == "0.0.0.0"


def test_none_config_safe(monkeypatch):
    """Passing config=None doesn't error and falls back to auto-detect."""
    _clear_ssh(monkeypatch)
    monkeypatch.setattr(plat, "IS_DOCKER", False)
    assert plat.default_bind_host(None) == "127.0.0.1"
