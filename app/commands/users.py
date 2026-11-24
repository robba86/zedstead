"""zsm users — the panel's accounts and their roles (TODO B4, plans/43 step 5).

This is the **only** way to create an account today, and it exists because the
alternative is editing ``users.json`` by hand — a file whose password field is
a scrypt hash, so hand-editing it is not something a person can do. The web
UI's Users page builds on the same store.

Deliberately not role-gated, and `plans/43` §5 says why: shell access to the
box is already full control, so asking a shell for credentials would be
security theatre. The web API is where roles are enforced.

Every command that **writes** runs the pre-accounts migration first
(`migrate_from_config`), which is a no-op once ``users.json`` exists. Without
it, the first ``zsm users add`` on an install that still has the single
password would create a store whose only account is whoever was just added —
and the old password stops working the moment accounts exist, so adding a
moderator would have locked the owner out of their own panel.

``list`` deliberately does **not** migrate. It is the one command here that
only reports, and a report that creates the file it is reporting on cannot
answer the question it was asked ("do I have accounts yet?").
"""

from __future__ import annotations

import click
from rich.console import Console
from rich.table import Table

from app.shared.cli_ui import confirm, print_error, print_success, print_warning
from app.shared.config import DayzConfig
from app.web.auth import check_new_password
from app.web.users import ROLES, UserStore, linked_fallback_hash, replace_fallback_copies

console = Console()


def prompt_new_password(text: str) -> str:
    """Ask for a new password twice, asking again while it is too short.

    `check_new_password` is the rule. Checking at the prompt rather than only
    in the store means a short entry is asked for again instead of ending the
    command; a ``--password`` flag still reaches the store's own check.
    ``zsm web``'s two prompts use this too.
    """

    def _checked(value: str) -> str:
        try:
            check_new_password(value)
        except ValueError as exc:
            raise click.BadParameter(str(exc)) from None
        return value

    return click.prompt(
        text, hide_input=True, confirmation_prompt=True, value_proc=_checked
    )


def _ni(ctx) -> bool:
    """Pull the non-interactive flag from the click context."""
    return bool(ctx.obj.get("non_interactive", False)) if ctx.obj else False


def _store() -> UserStore:
    """A store with the pre-accounts migration already applied.

    Loading the config is what makes that possible — the single
    ``web_password_hash`` is the material the first admin is made from. For
    the read-only ``list``, use ``UserStore()`` directly; see the module
    docstring.
    """
    return _store_and_config()[0]


def _store_and_config() -> tuple[UserStore, DayzConfig]:
    """`_store`, plus the config it migrated from, for the two commands that
    may have to write ``web_password_hash`` back (`_sync_linked_fallback`)."""
    config = DayzConfig.load()
    store = UserStore()
    store.migrate_from_config(config)
    return store, config


def _sync_linked_fallback(
    config: DayzConfig, user, *, removed: bool = False, previous_role: str | None = None
) -> None:
    """Keep ``config.web_password_hash`` tied to the account the migration
    made from it (plans/68; `linked_fallback_hash` is the rule), on this
    instance and on every other one holding the value it replaced
    (`replace_fallback_copies`, the web's rule too). A running ``zsm web``
    reads the new value on its next restart or settings save."""
    new_hash = linked_fallback_hash(user, removed=removed, previous_role=previous_role)
    if new_hash is None:
        return
    old_hash = config.web_password_hash
    config.web_password_hash = new_hash
    config.save()
    others = replace_fallback_copies(old_hash, new_hash, config.instance_id)
    if not new_hash:
        print_warning(
            f"{user.name} was made from the app's original single password, so "
            "that password is cleared too. It was the way in while users.json "
            "cannot be read; set a new one with `zsm web --set-password`."
        )
    else:
        console.print(
            "[dim]The app's original single password follows this account, "
            "so it changed too.[/dim]",
            highlight=False,
        )
    if others:
        console.print(
            f"[dim]Instances {', '.join(others)} held the same password, so it "
            f"{'changed' if new_hash else 'is cleared'} there too.[/dim]",
            highlight=False,
        )


