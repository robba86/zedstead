"""What is on a DayZ server zsm did not install (plans/78).

`scan(config)` reads the server directory ``config.server_dir`` names and
returns a `Report`: how the server was started, the ``@`` folders it holds and
whether that command loads them, any systemd unit that runs it, and the
mission. A pure reader: nothing here writes, moves or starts anything, so it
can run before anything else on a server someone already uses.

`apply(config, report)` writes zsm's own state from it and nothing else: the
mod list (mods.json) and the launch fields in config.json. No file on the
server is moved, renamed, deleted or rewritten by adopting.

The launch command comes from three places, best first:

1. the latest ``.RPT``: line 3 is the command the engine really ran (measured
   on 1.29, plans/78 step 0), read through `log_analysis`;
2. a systemd unit whose command runs this directory's server;
3. a start script (``*.sh``, ``*.bat``, ``*.cmd``) in the server directory.

The others are still listed, so a surface can show where they disagree.
"""

from __future__ import annotations

import os
import re
import shutil
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from collections.abc import Callable
from typing import TYPE_CHECKING

from app.modmgr import modlist
from app.modmgr.mod_info import parse_mod_file
from app.modmgr.mods import EXCLUDED_MOD_KEYWORDS, is_workshop_id
from app.ops import log_analysis
from app.parsers.servercfg import read_mission_template
from app.server.unit_guard import ZSM_UNIT_MARKER, is_zsm_unit
from app.shared.platform import IS_WINDOWS
from app.shared.zip_utils import is_bare_name
from app.xml.xml_classify import KIND_TYPES, terrain_from_mission
from app.xml.xml_economy import ce_bucket
from app.xml.xml_manual import normalize_source, set_source_state, unowned_children
from app.xml.xml_merge import iter_mod_xml_files
from app.xml.xml_text import parse_xml_text, strip_declaration
from app.xml.xml_utils import load_xml, save_xml

if TYPE_CHECKING:
    from app.shared.config import DayzConfig

SOURCE_RPT = "rpt"
SOURCE_UNIT = "unit"
SOURCE_SCRIPT = "script"
#: What a surface calls each source.
SOURCE_LABELS = {SOURCE_RPT: "server log", SOURCE_UNIT: "unit", SOURCE_SCRIPT: "start script"}

ID_FROM_META = "meta.cpp"
ID_KNOWN = "known"

SCRIPT_SUFFIXES = (".sh", ".bat", ".cmd")

#: The flags whose values zsm keeps in its own settings. The rest of the
#: command is ``extra_args``.
VALUE_FLAGS = {
    "config": "-config",
    "profiles": "-profiles",
    "battleye": "-BEpath",
    "port": "-port",
    "cpu_count": "-cpuCount",
}
_OWN_FLAGS = {f.lower() for f in (*VALUE_FLAGS.values(), "-mod", "-serverMod")}


#: A shell or cmd variable in a command: its value is not in the file.
_VARIABLE = re.compile(r"\$\{?\w+|%\w+%")
_BINARY = re.compile(r"DayZServer(?:_x64)?(?:\.exe)?", re.IGNORECASE)


@dataclass
class Launch:
    """One launch command and what it says."""

    source: str
    path: str
    command: str
    mods: list[str] = field(default_factory=list)
    server_mods: list[str] = field(default_factory=list)
    values: dict[str, str] = field(default_factory=dict)
    extra_args: list[str] = field(default_factory=list)
    #: The command names a variable (``$MODS``, ``%MODS%``) whose value the
    #: file does not hold, so what it reads from it is not the whole story.
    uses_variables: bool = False

    @property
    def source_label(self) -> str:
        return SOURCE_LABELS.get(self.source, self.source)


@dataclass
class ModFolder:
    name: str
    workshop_id: str = ""
    id_source: str = ""
    loaded: bool = False
    server_only: bool = False
    #: Why zsm cannot list this folder (`modlist.name_problem`), or "".
    #: Such a folder is reported and never adopted.
    problem: str = ""

    @property
    def local(self) -> bool:
        """No Workshop ID: zsm has nowhere to download it from (D1)."""
        return not self.workshop_id


@dataclass
class Unit:
    name: str
    scope: str
    path: str
    command: str
    #: Rendered by zsm (carries `ZSM_UNIT_MARKER`).
    zsm: bool
    #: The name zsm gives this instance's own unit (`server_unit`).
    zsm_name: bool


@dataclass
class PastedTypes:
    """A listed mod whose types are already in the mission's own types.xml
    (plans/78 D3). Merging the mod as well would put each class in twice:
    the engine says nothing, and the mod's stock rejects become the admin's
    errors in the log report (step 0)."""

    mod: str
    #: The mod's types files that hold one of them, server-dir relative with
    #: forward slashes: the exclusion list's key.
    files: list[str]
    #: The classes in both, as the mission spells them.
    classes: list[str]
    #: Classes the mission holds once, differently from the mod: DayZ's own
    #: entry for a class the mod redefines, or a paste the admin changed.
    #: Named, never removed (`_pasted_copies`).
    changed: list[str] = field(default_factory=list)


