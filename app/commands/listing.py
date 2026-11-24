"""zsm listing — what this install sends to zedstead.com, and a way to take it back.

The two opt-ins are ordinary settings (`zsm settings set site_ping true`,
or the web UI's Settings page); this group is about seeing and undoing them.
`status` prints the exact JSON of both payloads, because an opt-in only means
something if the admin can see what they opted into (plans/57). It works with
no config at all, so it can be read before `zsm init`.
"""

from __future__ import annotations

import json
from datetime import datetime

import click
from rich.console import Console

from app.ops import site_ops
from app.shared.cli_ui import print_success, print_warning
from app.shared.config import DayzConfig

console = Console()


def _on_off(value: bool) -> str:
    return "[green]on[/green]" if value else "[dim]off[/dim]"


def _when(ts) -> str:
    if not ts:
        return "never"
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OverflowError, OSError):
        return str(ts)


def _print_json(payload: dict) -> None:
    # soft_wrap: Rich would otherwise break a long line (a 253-character host
    # name) with a newline, and this has to be the JSON exactly as sent.
    console.print(json.dumps(payload, indent=2), markup=False, highlight=False, soft_wrap=True)


@click.group(name="listing")
def listing_group():
    """zedstead.com install ping and server listing: see what is sent, or forget it."""


@listing_group.command(name="status")
def listing_status():
    """Show both switches, the exact JSON each one sends, and the last results."""
    config = DayzConfig.load_or_none() or DayzConfig()
    console.print("[bold]zedstead.com[/bold]")
    console.print(f"  Install ping    {_on_off(config.site_ping)}   (site_ping)")
    console.print(f"  Public listing  {_on_off(config.site_listing)}   (site_listing)")
    console.print("  [dim]Only `zsm web` sends these; an install that never runs it sends nothing.[/dim]")

    base = site_ops.ENDPOINT
    install_id = config.site_install_id or "<install_id>"
    console.print()
    console.print(f"[bold]Install ping[/bold], at most once a day: POST {base}/v1/ping", highlight=False)
    ping = site_ops.ping_payload(config)
    # Its own random ID, not the listing's (plans/68 NET-16).
    ping["install_id"] = ping["install_id"] or "<made at the first ping>"
    _print_json(ping)
    console.print()
    console.print(
        f"[bold]Public listing[/bold], every 15 minutes while the server runs: "
        f"PUT {base}/v1/servers/{install_id}",
        highlight=False,
    )
    _print_json(site_ops.listing_payload(config))
    secret = "<set>" if config.site_secret else "<not set>"
    console.print(f"Authorization: Bearer {secret}  [dim](the listing key, never in the body)[/dim]")

    state = site_ops.read_state(config)
    last = state.get("last_result") or {}
    console.print()
    console.print(f"  Last ping       {_when(state.get('last_ping'))}")
    console.print(f"  Last listed     {_when(state.get('last_publish'))}")
    if last:
        outcome = "ok" if last.get("ok") else "failed"
        console.print(
            f"  Last result     {last.get('action', '?')} {outcome} at {_when(last.get('at'))}: "
            f"{last.get('message', '')}",
            markup=False,
            highlight=False,
        )
    else:
        console.print("  Last result     [dim]nothing sent yet[/dim]")


@listing_group.command(name="forget")
def listing_forget():
    """Remove the listing, clear the install ID and its key, and turn both switches off."""
    config = DayzConfig.load()
    had_ids = bool(config.site_install_id or config.site_secret)
    ok, message = site_ops.forget(config)
    config.save()

    if ok:
        print_success("Removed this server from the zedstead.com list.")
    elif not had_ids:
        console.print("[dim]Nothing to remove: this install has no install ID.[/dim]")
    else:
        print_warning(
            f"Could not remove the listing ({message}). With no heartbeat, "
            "zedstead.com drops it within 24 hours."
        )
    print_success("Install ID and key cleared; both switches are off.")
