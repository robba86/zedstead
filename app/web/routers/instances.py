"""The Instances page: list, add, clone, rename, set default and remove.

A web UI for instances was added later, which answers
plans/62 D6 the other way: until then the CLI was the only place to manage
them (plans/44 Q4). One panel serves every instance (plans/62): `state` is the
instance the page is about (its `/i/<id>/` prefix), and the panel follows
instances.json, so every write here refreshes it at once.

Every rule is `app/server/instances.py`'s, the same functions `zsm instances`
calls. What this router adds is what only a web request can get wrong:

- **Add and clone answer 403 while `web_lock_paths` is on.** They take a
  server directory from the browser, which is exactly what the lock exists to
  stop. Listing, renaming, the default and removing stay open.
- **Remove never deletes files** (`purge=False`). Deleting `instances/<id>`
  stays `zsm instances remove --purge`.
- **Two instances cannot be removed from here**: the one the panel started
  with, whose settings the listener runs on, and the one the page is about,
  whose config would be gone from under the page.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException

from app.server import instances
from app.server.service_control import is_server_running
from app.shared.config import DayzConfig
from app.shared.settings_registry import SERVER_BRANCH_CHOICES, SERVER_BRANCH_HELP
from app.shared.steam import server_branch
from app.web.deps import get_panel, get_state, require_session
from app.web.panel import Panel
from app.web.schemas import InstanceCreateRequest, InstanceLabelRequest
from app.web.state import WebState

router = APIRouter(prefix="/api/instances", tags=["instances"], dependencies=[Depends(require_session)])

LOCKED_MESSAGE = (
    "Adding an instance sets a server directory, and web_lock_paths is on. Use "
    "`zsm instances add` or `zsm instances clone` on the server, or lift the "
    "lock there with `zsm settings set web_lock_paths false`."
)


def _registry() -> instances.InstanceRegistry:
    """The registry, or 409 saying the file is broken. A hand edit gone wrong
    is fixed by hand; the CLI reports the same error."""
    from app.shared.paths import instances_file

    try:
        return instances.load_registry()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=409, detail=(
            f"{instances_file()} could not be read ({exc}). It lists the instances; "
            "fix it by hand.")) from exc


def _listing(state: WebState, panel: Panel) -> dict:
    registry = _registry()
    current = state.config.instance_id
    rows = []
    for row in instances.instance_rows(registry):
        ports = row.ports
        rows.append({
            "id": row.id,
            "label": row.label,
            "default": row.default,
            "current": row.id == current,
            # Whether the panel has it, i.e. whether the switcher can open it.
            "served": row.id in panel.bundles,
            "missing": row.config is None,
            "server_dir": row.config.server_dir if row.config else "",
            "branch": server_branch(row.config) if row.config else None,
            "ports": ports._asdict() if ports else None,
            "conflicts": list(row.conflicts),
            "running": bool(row.config and is_server_running(row.config)),
        })
    return {
        "current": current,
        "startup": panel.startup_id,
        "default": registry.default_id,
        "locked": state.paths_locked(),
        "max_label": instances.MAX_LABEL,
        # The Add form's line under its Branch select: the setting's own text.
        "branch_help": SERVER_BRANCH_HELP,
        # The line under each branch in that select.
        "branch_choice_help": SERVER_BRANCH_CHOICES,
        "instances": rows,
    }


async def _changed(panel: Panel) -> None:
    """Serve what instances.json now says, and tell every page: the switcher
    is on all of them. ``refresh`` announces an added or removed instance
    itself; a rename or a new default changes no bundle, so it is said here."""
    if not await panel.refresh():
        await panel.shared.hub.broadcast({"type": "instances_changed"})


@router.get("")
async def list_instances(state: WebState = Depends(get_state), panel: Panel = Depends(get_panel)) -> dict:
    """Every instance with its server dir, ports, port conflicts, and whether
    its server runs. ``current`` is the one the page is about."""
    await panel.refresh()
    return await asyncio.to_thread(_listing, state, panel)


@router.post("")
async def create(body: InstanceCreateRequest, state: WebState = Depends(get_state),
                 panel: Panel = Depends(get_panel)) -> dict:
    """`zsm instances add`, or `clone` when ``source`` names an instance."""
    if state.paths_locked():
        raise HTTPException(status_code=403, detail=LOCKED_MESSAGE)
    source = None
    if body.source:
        if not _registry().get(body.source):
            raise HTTPException(status_code=404, detail=f"No instance named '{body.source}'.")
        try:
            source = DayzConfig.for_instance(body.source)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=400, detail=(
                f"'{body.source}' has no config.json to copy.")) from exc
    try:
        created = await asyncio.to_thread(
            instances.create_instance, body.name.strip(), server_dir=body.server_dir.strip(),
            label=body.label, source=source, branch=body.branch,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await _changed(panel)
    return {
        "id": created.config.instance_id,
        "server_dir": created.config.server_dir,
        "ports": created.ports._asdict(),
        "notes": list(created.notes),
    }


@router.put("/{instance_id}")
async def rename(instance_id: str, body: InstanceLabelRequest,
                 panel: Panel = Depends(get_panel)) -> dict:
    """Change the label. The id names directories and units, so it stays."""
    registry = _registry()
    if not registry.get(instance_id):
        raise HTTPException(status_code=404, detail=f"No instance named '{instance_id}'.")
    try:
        registry = instances.set_label(registry, instance_id, body.label)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    instances.save_registry(registry)
    await _changed(panel)
    return {"id": instance_id, "label": registry.get(instance_id).label}


@router.post("/{instance_id}/default")
async def make_default(instance_id: str, panel: Panel = Depends(get_panel)) -> dict:
    """`zsm instances use`: what CLI commands, timers' next install and a
    newly started panel act on. A running panel keeps its startup instance."""
    registry = _registry()
    if not registry.get(instance_id):
        raise HTTPException(status_code=404, detail=f"No instance named '{instance_id}'.")
    instances.save_registry(instances.set_default(registry, instance_id))
    await _changed(panel)
    return {"default": instance_id}


@router.delete("/{instance_id}")
async def remove(instance_id: str, state: WebState = Depends(get_state),
                 panel: Panel = Depends(get_panel)) -> dict:
    """Remove *instance_id* from the list. Its files stay in `instances/<id>`."""
    registry = _registry()
    if not registry.get(instance_id):
        raise HTTPException(status_code=404, detail=f"No instance named '{instance_id}'.")
    if instance_id == panel.startup_id:
        raise HTTPException(status_code=409, detail=(
            f"The app was started with '{instance_id}' and runs on its settings, so it "
            f"cannot remove it. Use `zsm instances remove {instance_id}` with the app "
            "stopped, or start the app with another instance."))
    if instance_id == state.config.instance_id:
        raise HTTPException(status_code=409, detail=(
            f"This page is about '{instance_id}'. Switch to another instance to remove it."))
    problem = await asyncio.to_thread(instances.removal_problem, instance_id, registry)
    if problem:
        raise HTTPException(status_code=409, detail=problem)
    try:
        root = await asyncio.to_thread(instances.delete_instance, instance_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await _changed(panel)
    return {"removed": instance_id, "kept": str(root), "default": _registry().default_id}
