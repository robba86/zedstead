"""Guards the links from the app to its docs on zedstead.com.

The CLI's table is `app/shared/doc_links.py::COMMAND_DOCS`, and the web UI's is
`webui/src/docLinks.json`. Every doc and anchor they name must exist in the
zedstead-site checkout (`tests/conftest.py::site_doc`, skipped without it), so a
renamed heading on the site fails here instead of opening the page top.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.shared.doc_links import COMMAND_DOCS
from tests.conftest import site_doc

ROOT = Path(__file__).resolve().parents[1]
WEB_LINKS = json.loads((ROOT / "webui" / "src" / "docLinks.json").read_text(encoding="utf-8"))["pages"]


def _anchors(doc: str) -> set[str]:
    """The heading ids the site gives *doc*: GitHub's rule, as in zedstead-site
    `src/data/docs.js::slugger` (lower case, punctuation dropped, each space a
    hyphen, -1, -2 … for a repeat). Headings inside code fences are skipped."""
    seen: dict[str, int] = {}
    ids: set[str] = set()
    fenced = False
    for line in site_doc(f"{doc}.md").read_text(encoding="utf-8").splitlines():
        if line.startswith("```"):
            fenced = not fenced
            continue
        m = None if fenced else re.match(r"^#{1,6} (.+)$", line)
        if not m:
            continue
        text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", m.group(1)).replace("`", "")
        base = re.sub(r"\s", "-", re.sub(r"[^\w\s-]", "", text.lower().strip()))
        n = seen.get(base, 0)
        seen[base] = n + 1
        ids.add(f"{base}-{n}" if n else base)
    return ids


def _targets() -> list[tuple[str, str, str]]:
    """Every (where, doc, anchor) the CLI and the web UI link to."""
    out = [(f"zsm {name}", doc, anchor) for name, (doc, anchor) in COMMAND_DOCS.items()]
    for page, links in WEB_LINKS.items():
        out += [(f"web {page}", doc, anchor) for doc, anchor, _label in links]
    return out


@pytest.mark.parametrize(("where", "doc", "anchor"), _targets())
def test_every_doc_link_points_at_a_real_section(where: str, doc: str, anchor: str):
    ids = _anchors(doc)
    assert not anchor or anchor in ids, f"{where} links to {doc}#{anchor}, which {doc}.md has no heading for"


def test_every_command_has_a_docs_link():
    """A new top-level command gets its doc section in COMMAND_DOCS."""
    from app.__main__ import main

    missing = sorted(set(main.commands) - set(COMMAND_DOCS))
    assert not missing, f"commands without a COMMAND_DOCS entry: {missing}"
    stale = sorted(set(COMMAND_DOCS) - set(main.commands))
    assert not stale, f"COMMAND_DOCS names commands that do not exist: {stale}"


def test_subcommand_help_carries_its_groups_docs_link():
    from click.testing import CliRunner

    from app.__main__ import main

    result = CliRunner().invoke(main, ["mods", "list", "--help"])
    assert result.exit_code == 0, result.output
    assert "Docs: https://zedstead.com/docs/cli/#mods-maps-presets" in result.output


def test_every_web_page_key_is_a_page_help_key_or_route():
    """A docLinks.json key is a PAGE_HELP key or a routes.js path, so a page
    can find its links by the key it already uses."""
    known = set(re.findall(r"^\s*'([^']+)':", (ROOT / "webui/src/pageHelp.js").read_text(encoding="utf-8"), re.M))
    known |= set(re.findall(r'path: "([^"]+)"', (ROOT / "webui/src/routes.js").read_text(encoding="utf-8")))
    unknown = sorted(set(WEB_LINKS) - known)
    assert not unknown, f"docLinks.json keys that are no page: {unknown}"
