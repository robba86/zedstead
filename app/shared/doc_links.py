"""Links from the CLI's `--help` to the user docs on zedstead.com.

Every top-level command names the doc section that explains it, and
`add_docs_epilogs` writes that link under its help text and under the help of
each subcommand. `tests/test_doc_links.py` checks that every registered command
has an entry and that every doc and anchor exists in the zedstead-site checkout.
The web UI's table is `webui/src/docLinks.json`.
"""

from __future__ import annotations

import click

DOCS_URL = "https://zedstead.com/docs/"

# Command name -> (doc page, heading anchor). An empty anchor is the page top.
COMMAND_DOCS: dict[str, tuple[str, str]] = {
    "init": ("cli", "getting-started"),
    "setup": ("cli", "getting-started"),
    "adopt": ("install", "you-already-run-a-server"),
    "bootstrap-vps": ("deploy-on-a-vps", ""),
    "instances": ("instances", "several-servers-on-one-machine"),
    "run": ("cli", "server-lifecycle"),
    "start": ("cli", "server-lifecycle"),
    "stop": ("cli", "server-lifecycle"),
    "restart": ("cli", "server-lifecycle"),
    "status": ("cli", "server-lifecycle"),
    "supervise": ("cli", "server-lifecycle"),
    "steam-login": ("cli", "server-lifecycle"),
    "service": ("linux-service-and-firewall", "install-the-server-as-a-service"),
    "mods": ("cli", "mods-maps-presets"),
    "maps": ("cli", "mods-maps-presets"),
    "presets": ("cli", "mods-maps-presets"),
    "backup": ("cli", "backups"),
    "bundle": ("cli", "diagnostics--integrations"),
    "scheduler": ("cli", "scheduled-tasks"),
    "cleanup": ("cli", "cleanup"),
    "update-conflicts": ("cli", "after-a-dayz-update"),
    "settings": ("cli", "app-settings"),
    "users": ("cli", "app-accounts"),
    "doctor": ("cli", "diagnostics--integrations"),
    "info": ("cli", "diagnostics--integrations"),
    "changes": ("cli", "diagnostics--integrations"),
    "log-analyzer": ("cli", "diagnostics--integrations"),
    "listing": ("cli", "diagnostics--integrations"),
    "web": ("web-ui", ""),
    "self-update": ("cli", ""),
    "container-init": ("docker", ""),
    "sync-server-cfg": ("docker", ""),
}


def doc_url(doc: str, anchor: str = "") -> str:
    """The zedstead.com URL of *doc*, at *anchor* when one is given."""
    return f"{DOCS_URL}{doc}/" + (f"#{anchor}" if anchor else "")


def _set_epilog(cmd: click.Command, url: str) -> None:
    """Give *cmd* and every subcommand without an epilog of its own the docs line."""
    if not cmd.epilog:
        cmd.epilog = f"Docs: {url}"
    if isinstance(cmd, click.Group):
        for sub in cmd.commands.values():
            _set_epilog(sub, url)


def add_docs_epilogs(root: click.Group) -> None:
    """Put each command's docs link under its `--help`, and the CLI reference under the root's."""
    root.epilog = "\n\n".join(filter(None, [root.epilog, f"Docs: {doc_url('cli')}"]))
    for name, cmd in root.commands.items():
        if name in COMMAND_DOCS:
            _set_epilog(cmd, doc_url(*COMMAND_DOCS[name]))
