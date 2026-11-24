"""Keep the admin's edits to stock mission files through a server download (plans/67).

Every server download runs ``app_update <app> validate``, and Steam owns the
stock missions' files, so validate puts back the stock version of any of them
that differs. Measured in the test container in 2026 Q4: a Globals edit and
a quick tweak in ``dayzOffline.chernarusplus`` went back to stock along with the
mods' blocks. ``merge_xml`` re-adds the mods' blocks; nothing re-added the edits.

The record (``stock.json`` in ``config.paths.mission_stock_dir``) is the sha256
of every Steam-owned file of every stock mission as the last download left it,
with the app id and build it came from. Around each download:

1. ``before_download`` copies every Steam-owned file whose hash is not its
   recorded stock hash into ``pending/``. With no record yet, or one for the
   other branch's app, it copies all of them.
2. The caller runs SteamCMD.
3. ``after_download`` looks at each copy whose file the download replaced. If
   Bohemia did not change the file (its new hash is the recorded one; with no
   record, the build id stayed the same), the copy goes back. Otherwise it is a
   conflict, settled by ``conflict_policy``: "dayz" keeps DayZ's file, "mine"
   puts the admin's back, and "ask" (the default) keeps DayZ's file and holds
   the admin's in ``conflicts/`` until ``resolve`` is told which one wins. A
   conflict saves both sides to Versions first.
4. It records the new stock hashes.

A copy holds the file as it was, mod blocks included. The merge re-serializes
the file, so stripping the blocks does not give stock bytes back, and a merged
file always reads as edited. That is harmless, because ``merge_xml`` strips and
re-adds every mod block. It is also why a conflict says "the version you had",
never "your edits".

Reset-mission wants stock. The mission being reset (``resetting``) is never
copied or restored. The other stock missions, which its validate resets too,
are protected as usual.

The stock files themselves are kept in ``stock/`` beside the record (plans/71),
because a hash cannot say what the admin changed. Right after a download every
Steam-owned file is stock, so ``after_download`` copies the new ones before it
puts anything back. The store mirrors the record: for every recorded file, a
copy whose sha256 is the recorded hash, or nothing. ``base_for`` reads it, and
falls back to Versions and ``.backup`` when the hash matches there.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from app.shared.config import DayzConfig

Log = Callable[[str], None] | None

# What Steam writes into a stock mission: its top level, db/, env/ and Sakhal's
# pra/. Matched against depot 223354's manifest in 2026 Q4: every path it
# lists, except the binary areaflags.map, which is most of a mission's size and
# which nothing edits.
STOCK_SUBDIRS = ("", "db", "env", "pra")
STOCK_SUFFIXES = frozenset({".xml", ".json", ".c", ".cpp"})

POLICIES = ("ask", "dayz", "mine")

_RECORD = "stock.json"
_STORE = "stock"
_STORE_NEXT = "stock.next"
_PENDING = "pending"
_CONFLICTS = "conflicts"
_CONFLICTS_INDEX = "conflicts.json"


@dataclass
class Stash:
    """What `before_download` set aside, for `after_download`."""

    app_id: str
    buildid: str
    # rel path -> recorded stock sha256; None when there is no usable record.
    reference: dict[str, str] | None
    # rel path -> sha256 of the copy in pending/.
    copied: dict[str, str] = field(default_factory=dict)
    resetting: str | None = None


@dataclass
class Outcome:
    restored: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)  # settled whole-file (plans/67)
    merged: list[str] = field(default_factory=list)  # merged value by value (plans/71)
    policy: str = "ask"


# --- the files -------------------------------------------------------------


def _mpmissions(config: DayzConfig) -> Path:
    return config.server_dir_path / "mpmissions"


def stock_missions(config: DayzConfig) -> list[Path]:
    """The stock mission folders this server has, from the map catalog.

    An official map the catalog knows only by terrain (announced, before a
    zsm release names its mission) counts once a download has put its mission
    here, so the admin's edits to it survive the next update as well."""
    from app.maps.map_ops import vanilla_mission
    from app.shared.seed_data import VANILLA_MAPS

    root = _mpmissions(config)
    names = [vanilla_mission(config, name) for name in VANILLA_MAPS]
    return [root / name for name in names if name and (root / name).is_dir()]


def owned_files(mission_dir: Path) -> list[Path]:
    """The files of *mission_dir* that Steam writes. Never zsm's dot-files,
    `.backup`s, `custom/`, `expansion*/` or `storage_*`."""
    found: list[Path] = []
    for sub in STOCK_SUBDIRS:
        folder = mission_dir / sub if sub else mission_dir
        if not folder.is_dir():
            continue
        for path in sorted(folder.iterdir()):
            if path.is_file() and not path.name.startswith(".") and path.suffix.lower() in STOCK_SUFFIXES:
                found.append(path)
    return found


