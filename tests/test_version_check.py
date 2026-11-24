"""The update check asks this project's GitHub releases, never a package index."""

from __future__ import annotations

import json
import time

import pytest

from app.shared import version_check


class _Resp:
    def __init__(self, status: int, body: object = None):
        self.status_code = status
        self._body = body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(f"{self.status_code}")

    def json(self) -> object:
        return self._body


@pytest.fixture
def fake_get(monkeypatch):
    calls: list[str] = []
    answer: dict = {"resp": _Resp(404)}

    def _get(url, **kwargs):
        calls.append(url)
        return answer["resp"]

    monkeypatch.setattr("requests.get", _get)
    return calls, answer


def test_asks_the_latest_github_release(fake_get):
    calls, answer = fake_get
    answer["resp"] = _Resp(200, {"tag_name": "v2.0.0"})
    assert version_check.check_for_update("1.0.0") == "2.0.0"
    assert calls == ["https://api.github.com/repos/robba86/zedstead/releases/latest"]


def test_no_release_yet_is_no_update(fake_get):
    calls, _ = fake_get
    assert version_check.check_for_update("1.0.0") is None
    # The 404 is cached like any answer, so the next call does not ask again.
    assert version_check.check_for_update("1.0.0") is None
    assert len(calls) == 1


@pytest.mark.parametrize("tag", ["2.0.0-rc1<b>", "<script>", "", None, 7, "release"])
def test_a_tag_that_is_not_a_plain_version_is_no_update(fake_get, tag):
    _, answer = fake_get
    answer["resp"] = _Resp(200, {"tag_name": tag})
    assert version_check.check_for_update("1.0.0") is None


def test_older_or_equal_release_is_no_update(fake_get):
    _, answer = fake_get
    answer["resp"] = _Resp(200, {"tag_name": "1.0.0"})
    assert version_check.check_for_update("1.0.0") is None


def test_a_cache_written_by_the_old_source_is_ignored(fake_get):
    calls, answer = fake_get
    cache = version_check._cache_file()
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"latest": "99.0.0", "ts": time.time()}), encoding="utf-8")
    answer["resp"] = _Resp(200, {"tag_name": "v1.0.1"})
    assert version_check.check_for_update("1.0.0") == "1.0.1"
    assert len(calls) == 1


def test_a_network_failure_is_silent(monkeypatch):
    import requests

    def _boom(url, **kwargs):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr("requests.get", _boom)
    assert version_check.check_for_update("1.0.0") is None


def test_turned_off_it_makes_no_request(fake_get, sample_config):
    """version_check_enabled off: no request, and no banner from a cached answer."""
    calls, answer = fake_get
    answer["resp"] = _Resp(200, {"tag_name": "v2.0.0"})
    assert version_check.check_for_update("1.0.0") == "2.0.0"  # cached now
    calls.clear()
    sample_config.version_check_enabled = False
    assert version_check.check_for_update("1.0.0", config=sample_config) is None
    assert calls == []


def test_on_by_default(fake_get, sample_config):
    calls, answer = fake_get
    answer["resp"] = _Resp(200, {"tag_name": "v2.0.0"})
    assert sample_config.version_check_enabled is True
    assert version_check.check_for_update("1.0.0", config=sample_config) == "2.0.0"
    assert len(calls) == 1