@click.group(name="users")
def users_group():
    """App accounts: admin, moderator, viewer."""


@users_group.command(name="list")
@click.pass_context
def users_list(ctx):
    """List the accounts and their roles."""
    store = UserStore()  # no migration: see the module docstring
    store.load()
    if store.degraded:
        print_error(f"{store.path} exists but could not be read.")
        print_warning(
            "Sign-in has fallen back to the single password from config.json. "
            "Fix or move that file — no account change will be accepted until "
            "it reads."
        )
        ctx.exit(1)

    users = store.load()
    if not users:
        print_warning("No accounts yet.")
        console.print(
            "The app is still on its single password. "
            "[cyan]zsm users add <name> --role admin[/cyan] creates the first "
            "account, or set a password with [cyan]zsm web --set-password[/cyan] "
            "and it becomes one automatically.",
            highlight=False,
        )
        return

    table = Table(title=str(store.path), expand=False)
    table.add_column("Name", style="cyan")
    table.add_column("Role")
    # Whether each account has a second factor, never anything about it. This
    # is the only place the answer is visible at a glance, and "who is not
    # protected" is the question an admin asks (TODO B5).
    table.add_column("2FA")
    for user in users:
        table.add_row(user.name, user.role, "on" if user.totp_enabled else "—")
    console.print(table)


@users_group.command(name="add")
@click.argument("name")
@click.option(
    "--role",
    type=click.Choice(ROLES),
    default="viewer",
    show_default=True,
    help="viewer reads, moderator also acts on players and the server, admin does everything.",
)
@click.option("--password", default=None, help="Skip the prompt (visible in shell history).")
@click.pass_context
def users_add(ctx, name, role, password):
    """Create an account called NAME."""
    store = _store()
    if password is None:
        password = prompt_new_password(f"Password for {name}")
    try:
        user = store.add(name, password, role)
    except ValueError as exc:
        print_error(str(exc))
        ctx.exit(1)
    print_success(f"Added {user.name} as {user.role}.")


@users_group.command(name="remove")
@click.argument("name")
@click.pass_context
def users_remove(ctx, name):
    """Delete the account called NAME."""
    store, config = _store_and_config()
    if not confirm(f"Delete the account {name}?", default=False, non_interactive=_ni(ctx)):
        console.print("Cancelled.")
        return
    try:
        removed = store.remove(name)
    except ValueError as exc:
        print_error(str(exc))
        ctx.exit(1)
    if not removed:
        print_error(f"No such account: {name}")
        ctx.exit(1)
    print_success(f"Removed {name}.")
    _sync_linked_fallback(config, removed, removed=True)
    print_warning("Their open session stops working on their next request.")


@users_group.command(name="set-role")
@click.argument("name")
@click.argument("role", type=click.Choice(ROLES))
@click.pass_context
def users_set_role(ctx, name, role):
    """Change what NAME may do."""
    store, config = _store_and_config()
    before = store.get(name)
    try:
        user = store.set_role(name, role)
    except ValueError as exc:
        print_error(str(exc))
        ctx.exit(1)
    print_success(f"{user.name} is now {user.role}.")
    _sync_linked_fallback(config, user, previous_role=before.role if before else user.role)
    console.print("It takes effect on their next request — the role is never cached "
                  "in their session.", highlight=False)


@users_group.command(name="passwd")
@click.argument("name")
@click.option("--password", default=None, help="Skip the prompt (visible in shell history).")
@click.pass_context
def users_passwd(ctx, name, password):
    """Set a new password for NAME."""
    store, config = _store_and_config()
    if password is None:
        password = prompt_new_password(f"New password for {name}")
    try:
        user = store.set_password(name, password)
    except ValueError as exc:
        print_error(str(exc))
        ctx.exit(1)
    print_success(f"Password updated for {user.name}.")
    _sync_linked_fallback(config, user)
    print_warning(
        "Their open sessions are left alone — this store does not hold them. "
        "Use the app's own password change to sign other sessions out."
    )