def _owned(config: DayzConfig, skip: str | None = None) -> list[tuple[Path, str]]:
    root = _mpmissions(config)
    return [
        (path, path.relative_to(root).as_posix())
        for mission in stock_missions(config) if mission.name != skip
        for path in owned_files(mission)
    ]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- the record ------------------------------------------------------------


def _dir(config: DayzConfig) -> Path:
    return config.paths.mission_stock_dir


def stock_copy(config: DayzConfig, mission: str, rel: str) -> Path | None:
    """The kept stock copy of ``mpmissions/<mission>/<rel>``, or None. A *rel*
    or *mission* that would leave the store finds nothing."""
    store = (_dir(config) / _STORE).resolve()
    path = (store / mission / rel).resolve()
    if not path.is_relative_to(store):
        return None
    return path if path.is_file() else None


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, data: dict) -> None:
    from app.shared.json_utils import atomic_write_text

    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(data, indent=2, sort_keys=True) + "\n")


def _app_state(config: DayzConfig) -> dict:
    from app.parsers.acf_parser import parse_acf_file
    from app.shared.steam import server_app_id

    acf = config.server_dir_path / "steamapps" / f"appmanifest_{server_app_id(config)}.acf"
    try:
        data = parse_acf_file(acf)
    except (OSError, ValueError):
        return {}
    return data.get("AppState", data)


def installed_buildid(config: DayzConfig) -> str:
    """The build SteamCMD says is installed, or "" when it says nothing."""
    return str(_app_state(config).get("buildid", "") or "")


def installed_build_time(config: DayzConfig) -> int:
    """When SteamCMD last updated the server (the manifest's ``LastUpdated``,
    epoch seconds), or 0 when it says nothing."""
    try:
        return int(_app_state(config).get("LastUpdated", 0) or 0)
    except (TypeError, ValueError):
        return 0


def conflict_policy(config: DayzConfig) -> str:
    """`config.mission_update_conflict`; a value it does not know reads as "ask"."""
    value = (config.mission_update_conflict or "").strip().lower()
    return value if value in POLICIES else "ask"


# --- around a download -----------------------------------------------------


def before_download(config: DayzConfig, *, resetting: str | None = None, log: Log = None) -> Stash:
    """Copy aside every Steam-owned mission file the download would put back.

    *resetting* names a mission folder being reset to stock: none of its files
    are copied, and any conflict it still had is dropped.
    """
    from app.shared.steam import server_app_id

    emit = log or (lambda _line: None)
    app_id = server_app_id(config)
    record = _read_json(_dir(config) / _RECORD)
    files = record.get("files")
    reference = files if record.get("app_id") == app_id and isinstance(files, dict) and files else None

    pending = _dir(config) / _PENDING
    shutil.rmtree(pending, ignore_errors=True)
    stash = Stash(app_id=app_id, buildid=installed_buildid(config), reference=reference, resetting=resetting)
    for path, rel in _owned(config, skip=resetting):
        digest = _sha256(path)
        # A file the record does not have appeared after the last download, so
        # Steam did not write it and validate leaves it alone.
        if reference is not None and reference.get(rel) in (digest, None):
            continue
        dest = pending / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
        stash.copied[rel] = digest

    if resetting:
        _drop_conflicts(config, lambda rel: rel.split("/", 1)[0] == resetting)
    if stash.copied and reference is None:
        emit(f"No record of the stock mission files yet: set all {len(stash.copied)} aside for this update.")
    elif stash.copied:
        emit(f"Set aside {len(stash.copied)} changed stock mission file(s) for this update.")
    return stash


def download_failed(config: DayzConfig, stash: Stash, *, log: Log = None) -> None:
    """The download failed or stopped: put back every copy whose file it
    replaced or deleted, and leave the rest alone.

    The next download empties pending/ and copies only files that differ from
    stock, so a file validate reset before it failed had its one copy deleted.
    Putting it back makes the next download see the admin's file again."""
    pending = _dir(config) / _PENDING
    root = _mpmissions(config)
    restored: list[str] = []
    for rel, copy_hash in stash.copied.items():
        live, copy = root / rel, pending / rel
        if not copy.is_file() or (live.is_file() and _sha256(live) == copy_hash):
            continue
        _put_back(copy, live)
        restored.append(rel)
    if not log:
        return
    if restored:
        log(f"The server download failed after it replaced some mission files. Put your version back: "
            f"{', '.join(restored)}")
    elif stash.copied:
        log(f"The server download failed, so no mission files were put back. The copies taken "
            f"before it are in {pending} until the next update.")


