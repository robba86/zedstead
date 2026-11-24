"""`tests/conftest.py::bohemia_ce`: Bohemia's mission files, read at a pinned
commit, and the summary that names the tests a missing fixture skipped.

No download happens here: each test replaces `_ce_download` and points the
cache at its own `tmp_path`. The text the cutting helpers get is invented.
"""

from __future__ import annotations

import re
import urllib.error
from collections import Counter
from types import SimpleNamespace

import pytest

from tests import conftest
from tests.conftest import (
    CE_1_29,
    CE_SKIP,
    PRIVATE_SKIP,
    bohemia_ce,
    ce_block,
    ce_lines,
    ce_pick,
    count_fixture_skip,
    missing_fixture_lines,
)

PATH = "dayzOffline.chernarusplus/db/globals.xml"


@pytest.fixture()
def cache(monkeypatch, tmp_path):
    """An empty cache and no file read yet in this run."""
    monkeypatch.setattr(conftest, "_ce_cache_dir", lambda: tmp_path)
    monkeypatch.setattr(conftest, "_ce_texts", {})
    return tmp_path


@pytest.mark.parametrize("error", [
    urllib.error.URLError("[Errno -3] Temporary failure in name resolution"),
    urllib.error.HTTPError(f"{conftest.CE_RAW}/x", 404, "Not Found", None, None),
    TimeoutError("The read operation timed out"),
], ids=["offline", "http-404", "timeout"])
def test_a_failed_download_skips_the_test(cache, monkeypatch, error):
    def fail(url):
        raise error

    monkeypatch.setattr(conftest, "_ce_download", fail)
    with pytest.raises(pytest.skip.Exception, match=re.escape(f"{CE_SKIP}: {PATH}")):
        bohemia_ce(CE_1_29, PATH)
    assert not list(cache.rglob("*"))


def test_the_url_is_the_pinned_commit_and_a_second_read_does_not_download(cache, monkeypatch):
    urls = []

    def download(url):
        urls.append(url)
        return b'<?xml version="1.0"?>\r\n<variables>\r\n</variables>'

    monkeypatch.setattr(conftest, "_ce_download", download)
    text = bohemia_ce(CE_1_29, PATH)
    assert urls == [f"https://raw.githubusercontent.com/BohemiaInteractive/DayZ-Central-Economy/{CE_1_29}/{PATH}"]
    assert text == '<?xml version="1.0"?>\r\n<variables>\r\n</variables>'  # line ends as they were
    assert (cache / CE_1_29 / PATH).read_bytes() == text.encode()

    # Another run: nothing in memory, the cache on disk.
    monkeypatch.setattr(conftest, "_ce_texts", {})
    monkeypatch.setattr(conftest, "_ce_download", lambda url: pytest.fail("downloaded again"))
    assert bohemia_ce(CE_1_29, PATH) == text


def test_the_cache_is_pytest_s_own(pytestconfig):
    """In pytest's cache dir, which `.gitignore` covers, and the same dir in
    every test (`_isolate_config_dir` gives each test its own config dir)."""
    if not pytestconfig.pluginmanager.hasplugin("cacheprovider"):
        pytest.skip("run with -p no:cacheprovider")
    assert conftest._ce_cache_dir() == pytestconfig.cache.mkdir("bohemia-ce")


TEXT = (
    '<?xml version="1.0"?>\r\n<events>\r\n'
    '    <event name="A">\r\n        <nominal>1</nominal>\r\n    </event>\r\n'
    '    <event name="B"/>\r\n'
    '    <event name="C">\r\n        <nominal>3</nominal>\r\n    </event>\r\n'
    "</events>"
)


def test_lines_keep_their_ends_and_the_last_one_has_none():
    lines = ce_lines(TEXT)
    assert lines[0] == '<?xml version="1.0"?>\r\n' and lines[-1] == "</events>"
    assert "".join(lines) == TEXT


def test_a_pick_follows_the_one_before_it():
    lines = ce_lines(TEXT)
    assert ce_pick(lines, 'name="C"', "</event>") == ['    <event name="C">\r\n', "    </event>\r\n"]


