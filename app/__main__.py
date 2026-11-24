"""Entry point: python3 -m app"""

import os

import click

from app import TRADEMARK_NOTICE, __version__
from app.commands.adopt import adopt_cmd
from app.commands.backup import backup_group
from app.commands.bootstrap import bootstrap_vps
from app.commands.bundle import bundle_group
from app.commands.changes import changes_cmd
from app.commands.cleanup import cleanup_group
from app.commands.container import container_init, sync_server_cfg_cmd
from app.commands.context import report_missing_config
from app.commands.doctor import doctor_cmd
from app.commands.log_analyzer import log_analyzer
from app.commands.info import info
from app.commands.init import init, prompt_init_if_missing
from app.commands.instances import instances_group
from app.commands.listing import listing_group
from app.commands.maps import maps_group
from app.commands.mods import mods_group
from app.commands.presets import presets_group
from app.commands.run import run
from app.commands.scheduler import scheduler_group
from app.commands.settings import settings_group
from app.commands.update_conflicts import update_conflicts_group
from app.commands.users import users_group
from app.commands.service import restart as service_restart_cmd
from app.commands.service import service
from app.commands.service import start as service_start_cmd
from app.commands.service import stop as service_stop_cmd
from app.commands.self_update import self_update_cmd
from app.commands.setup import setup
from app.commands.status import status_cmd
from app.commands.supervise import supervise
from app.commands.steam import steam_login_cmd
from app.commands.web import web_cmd
from app.shared.config import INSTANCE_ENV, ConfigNotFound
from app.shared.doc_links import add_docs_epilogs
from app.shared.paths import secure_config_dir

# Zedstead's community Discord, shown under `zsm --help`.
DISCORD_URL = "https://discord.gg/3rFZPm3z6v"


class ConfigAwareGroup(click.Group):
    """Root group that reports an unconfigured install instead of crashing.

    Subcommands load their config in ~40 places (`load_context`, and plenty of
    direct `DayzConfig.load()` calls), so guarding each one both duplicates the
    handling and leaves the next command to forget it. Every one of those calls
    happens inside this group's `invoke`, so catching ConfigNotFound here covers
    all of them at once — a fresh box that has never run `zsm init` gets the
    "run zsm init first" line for any subcommand, not a Python traceback.
    """

    def invoke(self, ctx: click.Context):
        try:
            return super().invoke(ctx)
        except ConfigNotFound as e:
            report_missing_config(e)
            ctx.exit(1)


def enter_instance(ctx: click.Context, instance: str | None) -> None:
    """Settle which instance this process acts on, once (plans/44 step 4).

    Moves a pre-instances install into `instances/main` first, if it has not
    been. Then the name goes into `$ZSM_INSTANCE`, which `DayzConfig.load()`
    reads (D1): an explicit `--instance`, else the registry's default, pinned
    so that a default changed mid-run does not move a running panel.
    """
    from app.server import instance_migration
    from app.server.instances import load_registry
    from app.shared.paths import instances_file

    try:
        instance_migration.report(instance_migration.migrate())
        registry = load_registry()
    except (ValueError, KeyError, TypeError) as exc:  # a hand edit gone wrong
        raise click.ClickException(
            f"{instances_file()} could not be read ({exc}). It lists the "
            "instances under instances/; fix it by hand."
        ) from exc
    name = (instance or "").strip()
    if name and not registry.get(name):
        known = ", ".join(registry.ids()) or "none yet"
        raise click.BadParameter(f"no instance named '{name}' (known: {known}).",
                                 ctx=ctx, param_hint="--instance")
    name = name or registry.default_id
    if name:
        os.environ[INSTANCE_ENV] = name
    else:
        os.environ.pop(INSTANCE_ENV, None)


@click.group(
    cls=ConfigAwareGroup, invoke_without_command=True,
    epilog=f"Questions and help: {DISCORD_URL} (Zedstead on Discord).",
)
@click.option("-y", "--yes", "non_interactive", is_flag=True, help="Skip all confirmations.")
@click.option("-v", "--verbose", is_flag=True, help="Verbose output.")
@click.option(
    "--instance", metavar="NAME", envvar=INSTANCE_ENV,
    help="The instance to act on. Default: the one instances.json names.",
)
@click.version_option(
    __version__,
    prog_name="zsm",
    message="%(prog)s %(version)s\n" + TRADEMARK_NOTICE,
)
@click.pass_context
def main(ctx, non_interactive, verbose, instance):
    """DayZ server management CLI.

    Run with no command to open the web UI (same as `zsm web`).
    """
    ctx.ensure_object(dict)
    ctx.obj["non_interactive"] = non_interactive
    ctx.obj["verbose"] = verbose
    # First, so the migration and every write after it land in an owner-only dir.
    secure_config_dir()
    enter_instance(ctx, instance)

    if ctx.invoked_subcommand is None:
        # No subcommand: set the tool up if it has never been configured, then
        # hand over to the web UI — the interactive surface.
        if not prompt_init_if_missing(ctx):
            return
        ctx.invoke(web_cmd)


# Register all commands
main.add_command(init)
main.add_command(adopt_cmd, "adopt")
main.add_command(setup)
main.add_command(bootstrap_vps)
main.add_command(container_init)
main.add_command(sync_server_cfg_cmd)
main.add_command(supervise)
main.add_command(run)
main.add_command(mods_group, "mods")
main.add_command(maps_group, "maps")
main.add_command(cleanup_group, "cleanup")
main.add_command(info)
main.add_command(service)
main.add_command(log_analyzer)
main.add_command(doctor_cmd, "doctor")
main.add_command(bundle_group, "bundle")
main.add_command(backup_group, "backup")
main.add_command(scheduler_group, "scheduler")
main.add_command(changes_cmd, "changes")
main.add_command(presets_group, "presets")
main.add_command(settings_group, "settings")
main.add_command(users_group, "users")
main.add_command(instances_group, "instances")
main.add_command(listing_group, "listing")
main.add_command(update_conflicts_group, "update-conflicts")
main.add_command(steam_login_cmd, "steam-login")
main.add_command(web_cmd, "web")
main.add_command(self_update_cmd, "self-update")

# Top-level shortcuts for the most common service actions.
main.add_command(service_start_cmd, "start")
main.add_command(service_stop_cmd, "stop")
main.add_command(service_restart_cmd, "restart")
main.add_command(status_cmd, "status")

# Last, so every command above gets its zedstead.com link.
add_docs_epilogs(main)


if __name__ == "__main__":
    main()
