"""Config file versioning with timestamped shadow copies.

Provides automatic snapshotting before config writes, multi-version
history, diff between versions, and restore with undo.

**Every function works on one store** — an instance's `versions/` directory
(plans/44). Functions given a file path find the store themselves, from the
instance whose server holds the file (`store_for`), because their busiest
caller is `save_xml`/`save_json`, which knows the path and nothing else.
Functions that only have a file *name* or an index take the store from their
caller, who holds a config: ``store=config.paths.versions_dir``.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from difflib import unified_diff
from io import StringIO
from pathlib import Path

from app.shared.actor import current as _actor, operation_id as _operation

# (source path, version number) -> the operation that recorded it, in this
# process only: lets _note_unchanged see that the newest record is this same
# operation's own outer cause (a preset before the save inside it).
_recorded_by: dict[tuple[str, int], str] = {}
from app.shared.json_utils import on_atomic_write
from app.xml.xml_text import parse_xml_text

# Source path -> store, for every file this process took a version of: the
# files whose writes note_written records. Filled by snapshot and restore.
_tracked: dict[str, Path] = {}

# A change Zedstead did not write: a text editor, another program, a game update.
OUTSIDE_TRIGGER = "outside_edit"
OUTSIDE_ACTOR = "Outside Zedstead"


@dataclass
class VersionEntry:
    """Metadata for a single versioned snapshot."""

    file_name: str
    version: int
    timestamp: str  # ISO format
    trigger: str  # auto_save, pre_merge, pre_tweak, editor, manual, pre_restore
    size_bytes: int
    sha256: str
    note: str = ""
    source_path: str = ""  # absolute path of the original file
    # Older versions with this same content, folded into this one by
    # _drop_repeats: {version, timestamp, trigger, note} each, no copy kept.
    # The Timeline still shows them as changes (timeline, find_version).
    repeats: list[dict] = field(default_factory=list)
    # Who made the write this version precedes: a panel user, "CLI (<user>)"
    # or "Zedstead" (app/shared/actor.py). Empty for a version from before.
    actor: str = ""


@dataclass
class VersionIndex:
    """Index of all version entries, persisted as JSON."""

    entries: list[VersionEntry] = field(default_factory=list)
    # Source path -> {sha256, timestamp, actor}: the content Zedstead last wrote
    # (note_written) or last saw (snapshot). A live file that differs was
    # changed outside Zedstead (timeline, _record_outside).
    written: dict[str, dict] = field(default_factory=dict)

    def get_versions(self, file_name: str, source_path: str = "") -> list[VersionEntry]:
        """Get all versions for a file, newest first.

        When *source_path* is given, only entries whose ``source_path``
        matches (or legacy entries with no ``source_path``) are returned.
        """
        matches = [e for e in self.entries if e.file_name == file_name]
        if source_path:
            matches = [
                e for e in matches
                if e.source_path == source_path or not e.source_path
            ]
        return sorted(matches, key=lambda e: e.version, reverse=True)

    def get_latest(self, file_name: str, source_path: str = "") -> VersionEntry | None:
        versions = self.get_versions(file_name, source_path)
        return versions[0] if versions else None

    def next_version(self, file_name: str, source_path: str = "") -> int:
        """One past the highest number this file used, a recorded repeat's
        included (snapshot's unchanged case, _drop_repeats)."""
        numbers = [
            n
            for e in self.get_versions(file_name, source_path)
            for n in [e.version, *(r.get("version", 0) for r in e.repeats)]
        ]
        return max(numbers) + 1 if numbers else 1

    def versioned_files(self) -> list[tuple[str, int]]:
        """List all files with version count, sorted by name."""
        counts: dict[str, int] = {}
        for e in self.entries:
            counts[e.file_name] = counts.get(e.file_name, 0) + 1
        return sorted(counts.items())


def store_for(file_path: Path | None) -> Path:
    """The versions store of the instance that owns *file_path*.

    Owning means the file is under that instance's `server_dir` (a mission
    file) or under its own config directory (a manual XML file). With several
    matches the deepest wins. A file no instance owns — and every file, until
    plans/44 step 4 creates the first instance — goes to the pre-instances
    store, which is where it went before instances existed.
    """
    from app.server.instances import load_registry
    from app.shared.config import ConfigNotFound, DayzConfig
    from app.shared.paths import InstancePaths, instance_root

    best: tuple[int, Path] | None = None
    if file_path is not None:
        target = Path(file_path).resolve()
        for inst in load_registry().instances:
            try:
                cfg = DayzConfig.for_instance(inst.id)
            except (ConfigNotFound, ValueError, OSError):
                continue
            roots = [cfg.paths.root]
            if cfg.server_dir:
                roots.append(cfg.server_dir_path)
            for root in roots:
                root = root.resolve()
                if target.is_relative_to(root) and (best is None or len(root.parts) > best[0]):
                    best = (len(root.parts), cfg.paths.versions_dir)
    if best:
        return best[1]
    return InstancePaths(instance_root("")).versions_dir


def _index_path(store: Path) -> Path:
    return store / "index.json"


def _version_filename(entry: VersionEntry) -> str:
    ts = entry.timestamp.replace(":", "").replace("-", "")
    return f"{entry.file_name}.{ts}.v{entry.version}.bak"


def load_index(store: Path) -> VersionIndex:
    """Load the version index from disk."""
    path = _index_path(store)
    if not path.exists():
        return VersionIndex()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = [VersionEntry(**e) for e in data.get("entries", [])]
        return VersionIndex(entries=entries, written=data.get("written", {}))
    except (json.JSONDecodeError, TypeError, KeyError):
        return VersionIndex()


def save_index(index: VersionIndex, store: Path) -> None:
    """Persist the version index to disk."""
    store.mkdir(parents=True, exist_ok=True)
    data = {"entries": [asdict(e) for e in index.entries], "written": index.written}
    _index_path(store).write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(
    file_path: Path,
    trigger: str = "auto_save",
    note: str = "",
    max_versions: int = 50,
    *,
    store: Path | None = None,
    content: Path | None = None,
) -> VersionEntry | None:
    """Create a versioned snapshot. Returns None if file unchanged.

    *content* snapshots another file's bytes as a version of *file_path*: a
    copy taken aside, such as the version a server update replaced (plans/67).
    """
    source = content or file_path
    if not source.is_file():
        return None

    store = store or store_for(file_path)
    store.mkdir(parents=True, exist_ok=True)
    index = load_index(store)

    current_hash = _file_hash(source)
    sp = str(file_path)
    _tracked[sp] = store
    if content is None:
        # The file differs from what Zedstead last wrote: keep that as its own
        # change first, then this version on top of it.
        _record_outside(index, file_path, current_hash, store)
        _mark_seen(index, sp, current_hash, store)
    latest = index.get_latest(file_path.name, source_path=sp)
    if latest and latest.sha256 == current_hash:
        # No new copy: the newest version holds these bytes already. The write
        # that follows is still an event (who, why, when), or the Timeline
        # credits it to whatever kept that version.
        _note_unchanged(index, latest, file_path, trigger, note, store)
        return None

    version = index.next_version(file_path.name, source_path=sp)
    now = datetime.now(timezone.utc)
    entry = VersionEntry(
        file_name=file_path.name,
        version=version,
        timestamp=now.isoformat(),
        trigger=trigger,
        size_bytes=source.stat().st_size,
        sha256=current_hash,
        note=note,
        source_path=str(file_path),
        actor=_actor(),
    )

    dest = store / _version_filename(entry)
    shutil.copy2(source, dest)

    index.entries.append(entry)
    _recorded_by[(sp, version)] = _operation()
    _drop_repeats(index, file_path.name, sp, store)

    # Rotate old versions
    versions = index.get_versions(file_path.name, source_path=sp)
    if len(versions) > max_versions:
        for old in versions[max_versions:]:
            old_path = store / _version_filename(old)
            old_path.unlink(missing_ok=True)
            index.entries.remove(old)

    save_index(index, store)
    return entry


def restore(file_path: Path, version: int, *, store: Path | None = None) -> bool:
    """Restore a file to a specific version. Snapshots current state first."""
    store = store or store_for(file_path)
    index = load_index(store)
    entry = _find_entry(index, file_path.name, version, source_path=str(file_path))
    if not entry:
        return False

    src = store / _version_filename(entry)
    if not src.is_file():
        return False

    # The snapshot below can drop this very version, when the file is already
    # back at an earlier copy of it (_drop_repeats), so the bytes to restore
    # are set aside first.
    import tempfile

    _tracked[str(file_path)] = store
    fd, held = tempfile.mkstemp(dir=store, prefix=".restore-")
    os.close(fd)
    try:
        shutil.copy2(src, held)
        # Snapshot current state before restoring (so restore is undoable)
        if file_path.is_file():
            snapshot(
                file_path, trigger="pre_restore", note=f"before restore to v{version}", store=store,
            )
        shutil.copy2(held, file_path)
        note_written(file_path)
    finally:
        Path(held).unlink(missing_ok=True)
    return True


def _file_line(index: VersionIndex, file_name: str, source_path: str) -> list[tuple[int, VersionEntry]]:
    """Every version number of one file in order, each with the entry that
    holds its content: its own, or the newer copy it was folded into."""
    line: list[tuple[int, VersionEntry]] = []
    for e in index.entries:
        if e.file_name == file_name and e.source_path == source_path:
            line.append((e.version, e))
            line.extend((r["version"], e) for r in e.repeats if "version" in r)
    return sorted(line, key=lambda pair: pair[0])


def timeline(
    store: Path, limit: int = 500, *, offset: int = 0, source_path: str | None = None
) -> list[dict]:
    """Every stored version that records a real change, newest first: *limit*
    of them after the newest *offset*, of one file when *source_path* is set.

    A version holds a file as it was just before a write, so its timestamp
    and trigger say when the file changed and why. A preset or tweak keeps
    every file it might touch, so a version whose next content (the next
    version, or the live file) has the same hash records no change and is
    left out: a food tweak never shows as a change to globals.xml. Folded
    repeats (_drop_repeats) count as versions. Legacy entries without a
    source path are left out: their file cannot be told apart.
    """
    index = load_index(store)
    events = []
    files = {
        (e.file_name, e.source_path)
        for e in index.entries
        if e.source_path and (source_path is None or e.source_path == source_path)
    }
    for file_name, source_path in files:
        line = _file_line(index, file_name, source_path)
        live = Path(source_path)
        live_hash = _file_hash(live) if live.is_file() else None
        written = index.written.get(source_path, {}).get("sha256")
        if written and live_hash and live_hash != written:
            # Changed outside Zedstead since its last write, and no Zedstead save
            # since: no version holds it yet (get_outside_diff shows it).
            events.append({
                "file_name": file_name,
                "source_path": source_path,
                "version": None,
                "pending": True,
                "timestamp": _outside_time(live, index.written[source_path]),
                "trigger": OUTSIDE_TRIGGER,
                "note": "",
                "actor": OUTSIDE_ACTOR,
            })
        for i, (version, holder) in enumerate(line):
            # The newest version's write left what Zedstead wrote, not
            # necessarily the live file (an outside edit came after).
            after = line[i + 1][1].sha256 if i + 1 < len(line) else (written or live_hash)
            if after == holder.sha256:
                continue
            own = holder if holder.version == version else next(
                r for r in holder.repeats if r.get("version") == version
            )
            event = {
                "file_name": file_name,
                "source_path": source_path,
                "version": version,
                "timestamp": own.timestamp if own is holder else own["timestamp"],
                "trigger": own.trigger if own is holder else own["trigger"],
                "note": own.note if own is holder else own.get("note", ""),
                "actor": own.actor if own is holder else own.get("actor", ""),
            }
            # Folded: an older event whose copy a newer version holds. A
            # later record on an older copy (_note_unchanged) is no fold.
            if own is not holder and version < holder.version:
                event["repeat_of"] = holder.version
            events.append(event)
    # The path and version break a tie, so a page after *offset* never repeats
    # or skips an event.
    events.sort(key=lambda e: (e["timestamp"], e["source_path"], e["version"] or 0), reverse=True)
    return events[offset:offset + limit]


def get_diff_change(
    file_path: Path, version: int, current: Path | None = None, *, store: Path | None = None
) -> str:
    """What the write after *version* changed: a unified diff from that
    version to the file's next version (a folded repeat counts), or to the
    live file (or *current*, as in :func:`get_diff_current`) when it is the
    newest."""
    store = store or store_for(file_path)
    index = load_index(store)
    line = _file_line(index, file_path.name, str(file_path))
    at = next((i for i, (v, _) in enumerate(line) if v == version), None)
    if at is None:
        return ""
    entry = line[at][1]
    if at + 1 < len(line):
        number, after = line[at + 1]
        content_new = _version_diff_text(after, store)
        label = f"{file_path.name} (v{number})"
    else:
        content_new = _current_diff_text(current or file_path)
        label = f"{file_path.name} (current)"
    content_old = _version_diff_text(entry, store)
    if content_old is None or content_new is None:
        return ""
    return "".join(
        unified_diff(
            content_old.splitlines(keepends=True),
            content_new.splitlines(keepends=True),
            fromfile=f"{file_path.name} (v{version})",
            tofile=label,
        )
    )


def try_normalize_xml(content: str) -> tuple[str, bool]:
    """Re-indent XML with tabs for a stable diff. Returns (text, parsed_ok).

    Falls back to raw content on parse failure so the diff is still
    computable; callers that care can branch on ``parsed_ok=False``.

    Deliberately different from `xml_text.format_xml_text` in two ways, and it
    is not an oversight: this one *canonicalizes* the XML declaration (a
    quote-style difference must not read as a change in a diff, whereas an
    editor must never restyle the user's own file), and it parses without
    comments — which means a version differing from the live file *only* in a
    ``<!-- START MOD: @X -->`` marker normalizes to identical. That is a known
    wart, not a design goal; fixing it changes what the Analysis page's diffs
    and counters show, so it is its own change.
    """
    try:
        tree = parse_xml_text(content)
    except ET.ParseError:
        return content, False
    ET.indent(tree, space="\t")
    out = StringIO()
    tree.write(out, encoding="unicode", xml_declaration=True)
    result = out.getvalue()
    if not result.endswith("\n"):
        result += "\n"
    return result, True


def normalize_xml(content: str) -> str:
    """Normalize XML formatting for consistent diffs (raw fallback on parse error)."""
    text, _ok = try_normalize_xml(content)
    return text


def get_diff(file_name: str, version_a: int, version_b: int, *, store: Path) -> str:
    """Get unified diff between two versions."""
    index = load_index(store)
    entry_a = _find_entry(index, file_name, version_a)
    entry_b = _find_entry(index, file_name, version_b)
    if not entry_a or not entry_b:
        return ""

    content_a = _read_version_content(entry_a, store)
    content_b = _read_version_content(entry_b, store)
    if content_a is None or content_b is None:
        return ""

    if file_name.endswith(".xml"):
        content_a = normalize_xml(content_a)
        content_b = normalize_xml(content_b)

    diff = unified_diff(
        content_a.splitlines(keepends=True),
        content_b.splitlines(keepends=True),
        fromfile=f"{file_name} (v{version_a})",
        tofile=f"{file_name} (v{version_b})",
    )
    return "".join(diff)


def _current_diff_text(file_path: Path, cache: dict | None = None) -> str | None:
    """The live file's content as the diff sees it, or None when it's gone.

    XML is normalized the same way stored versions are, so callers comparing
    against a version compare like-for-like. ``cache`` (a caller-owned dict) is
    keyed by path so a batch of entries for one file only reads and re-indents
    it once.
    """
    key = str(file_path)
    if cache is not None and key in cache:
        return cache[key]

    text: str | None = None
    if file_path.is_file():
        try:
            text = file_path.read_text(encoding="utf-8")
        except OSError:
            text = None
        if text is not None and file_path.suffix == ".xml":
            text = normalize_xml(text)

    if cache is not None:
        cache[key] = text
    return text


def _version_diff_text(entry: VersionEntry, store: Path) -> str | None:
    """A stored version's content as the diff sees it, or None when the backup
    file is missing."""
    content = _read_version_content(entry, store)
    if content is None:
        return None
    return normalize_xml(content) if entry.file_name.endswith(".xml") else content


def is_identical_to_current(
    entry: VersionEntry,
    cache: dict | None = None,
    current: Path | None = None,
    *,
    store: Path,
) -> bool:
    """True when *entry* has no differences against the live file.

    Exactly mirrors ``get_diff_current(...) == ""`` — including its degenerate
    cases (missing backup or missing live file both diff to nothing) — so a UI
    that hides "identical" versions can never hide a row whose diff would have
    shown content. Entries without a ``source_path`` (legacy index rows) can't
    be diffed at all and are reported as *not* identical, so they stay visible.

    Pass a shared ``cache`` dict when checking many entries for one file.
    ``current`` is the file holding the live content when that is not the
    snapshotted path, as for ``get_diff_current``.
    """
    if not entry.source_path:
        return False

    current_text = _current_diff_text(current or Path(entry.source_path), cache)
    if current_text is None:
        return True

    stored = _version_diff_text(entry, store)
    if stored is None:
        return True

    return stored == current_text


def get_diff_current(
    file_path: Path, version: int, current: Path | None = None, *, store: Path | None = None
) -> str:
    """Get diff between a version and the current file.

    ``current`` is the file holding the live content when that is not
    *file_path*: BattlEye's active copy of beserver_x64.cfg while the server
    runs (``battleye_cfg.live_file``). The diff is still labelled with
    *file_path*'s name.
    """
    store = store or store_for(file_path)
    index = load_index(store)
    entry = _find_entry(index, file_path.name, version, source_path=str(file_path))
    if not entry:
        return ""

    content_new = _current_diff_text(current or file_path)
    if content_new is None:
        return ""

    content_old = _version_diff_text(entry, store)
    if content_old is None:
        return ""

    diff = unified_diff(
        content_old.splitlines(keepends=True),
        content_new.splitlines(keepends=True),
        fromfile=f"{file_path.name} (v{version})",
        tofile=f"{file_path.name} (current)",
    )
    return "".join(diff)


def delete_version(file_name: str, version: int, *, store: Path) -> bool:
    """Delete a specific version."""
    index = load_index(store)
    entry = _find_entry(index, file_name, version)
    if not entry:
        return False

    path = store / _version_filename(entry)
    path.unlink(missing_ok=True)
    index.entries.remove(entry)
    save_index(index, store)
    return True


def get_outside_diff(file_path: Path, current: Path | None = None, *, store: Path | None = None) -> str:
    """What changed outside Zedstead since its last write: a unified diff from
    what Zedstead last wrote or saw to the live file (or *current*)."""
    store = store or store_for(file_path)
    index = load_index(store)
    seen = index.written.get(str(file_path))
    data = _written_bytes(file_path, seen["sha256"], store) if seen else None
    content_new = _current_diff_text(current or file_path)
    if data is None or content_new is None:
        return ""
    content_old = data.decode("utf-8", errors="replace")
    if file_path.suffix == ".xml":
        content_old = normalize_xml(content_old)
    return "".join(
        unified_diff(
            content_old.splitlines(keepends=True),
            content_new.splitlines(keepends=True),
            fromfile=f"{file_path.name} (last Zedstead write)",
            tofile=f"{file_path.name} (current)",
        )
    )


def note_written(file_path: Path) -> None:
    """Record the content Zedstead just wrote to *file_path*, so a later change
    made outside Zedstead can be told apart (``on_atomic_write`` calls it after
    every atomic write). Only a file this process took a version of counts.
    Keeps one copy per content in ``<store>/written/``, for the diff."""
    sp = str(file_path)
    store = _tracked.get(sp)
    if store is None or not file_path.is_file():
        return
    data = file_path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    folder = store / "written"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{sha}.bak").write_bytes(data)
    index = load_index(store)
    _set_written(index, sp, sha, store)
    save_index(index, store)


on_atomic_write(note_written)


def _set_written(index: VersionIndex, source_path: str, sha: str, store: Path) -> None:
    """Point *source_path*'s record at *sha*, and drop the copy of its old
    content when no other file's record uses it."""
    old = index.written.get(source_path, {}).get("sha256")
    index.written[source_path] = {
        "sha256": sha,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "actor": _actor(),
    }
    if old and old != sha and all(w.get("sha256") != old for w in index.written.values()):
        (store / "written" / f"{old}.bak").unlink(missing_ok=True)


def _mark_seen(index: VersionIndex, source_path: str, sha: str, store: Path) -> None:
    """A snapshot saw *sha*: Zedstead knows this content now. Its bytes are the
    version the snapshot keeps, so no copy is written (_written_bytes)."""
    _set_written(index, source_path, sha, store)


def _written_bytes(file_path: Path, sha: str, store: Path) -> bytes | None:
    """The bytes Zedstead last wrote or saw for *file_path*: its copy in
    ``written/``, else a version with that content."""
    path = store / "written" / f"{sha}.bak"
    try:
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() == sha:
            return data
    except OSError:
        pass
    return content_with_hash(file_path, sha, store=store)


def _outside_time(live: Path, seen: dict) -> str:
    """When an outside change happened: the file's modification time, unless
    that is before Zedstead's own last write (a copy that kept an old time)."""
    try:
        mtime = datetime.fromtimestamp(live.stat().st_mtime, timezone.utc)
    except OSError:
        return datetime.now(timezone.utc).isoformat()
    since = seen.get("timestamp", "")
    return mtime.isoformat() if mtime.isoformat() > since else since


def _record_outside(index: VersionIndex, file_path: Path, current_hash: str, store: Path) -> None:
    """Keep a change made outside Zedstead as its own version: the content Zedstead
    last wrote, as the version before the change, credited to OUTSIDE_ACTOR.
    Nothing when the file holds what Zedstead wrote, or that content is gone."""
    sp = str(file_path)
    seen = index.written.get(sp)
    if not seen or seen.get("sha256") == current_hash:
        return
    data = _written_bytes(file_path, seen["sha256"], store)
    if data is None:
        return
    latest = index.get_latest(file_path.name, source_path=sp)
    if latest and latest.sha256 == seen["sha256"]:
        # The newest version holds it already (a snapshot with no write
        # after it): the outside change is the event on that version.
        latest.repeats.append({
            "version": index.next_version(file_path.name, source_path=sp),
            "timestamp": _outside_time(file_path, seen),
            "trigger": OUTSIDE_TRIGGER,
            "note": "",
            "actor": OUTSIDE_ACTOR,
        })
        return
    entry = VersionEntry(
        file_name=file_path.name,
        version=index.next_version(file_path.name, source_path=sp),
        timestamp=_outside_time(file_path, seen),
        trigger=OUTSIDE_TRIGGER,
        size_bytes=len(data),
        sha256=seen["sha256"],
        source_path=sp,
        actor=OUTSIDE_ACTOR,
    )
    (store / _version_filename(entry)).write_bytes(data)
    index.entries.append(entry)


def cleanup_all(max_versions: int = 50, *, store: Path) -> int:
    """Enforce max versions across all files, and keep one version per
    distinct content of each file (:func:`_drop_repeats`). Returns count removed."""
    index = load_index(store)
    removed = 0
    for file_name, source_path in {(e.file_name, e.source_path) for e in index.entries if e.source_path}:
        removed += _drop_repeats(index, file_name, source_path, store)
    for file_name, _count in index.versioned_files():
        versions = index.get_versions(file_name)
        if len(versions) > max_versions:
            for old in versions[max_versions:]:
                path = store / _version_filename(old)
                path.unlink(missing_ok=True)
                index.entries.remove(old)
                removed += 1
    if removed:
        save_index(index, store)
    return removed


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _drop_repeats(index: VersionIndex, file_name: str, source_path: str, store: Path) -> int:
    """Keep only the newest version of each distinct content of one file.

    A file that goes A -> B -> A (a preset applied and reset, a restore, an
    undo by hand) kept a copy of A at every step, so the Versions list filled
    with copies identical to each other and to the live file (2026-11-03).
    The newest copy says when the file last held that content; the older ones
    add nothing a diff or restore needs. Only entries with this exact source
    path count: a legacy one without it can be another mission's file.
    """
    kept: dict[str, VersionEntry] = {}
    removed = 0
    for e in index.get_versions(file_name, source_path=source_path):
        if e.source_path != source_path:
            continue
        newer = kept.get(e.sha256)
        if newer is None:
            kept[e.sha256] = e
            continue
        # The copy goes; when and why the file held this content stays, on
        # the version that keeps it, so the Timeline keeps the event.
        newer.repeats.extend(e.repeats)
        newer.repeats.append(
            {"version": e.version, "timestamp": e.timestamp, "trigger": e.trigger, "note": e.note, "actor": e.actor}
        )
        (store / _version_filename(e)).unlink(missing_ok=True)
        index.entries.remove(e)
        removed += 1
    return removed


def _note_unchanged(
    index: VersionIndex, latest: VersionEntry, file_path: Path, trigger: str, note: str, store: Path
) -> None:
    """Record a write whose before-content the newest version already holds,
    as a repeat on it. Only the newest such record can precede a change (the
    content stayed the same up to it), so a trailing one is replaced, never
    piled up."""
    number = index.next_version(file_path.name, source_path=str(file_path))
    # This operation already recorded the newest event (a preset, then the
    # save inside it): its outer cause stands.
    if _recorded_by.get((str(file_path), number - 1)) == _operation():
        save_index(index, store)  # snapshot's record of what it saw
        return
    record = {
        "version": number,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "trigger": trigger,
        "note": note,
        "actor": _actor(),
    }
    if latest.repeats and latest.repeats[-1].get("version") == number - 1:
        record["version"] = number - 1
        latest.repeats[-1] = record
    else:
        latest.repeats.append(record)
    _recorded_by[(str(file_path), record["version"])] = _operation()
    save_index(index, store)


def find_version(
    index: VersionIndex, file_name: str, version: int, source_path: str = ""
) -> tuple[VersionEntry | None, bool]:
    """The entry that holds *version*'s content, and whether *version* is a
    repeat folded into it (_drop_repeats) rather than the entry itself."""
    entry = _find_entry(index, file_name, version, source_path=source_path)
    if entry is not None:
        return entry, False
    for e in index.get_versions(file_name, source_path=source_path):
        if any(r.get("version") == version for r in e.repeats):
            return e, True
    return None, False


def _find_entry(
    index: VersionIndex, file_name: str, version: int, source_path: str = ""
) -> VersionEntry | None:
    for e in index.entries:
        if e.file_name == file_name and e.version == version:
            if source_path and e.source_path and e.source_path != source_path:
                continue
            return e
    return None


def content_with_hash(file_path: Path, sha256: str, *, store: Path) -> bytes | None:
    """The bytes of a version of *file_path* whose sha256 is *sha256*, or None.

    A server update looks for the stock file it compared against this way
    (plans/71): the first editor save of a stock file stored the stock bytes.
    """
    index = load_index(store)
    for entry in index.get_versions(file_path.name, source_path=str(file_path)):
        if entry.sha256 != sha256:
            continue
        path = store / _version_filename(entry)
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if hashlib.sha256(data).hexdigest() == sha256:
            return data
    return None


def version_path(entry: VersionEntry, store: Path) -> Path:
    """Where *entry*'s bytes are kept in *store*."""
    return store / _version_filename(entry)


def contents_of(paths) -> dict[Path, bytes]:
    """The bytes of each existing file in *paths*, read before a save.

    Half of the editors' "every save keeps the file it replaced" guarantee:
    the route reads the files with this, runs the editor's save, then calls
    :func:`keep_replaced`. A file that does not exist yet has nothing to keep.
    """
    before: dict[Path, bytes] = {}
    for path in paths:
        path = Path(path)
        try:
            before[path] = path.read_bytes()
        except OSError:
            continue
    return before


def keep_replaced(before: dict[Path, bytes], trigger: str = "editor") -> list[VersionEntry]:
    """Store the bytes a save replaced as a version, where no version holds them.

    Run after a save succeeded, with what :func:`contents_of` read before it.
    An editor's own snapshot already holds those bytes, so this adds nothing
    then; a save path that forgot its snapshot still leaves the file it
    replaced restorable. Run only after success, so a refused save leaves no
    version (app/web/CLAUDE.md).
    """
    import tempfile

    kept: list[VersionEntry] = []
    for path, old in before.items():
        try:
            if path.read_bytes() == old:
                continue
        except OSError:
            pass  # deleted by the save: what it held is still worth keeping
        store = store_for(path)
        old_hash = hashlib.sha256(old).hexdigest()
        index = load_index(store)
        if any(e.sha256 == old_hash for e in index.get_versions(path.name, source_path=str(path))):
            continue
        store.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=store, prefix=".replaced-")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(old)
            entry = snapshot(path, trigger=trigger, store=store, content=Path(tmp))
        finally:
            Path(tmp).unlink(missing_ok=True)
        if entry is not None:
            kept.append(entry)
    return kept


