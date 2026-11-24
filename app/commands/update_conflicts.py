"""zsm update-conflicts — stock mission files a DayZ update changed after you had (plans/67).

A server update keeps the admin's changes to Bohemia's mission files. When
Bohemia changed one of those files too, the two are merged value by value
(plans/71): each change comes in, and a value both changed follows the
`mission_update_conflict` setting and is listed here. A file that cannot be
merged waits here whole, as before. Both versions are in Versions.
"""

from __future__ import annotations

import click
from rich.console import Console

from app.ops import mission_stock
from app.shared.cli_ui import print_success, print_warning
from app.shared.config import DayzConfig

console = Console()


def _plain(line: str) -> None:
    console.print(line, markup=False)


@click.group(name="update-conflicts")
def update_conflicts_group():
    """Stock mission files both you and a DayZ update changed."""


@update_conflicts_group.command(name="list")
def list_cmd():
    """List the files waiting for a choice."""
    config = DayzConfig.load()
    rows = mission_stock.list_conflicts(config)
    if not rows:
        console.print("No update conflicts are waiting.")
        return
    whole = False
    for row in rows:
        build = f"DayZ build {row.get('build_before') or '?'} -> {row.get('build_after') or '?'}"
        kind = "merged" if row.get("merged") else "whole file"
        console.print(f"{row['file']}  ({kind}, {build}, {row.get('time', '')})", markup=False, soft_wrap=True)
        whole = whole or not row.get("merged")
        for value in row.get("values", []):
            console.print(f"  {_value_line(value)}", markup=False, highlight=False, soft_wrap=True)
    if any(row.get("merged") for row in rows):
        console.print(
            "Values: zsm update-conflicts keep-mine|keep-dayz <file> --value <id>, or dismiss <file> "
            "--value <id> to leave what is live. Without --value, a merged file is replaced whole.",
            markup=False,
        )
    if whole:
        console.print(
            "Whole files: DayZ's new file is live. Compare with zsm update-conflicts diff <file>, then run "
            "zsm update-conflicts keep-mine <file> or keep-dayz <file> (or --all).",
            markup=False,
        )


_KIND_WORDS = {
    "both_changed": "both changed",
    "dayz_removed": "DayZ removed it",
    "you_removed": "you removed it",
}


def _value_line(value: dict) -> str:
    live = "yours" if value.get("live") == "mine" else "DayZ's"
    state = "waiting, DayZ's is live" if value.get("waiting") else f"{live} is live"
    return (
        f"[{value.get('id')}] {value.get('entry')} {value.get('field')}: "
        f"yours {_short(value.get('mine'))}, DayZ's {_short(value.get('theirs'))}, "
        f"was {_short(value.get('base'))} ({_KIND_WORDS.get(value.get('kind'), value.get('kind'))}; {state})"
    )


def _short(value) -> str:
    if value is None:
        return "(none)"
    text = " ".join(str(value).split())
    return text if len(text) <= 60 else text[:57] + "..."


_DIFF_STYLES = (("+++", "bold"), ("---", "bold"), ("@@", "cyan"), ("+", "green"), ("-", "red"))


@update_conflicts_group.command(name="diff")
@click.argument("file")
def diff_cmd(file):
    """Show what separates DayZ's new file from the version you had."""
    config = DayzConfig.load()
    diff = mission_stock.conflict_diff(config, file)
    if diff is None:
        raise click.UsageError(f"{file} is not waiting for a choice (see zsm update-conflicts list).")
    if not diff:
        console.print(
            "The two differ only in mod blocks, which the merge puts back either way. "
            "Keeping DayZ's loses nothing of yours.",
            markup=False,
        )
        return
    for line in diff.splitlines():
        style = next((s for prefix, s in _DIFF_STYLES if line.startswith(prefix)), None)
        console.print(line, style=style, markup=False, highlight=False, soft_wrap=True)
    console.print(
        "+ lines are only in your version, - lines only in DayZ's. Mod blocks are left out.",
        markup=False,
    )


def _resolve(files: tuple[str, ...], all_: bool, keep: str, values: tuple[str, ...] = ()) -> None:
    if values:
        _settle_values(files, all_, keep, values)
        return
    if not files and not all_:
        raise click.UsageError("Name the files (as zsm update-conflicts list prints them) or pass --all.")
    config = DayzConfig.load()
    chosen, _merged = mission_stock.settle(config, None if all_ else list(files), keep, log=_plain)
    missing = sorted(set(files) - set(chosen))
    if missing:
        print_warning(f"Not waiting for a choice: {', '.join(missing)}")
    if not chosen:
        return
    print_success(f"Settled {len(chosen)} file(s).")


def _settle_values(files: tuple[str, ...], all_: bool, keep: str, values: tuple[str, ...]) -> None:
    if all_ or len(files) != 1:
        raise click.UsageError("--value takes exactly one file, as zsm update-conflicts list prints it.")
    config = DayzConfig.load()
    settled, _merged = mission_stock.settle_values(config, files[0], list(values), keep, log=_plain)
    missing = sorted(set(values) - set(settled))
    if missing:
        print_warning(f"Not listed for {files[0]}: {', '.join(missing)}")
    if settled:
        print_success(f"Settled {len(settled)} value(s).")


_VALUE_HELP = "A value's id from zsm update-conflicts list (repeatable). Without it, the whole file."


@update_conflicts_group.command(name="keep-mine")
@click.argument("files", nargs=-1)
@click.option("--all", "all_", is_flag=True, help="Every file waiting for a choice.")
@click.option("--value", "values", multiple=True, help=_VALUE_HELP)
def keep_mine(files, all_, values):
    """Put the version you had back (your mods are merged back in)."""
    _resolve(files, all_, "mine", values)


@update_conflicts_group.command(name="keep-dayz")
@click.argument("files", nargs=-1)
@click.option("--all", "all_", is_flag=True, help="Every file waiting for a choice.")
@click.option("--value", "values", multiple=True, help=_VALUE_HELP)
def keep_dayz(files, all_, values):
    """Keep DayZ's new file; the version you had stays in Versions."""
    _resolve(files, all_, "dayz", values)


@update_conflicts_group.command(name="dismiss")
@click.argument("file")
@click.option("--value", "values", multiple=True, required=True, help="A value's id from zsm update-conflicts list (repeatable).")
def dismiss(file, values):
    """Take values of a merged file off the list, leaving the file as it is."""
    _settle_values((file,), False, "ok", values)
