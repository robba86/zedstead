"""Tests for the deploy-on-a-vps page — the per-provider VPS guide (TODO F3).

The page is on zedstead.com since 2026 Q4: `src/docs/deploy-on-a-vps.md` in the
zedstead-site repo. These tests read it there and skip without that checkout.

Nothing here creates a VPS. What *can* be checked without one is the thing that
makes a deploy guide worthless when it rots: a command that no longer exists,
a flag that was renamed, or a link to a file somebody moved. Someone following
this page is typing into a fresh box with no way to tell a stale instruction
from their own mistake.

The approach is `tests/test_user_data.py`'s: parse the real commands out of the
document and put them through the real Click CLI.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.conftest import SITE_DOCS, site_doc

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def text() -> str:
    return site_doc("deploy-on-a-vps.md").read_text(encoding="utf-8")


def _fenced(text: str) -> list[str]:
    """Every line inside a fenced code block."""
    lines: list[str] = []
    inside = False
    for line in text.splitlines():
        if line.startswith("```"):
            inside = not inside
            continue
        if inside and line.strip():
            lines.append(line.strip())
    return lines


def _zsm_commands(text: str) -> list[list[str]]:
    """Every `zsm …` invocation in a code block, as argv."""
    out = []
    for line in _fenced(text):
        line = line.split("#")[0].strip()
        if line.startswith("zsm "):
            out.append(line.split()[1:])
    return out


# ── the commands ────────────────────────────────────────────────────────


def test_the_guide_actually_tells_you_to_run_something(text):
    """A guard on the parser: if the fence syntax ever changes, every test
    below would pass by finding nothing."""
    assert len(_zsm_commands(text)) >= 6


def test_every_zsm_command_parses_against_the_real_cli(text):
    """The drift that matters — a renamed command or flag."""
    from click.testing import CliRunner

    from app.__main__ import main

    runner = CliRunner()
    for argv in _zsm_commands(text):
        # --help resolves the whole path and validates the flags without
        # running anything: `zsm service firewall --apply` on the dev box
        # would be a genuinely bad test.
        result = runner.invoke(main, [*argv, "--help"])
        assert result.exit_code == 0, f"zsm {' '.join(argv)} -> {result.output}"


def test_it_never_tells_you_to_run_the_firewall_apply_before_reporting(text):
    """`--apply` sets deny-by-default. Ordering is the whole safety story:
    the report has to come first on the page, or the page is the lockout."""
    report = text.index("zsm service firewall\n")
    apply_at = text.index("zsm service firewall --apply")
    assert report < apply_at


def test_bootstrap_is_reported_before_it_is_applied(text):
    assert text.index("zsm bootstrap-vps --user dayz\n") < text.index(
        "zsm bootstrap-vps --user dayz --apply"
    )


def test_nothing_is_run_from_the_repo_before_it_is_downloaded(text):
    """The guide's own ordering bug, caught once already: step 4 told you to
    run `./install.sh --check-deps` and step 5 was where you downloaded it.
    A reader following the page in order hits "no such file"."""
    download = text.index("install.sh -o /tmp/zsm-install.sh")
    for line in _fenced(text):
        if line.startswith("./install.sh") or line.startswith("sh /tmp/zsm-install.sh"):
            assert text.index(line) > download, line


def test_no_command_is_offered_with_a_bare_sudo(text):
    """zsm never runs bare `sudo` — a tty-less call hangs on the prompt —
    and a guide that teaches the habit undoes that."""
    for line in _fenced(text):
        assert not line.startswith("sudo "), line


def test_there_is_no_heredoc_anywhere(text):
    """Pasting one leaves the reader's terminal at a `heredoc>` prompt."""
    assert "<<'EOF'" not in text and "<<EOF" not in text


# ── the links ───────────────────────────────────────────────────────────


def test_every_relative_link_resolves(text):
    """`../x` is a file of this repo (the site links it on GitHub), `x.md`
    another docs page. The site's build checks the same, anchors included."""
    targets = re.findall(r"\]\((\.\./[^)#]+|[a-zA-Z0-9][^):#]*\.md)(?:#[^)]*)?\)", text)
    assert targets
    for target in targets:
        if target.startswith("../"):
            assert (ROOT / target[3:]).exists(), target
        else:
            assert (SITE_DOCS / target).exists(), target


def test_the_download_one_liners_are_the_readme_s_word_for_word(text):
    """The first thing a stranger types. The README's URLs are known-live;
    the shorter zedstead.com ones are not confirmed to be served yet (TODO F1),
    and a deploy page is the worst place to find that out."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for line in _fenced(text):
        if line.startswith("curl "):
            assert line in readme, line
    assert "https://zedstead.com/preflight.sh -o" not in text


def test_it_points_at_the_reverse_proxy_guide_rather_than_repeating_it(text):
    """B3's recipes live in one file. A second copy here is the one that
    would keep the WebSocket mistake alive after the first is fixed."""
    assert "reverse-proxy.md" in text
    assert "proxy_pass" not in text or "location /ws" in text


def test_the_readme_links_here(text):
    """A guide nobody can find is F3 unfinished."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "https://zedstead.com/docs/deploy-on-a-vps/" in readme


# ── the claims ──────────────────────────────────────────────────────────


def test_it_points_at_the_sizing_table_rather_than_copying_it(text):
    """The sizing table is on zedstead.com/hosting/, rendered from the site's
    SIZING_ROWS (zedstead-site, 2026 Q4). A second copy here is one that can
    disagree with the tool, so the guide links to it instead."""
    assert "https://zedstead.com/hosting/" in text
    assert "| RAM |" not in text


def test_the_port_list_matches_what_the_firewall_would_actually_open(text):
    """Stock ports, named in prose. If a default changes, the prose is wrong
    in the one place a reader would copy it into a provider's firewall UI."""
    from app.ops import firewall_ops
    from app.shared.config import DayzConfig

    ports = firewall_ops.required_ports(DayzConfig())
    assert "2302/udp" in text
    assert str(ports[-1].port) in text  # the Steam query port
    # And it must say the list is derived, not fixed — a reader who changed
    # server_port and copied these numbers has a server nobody can find.
    assert "derived from your config" in text


def test_it_does_not_promise_ten_minutes(text):
    """F3's own title says "10 minutes". Steam Guard needs a human reading
    their phone and SteamCMD downloads several GB, so the page says what it
    can stand behind instead."""
    assert "an hour" in text