def after_download(config: DayzConfig, stash: Stash, *, log: Log = None, remerge_mods: bool = True) -> Outcome:
    """Put back what the download replaced, merge or settle conflicts, record
    the new stock. A merged file has no mod blocks, so the merge runs afterwards
    when one is in the configured mission (*remerge_mods* False skips that, for
    tests)."""
    emit = log or (lambda _line: None)
    root = _mpmissions(config)
    pending = _dir(config) / _PENDING
    buildid = installed_buildid(config)
    same_build = bool(stash.buildid) and stash.buildid == buildid
    outcome = Outcome(policy=conflict_policy(config))
    new_stock: dict[str, str] = {}

    staging = _dir(config) / _STORE_NEXT
    shutil.rmtree(staging, ignore_errors=True)
    staged: dict[str, str] = {}
    not_stock: set[str] = set()
    merge_lines: list[str] = []

    for rel, copy_hash in stash.copied.items():
        live, copy = root / rel, pending / rel
        if not live.is_file():
            # The download deleted it. Nothing to compare: keep what was there.
            _put_back(copy, live)
            outcome.restored.append(rel)
            not_stock.add(rel)
            continue
        live_hash = _sha256(live)
        if live_hash == copy_hash:
            continue  # the download left it alone
        new_stock[rel] = live_hash
        # The live file is DayZ's new stock until something is put back over it.
        _copy_into(live, staging / rel)
        staged[rel] = live_hash
        if stash.reference is not None:
            unchanged = stash.reference.get(rel) == live_hash
        else:
            unchanged = same_build
        if unchanged:
            _put_back(copy, live)
            outcome.restored.append(rel)
            continue
        merged = _merge(config, stash, rel, live, copy, outcome.policy, buildid, emit)
        if merged:
            outcome.merged.append(rel)
            merge_lines.append(merged)
        else:
            outcome.conflicts.append(rel)
            _settle(config, rel, live, copy, outcome.policy, stash.buildid, buildid)

    files: dict[str, str] = {}
    live_is_stock: dict[str, Path] = {}
    for path, rel in _owned(config):
        if rel in new_stock:
            files[rel] = new_stock[rel]
        elif rel in stash.copied and stash.reference is not None and rel in stash.reference:
            files[rel] = stash.reference[rel]  # still the admin's: keep the stock hash we had
        else:
            files[rel] = _sha256(path)
            if rel not in not_stock:
                live_is_stock[rel] = path
    _refresh_store(config, files, stash.reference, staged, live_is_stock)
    _write_json(_dir(config) / _RECORD, {"app_id": stash.app_id, "buildid": buildid, "files": files})
    shutil.rmtree(pending, ignore_errors=True)
    shutil.rmtree(staging, ignore_errors=True)

    if outcome.restored:
        emit(f"Kept your changes after the update: {', '.join(outcome.restored)}")
    for line in merge_lines:
        emit(line)
    for rel in outcome.conflicts:
        emit(_conflict_line(rel, outcome.policy, buildid))
    if outcome.merged and remerge_mods:
        _remerge_after_update(config, outcome.merged, emit)
    return outcome


def _put_back(copy: Path, live: Path) -> None:
    """Put the admin's own copy back live, recorded as Zedstead's write, so the
    Timeline does not show it as a change made outside Zedstead."""
    from app.shared.json_utils import copy_file

    copy_file(copy, live)


def _copy_into(source: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)


# --- the stock files themselves (plans/71) ---------------------------------


def _refresh_store(
    config: DayzConfig, files: dict[str, str], old: dict[str, str] | None,
    staged: dict[str, str], live_is_stock: dict[str, Path],
) -> None:
    """Make ``stock/`` mirror the record about to be written: for every file,
    a copy whose sha256 is the recorded hash, or nothing.

    A copy already there stays when its file's recorded hash did not change,
    since it was written to match that hash. Otherwise the copy comes from the
    staged new stock or from a live file that is still stock.
    """
    store = _dir(config) / _STORE
    staging = _dir(config) / _STORE_NEXT
    for rel, digest in files.items():
        dest = store / rel
        if staged.get(rel) == digest:
            dest.parent.mkdir(parents=True, exist_ok=True)
            (staging / rel).replace(dest)
        elif old is not None and old.get(rel) == digest and dest.is_file():
            continue
        elif rel in live_is_stock:
            _copy_into(live_is_stock[rel], dest)
        else:
            dest.unlink(missing_ok=True)
    if store.is_dir():
        for path in sorted(store.rglob("*"), reverse=True):
            if path.is_file() and path.relative_to(store).as_posix() not in files:
                path.unlink()
            elif path.is_dir() and not any(path.iterdir()):
                path.rmdir()


