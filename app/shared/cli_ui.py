"""CLI UI helpers — Rich-styled prints + InquirerPy-backed prompts.

They live in `app/shared/` because every top-level CLI subcommand needs them
and they're tiny — not enough to justify a separate framework package.
"""

from __future__ import annotations

import asyncio
import re
import sys
import threading
import time
from collections.abc import Callable

from InquirerPy import inquirer
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel

console = Console()

# The style tags a module's own lines carry. Only these are removed, so a
# mod name or path with brackets in it is never read as markup.
_STYLE_TAG = re.compile(r"(?<!\\)\[/?(?:bold|dim|green|yellow|red|cyan)(?: (?:bold|dim|green|yellow|red|cyan))*\]")


def plain_text(line: str) -> str:
    """A line written as Rich markup, as the text a job log or a
    `markup=False` print shows: the style tags gone, `escape()`'s backslash
    before a bracket taken off."""
    return _STYLE_TAG.sub("", line).replace("\\[", "[")


def markup_emitter(log: Callable[[str], None] | None) -> Callable[[str], None]:
    """Where a module that writes Rich markup sends its lines: the console
    renders them, a caller's log gets `plain_text` (the update pipeline and
    the web's job log print a line as it is)."""
    if log is None:
        return console.print
    return lambda line: log(plain_text(line))


# ---------------------------------------------------------------------------
# Print helpers
# ---------------------------------------------------------------------------

def print_header(text: str) -> None:
    console.print(Panel(f"[bold]{text}[/bold]", expand=False))


def print_success(text: str) -> None:
    console.print(f"[green]✅ {text}[/green]")


def print_warning(text: str) -> None:
    console.print(f"[yellow]⚠️  {text}[/yellow]")


def print_error(text: str) -> None:
    console.print(f"[red]❌ {text}[/red]")


def print_step(step: int, total: int, text: str) -> None:
    console.print()
    console.print(f"[bold cyan]▶ [{step}/{total}][/bold cyan] {text}")


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

def stdin_is_a_tty() -> bool:
    """`sys.stdin.isatty()`, defensively. A stdin that is closed, replaced by a
    test harness or absent under `pythonw` must not raise out of a prompt."""
    try:
        return bool(sys.stdin is not None and sys.stdin.isatty())
    except (AttributeError, ValueError):
        return False


def confirm(
    prompt: str,
    *,
    timeout: int = 60,
    default: bool = False,
    non_interactive: bool = False,
) -> bool:
    """Yes/no prompt. Returns `default` on timeout or when non-interactive.

    **The timeout applies only when stdin is not a tty**, which is the case it
    was written for: stopping an unattended run from hanging forever. It used
    to apply to a person at a keyboard too, silently — the docstring claimed a
    countdown and nothing was displayed until it had already fired. Measured on
    the stage 8b box (F27): `zsm service firewall --apply` prints eight
    commands, an RCon exposure warning and an `incomplete:` note, tells the
    operator to read them, and then took the run away sixty seconds later.

    A visible countdown was the other option and is worse: a timer on a prompt
    someone is meant to think about is pressure, and the prompts here are the
    destructive ones. Most callers pass `default=True` — under the old
    behaviour, walking away from "Delete all log files?" *confirmed* it.

    **`confirm_within` is the deliberate exception**, and the two must not be
    merged: this one asks *"shall I do this?"*, where silence means the
    operator is thinking; that one asks *"did it work?"*, where silence means
    they are gone. See its docstring and plans/34.
    """
    if non_interactive:
        # A No under -y used to end the command without a word
        # (`zsm -y mods remove`): say that -y does not answer yes here.
        if not default:
            console.print(
                f"[dim]{escape(prompt)} No: -y does not confirm this step. "
                "Run the command without -y to answer it.[/dim]"
            )
        return default

    result: list[bool] = []

    def _ask() -> None:
        try:
            answer = inquirer.confirm(message=prompt, default=default).execute()
            result.append(answer)
        except (EOFError, KeyboardInterrupt):
            result.append(False)

    ask_thread = threading.Thread(target=_ask, daemon=True)
    ask_thread.start()

    deadline = None if stdin_is_a_tty() else timeout
    start = time.monotonic()
    while ask_thread.is_alive():
        if deadline is not None and time.monotonic() - start >= deadline:
            action = "yes" if default else "no"
            console.print(f"\n[dim]Timeout — auto-selecting {action}[/dim]")
            return default
        # Poll rather than a bare join() even when there is no deadline, so
        # Ctrl-C stays responsive in the main thread.
        ask_thread.join(timeout=1)

    return result[0] if result else default


def confirm_within(prompt: str, *, seconds: float, on_timeout_label: str) -> bool:
    """Yes/no whose deadline is the point: True only on an explicit yes, typed
    within *seconds*. Timeout, no, Ctrl-C and end-of-input all return False.

    The inverse of `confirm`, on purpose (plans/34):

    ============  ======================  ===========================
                  `confirm`               `confirm_within`
    ============  ======================  ===========================
    question      shall I do this?        did it work?
    silence       they are thinking       they are gone
    tty deadline  **no**                  **yes** — it is the net
    countdown     none (it is pressure)   visible and updating
    ============  ======================  ===========================

    `confirm`'s argument against a countdown is right there and inverted here:
    the operator must see how long they have, because the only way to answer
    honestly is from a *second* session they may still be opening. So the
    deadline applies whether or not stdin is a tty, and a toolbar under the
    prompt counts it down.

    With no terminal at all nobody can answer, so this returns False at once
    rather than waiting out the clock to conclude the same thing.

    *on_timeout_label* is printed when the deadline passes — the caller's
    words, because this module must not know what is being undone.

    Not a thread like `confirm`: a timed-out prompt left running in a daemon
    thread would keep owning the terminal (raw mode, no echo) after the
    function returned, on exactly the path this exists for. prompt_toolkit's
    own `prompt_async` under `asyncio.wait_for` is cancelled cleanly and hands
    the terminal back; Ctrl-C still interrupts it at once. Do not call this
    from inside a running event loop.
    """
    if not stdin_is_a_tty():
        console.print(
            f"[yellow]No terminal to answer on, so nobody can confirm — "
            f"{on_timeout_label}[/yellow]"
        )
        return False

    deadline = time.monotonic() + seconds
    try:
        answer = asyncio.run(_ask_until(prompt, deadline))
    except (asyncio.TimeoutError, TimeoutError):  # distinct classes before 3.11
        console.print(f"\n[yellow]No answer within {seconds:g} s — {on_timeout_label}[/yellow]")
        return False
    except (EOFError, KeyboardInterrupt):
        return False
    return answer.strip().lower() in ("y", "yes")


def countdown_text(deadline: float) -> str:
    """The toolbar under a `confirm_within` prompt."""
    left = max(0, int(deadline - time.monotonic() + 0.999))
    return f" {left} s left — no answer means no "


async def _ask_until(prompt: str, deadline: float) -> str:
    return await asyncio.wait_for(
        _prompt_async(f"{prompt} [y/N] ", lambda: countdown_text(deadline)),
        timeout=max(0.0, deadline - time.monotonic()),
    )


async def _prompt_async(message: str, toolbar) -> str:
    """The one call into prompt_toolkit — the seam tests replace."""
    from prompt_toolkit import PromptSession

    return await PromptSession().prompt_async(
        message, bottom_toolbar=toolbar, refresh_interval=0.5
    )


def select(prompt: str, choices: list[str]) -> str:
    """Arrow-key single selection."""
    return inquirer.select(message=prompt, choices=choices).execute()