@dataclass
class Report:
    server_dir: str
    launch: Launch | None
    launches: list[Launch]
    mods: list[ModFolder]
    #: Named by the launch command, with no folder on disk.
    missing: list[str]
    units: list[Unit]
    mission: str | None
    rpt: str | None
    pasted: list[PastedTypes] = field(default_factory=list)

    def to_dict(self) -> dict:
        data = asdict(self)
        if self.launch is not None:
            data["launch"]["source_label"] = self.launch.source_label
        for row, mod in zip(data["mods"], self.mods):
            row["local"] = mod.local
        return data


# --- launch commands ---------------------------------------------------------


def _strip_quotes(value: str) -> str:
    return value.strip().strip("'\"").strip()


def _flag_value(cmd: str, flag: str) -> str | None:
    """*flag*'s value in *cmd*: quoted whole (``"-profiles=a b"``), or up to
    the next flag (the RPT's form, where the quotes are gone)."""
    name = re.escape(flag)
    for pattern in (rf'"{name}=([^"]*)"', rf"'{name}=([^']*)'"):
        m = re.search(pattern, cmd, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    m = re.search(rf"(?:^|\s){name}=(.*?)(?=\s+[\"']?-\w|\s*$)", cmd, re.IGNORECASE)
    return _strip_quotes(m.group(1)) if m else None


def _after_binary(cmd: str) -> str:
    """The arguments: what follows the server binary (a unit's ``bash -c``
    and a script's ``start "" /wait`` come before it)."""
    m = _BINARY.search(cmd)
    if not m:
        return cmd
    rest = cmd[m.end():]
    # The binary's own closing quote, when the path was quoted.
    return rest[1:] if rest[:1] in "\"'" else rest


def _extra_args(args: str) -> list[str]:
    extra = []
    for token in re.findall(r'"[^"]*"|\'[^\']*\'|\S+', args):
        word = _strip_quotes(token)
        if not word.startswith("-"):
            continue
        if word.split("=", 1)[0].lower() in _OWN_FLAGS:
            continue
        extra.append(word)
    return extra


def read_launch(source: str, path: Path, command: str) -> Launch:
    """What *command* says. The RPT's form is read by `log_analysis`, which
    knows a name may hold a space there; a unit's or a script's is quoted."""
    args = _after_binary(command)
    if source == SOURCE_RPT:
        lists = log_analysis.logged_mod_args(args)
        mods, server_mods = lists.get("mod_args", []), lists.get("server_mod_args", [])
    else:
        mods = log_analysis.parse_mods(args)
        server_mods = log_analysis.parse_server_mods(args)
    values = {}
    for key, flag in VALUE_FLAGS.items():
        value = _flag_value(args, flag)
        if value:
            values[key] = value
    return Launch(
        source=source,
        path=str(path),
        command=command,
        mods=mods,
        server_mods=server_mods,
        values=values,
        extra_args=_extra_args(args),
        uses_variables=bool(_VARIABLE.search(args)),
    )


def script_command(path: Path) -> str | None:
    """The line of a start script that runs the server, its continuation
    lines (``\\`` in sh, ``^`` in cmd) joined, or None."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    joined: list[str] = []
    pending = ""
    for raw in text.splitlines():
        line = raw.strip()
        if line.endswith("\\") or line.endswith("^"):
            pending += line[:-1] + " "
            continue
        joined.append(pending + line)
        pending = ""
    if pending:
        joined.append(pending)
    for line in joined:
        if line.startswith(("#", "::")) or line.lower().startswith("rem "):
            continue
        if _BINARY.search(line):
            return line
    return None


def start_scripts(server_dir: Path) -> list[Launch]:
    found = []
    try:
        entries = sorted(server_dir.iterdir())
    except OSError:
        return []
    for path in entries:
        if not path.is_file() or path.suffix.lower() not in SCRIPT_SUFFIXES:
            continue
        cmd = script_command(path)
        if cmd:
            found.append(read_launch(SOURCE_SCRIPT, path, cmd))
    return found


# --- systemd units -----------------------------------------------------------


def unit_dirs() -> list[tuple[str, Path]]:
    """``(scope, directory)`` for both scopes, whichever this install uses:
    a unit of the admin's own can be in either."""
    from app.shared.systemd_scope import SystemdScope

    return [("system", SystemdScope(False).unit_dir), ("user", SystemdScope(True).unit_dir)]


def _unit_key(path: Path, key: str) -> str | None:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for line in lines:
        line = line.strip()
        if line.startswith(f"{key}="):
            return line[len(key) + 1:].strip()
    return None


def _same_dir(value: str, server_dir: Path) -> bool:
    try:
        return Path(value).expanduser().resolve() == server_dir.resolve()
    except (OSError, RuntimeError, ValueError):
        return False


#: A start script larger than this is not read: it is not a start script.
_SCRIPT_LIMIT = 64 * 1024


def _script_runs_server(command: str, workdir: str | None, server_dir: Path) -> bool:
    """The unit starts a script in the server directory (``ExecStart=…/start.sh``)
    that runs the server. Only a file inside the server directory counts, so a
    backup script that merely runs there is not taken for the server's unit."""
    base = Path(workdir.lstrip("-")).expanduser() if workdir else None
    for word in re.findall(r"[^\s'\"]+", command):
        path = Path(word).expanduser()
        if not path.is_absolute():
            if base is None:
                continue
            path = base / path
        try:
            if not path.is_file() or path.stat().st_size > _SCRIPT_LIMIT:
                continue
            if not _same_dir(str(path.resolve().parent), server_dir):
                continue
            if _BINARY.search(path.read_text(encoding="utf-8", errors="replace")):
                return True
        except OSError:
            continue
    return False


def _runs_server_dir(command: str, workdir: str | None, server_dir: Path) -> bool:
    if not _BINARY.search(command):
        return _script_runs_server(command, workdir, server_dir)
    if workdir and _same_dir(workdir.lstrip("-"), server_dir):
        return True
    # A unit without WorkingDirectory= names the binary by its full path.
    m = re.search(r"([^\s'\"]+)/DayZServer", command)
    return bool(m) and _same_dir(m.group(1), server_dir)


def server_units(config: DayzConfig) -> list[Unit]:
    """Every unit, in either scope, whose command runs this server directory."""
    if IS_WINDOWS:
        return []
    from app.shared.systemd_scope import server_unit

    server_dir = config.server_dir_path
    own_name = server_unit(config)
    units = []
    for scope, directory in unit_dirs():
        try:
            paths = sorted(directory.glob("*.service"))
        except OSError:
            continue
        for path in paths:
            if not path.is_file():
                continue
            try:
                command = log_analysis.extract_execstart(path)
            except OSError:
                continue
            if not command or not _runs_server_dir(command, _unit_key(path, "WorkingDirectory"), server_dir):
                continue
            try:
                zsm = is_zsm_unit(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                zsm = False
            units.append(Unit(
                name=path.name, scope=scope, path=str(path), command=command,
                zsm=zsm, zsm_name=path.name == own_name,
            ))
    return units


# --- the latest RPT ----------------------------------------------------------


def _profiles_candidates(server_dir: Path, values: list[str]) -> list[Path]:
    """Where the engine may have written its logs: each ``-profiles=`` a
    command names, zsm's default, and the folders in the server directory,
    since the launch command is what is not known yet."""
    dirs: list[Path] = []
    for value in [*values, "profiles"]:
        path = Path(value).expanduser()
        dirs.append(path if path.is_absolute() else server_dir / path)
    try:
        dirs += [
            p for p in sorted(server_dir.iterdir())
            if p.is_dir() and not p.name.startswith(("@", "."))
        ]
    except OSError:
        pass
    seen, out = set(), []
    for d in dirs:
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out


def latest_rpt(server_dir: Path, profiles_values: list[str]) -> Path | None:
    logs = []
    for directory in _profiles_candidates(server_dir, profiles_values):
        logs += log_analysis.rpt_files_in(directory)
    if not logs:
        return None
    return max(logs, key=lambda f: (f.stat().st_mtime, f.name))


# --- the @ folders -----------------------------------------------------------


def _known_ids(config: DayzConfig) -> dict[str, str]:
    try:
        return modlist.workshop_ids(config)
    except modlist.ModListError:
        from app.modmgr.mod_ops import registry_mod_ids

        return registry_mod_ids()


def mod_folders(config: DayzConfig, launch: Launch | None) -> list[ModFolder]:
    server_dir = config.server_dir_path
    known = _known_ids(config)
    loaded = set(launch.mods) if launch else set()
    server_side = set(launch.server_mods) if launch else set()
    folders = []
    try:
        entries = sorted(server_dir.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return []
    for path in entries:
        if not path.name.startswith("@") or not path.is_dir():
            continue
        name = path.name
        mod = ModFolder(
            name=name,
            loaded=name in loaded or name in server_side,
            server_only=name in server_side,
            problem=modlist.name_problem(name) or "",
        )
        # A local mod often carries publishedid = 0.
        published = parse_mod_file(path / "meta.cpp").get("publishedid", "")
        if is_workshop_id(published) and published.strip("0"):
            mod.workshop_id, mod.id_source = published, ID_FROM_META
        elif is_workshop_id(known.get(name, "")):
            mod.workshop_id, mod.id_source = known[name], ID_KNOWN
        folders.append(mod)
    return folders


# --- the report --------------------------------------------------------------


# --- mod types pasted into the mission (D3) ----------------------------------


def mission_types_path(server_dir: Path, mission: str | None) -> Path | None:
    if not mission or not is_bare_name(mission):
        return None
    return server_dir / "mpmissions" / mission / "db" / "types.xml"


def _own_types(path: Path) -> tuple[ET.ElementTree, list[ET.Element]] | None:
    """The mission file's tree and the ``<type>`` entries no mod merged."""
    try:
        tree = load_xml(path, preserve_comments=True)
    except (ET.ParseError, OSError, UnicodeDecodeError):
        return None
    own = [c for c in unowned_children(tree.getroot()) if c.tag == "type"]
    return tree, own


def _type_names(path: Path) -> set[str]:
    """Every ``<type name>`` in a mod file, lower-cased (DayZ resolves class
    names in any case). A rootless fragment is read too: the merge links one
    as types."""
    try:
        text = strip_declaration(path.read_text(encoding="utf-8-sig"))
        root = parse_xml_text(f"<r>\n{text}\n</r>").getroot()
    except (ET.ParseError, OSError, UnicodeDecodeError, ValueError):
        return set()
    return {(t.get("name") or "").lower() for t in root.iter("type")} - {""}


def pasted_types(server_dir: Path, mission: str | None, mods: list[str]) -> list[PastedTypes]:
    """Each of *mods* whose types files name a class the mission's own
    types.xml already holds. Matched by ``<type name>``, case-insensitive."""
    path = mission_types_path(server_dir, mission)
    found = _own_types(path) if path and path.is_file() and mods else None
    if not found:
        return []
    own = {(t.get("name") or "").lower(): t.get("name") for t in found[1]}
    own.pop("", None)
    if not own:
        return []

    by_mod: dict[str, PastedTypes] = {}
    for mod, xml_file in iter_mod_xml_files(
        server_dir, EXCLUDED_MOD_KEYWORDS, [],
        mods_only=set(mods), terrain=terrain_from_mission(mission),
    ):
        bucket = ce_bucket(xml_file)
        if bucket is None or bucket[0] != KIND_TYPES:
            continue
        hits = {own[n] for n in _type_names(xml_file) if n in own}
        if not hits:
            continue
        entry = by_mod.setdefault(mod, PastedTypes(mod, [], []))
        entry.files.append(normalize_source(str(xml_file.relative_to(server_dir))))
        entry.classes = sorted(set(entry.classes) | hits, key=str.lower)
    # A class the mod redefines is pasted only when the mission holds a copy
    # beyond its own entry (`_pasted_copies`); stock's alone is not a paste.
    for entry in by_mod.values():
        gone, kept = _pasted_copies(
            found[1], {c.lower() for c in entry.classes}, _mod_entries(server_dir, entry.files))
        names = {(t.get("name") or "").lower() for t in gone}
        entry.classes = [c for c in entry.classes if c.lower() in names]
        entry.changed = kept
    return [by_mod[m] for m in mods if m in by_mod and (by_mod[m].classes or by_mod[m].changed)]


def keep_pasted(config: DayzConfig, entry: PastedTypes) -> list[str]:
    """"Already in my mission, do not add": turn the mod's overlapping types
    files off in the exclusion list. The mission is not touched."""
    for source in entry.files:
        set_source_state(source, "excluded", manual_dir=config.paths.custom_xml_dir)
    return [
        f"{entry.mod}: {len(entry.files)} types file(s) turned off; the "
        f"{len(entry.classes)} class(es) stay as your mission has them."
    ]


def _shape(element: ET.Element) -> tuple:
    """An entry as a value: tag, attributes, text and children, without the
    whitespace between them, and the name in any case."""
    attrib = {k: (v.lower() if k == "name" else v) for k, v in element.attrib.items()}
    return (
        element.tag, tuple(sorted(attrib.items())), (element.text or "").strip(),
        tuple(_shape(c) for c in element if isinstance(c.tag, str)),
    )


def _mod_entries(server_dir: Path, files: list[str]) -> dict[str, set[tuple]]:
    """The mod's own ``<type>`` entries by lower-cased name."""
    entries: dict[str, set[tuple]] = {}
    for rel in files:
        try:
            text = strip_declaration((server_dir / rel).read_text(encoding="utf-8-sig"))
            root = parse_xml_text(f"<r>\n{text}\n</r>").getroot()
        except (ET.ParseError, OSError, UnicodeDecodeError, ValueError):
            continue
        for t in root.iter("type"):
            entries.setdefault((t.get("name") or "").lower(), set()).add(_shape(t))
    return entries


def _pasted_copies(own: list[ET.Element], wanted: set[str], mods: dict[str, set[tuple]]
                   ) -> tuple[list[ET.Element], list[str]]:
    """Which of the mission's entries for *wanted* are the pasted copies.

    A mod may redefine one of Bohemia's classes (RadZone's yellow NBC gear),
    so a name alone does not say the entry was pasted: removing by name took
    the stock entries out with the pasted ones (plans/78 step 9). An entry
    identical to the mod's is pasted, and so is every copy after the first of
    a class the mission lists twice (a paste goes after what was there). A
    single entry that differs from the mod's may be stock or an edited paste,
    so it stays and is named."""
    by_name: dict[str, list[ET.Element]] = {}
    for t in own:
        name = (t.get("name") or "").lower()
        if name in wanted:
            by_name.setdefault(name, []).append(t)
    gone: list[ET.Element] = []
    kept: list[str] = []
    for name, found in by_name.items():
        same = [t for t in found if _shape(t) in mods.get(name, set())]
        if same:
            # Keep one entry when every copy is the mod's: the class must not vanish
            # from a mission that listed it twice (stock + paste of the same text).
            gone += same if len(same) < len(found) or len(found) == 1 else same[1:]
        elif len(found) > 1:
            gone += found[1:]
        else:
            kept.append(found[0].get("name") or name)
    return gone, sorted(kept, key=str.lower)


def remove_pasted(config: DayzConfig, mission: str | None, entry: PastedTypes) -> list[str]:
    """"Remove my pasted entries and let zsm manage them": take the pasted
    copies of the mod's classes out of the mission's own types.xml
    (`_pasted_copies` decides which). `save_xml` keeps the file as it was in
    Analysis -> Change history first. The mod's files are turned back on, and its
    entries arrive with the next mod XML apply."""
    server_dir = config.server_dir_path
    path = mission_types_path(server_dir, mission)
    found = _own_types(path) if path and path.is_file() else None
    if not found:
        raise AdoptError(f"The mission's db/types.xml could not be read: {path}")
    tree, own = found
    wanted = {c.lower() for c in entry.classes + entry.changed}
    gone, kept = _pasted_copies(own, wanted, _mod_entries(server_dir, entry.files))
    root = tree.getroot()
    for element in gone:
        root.remove(element)
    if gone:
        save_xml(tree, path)
    for source in entry.files:
        set_source_state(source, "", manual_dir=config.paths.custom_xml_dir)
    from app.shared.systemd_scope import zsm_command

    lines = [
        f"{entry.mod}: removed {len(gone)} entr{'y' if len(gone) == 1 else 'ies'} from "
        f"{path.name}; the version before is in Analysis -> Change history.",
    ]
    if kept:
        lines.append(
            f"Left in {path.name}, because each differs from {entry.mod}'s own entry and may be "
            f"DayZ's or your edited copy: {', '.join(kept)}. Remove the ones you pasted by hand."
        )
    lines.append(f"Apply the mod XML to add the mod's own: {zsm_command(config, 'run xml-only')}")
    return lines


def scan(config: DayzConfig) -> Report:
    """What is on the server in ``config.server_dir``. Reads only."""
    server_dir = config.server_dir_path
    units = server_units(config)
    scripts = start_scripts(server_dir)
    # Zedstead's own unit is never the server's launch command: it is rendered
    # from Zedstead's config, so reading it back is circular. A mod change had
    # installed one on an adopted server, and Adopt then took its empty
    # -mod= and listed every mod as not loaded.
    others = [read_launch(SOURCE_UNIT, Path(u.path), u.command) for u in units if not u.zsm] + scripts
    rpt = latest_rpt(server_dir, [l.values["profiles"] for l in others if "profiles" in l.values])
    launches = []
    if rpt is not None:
        cmd = log_analysis.logged_command_line(rpt)
        if cmd:
            launches.append(read_launch(SOURCE_RPT, rpt, cmd))
    launches += others
    launch = launches[0] if launches else None

    mods = mod_folders(config, launch)
    on_disk = {m.name for m in mods}
    missing = [n for n in (launch.mods + launch.server_mods if launch else []) if n not in on_disk]

    cfg = (launch.values.get("config") if launch else None) or config.config_path
    cfg_path = Path(cfg).expanduser()
    if not cfg_path.is_absolute():
        cfg_path = server_dir / cfg_path
    mission = read_mission_template(cfg_path)
    return Report(
        server_dir=str(server_dir),
        launch=launch,
        launches=launches,
        mods=mods,
        missing=missing,
        units=units,
        mission=mission,
        rpt=str(rpt) if rpt else None,
        pasted=pasted_types(server_dir, mission, [m.name for m in mods]),
    )


# --- applying it -------------------------------------------------------------


class AdoptError(ValueError):
    """The report cannot be applied. The message says why and what to do."""


def _launch_settings(config: DayzConfig, launch: Launch) -> tuple[dict, list[str]]:
    """The config fields *launch* sets, and a line for each value left out
    because its setter's own check refuses it (`launch_field_problem`)."""
    from app.server.service_control import launch_field_problem

    values = launch.values
    fields = {
        "config_path": values.get("config"),
        "profiles_path": values.get("profiles"),
        "battleye_path": values.get("battleye"),
    }
    args = list(launch.extra_args)
    if values.get("cpu_count"):
        args.append(f"-cpuCount={values['cpu_count']}")
    fields["server_args"] = " ".join(args)

    updates: dict = {}
    skipped: list[str] = []
    for key, value in fields.items():
        if value is None:
            continue
        problem = launch_field_problem(config, key, value)
        if problem:
            skipped.append(f"{key} was left as it is: {problem}")
        else:
            updates[key] = value
    port = values.get("port")
    if port is not None:
        if port.isascii() and port.isdigit() and 1 <= int(port) <= 65535:
            updates["server_port"] = int(port)
        else:
            skipped.append(f"server_port was left as it is: -port={port} is not a port number.")
    return updates, skipped


def _mod_rows(report: Report) -> tuple[list[modlist.Mod], list[str]]:
    """The mod list in launch order (``-mod=``, then ``-serverMod=``), then the
    folders the command does not load, disabled; and a line for each folder
    left out."""
    launch = report.launch
    folders = {m.name: m for m in report.mods}
    lines: list[str] = []
    rows: list[modlist.Mod] = []
    order = list(dict.fromkeys([*launch.mods, *launch.server_mods]))
    order += [m.name for m in report.mods if m.name not in order]
    for name in order:
        folder = folders.get(name)
        if folder is None:
            lines.append(f"{name} is in the launch command and has no folder in the server directory; not listed.")
            continue
        if folder.problem:
            lines.append(f"{name} was not listed: {folder.problem}")
            continue
        rows.append(modlist.Mod(
            name=name,
            id=folder.workshop_id,
            enabled=folder.loaded,
            server_only=folder.server_only,
            local=folder.local,
        ))
    return rows, lines


def apply(config: DayzConfig, report: Report) -> list[str]:
    """Write the mod list and the launch fields from *report*. Returns what it
    did, a markup-free line each. The CLI's path; the web runs `apply_to`
    inside `WebState.mutate_config`, the one place it may save the config."""
    write, lines = apply_to(config, report)
    write()
    config.save()
    return lines


def apply_to(config: DayzConfig, report: Report) -> tuple[Callable[[], None], list[str]]:
    """Set the launch fields from *report* on *config*, unsaved, and return
    the mod list's write as a function, with the lines saying what it does.
    Nothing is written until that function runs, so a caller that refuses the
    change first (the web's path lock) writes nothing.

    The list keeps the launch command's order, with manual load order on, so
    the next launch command loads the mods in the order the server ran with.

    A first-run step: refused when the mod list already holds a mod, since the
    list is then zsm's and adopting again would overwrite choices made since.
    Also refused without a launch command, or with one that reads a variable,
    because then which folders the server loads is not known.
    """
    if modlist.installed(config):
        raise AdoptError(
            "The mod list already holds mods, so this server is Zedstead's already; "
            "adopting writes the list only on a first run."
        )
    launch = report.launch
    if launch is None:
        raise AdoptError(
            "No launch command was found (no server log, unit or start script), so "
            "which mods the server loads is not known. Start the server once the way "
            "you always do, then adopt it."
        )
    if launch.uses_variables:
        raise AdoptError(
            f"{launch.path} builds the launch command from variables, so which mods "
            "it loads is not in the file. Start the server once the way you always do: "
            "its log then records the command, and adopting reads that."
        )

    rows, lines = _mod_rows(report)
    updates, skipped = _launch_settings(config, launch)

    def take(ml: modlist.ModList) -> None:
        if ml.mods:
            raise AdoptError("The mod list was filled while adopting; nothing was written.")
        ml.mods = rows
        # The order the server ran with is the order it keeps: manual load
        # order, seeded from it, so the next -mod= is the one it had (step 3).
        ml.manual = True
        ml.pinned = [r.name for r in rows]

    def write() -> None:
        # Before the list carries them: remember_id skips a name the list knows.
        for row in rows:
            if row.id and not row.local:
                modlist.remember_id(config, row.name, row.id)
        modlist.update(config, take)

    for key, value in updates.items():
        setattr(config, key, value)

    loaded = sum(1 for r in rows if r.enabled)
    lines.insert(0, f"Mod list: {len(rows)} mod(s), {loaded} loaded, from the {launch.source_label} ({launch.path}).")
    lines.insert(1, "Load order: manual, in the order the server was started with.")
    local = [r.name for r in rows if r.local]
    if local:
        lines.append(f"Kept as your own (no Workshop ID, never downloaded): {', '.join(local)}.")
    off = [r.name for r in rows if not r.enabled]
    if off:
        lines.append(f"Not loaded by the launch command, listed as disabled: {', '.join(off)}.")
    for key, value in updates.items():
        lines.append(f"{key} = {value if value != '' else '(none)'}")
    lines += skipped
    note = handover(config, report)
    if note:
        lines.append(note)
    return write, lines


def handover(config: DayzConfig, report: Report) -> str:
    """What still starts the server after adopting, when it is not zsm's own
    unit. Until zsm's unit runs it, the mod list zsm keeps does not reach the
    launch command: the unit guard leaves a unit zsm did not write in place,
    and an update says so in one log line only. Empty when zsm's unit is the
    one, and on Windows (no units)."""
    if IS_WINDOWS or any(u.zsm for u in report.units):
        return ""
    from app.shared.systemd_scope import zsm_command

    install = zsm_command(config, "service install")
    same = [u.name for u in report.units if u.zsm_name]
    other = [u.name for u in offered_units(report)]
    if same:
        return (
            f"Your own unit {same[0]} still starts the server, so what you change in Zedstead's mod list "
            f"does not reach it until Zedstead's unit replaces it: {install} --replace "
            "(copy yours first if you want to keep it)."
        )
    if other:
        return (
            f"{', '.join(other)} still start(s) the server, so what you change in Zedstead's mod list does "
            f"not reach it until Zedstead's unit runs it: {install}, then stop yours starting at boot "
            "(Adopt page, or zsm adopt --disable-unit NAME)."
        )
    return (
        "Nothing Zedstead wrote starts the server yet, so what you change in Zedstead's mod list reaches it "
        f"only through Zedstead's unit: {install}."
    )


# --- what is wrong, and what zsm can fix (D6, step 6) -------------------------

#: Doctor rows that read what Apply has not written yet, so before adopting
#: they would report on an empty mod list or on zsm's own unit rather than
#: on this server. Skipped with the reason; Doctor checks them after Apply.
UNADOPTED_ROWS = {
    "Mod list": "reads Zedstead's mod list, which Apply writes; the report's mod table is this server's",
    "Mod load order": "reads Zedstead's mod list, which Apply writes",
    "Mod dependencies": "reads Zedstead's mod list, which Apply writes; Doctor checks it after Apply",
    "requiredAddons": "reads Zedstead's mod list, which Apply writes; Doctor checks it after Apply",
    "Bikey signatures": "reads Zedstead's mod list, which Apply writes; the Mod keys row checks the folders instead",
    "Service": "is about Zedstead's own unit; the report lists the units that run this server",
    "Crash recovery": "is about Zedstead's own unit; the report lists the units that run this server",
}

FIX_COPY_KEYS = "copy_keys"
FIX_PASTED_TYPES = "pasted_types"


def preview_config(config: DayzConfig, report: Report) -> DayzConfig:
    """*config* with the launch fields Apply would take, in memory only: the
    checks then read the server's own profiles folder, cfg and port."""
    if report.launch is None:
        return config
    updates, _skipped = _launch_settings(config, report.launch)
    return replace(config, **updates)


def _bikeys(directory: Path) -> list[Path]:
    """A mod's keys, found as `fileops.copy_bikey_files` finds them."""
    return sorted(
        p for p in directory.rglob("*.bikey") if p.is_file() and not p.is_symlink()
    )


def missing_keys(server_dir: Path, report: Report) -> tuple[dict[str, list[Path]], list[str]]:
    """For each loaded mod, the keys in its folder that ``keys/`` lacks; and
    the loaded mods with no key in their folder at all."""
    keys_dir = server_dir / "keys"
    have = {os.path.normcase(p.name) for p in keys_dir.glob("*.bikey")} if keys_dir.is_dir() else set()
    missing: dict[str, list[Path]] = {}
    keyless: list[str] = []
    for mod in report.mods:
        # A -serverMod= is loaded by the server alone; no client checks its key.
        if not mod.loaded or mod.problem or mod.server_only:
            continue
        keys = [k for k in _bikeys(server_dir / mod.name) if k.name.lower() != "dayz.bikey"]
        if not keys:
            keyless.append(mod.name)
            continue
        lacking = [k for k in keys if os.path.normcase(k.name) not in have]
        if lacking:
            missing[mod.name] = lacking
    return missing, keyless


def _keys_row(server_dir: Path, report: Report) -> dict:
    missing, keyless = missing_keys(server_dir, report)
    parts = [f"{mod}: {', '.join(k.name for k in keys)} not in keys/" for mod, keys in missing.items()]
    parts += [f"{mod} has no .bikey in its folder" for mod in keyless]
    if not parts:
        return {"label": "Mod keys", "ok": True, "detail": "Every loaded mod's key is in keys/."}
    row = {"label": "Mod keys", "ok": False, "detail": "; ".join(parts) + "."}
    if missing:
        row["fix"] = FIX_COPY_KEYS
    return row


def _unloaded_row(report: Report) -> dict:
    unloaded = [m.name for m in report.mods if not m.loaded and not m.problem]
    if not unloaded:
        return {"label": "Mod folders not loaded", "ok": True, "detail": "The server loads every mod folder."}
    # Off is not wrong (app/web/CLAUDE.md: a row for something merely off passes).
    return {
        "label": "Mod folders not loaded",
        "ok": True,
        "detail": (
            f"Off: {', '.join(unloaded)} on disk, not in the launch command. "
            "Apply lists them disabled; turn one on on the Mods page to load it."
        ),
    }


def _pasted_row(report: Report) -> dict:
    if not report.pasted:
        return {"label": "Mod types in the mission", "ok": True,
                "detail": "No listed mod's types are pasted into the mission's types.xml."}
    parts = [f"{p.mod} ({len(p.classes)} class(es))" for p in report.pasted if p.classes]
    changed = [f"{p.mod}: {', '.join(p.changed)}" for p in report.pasted if p.changed]
    detail = ""
    if parts:
        detail = (
            f"Already pasted into db/types.xml: {', '.join(parts)}. Merging the mod as well "
            "defines each class twice. Choose per mod: keep yours and turn the mod's file off, "
            "or remove yours and let Zedstead add the mod's."
        )
    if changed:
        detail += (" " if detail else "") + (
            f"In db/types.xml once and different from the mod's entry ({'; '.join(changed)}): "
            "DayZ's own entry for a class the mod redefines, or a paste you changed. "
            "Removing never takes these; delete the ones you pasted by hand, or keep yours."
        )
    return {"label": "Mod types in the mission", "ok": False, "detail": detail, "fix": FIX_PASTED_TYPES}


def findings(config: DayzConfig, report: Report) -> dict:
    """Doctor's checks and the latest log's analysis, run against the server as
    it is, before anything is adopted, plus adopting's own three checks.
    Reads only; nothing is saved (`preview_config` lives in memory)."""
    from app.commands.doctor import _run_all_checks

    preview = preview_config(config, report)
    rows, _scan = _run_all_checks(preview)
    checks: list[dict] = []
    skipped: list[dict] = []
    for label, ok, detail in rows:
        if label in UNADOPTED_ROWS:
            skipped.append({"label": label, "reason": UNADOPTED_ROWS[label]})
        else:
            checks.append({"label": label, "ok": ok, "detail": detail})
    server_dir = preview.server_dir_path
    adopt_rows = [_keys_row(server_dir, report), _unloaded_row(report), _pasted_row(report)]
    if server_dir.is_dir():
        checks += adopt_rows
    else:
        # All three read the server dir: with none they found nothing to
        # object to and showed green. The Server
        # directory row above already says it is missing.
        skipped += [{"label": row["label"], "reason": "no server directory"} for row in adopt_rows]
    note = handover(config, report)
    if note:
        # Advice, not a fault: the server runs as it always did (app/web/CLAUDE.md, "off passes").
        checks.append({"label": "What starts the server", "ok": True, "detail": note})

    log = None
    if report.rpt:
        log = log_analysis.build_log_report(preview, Path(report.rpt))
    return {"checks": checks, "skipped": skipped, "log": log}


def copy_missing_keys(config: DayzConfig, report: Report) -> list[str]:
    """The fix for the Mod keys row, run on the admin's click: copy each loaded
    mod's keys that ``keys/`` lacks. Adds files only; a key already there is
    never replaced."""
    server_dir = config.server_dir_path
    missing, _keyless = missing_keys(server_dir, report)
    keys_dir = server_dir / "keys"
    lines: list[str] = []
    for mod, keys in missing.items():
        keys_dir.mkdir(exist_ok=True)
        copied = []
        for key in keys:
            target = keys_dir / key.name
            if target.exists() or target.is_symlink():
                continue  # never replaced, and never written through a link
            shutil.copy2(key, target)
            copied.append(key.name)
        if copied:
            lines.append(f"{mod}: copied {', '.join(copied)} into keys/.")
    return lines or ["Every loaded mod's key was already in keys/."]


def disable_unit(report: Report, name: str, *, can_prompt: bool = True) -> list[str]:
    """D2's offer, run on the admin's click: a unit of the admin's own that runs
    this server under another name than zsm's is disabled at boot, so the two
    do not both start the server. Its file is left as it is.

    *can_prompt* False (a web request, no terminal) refuses a system unit when
    sudo would ask for a password, naming the command, instead of hanging."""
    import subprocess

    from app.shared.systemd_scope import SystemdScope, sudo_available_noninteractive

    unit = next((u for u in offered_units(report) if u.name == name), None)
    if unit is None:
        raise AdoptError(f"{name} is not a unit of your own that runs this server under another name.")
    scope = SystemdScope(user_mode=unit.scope == "user")
    if not scope.user_mode and not can_prompt and not sudo_available_noninteractive():
        raise AdoptError(
            f"Disabling {unit.name} needs sudo, which would ask for a password. "
            f"Run on the server: sudo systemctl disable {unit.name}"
        )
    try:
        subprocess.run(scope.systemctl_argv("disable", unit.name), check=True, env=scope.subprocess_env())
    except (OSError, subprocess.CalledProcessError) as exc:
        raise AdoptError(f"Could not disable {unit.name}: {exc}") from exc
    return [
        f"{unit.name} ({unit.scope}) no longer starts at boot; its file is left as it is. "
        "It keeps running until you stop it."
    ]


def offered_units(report: Report) -> list[Unit]:
    """The units D2 offers to disable: the admin's own, under another name."""
    return [u for u in report.units if not u.zsm and not u.zsm_name]