def shipped_copy(config: DayzConfig, path: Path) -> Path | None:
    """The game's own copy of *path* for the installed version, or None.

    What *Restore from backup* puts back for a Steam-owned file of a stock
    mission (`xml_utils.restore_file`). The store holds it from the last
    download, so it is always the installed version's file, never the older
    one a `.backup` can hold. None for a file of a community mission, a file
    Steam does not own, or a store without that file.
    """
    root = _mpmissions(config).resolve()
    try:
        rel = Path(path).resolve().relative_to(root).as_posix()
    except ValueError:
        return None
    mission, _, inner = rel.partition("/")
    if not inner or mission not in {m.name for m in stock_missions(config)}:
        return None
    folder, _, name = inner.rpartition("/")
    # owned_files' rule, by name, so a file the admin deleted still restores.
    if folder not in STOCK_SUBDIRS or name.startswith(".") or Path(name).suffix.lower() not in STOCK_SUFFIXES:
        return None
    return stock_copy(config, mission, inner)


def base_for(config: DayzConfig, rel: str, stock_hash: str) -> bytes | None:
    """The stock file *rel* was compared against, or None when nothing holds it.

    Looked for in the store, then in Versions (the first save of a stock file
    stored the stock bytes), then in the file's ``.backup``. A candidate counts
    only when its sha256 is *stock_hash*.
    """
    from app.shared.config_versions import content_with_hash

    if not stock_hash:
        return None
    live = _mpmissions(config) / rel
    data = _read_if_hash(_dir(config) / _STORE / rel, stock_hash)
    if data is None:
        data = content_with_hash(live, stock_hash, store=config.paths.versions_dir)
    if data is None:
        data = _read_if_hash(live.with_name(live.name + ".backup"), stock_hash)
    return data


def _read_if_hash(path: Path, digest: str) -> bytes | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return data if hashlib.sha256(data).hexdigest() == digest else None