@users_group.group(name="totp")
def users_totp():
    """Two-factor sign-in: an authenticator code on top of the password."""


@users_totp.command(name="enable")
@click.argument("name")
@click.option(
    "--no-verify",
    is_flag=True,
    help="Save the key without checking a code first. You may lock the account out.",
)
@click.pass_context
def users_totp_enable(ctx, name, no_verify):
    """Turn on two-factor sign-in for NAME.

    Prints a setup key to type into an authenticator app, then asks for a code
    to prove the app took it. Nothing is saved until that code checks out —
    a key the phone never received is an account nobody can sign in to, and
    the way back in is this command with `disable`.
    """
    from app.web import totp

    store = _store()
    user = store.get(name)
    if user is None:
        print_error(f"No such account: {name}")
        ctx.exit(1)
    if user.totp_enabled:
        print_warning(f"{user.name} already has two-factor sign-in on.")
        console.print(
            "Running this again replaces the key, which stops the old one working.",
            highlight=False,
        )
        if not confirm("Replace it?", default=False, non_interactive=_ni(ctx)):
            console.print("Cancelled.")
            return

    secret = totp.generate_secret()
    console.print()
    console.print(f"Setup key for [cyan]{user.name}[/cyan]:")
    console.print(f"  [bold]{totp.format_secret(secret)}[/bold]")
    console.print()
    console.print("Or this URI, if your app takes one:", highlight=False)
    console.print(f"  {totp.provisioning_uri(secret, user.name)}", highlight=False)
    console.print()

    if not no_verify:
        # Deliberately not a loop. A wrong code here is usually a clock that
        # needs fixing or a key that went in wrong, and retrying the same two
        # is what people do instead of checking either.
        typed = click.prompt("Enter the 6-digit code the app is showing")
        if totp.verify(secret, typed) is None:
            print_error("That code is not right — nothing was saved.")
            console.print(
                "Check the key went in exactly as printed, and that the phone's "
                "clock is set automatically. Then run this again.",
                highlight=False,
            )
            ctx.exit(1)

    try:
        store.set_totp(user.name, secret)
    except ValueError as exc:
        print_error(str(exc))
        ctx.exit(1)
    print_success(f"Two-factor sign-in is on for {user.name}.")
    if no_verify:
        print_warning(
            "No code was checked. If the app did not take that key, "
            f"`zsm users totp disable {user.name}` is the way back in."
        )
    print_warning("Their open sessions are left alone — this changes the next sign-in.")


@users_totp.command(name="disable")
@click.argument("name")
@click.pass_context
def users_totp_disable(ctx, name):
    """Turn off two-factor sign-in for NAME.

    **This is the recovery path**, and the reason it lives here rather than in
    the app: an account whose phone is lost cannot sign in to fix itself, and
    a web route that could strip another admin's second factor would make every
    admin session as strong as the weakest admin's password. Shell access to
    this box is already full control, so nothing is given away by putting it
    here.
    """
    store = _store()
    user = store.get(name)
    if user is None:
        print_error(f"No such account: {name}")
        ctx.exit(1)
    if not user.totp_enabled:
        print_warning(f"{user.name} does not have two-factor sign-in on.")
        return
    if not confirm(
        f"Turn off two-factor sign-in for {user.name}?",
        default=False,
        non_interactive=_ni(ctx),
    ):
        console.print("Cancelled.")
        return

    try:
        store.set_totp(user.name, "")
    except ValueError as exc:
        print_error(str(exc))
        ctx.exit(1)
    print_success(f"Two-factor sign-in is off for {user.name}.")
    print_warning("Their password alone now signs them in.")
