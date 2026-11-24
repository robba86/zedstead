"""Guards the Help page against the code it describes.

The Help was app/data/HELP.md, rendered by the panel's /help page, until it
moved to zedstead.com in 2026 Q4 as `src/docs/help.md` in the zedstead-site repo.
In 2026 Q4 that page went too: its getting-started list and its tour of the
panel's pages are part of `web-ui.md`, and its troubleshooting is
`troubleshooting.md`. These tests read both from that checkout
(`tests/conftest.py::site_doc`) and skip without it.

Its `[label](zsm:<slug>)` links used to be resolved by the panel; the site
shows them as text, so there is no slug contract left to check here.
"""

from __future__ import annotations

import re

import pytest

from tests.conftest import site_doc


@pytest.fixture(scope="module")
def help_text() -> str:
    return "\n".join(site_doc(name).read_text(encoding="utf-8") for name in ("web-ui.md", "troubleshooting.md"))


def test_the_help_is_present_and_substantial(help_text: str):
    for heading in ("## Getting started", "## The pages", "# Troubleshooting"):
        assert heading in help_text, f"the help lost its {heading!r} section"


def test_the_cli_reference_documents_the_real_commands():
    """A command that has been renamed should not linger in the CLI reference.

    Help carried its own copy of the reference until 2026 Q4; it links to
    cli.md now, so this reads that page."""
    from app.__main__ import main

    text = site_doc("cli.md").read_text(encoding="utf-8")
    documented = set(re.findall(r"^zsm ([a-z][a-z-]*)", text, re.MULTILINE))
    assert len(documented) > 10, "the parser found no commands: did the page's format change?"
    registered = set(main.commands)
    unknown = sorted(documented - registered)
    assert not unknown, f"cli.md documents commands that don't exist: {unknown}"