def _settle(config: DayzConfig, rel: str, live: Path, copy: Path, policy: str, before: str, after: str) -> None:
    """One file both sides changed. Both go to Versions; then the policy."""
    from app.shared.config_versions import snapshot

    build = after or "unknown"
    theirs = {"trigger": "server_update", "note": f"DayZ build {build}'s new file"}
    yours = {"trigger": "server_update", "note": f"The version you had before DayZ build {build}", "content": copy}
    # The one that stays live goes last, so the merge's own snapshot of the
    # live file right after is not a second copy of it.
    for kwargs in ((theirs, yours) if policy == "mine" else (yours, theirs)):
        snapshot(live, **kwargs)
    if policy == "mine":
        _put_back(copy, live)
    elif policy == "ask":
        dest = _dir(config) / _CONFLICTS / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(copy, dest)
        index = _read_json(_dir(config) / _CONFLICTS_INDEX)
        index[rel] = {
            "build_before": before,
            "build_after": after,
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        _write_json(_dir(config) / _CONFLICTS_INDEX, index)


# --- merging value by value (plans/71) ------------------------------------


def _merge(config: DayzConfig, stash: Stash, rel: str, live: Path, copy: Path,
           policy: str, buildid: str, emit: Callable[[str], None]) -> str | None:
    """Merge the admin's version of *rel* into DayZ's new file, value by value.
    Returns the log line, or None when the file cannot be merged and the
    whole-file choice has to run instead."""
    from app.shared.config_versions import snapshot
    from app.shared.json_utils import atomic_write_bytes
    from app.shared.merge3 import merge3

    if stash.reference is None or rel not in stash.reference:
        return None  # no record: there is no old stock to merge against
    base = base_for(config, rel, stash.reference[rel])
    if base is None:
        emit(f"{rel}: the stock file it was made from is not kept here, so it cannot be merged value by value.")
        return None
    mission_dir, file_key = _split(config, rel)
    result = merge3(
        rel, base, copy.read_bytes(), live.read_bytes(),
        prefer="mine" if policy == "mine" else "dayz", sidecars=_sidecar_records(mission_dir, file_key),
    )
    if result.fallback:
        emit(f"{rel} cannot be merged value by value ({result.fallback}).")
        return None
    build = buildid or "unknown"
    snapshot(live, trigger="server_update", note=f"DayZ build {build}'s new file")
    snapshot(live, trigger="server_update", note=f"The version you had before DayZ build {build}", content=copy)
    if result.yours or result.rows or result.tweaks:
        atomic_write_bytes(live, result.text.encode("utf-8", errors="surrogateescape"))
        # Labelled here, so the merge's own snapshot before it adds the mods
        # back finds this content already kept, not an unexplained version.
        snapshot(live, trigger="server_update", note=f"Merged with DayZ build {build}, before the mods went back in")
    # else only the mods differed: DayZ's file stays byte for byte.
    _apply_sidecar_updates(mission_dir, file_key, result.sidecar_updates)
    _hold_merge(config, rel, copy, result, policy, stash.buildid, buildid)
    return _merge_line(rel, result, policy, buildid)


def _split(config: DayzConfig, rel: str) -> tuple[Path, str]:
    """(mission folder, path inside it) of a record path."""
    mission, _, inner = rel.partition("/")
    return _mpmissions(config) / mission, inner


def _sidecar_names() -> tuple[str, ...]:
    from app.presets.preset_engine import PRESET_BASELINES
    from app.presets.quick_tweaks import _TWEAK_BASELINES

    return (_TWEAK_BASELINES, PRESET_BASELINES)


def _sidecar_records(mission_dir: Path, file_key: str) -> dict[str, dict]:
    from app.presets.field_baselines import load_baselines
    from app.shared.merge3 import SIDECAR_FILES

    if file_key not in SIDECAR_FILES:
        return {}
    out = {}
    for sidecar in _sidecar_names():
        records = load_baselines(mission_dir, sidecar).get(file_key)
        if isinstance(records, dict) and records:
            out[sidecar] = records
    return out


def _apply_sidecar_updates(mission_dir: Path, file_key: str, updates: dict) -> None:
    from app.presets.field_baselines import load_baselines, save_baselines

    for sidecar, entries in updates.items():
        fields = load_baselines(mission_dir, sidecar)
        records = fields.setdefault(file_key, {})
        for entry, values in entries.items():
            records.setdefault(entry, {}).update(values)
        save_baselines(mission_dir, fields, sidecar)


def _hold_merge(config: DayzConfig, rel: str, copy: Path, result, policy: str, before: str, after: str) -> None:
    """List a merged file's values for the Update page, and keep the admin's
    whole file so "Use my whole file" still works (D3).

    A value still waiting from an earlier update is carried over, with DayZ's
    value as it is now, unless this merge lists the same value again. Nothing
    is listed when nothing is left to look at.
    """
    index = _read_json(_dir(config) / _CONFLICTS_INDEX)
    earlier = index.get(rel) if isinstance(index.get(rel), dict) else {}
    rows = list(result.rows)
    if earlier.get("merged"):
        rows += _carried(rel, earlier.get("values", []), result)
    elif earlier:
        # A whole-file conflict still waiting: what the admin had is the held
        # copy from then, not the file DayZ's version has been live in since.
        copy = _dir(config) / _CONFLICTS / rel
    if not rows:
        _drop_conflicts(config, lambda name: name == rel)
        return
    if copy != _dir(config) / _CONFLICTS / rel:
        _copy_into(copy, _dir(config) / _CONFLICTS / rel)
    index = _read_json(_dir(config) / _CONFLICTS_INDEX)
    index[rel] = {
        "build_before": before,
        "build_after": after,
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "merged": True,
        "dayz": result.dayz,
        "yours": result.yours,
        "tweaks": result.tweaks,
        "values": [
            {**row, "id": str(i), "waiting": row.get("waiting", policy == "ask" and row["kind"] != "dayz_removed")}
            for i, row in enumerate(rows, 1)
        ],
    }
    _write_json(_dir(config) / _CONFLICTS_INDEX, index)


def _carried(rel: str, earlier: list[dict], result) -> list[dict]:
    """The earlier update's rows still waiting, not listed again by *result*,
    with "theirs" read from the merged text (DayZ's value is what is live)."""
    from app.shared.merge3 import json_value_at
    from app.xml import xml_merge3 as xm

    listed = {json.dumps([row["keys"], row["target"]]) for row in result.rows}
    waiting = [row for row in earlier if row.get("waiting") and json.dumps([row.get("keys"), row.get("target")]) not in listed]
    if not waiting:
        return []
    is_json = rel.lower().endswith(".json")
    tree = json.loads(result.text) if is_json else xm.parse(result.text)
    out = []
    for row in waiting:
        keys, target = row.get("keys") or [], row.get("target") or []
        theirs = json_value_at(tree, keys) if is_json else xm.value_at(tree, keys, target, rel)
        out.append({**{k: v for k, v in row.items() if k != "id"}, "theirs": theirs})
    return out


def _merge_line(rel: str, result, policy: str, buildid: str) -> str:
    build = f"DayZ build {buildid}" if buildid else "the DayZ update"
    parts = [f"took {result.dayz} of DayZ's changes", f"kept {result.yours} of yours"]
    if result.tweaks:
        parts.append(f"scaled {result.tweaks} tweaked value(s) from DayZ's new numbers")
    line = f"Merged {rel} with {build}: {', '.join(parts)}."
    both = sum(1 for row in result.rows if row["kind"] != "dayz_removed")
    removed = len(result.rows) - both
    if both:
        kept = {"mine": "kept yours", "dayz": "kept DayZ's"}.get(policy, "DayZ's is live until you choose")
        line += f" {both} value(s) were changed by both sides: {kept}."
    if removed:
        line += f" DayZ removed {removed} entr{'y' if removed == 1 else 'ies'} you had changed."
    if result.rows:
        line += " See the Update page or: zsm update-conflicts list"
    return line


def _conflict_line(rel: str, policy: str, buildid: str) -> str:
    build = f"DayZ build {buildid}" if buildid else "The DayZ update"
    head = f"{build} changed {rel}, which differed from stock here."
    if policy == "mine":
        return f"{head} Your version was put back; DayZ's new file is saved in Versions."
    if policy == "dayz":
        return f"{head} DayZ's new file is kept; the version you had is saved in Versions."
    return (f"{head} DayZ's new file is live for now; choose which one to keep on the Update "
            f"page or with: zsm update-conflicts list")


# --- conflicts waiting for a choice ----------------------------------------


def list_conflicts(config: DayzConfig) -> list[dict]:
    """Every conflict still waiting for a choice, oldest first."""
    index = _read_json(_dir(config) / _CONFLICTS_INDEX)
    held = _dir(config) / _CONFLICTS
    rows = [
        {"file": rel, **meta} for rel, meta in index.items()
        if isinstance(meta, dict) and (held / rel).is_file()
    ]
    return sorted(rows, key=lambda row: (row.get("time", ""), row["file"]))


def resolve(config: DayzConfig, files: list[str] | None, keep: str, *, log: Log = None) -> list[str]:
    """Settle waiting conflicts a whole file at a time: *keep* "mine" puts the
    held version back, "dayz" keeps DayZ's file (for a merged file, puts DayZ's
    whole new file back). *files* None means all of them. Returns the files
    settled. `settle` is the same plus the merge that puts the mods back.

    A file name not on the list is ignored, never joined onto a path.
    """
    return _resolve(config, files, keep, log)[0]


def settle(config: DayzConfig, files: list[str] | None, keep: str, *, log: Log = None) -> tuple[list[str], bool]:
    """`resolve`, then the merge for every file a whole new version went into.
    Returns the files settled and whether the merge ran."""
    from app.xml.xml_utils import one_version_per_file

    with one_version_per_file():
        chosen, rewritten = _resolve(config, files, keep, log)
        return chosen, bool(rewritten) and remerge(config, rewritten, log=log)


def _resolve(config: DayzConfig, files: list[str] | None, keep: str, log: Log) -> tuple[list[str], list[str]]:
    from app.shared.json_utils import atomic_write_bytes
    from app.xml.xml_utils import snapshot_before_write

    if keep not in ("mine", "dayz"):
        raise ValueError(f"keep must be 'mine' or 'dayz', not {keep!r}")
    emit = log or (lambda _line: None)
    rows = {row["file"]: row for row in list_conflicts(config)}
    chosen = list(rows) if files is None else [rel for rel in rows if rel in set(files)]
    held = _dir(config) / _CONFLICTS
    rewritten: list[str] = []
    for rel in chosen:
        live = _mpmissions(config) / rel
        if keep == "mine":
            snapshot_before_write(live, trigger="update_conflict")
            _put_back(held / rel, live)
            rewritten.append(rel)
            emit(f"Put your version of {rel} back.")
        elif rows[rel].get("merged"):
            data = base_for(config, rel, _recorded_hash(config, rel))
            if data is None:
                emit(f"DayZ's new {rel} is not kept any more; the merged file stays.")
                continue
            snapshot_before_write(live, trigger="update_conflict")
            atomic_write_bytes(live, data)
            rewritten.append(rel)
            emit(f"Put DayZ's whole new {rel} back.")
        else:
            emit(f"Kept DayZ's new {rel}.")
    _drop_conflicts(config, lambda rel: rel in chosen)
    return chosen, rewritten


def _recorded_hash(config: DayzConfig, rel: str) -> str:
    files = _read_json(_dir(config) / _RECORD).get("files")
    return files.get(rel, "") if isinstance(files, dict) else ""


def settle_values(config: DayzConfig, rel: str, ids: list[str], keep: str, *, log: Log = None) -> tuple[list[str], bool]:
    """Settle listed values of a merged file: *keep* "mine" or "dayz" makes that
    side live, "ok" leaves the file as it is and only takes the row off the list.
    Returns the ids settled and whether the merge ran.

    An entry that is gone from the live file since (edited by hand) is reported
    and taken off the list; nothing is written for it.
    """
    from app.xml.xml_utils import one_version_per_file

    if keep not in ("mine", "dayz", "ok"):
        raise ValueError(f"keep must be 'mine', 'dayz' or 'ok', not {keep!r}")
    emit = log or (lambda _line: None)
    index_path = _dir(config) / _CONFLICTS_INDEX
    index = _read_json(index_path)
    meta = index.get(rel)
    if not isinstance(meta, dict) or not meta.get("merged"):
        return [], False
    wanted = set(ids)
    rows = [row for row in meta.get("values", []) if row.get("id") in wanted]
    apply = [row for row in rows if keep != "ok" and row.get("live") != keep]
    merged = False
    with one_version_per_file():
        if apply and _apply_values(config, rel, apply, keep, emit):
            merged = remerge(config, [rel], log=log)
        settled = [row["id"] for row in rows]
        meta["values"] = [row for row in meta.get("values", []) if row.get("id") not in set(settled)]
        if meta["values"]:
            _write_json(index_path, index)
        else:
            _drop_conflicts(config, lambda name: name == rel)
    return settled, merged


def _apply_values(config: DayzConfig, rel: str, rows: list[dict], keep: str, emit) -> bool:
    """Write *keep*'s side of each row into the live file. Returns whether
    anything was written."""
    live = _mpmissions(config) / rel
    side = "mine" if keep == "mine" else "theirs"
    who = "yours" if keep == "mine" else "DayZ's"
    if live.suffix.lower() == ".json":
        return _apply_json_values(live, rows, side, who, emit)
    return _apply_xml_values(live, rel, rows, side, who, emit)


def _apply_xml_values(live: Path, rel: str, rows: list[dict], side: str, who: str, emit) -> bool:
    import xml.etree.ElementTree as ET

    from app.xml import xml_merge3 as xm
    from app.xml.xml_merge import strip_mod_blocks_text
    from app.xml.xml_utils import save_xml

    try:
        root = xm.parse(strip_mod_blocks_text(live.read_text(encoding="utf-8"))[0])
    except (OSError, xm.Unmergeable) as exc:
        emit(f"{rel} could not be read, so nothing was changed ({exc}).")
        return False
    wrote = False
    for row in rows:
        value, target, keys = row.get(side), row.get("target") or [], row.get("keys") or []
        label = f"{row.get('entry')} {row.get('field')}"
        if target[:1] == ["entry"]:
            parent = xm.find_parent(root, keys, rel)
            current = xm.find_element(root, keys, rel)
            if parent is None:
                emit(f"{rel}: {label} is gone from the file since the update; nothing changed.")
                continue
            at = list(parent).index(current[0]) if current else len(parent)
            for el in current:
                parent.remove(el)
            if value:
                for offset, el in enumerate(list(xm.parse(f"<zsm-values>{value}</zsm-values>"))):
                    parent.insert(at + offset, el)
        else:
            found = xm.find_element(root, keys, rel)
            if len(found) != 1:
                emit(f"{rel}: {label} is gone from the file since the update; nothing changed.")
                continue
            if target[:1] == ["attr"]:
                if value is None:
                    found[0].attrib.pop(target[1], None)
                else:
                    found[0].set(target[1], value)
            else:
                found[0].text = value
        wrote = True
        emit(f"{rel}: {label} is now {who}.")
    if wrote:
        save_xml(ET.ElementTree(root), live)
    return wrote


def _apply_json_values(live: Path, rows: list[dict], side: str, who: str, emit) -> bool:
    from app.shared.json_utils import save_json
    from app.shared.merge3 import find_json

    try:
        doc = json.loads(live.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        emit(f"{live.name} could not be read, so nothing was changed ({exc}).")
        return False
    wrote = False
    for row in rows:
        label = f"{row.get('entry')} {row.get('field')}"
        parent, slot = find_json(doc, row.get("keys") or [])
        if parent is None:
            emit(f"{live.name}: {label} is gone from the file since the update; nothing changed.")
            continue
        value = row.get(side)
        if value is None:
            if isinstance(parent, dict):
                parent.pop(slot, None)
            elif slot is not None:
                del parent[slot]
        elif isinstance(parent, list) and slot is None:
            parent.append(json.loads(value))
        else:
            parent[slot] = json.loads(value)
        wrote = True
        emit(f"{live.name}: {label} is now {who}.")
    if wrote:
        save_json(live, doc)
    return wrote


def conflict_diff(config: DayzConfig, rel: str) -> str | None:
    """Unified diff from the live file to the version the admin had, or None
    when *rel* is not waiting for a choice. "+" lines are only in the admin's
    version, "-" lines only in the live one: DayZ's new file, or for a merged
    file the merge.

    Mod blocks are left out of both sides, because the merge puts them back
    whichever file is kept. XML is normalized the way Versions diffs it. An
    empty diff means the two differ only in mod blocks.
    """
    from difflib import unified_diff

    row = next((row for row in list_conflicts(config) if row["file"] == rel), None)
    if row is None:
        return None
    live = _mpmissions(config) / rel
    dayz = _diff_text(live) if live.is_file() else ""
    mine = _diff_text(_dir(config) / _CONFLICTS / rel)
    live_name = "the merged file" if row.get("merged") else "DayZ's new file"
    return "".join(unified_diff(
        dayz.splitlines(keepends=True),
        mine.splitlines(keepends=True),
        fromfile=f"{rel} ({live_name}, live)",
        tofile=f"{rel} (the version you had)",
    ))


def _diff_text(path: Path) -> str:
    from app.shared.config_versions import normalize_xml
    from app.xml.xml_merge import strip_mod_blocks_text

    text = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix == ".xml":
        text = normalize_xml(strip_mod_blocks_text(text)[0])
    return text


def _drop_conflicts(config: DayzConfig, which: Callable[[str], bool]) -> None:
    index_path = _dir(config) / _CONFLICTS_INDEX
    index = _read_json(index_path)
    gone = [rel for rel in index if which(rel)]
    if not gone:
        return
    for rel in gone:
        (_dir(config) / _CONFLICTS / rel).unlink(missing_ok=True)
        del index[rel]
    _write_json(index_path, index)


def needs_merge(config: DayzConfig, files: list[str]) -> bool:
    """Whether a put-back file is in the configured mission, whose mod blocks
    the merge has to refresh."""
    from app.modmgr.mods import get_current_mission

    mission = get_current_mission(config.server_cfg_path)
    return bool(mission) and any(rel.split("/", 1)[0] == mission for rel in files)


# --- for the callers that run SteamCMD through a runner --------------------


async def run_download(
    runner, config: DayzConfig, cmd: list[str], *,
    resetting: str | None = None, log: Log = None, remerge_mods: bool = True,
) -> int:
    """`runner.run_command(cmd)` with the mission files kept around it. Returns
    SteamCMD's exit code; the caller decides what a failure means.
    *remerge_mods* False when the caller's own merge step runs next."""
    import asyncio

    stash = await asyncio.to_thread(before_download, config, resetting=resetting, log=log)
    try:
        returncode = await runner.run_command(cmd)
    except BaseException:
        # A cancelled job or a crash: the copies go back as for a failure.
        download_failed(config, stash, log=log)
        raise
    if returncode != 0:
        download_failed(config, stash, log=log)
    else:
        await asyncio.to_thread(after_download, config, stash, log=log, remerge_mods=remerge_mods)
    return returncode


def _remerge_after_update(config: DayzConfig, files: list[str], emit: Callable[[str], None]) -> None:
    """The mods go back into a merged file. A failure is reported, never
    raised: the download worked and the merged file is on disk."""
    try:
        remerge(config, files, log=emit)
    except Exception as exc:  # noqa: BLE001 - the job log says what failed
        emit(f"Could not merge your mods back in after the update ({exc}). Use Re-apply all on Configs → Custom config, or run: zsm mods copy")


def remerge(config: DayzConfig, files: list[str], *, log: Log = None) -> bool:
    """After "keep mine": run the update's merge step when a put-back file is in
    the configured mission. Returns whether it ran."""
    if not needs_merge(config, files):
        return False
    from app.ops import pipeline

    emit = log or (lambda _line: None)
    ctx = pipeline.PipelineContext(config=config, emit=emit, runner=None, should_run=lambda _key: True)
    mission = pipeline._merge_xml_sync(ctx)
    emit(f"Merged your mods back into {mission}.")
    return True
