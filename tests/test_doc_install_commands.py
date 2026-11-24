"""One rule, swept across every page a stranger reads: a command that downloads
one of zsm's own scripts must be the README's, character for character.

Two ways that goes wrong, and both had already happened by 2026 Q3:

* **A URL that does not resolve.** `zedstead.com` is bought but not delegated —
  NXDOMAIN straight from the `.app` registry — so the short form the site
  prints downloads nothing, anywhere. The migrating-from-a-host page shipped
  it as the *first* command under "Set up the new box".
* **A different form of the same command.** That same line piped the installer
  into `sh` instead of downloading it and running it, so the one page using an
  unreachable URL was also the one page where a truncated download would run.

`tests/test_deploy_guide.py` pins this for one guide and `tests/test_user_data.py`
for the cloud-init template, which is the copy nobody is watching when it fails.
This is the sweep: the next page written cannot reintroduce it somewhere else.
"""

from __future__ import annotations

from pathlib import Path

from tests.conftest import site_doc_pages

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"

#: The scripts this repo publishes for people to download and run.
OUR_SCRIPTS = ("install.sh", "preflight.sh", "uninstall.sh")


def _pages() -> list[Path]:
    """Every Markdown page written for a reader — the README and the docs.

    The docs live in the `zedstead-site` repo since 2026 Q4 (`src/docs/`,
    found through `tests/conftest.py::SITE_DOCS`); without that checkout the
    sweep covers the README alone. The site's own pages stay out of scope: they
    are *served from* zedstead.com, so the short URLs are correct there. The docs
    are held to the README's form anyway, so a page that moves back here still
    passes. `plans/`, `TODO.md` and `DONE.md` are out because they discuss these
    commands rather than tell anyone to run them.
    """
    return [README] + site_doc_pages()


def _fenced(text: str) -> list[str]:
    lines: list[str] = []
    inside = False
    for line in text.splitlines():
        if line.startswith("```"):
            inside = not inside
            continue
        if inside and line.strip():
            lines.append(line.strip())
    return lines


def _download_lines(text: str) -> list[str]:
    return [
        line
        for line in _fenced(text)
        if line.startswith("curl ") and any(name in line for name in OUR_SCRIPTS)
    ]


def test_the_sweep_actually_finds_commands():
    """Without this the whole file passes by matching nothing — which is what
    a fenced-block parser does the day someone switches to indented blocks."""
    assert len(_download_lines(README.read_text(encoding="utf-8"))) >= 3


def test_every_page_downloads_our_scripts_the_readme_s_way():
    known_good = set(_download_lines(README.read_text(encoding="utf-8")))
    for page in _pages():
        if page == README:
            continue
        for line in _download_lines(page.read_text(encoding="utf-8")):
            assert line in known_good, f"{page.name}: {line}"


def test_no_page_sends_a_reader_to_a_domain_that_does_not_resolve():
    """Separate from the rule above because it is the failure a reader cannot
    diagnose: `curl -fsSL` against an unresolvable host prints nothing and
    exits non-zero, and the next line runs a file that was never written."""
    for page in _pages():
        for line in _download_lines(page.read_text(encoding="utf-8")):
            assert "zedstead.com" not in line, f"{page.name}: {line}"