def removed_names(
    path: Path,
    names_of,
    *,
    store: Path | None = None,
    limit: int = 100,
) -> list[dict]:
    """Names earlier versions of *path* list and the live file does not.

    The server-side trash of a list editor (the Ignore list's "Recently
    removed"): since every editor save keeps the file it replaced, a removed
    entry is still in the version taken at that save. *names_of* reads the
    names from a file. Newest removal first; ``removed_at`` is the time of the
    newest version that still lists the name, which is the save that dropped
    it. A version that cannot be read is skipped.
    """
    store = store or store_for(path)
    current = set(names_of(path)) if path.is_file() else set()
    found: dict[str, dict] = {}
    for entry in load_index(store).get_versions(path.name, source_path=str(path)):
        # A legacy entry without a source path can be another mission's file
        # of the same name: its names were never removed from this one.
        if entry.source_path != str(path):
            continue
        if len(found) >= limit:
            break
        try:
            names = names_of(version_path(entry, store))
        except Exception:  # noqa: BLE001 — one bad version never hides the rest
            continue
        for name in names:
            if name and name not in current and name not in found:
                found[name] = {"name": name, "removed_at": entry.timestamp, "version": entry.version}
                if len(found) >= limit:
                    break
    return list(found.values())


def _read_version_content(entry: VersionEntry, store: Path) -> str | None:
    path = store / _version_filename(entry)
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")
