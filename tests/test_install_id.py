"""The install id the browser keys its cached jobs on (app/web/install_id.py)."""

from __future__ import annotations

from app.shared import paths
from app.web.install_id import install_id


def test_the_id_is_made_once_and_kept():
    first = install_id()

    assert len(first) == 16
    assert install_id() == first
    assert paths.install_id_file().read_text(encoding="utf-8").strip() == first


def test_a_new_config_dir_gets_a_new_id(monkeypatch, tmp_path):
    """A reinstall starts from an empty config dir, so its jobs cache is not the old one's."""
    first = install_id()
    monkeypatch.setattr(paths, "CONFIG_DIR", tmp_path / "fresh")
    (tmp_path / "fresh").mkdir()

    assert install_id() != first


def test_auth_me_serves_the_id(sample_config):
    from fastapi.testclient import TestClient

    from app.web import server
    from app.web.auth import hash_password

    sample_config.web_password_hash = hash_password("pw-for-test")
    app = server.create_app(sample_config)
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"password": "pw-for-test"})
        assert client.get("/api/auth/me").json()["install"] == install_id()
