"""Tests for app/shared/cli_ui.py.

`confirm` is covered for its timeout: the prompts behind it are the
destructive ones, and the timeout used to fire on a person at a keyboard with
nothing on screen saying a timer was running (F27, measured on the stage 8b
box — `service firewall --apply` prints eight commands and an RCon warning,
tells the operator to read them, and took the run away sixty seconds later).

`confirm_within` is its deliberate opposite (plans/34): a deadline that
applies to a person at a keyboard too, because silence there means they lost
the connection. The last test here pins the two disagreeing.

Every test here uses a fraction-of-a-second timeout. **Do not write one that
really waits for the 60s default.**
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from app.shared import cli_ui


class _FakePrompt:
    """Stands in for `inquirer.confirm(...)`. `release` lets a test hold the
    prompt open past the deadline without sleeping for a fixed time."""

    def __init__(self, answer: bool, release: threading.Event):
        self.answer = answer
        self.release = release

    def execute(self) -> bool:
        self.release.wait(timeout=5)
        return self.answer


@pytest.fixture
def prompt(monkeypatch):
    release = threading.Event()
    answers: list[bool] = [True]

    def fake_confirm(message, default):  # noqa: ARG001 — signature must match
        return _FakePrompt(answers[0], release)

    monkeypatch.setattr(cli_ui.inquirer, "confirm", fake_confirm)
    try:
        yield release, answers
    finally:
        release.set()


def test_confirm_times_out_when_stdin_is_not_a_tty(monkeypatch, prompt, capsys):
    """The case the timeout was written for: an unattended run must not hang."""
    release, _answers = prompt
    monkeypatch.setattr(cli_ui, "stdin_is_a_tty", lambda: False)

    assert cli_ui.confirm("Apply these rules?", timeout=0.05, default=False) is False
    assert "Timeout" in capsys.readouterr().out
    assert not release.is_set()  # it really did give up on a prompt still open


def test_confirm_returns_default_true_on_timeout_without_a_tty(monkeypatch, prompt):
    """`default` is not touched by this change — a non-tty timeout still
    answers whatever the caller asked for, including True."""
    monkeypatch.setattr(cli_ui, "stdin_is_a_tty", lambda: False)
    assert cli_ui.confirm("Overwrite?", timeout=0.05, default=True) is True


def test_confirm_does_not_time_out_a_person_at_a_keyboard(monkeypatch, prompt):
    """F27. With a tty there is no deadline: the operator is reading, and the
    answer they eventually give is the one that counts."""
    release, answers = prompt
    answers[0] = True
    monkeypatch.setattr(cli_ui, "stdin_is_a_tty", lambda: True)

    threading.Timer(0.2, release.set).start()
    start = time.monotonic()
    answer = cli_ui.confirm("Apply these rules?", timeout=0.05, default=False)

    assert answer is True
    assert time.monotonic() - start > 0.05  # it outlived the timeout it was given


def test_confirm_non_interactive_never_prompts(monkeypatch):
    """No thread, no tty question — `--yes` and web callers take this path."""
    def explode(*_a, **_k):
        raise AssertionError("non_interactive must not reach the prompt")

    monkeypatch.setattr(cli_ui.inquirer, "confirm", explode)
    assert cli_ui.confirm("Apply?", non_interactive=True, default=True) is True
    assert cli_ui.confirm("Apply?", non_interactive=True, default=False) is False


def test_confirm_says_why_a_no_under_yes(monkeypatch):
    """`zsm -y mods remove` answered No and ended without a word."""
    lines = []
    monkeypatch.setattr(cli_ui.console, "print", lambda text, **_k: lines.append(text))
    cli_ui.confirm("Remove 1 mod(s)?", non_interactive=True, default=False)
    cli_ui.confirm("Apply?", non_interactive=True, default=True)
    assert len(lines) == 1
    assert "Remove 1 mod(s)? No: -y does not confirm this step." in lines[0]


def test_stdin_is_a_tty_survives_a_broken_stdin(monkeypatch):
    """A closed or replaced stdin must not raise out of a prompt — pytest's own
    capture replaces it, which is exactly the shape that would bite."""
    class _Closed:
        def isatty(self):
            raise ValueError("I/O operation on closed file")

    monkeypatch.setattr(cli_ui.sys, "stdin", _Closed())
    assert cli_ui.stdin_is_a_tty() is False
    monkeypatch.setattr(cli_ui.sys, "stdin", None)
    assert cli_ui.stdin_is_a_tty() is False


# ── confirm_within — the prompt whose deadline is the safety net ─────────


@pytest.fixture
def typed(monkeypatch):
    """Replace the prompt_toolkit call. ``answer`` None = the operator never
    types anything; the fake waits on an event nobody sets."""
    state: dict = {"answer": None, "asked": 0}

    async def fake_prompt(message, toolbar):  # noqa: ARG001
        state["asked"] += 1
        state["toolbar"] = toolbar()
        if state["answer"] is None:
            await asyncio.Event().wait()
        return state["answer"]

    monkeypatch.setattr(cli_ui, "_prompt_async", fake_prompt)
    monkeypatch.setattr(cli_ui, "stdin_is_a_tty", lambda: True)
    return state


def test_confirm_within_an_explicit_yes_is_true(typed):
    typed["answer"] = "y"
    assert cli_ui.confirm_within("Still connected?", seconds=5, on_timeout_label="x") is True


@pytest.mark.parametrize("answer", ["", "n", "no", "maybe"])
def test_confirm_within_anything_but_yes_is_false(typed, answer):
    typed["answer"] = answer
    assert cli_ui.confirm_within("Still connected?", seconds=5, on_timeout_label="x") is False


def test_confirm_within_times_out_at_a_keyboard_and_says_what_happens(typed, capsys):
    start = time.monotonic()
    assert (
        cli_ui.confirm_within(
            "Still connected?", seconds=0.05, on_timeout_label="the firewall reverts"
        )
        is False
    )
    assert time.monotonic() - start < 2
    out = capsys.readouterr().out
    assert "No answer within 0.05 s" in out
    assert "the firewall reverts" in out


def test_confirm_within_shows_a_countdown(typed):
    typed["answer"] = "y"
    cli_ui.confirm_within("Still connected?", seconds=90, on_timeout_label="x")
    assert typed["toolbar"].strip().startswith("90 s left")


def test_confirm_within_ctrl_c_is_not_a_yes(monkeypatch):
    async def interrupted(message, toolbar):  # noqa: ARG001
        raise KeyboardInterrupt

    monkeypatch.setattr(cli_ui, "_prompt_async", interrupted)
    monkeypatch.setattr(cli_ui, "stdin_is_a_tty", lambda: True)
    assert cli_ui.confirm_within("Still connected?", seconds=5, on_timeout_label="x") is False


def test_confirm_within_without_a_terminal_answers_at_once(monkeypatch, typed, capsys):
    # Nobody can answer, so waiting out the clock only delays the same answer.
    monkeypatch.setattr(cli_ui, "stdin_is_a_tty", lambda: False)
    start = time.monotonic()
    assert (
        cli_ui.confirm_within("Still connected?", seconds=30, on_timeout_label="it reverts")
        is False
    )
    assert time.monotonic() - start < 1
    assert typed["asked"] == 0
    assert "it reverts" in capsys.readouterr().out


def test_confirm_and_confirm_within_disagree_about_a_keyboard(monkeypatch, prompt, typed):
    """The tripwire (plans/34 point 4). At a tty, `confirm` waits for the
    operator however long they take; `confirm_within` gives up at its
    deadline. Collapse either into the other and this fails — which is the
    point: one of them deleted is either F27 back, or the safety net gone."""
    release, answers = prompt
    answers[0] = True
    threading.Timer(0.2, release.set).start()
    assert cli_ui.confirm("Apply?", timeout=0.05, default=False) is True

    assert cli_ui.confirm_within("Still connected?", seconds=0.05, on_timeout_label="x") is False


def test_plain_text_drops_style_tags_and_keeps_a_name_s_brackets():
    from rich.markup import escape

    line = f"[dim]  Copied {escape('[red]k.bikey')} from @CF[/dim] [bold cyan]ok[/bold cyan]"
    assert cli_ui.plain_text(line) == "  Copied [red]k.bikey from @CF ok"
    assert cli_ui.plain_text("[CE] Missing @Mod[1]") == "[CE] Missing @Mod[1]"