def test_a_block_ends_where_its_element_or_bracket_closes():
    lines = ce_lines(TEXT)
    assert "".join(ce_block(lines, 'name="A"')) == '    <event name="A">\r\n        <nominal>1</nominal>\r\n    </event>\r\n'
    assert ce_block(lines, 'name="B"') == ['    <event name="B"/>\r\n']
    json_lines = ce_lines('{\n\t"Areas": [\n\t\t{ "Name": "X",\n\t\t\t"Pos": [ 1, 2 ]\n\t\t},\n\t\t{ "Name": "Y" }\n\t]\n}')
    assert "".join(ce_block(json_lines, '"X"')) == '\t\t{ "Name": "X",\n\t\t\t"Pos": [ 1, 2 ]\n\t\t},\n'


# ── the private fixtures ──────────────────────────────────────────────


def test_a_private_fixture_is_read_byte_for_byte(monkeypatch, tmp_path):
    (tmp_path / "rpt").mkdir()
    (tmp_path / "rpt" / "x.RPT").write_bytes(b"line one\r\nline two\t\r\nno final newline")
    monkeypatch.setattr(conftest, "PRIVATE", tmp_path)
    assert conftest.private_fixture("rpt/x.RPT") == "line one\r\nline two\t\r\nno final newline"


def test_a_missing_private_fixture_skips_the_test(monkeypatch, tmp_path):
    monkeypatch.setattr(conftest, "PRIVATE", tmp_path / "private")
    with pytest.raises(pytest.skip.Exception, match=re.escape(f"{PRIVATE_SKIP}: rpt/x.RPT")):
        conftest.private_fixture("rpt/x.RPT")


# ── the summary of missing fixtures ───────────────────────────────────


def _report(nodeid: str, reason: str, skipped: bool = True):
    return SimpleNamespace(skipped=skipped, nodeid=nodeid, longrepr=("x.py", 1, f"Skipped: {reason}"))


def _no_skips() -> dict:
    return {CE_SKIP: Counter(), PRIVATE_SKIP: Counter()}


def test_a_skip_for_a_missing_fixture_is_counted_by_kind_and_test_file():
    skips = _no_skips()
    for report in [
        _report("tests/test_a.py::test_one", f"{CE_SKIP}: db/types.xml (url): offline"),
        _report("tests/test_a.py::TestX::test_two[lf]", f"{CE_SKIP}: db/events.xml (url): offline"),
        _report("tests/test_b.py::test_three", f"{PRIVATE_SKIP}: rpt/stock.RPT"),
        _report("tests/test_c.py::test_four", "dayz-docs/vanilla is not in this checkout"),
        _report("tests/test_d.py::test_five", f"{CE_SKIP}: x", skipped=False),
    ]:
        count_fixture_skip(report, skips)
    assert skips == {CE_SKIP: Counter({"tests/test_a.py": 2}), PRIVATE_SKIP: Counter({"tests/test_b.py": 1})}


def test_the_summary_names_the_count_the_files_and_the_fix():
    skips = _no_skips()
    skips[CE_SKIP].update({"tests/test_a.py": 2, "tests/test_0.py": 1})
    skips[PRIVATE_SKIP].update({"tests/test_b.py": 1})
    assert missing_fixture_lines(skips) == [
        "3 tests skipped because Bohemia's mission files could not be downloaded:",
        "  tests/test_0.py (1)",
        "  tests/test_a.py (2)",
        "  Fix: let this machine reach raw.githubusercontent.com, then run the tests again.",
        "1 test skipped because tests/private/ does not hold their fixture (server output):",
        "  tests/test_b.py (1)",
        "  Expected for a contributor: the maintainer keeps these fixtures out of the repository.",
        "This run is not green: the tests above did not run.",
    ]


def test_a_run_with_every_fixture_has_no_summary():
    assert missing_fixture_lines(_no_skips()) == []


def test_the_section_is_written_only_when_a_fixture_was_missing(monkeypatch):
    written = []
    reporter = SimpleNamespace(
        section=lambda title, **kw: written.append(("section", title)),
        line=lambda text, **kw: written.append(("line", text)),
    )
    skips = _no_skips()
    skips[CE_SKIP]["tests/test_a.py"] = 1
    monkeypatch.setattr(conftest, "_fixture_skips", skips)
    conftest.pytest_terminal_summary(reporter, 0, None)
    assert written[0] == ("section", "fixtures missing: this run is not green")
    assert written[-1] == ("line", "This run is not green: the tests above did not run.")

    written.clear()
    monkeypatch.setattr(conftest, "_fixture_skips", _no_skips())
    conftest.pytest_terminal_summary(reporter, 0, None)
    assert written == []
