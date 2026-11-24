"""Form adapters for server-root config: serverDZ.cfg, BattlEye, cfggameplay.json."""

from __future__ import annotations

import copy
import json
import math
import os
import re
from decimal import Decimal
from typing import Any, Callable

from app.parsers.battleye_cfg import (
    check_beserver_cfg,
    check_beserver_values,
    live_beserver_cfg,
    read_beserver_cfg,
    refuse_while_running,
    write_beserver_cfg,
)
from app.parsers.cfggameplay import (
    CHOICES as _GP_CHOICES,
    CHOICE_HELP as _GP_CHOICE_HELP,
    OPEN_CHOICES as _GP_OPEN_CHOICES,
    FIELD_DESCRIPTIONS as _GP_DESC,
    FLOAT_LIST_SIZES as _GP_LIST_SIZES,
    RANGES as _GP_RANGES,
    UNITS as _GP_UNITS,
    get_cfggameplay_path,
    read_cfggameplay,
    same_value,
    scalar_value as _gp_scalar,
    value_kind as _gp_kind,
    write_cfggameplay,
)
from app.parsers.servercfg import (
    DESCRIPTION_LIMIT,
    FIELD_DESCRIPTIONS as _SERVERDZ_DESC,
    FIELD_UNITS as _SERVERDZ_UNITS,
    SYSTEM_TIME,
    cfg_value_problem,
    check_server_cfg,
    is_server_time,
    quoted_like_stock,
    read_cfg_list,
    read_cfg_value,
    read_cfg_values,
    REPLACED_CFG_KEYS,
    REPLACED_IN,
    remove_cfg_key,
    replaced_keys,
    write_cfg_list,
    write_cfg_value,
)
from app.shared.config import DayzConfig
from app.shared.container import env_lock_note, env_lock_problem, env_locked_fields
from app.shared.config_versions import snapshot
from app.web.editors import EditorSpec, register
from app.web.editors._help import field_help, label_without_unit, unit_attr
from app.web.editors._mod_json import coerce_list, list_item_type
from app.web.editors._paths import mission_dir
from app.web.editors._raw import RawTarget, raw_targets_from, refuse_if

# The two files behind the Server and BattlEye editors' Raw tabs and Upload.
# serverDZ.cfg is a raw target of both, so its check is attached to the file
# here, once (plans/51 C).
_SERVER_CFG_RAW = (lambda c: c.server_cfg_path, "cfg", refuse_if(check_server_cfg))
from app.xml.dayzsetting_xml import FIELD_DESCRIPTIONS as _CORE_DESC


def _BESERVER_CFG_RAW(config: DayzConfig) -> RawTarget:
    """beserver_x64.cfg: shown from the copy BattlEye is using, and a Raw save
    or Upload is refused while the server runs (``live_beserver_cfg``)."""
    path = config.beserver_cfg_path
    return RawTarget(
        name=path.name,
        path=path,
        language="cfg",
        validate=refuse_if(check_beserver_cfg),
        guard=lambda: refuse_while_running(config),
        read_path=live_beserver_cfg(config),
        adopt=_adopt_beserver_text,
    )


def _adopt_beserver_text(config: DayzConfig, text: str) -> None:
    """A Raw save or Upload of beserver_x64.cfg sets zsm's RCon pair as the
    BattlEye form does, or zsm dials with the password the file replaced."""
    from app.parsers.battleye_cfg import parse_beserver_text
    from app.rcon.rcon_ops import adopt_beserver_rcon

    adopt_beserver_rcon(config, parse_beserver_text(text))

# Web-authored help for fields the FIELD_DESCRIPTIONS maps don't describe (RCon
# creds, launch flags).
_BATTLEYE_HELP: dict[str, str] = {
    "BattlEye": "Enable BattlEye anti-cheat. Turn off only for local testing.",
    "RConPassword": "Password for the RCon (BattlEye remote console) connection Zedstead uses "
    "for the scheduler, chat logging, and admin actions. BattlEye RCon is unencrypted "
    "UDP, so anyone who can reach the RCon port can try this password: make it long "
    "and random.",
    "RConPort": "UDP port BattlEye listens on for RCon. Must match Zedstead's RCon settings.",
    # Measured on a running server (app/ops/CLAUDE.md, "RCon exposure"):
    # with RConIP unset the server holds 0.0.0.0:<port>, and with
    # RConIP 127.0.0.1 it holds 127.0.0.1:<port>. Don't call loopback the default.
    "RConIP": "Address BattlEye binds RCon to. Empty means every interface (0.0.0.0), "
    "so RCon is reachable from wherever the server's UDP ports are, and the default "
    "RCon port 2305 is also one of the ports players need open. Set 127.0.0.1 to keep "
    "RCon on this machine; Zedstead's own RCon client connects there, and a remote RCon "
    "tool then needs a VPN or an SSH tunnel.",
    # dayz-docs/wiki/pages/server-configuration.wiki: "Enables/Disables RCon
    # functions (kick/ban/connection restrictions)". It is not an address filter.
    "RestrictRCon": "Bohemia: enables or disables RCon functions (kick, ban, connection "
    "restrictions). It does not change who can reach RCon; RCon IP does that.",
}
_RUNCOMMAND_HELP: dict[str, str] = {
    "config_path": "Path passed as -config=PATH (the serverDZ.cfg the server loads).",
    "profiles_path": "Path passed as -profiles=PATH (logs, bans, and profile data).",
    "battleye_path": "Path passed as -BEpath=PATH (BattlEye working directory).",
    "server_port": "Game port passed as -port=N. Clients connect here (default 2302).",
    "adminLog": "-adminLog: write admin actions to the .ADM log.",
    "netLog": "-netLog: log network traffic (verbose; for debugging).",
    "doLogs": "-doLogs: enable the FPS/memory/player logging driven by serverDZ.cfg.",
    "freezeCheck": "-freezeCheck: crash the server if the main loop freezes (aids restarts).",
    "cpuCount": "-cpuCount=N: logical CPU cores used for parallel tasks, at most the cores this machine has. Empty uses the engine default.",
    "limitFPS": "-limitFPS=N: cap server FPS to save CPU, 1 to 200. Empty means no cap.",
    "filePatching": "-filePatching: allow loading unpacked files (needed by some mods; a security tradeoff).",
    "serverMod": "-serverMod=A;B: server-side-only mods (not sent to clients).",
    "storage": "-storage=PATH: override the persistence storage location.",
    "mission": "-mission=NAME: override the mission folder (conflicts with Change Map).",
    "_extras": "Any launch flags Zedstead does not recognize, kept verbatim. Not -port, -config, -profiles, -BEpath, -mod or -serverMod: Zedstead sets those from their own fields.",
}

# ---------------------------------------------------------------------------
# serverDZ.cfg
# ---------------------------------------------------------------------------


def _all_cfg_fields() -> list[tuple[str, str, str]]:
    from app.commands.config_cmd import _CFG_CATEGORIES

    return [item for items in _CFG_CATEGORIES.values() for item in items]


def _coerce(kind: str, raw: str | None) -> Any:
    if raw is None:
        return None
    if kind == "bool":
        # Any case: the engine reads FALSE as false too.
        return raw.strip().lower() not in ("0", "", "false")
    if kind == "int":
        try:
            return int(raw)
        except ValueError:
            return raw
    if kind == "float":
        try:
            return float(raw)
        except ValueError:
            return raw
    return raw


def _format_value(kind: str, value: Any) -> str:
    if kind == "bool":
        return "1" if value else "0"
    return str(value)


# Switches Bohemia's own serverDZ.cfg writes as true/false
# (server-configuration.wiki:22-23 and :89). Absent from a file, they are
# written that way; any switch already in the file keeps the file's style.
WORD_BOOL_KEYS = frozenset({"disableBanlist", "disablePrioritylist", "disableMultiAccountMitigation"})


def _bool_in_style(key: str, text: str, current: str | None) -> str:
    """``text`` ("1"/"0") in the style the file already uses for *key*:
    true/false in the file's own case, or 0/1."""
    on = text == "1"
    word = "true" if on else "false"
    old = (current or "").strip()
    if old.lower() in ("true", "false"):
        if old.isupper():
            return word.upper()
        if old[0].isupper():
            return word.capitalize()
        return word
    if not old and key in WORD_BOOL_KEYS:
        return word
    return text


def _num(value: float) -> str:
    # A whole number in full: `:g` writes 2147483647 as 2.14748e+09.
    return str(int(value)) if value == int(value) else f"{value:g}"


def _range_problem(label: str, number: float, low: float | None, high: float | None) -> str | None:
    if low is not None and high is not None and not low <= number <= high:
        if low == high:
            return f"{label} must be {_num(low)}"
        return f"{label} must be between {_num(low)} and {_num(high)}"
    if low is not None and number < low:
        return f"{label} must be at least {_num(low)}"
    if high is not None and number > high:
        return f"{label} must be at most {_num(high)}"
    return None


def _text_problem(key: str, text: str) -> str | None:
    """What the wiki says is wrong with a text field's value, or None."""
    from app.commands.config_cmd import cfg_choices

    choices = cfg_choices(key)
    if choices is not None and text not in choices:
        return "must be " + " or ".join(choices)
    if key == "serverTime" and not is_server_time(text):
        # server-configuration.wiki:38-39.
        return f"must be {SYSTEM_TIME} or a date and time written YYYY/MM/DD/HH/MM, e.g. 2015/4/8/17/23"
    if key == "description" and len(text) > DESCRIPTION_LIMIT:
        # server-configuration.wiki:17 "max length 255 characters".
        return f"is {len(text)} characters long; DayZ takes at most {DESCRIPTION_LIMIT}"
    return None


def _checked_value(label: str, key: str, kind: str, value: Any) -> str | list[str] | None:
    """The text to write for one serverDZ.cfg field, or a ValueError naming it.
    A ``list`` field (motd[]) gives its non-blank lines. A number field left
    empty gives None: no key needs to be in the file, so the save takes the
    key out and the server uses its default.

    A number is refused when it is not one, when an ``int`` field gets a
    fraction, and when it is outside ``cfg_range`` (F30: ``maxPlayers = -5``
    was written as is). The server reads the file as it finds it, so a value
    it cannot use surfaces only when it boots.
    """
    from app.commands.config_cmd import cfg_range

    if kind == "list":
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ValueError(f"{label} needs a list of lines")
        items = [item for item in value if item.strip()]
        for item in items:
            problem = cfg_value_problem(item, quoted=True)
            if problem:
                raise ValueError(f"{label}: a line {problem}")
        return items
    if kind not in ("int", "float"):
        # The writer refuses the same thing (plans/68 LB-4); asked here too, so
        # the message names the field and a refused body writes nothing. A
        # text field is written quoted where Bohemia's file quotes it
        # (plans/68 A-3), so it is checked as a quoted value.
        text = _format_value(kind, value)
        problem = cfg_value_problem(text, quoted=quoted_like_stock(key)) or _text_problem(key, text)
        if problem:
            raise ValueError(f"{label} {problem}")
        return text
    text = "" if value is None or isinstance(value, bool) else str(value).strip()
    if not text and not isinstance(value, bool):
        return None
    try:
        number = float(text)
    except ValueError:
        number = math.nan
    if not math.isfinite(number):
        raise ValueError(f"{label} needs a number")
    if kind == "int":
        if number != int(number):
            raise ValueError(f"{label} must be a whole number")
        number = int(number)
    problem = _range_problem(label, number, *cfg_range(key))
    if problem:
        raise ValueError(problem)
    if kind == "int":
        return str(number)
    # As typed, but never in exponent form.
    return text if "e" not in text.lower() else format(Decimal(text), "f")


# A server-file key the container bootstrap writes from config at every start
# (`container.sync_server_cfg` / `sync_battleye_cfg`), and the config field.
_ENV_CFG_KEYS = {
    "hostname": "server_name",
    "steamQueryPort": "steam_query_port",
    "RConPassword": "rcon_password",
    "RConPort": "rcon_port",
}


def _env_cfg_keys(config: DayzConfig) -> dict[str, str]:
    """``{file key: env var}`` for the keys the container's environment sets."""
    env_set = env_locked_fields(config)
    keys = {k: env_set[f] for k, f in _ENV_CFG_KEYS.items() if f in env_set}
    if not config.steam_query_port:
        # 0 = unmanaged: sync_server_cfg leaves steamQueryPort alone.
        keys.pop("steamQueryPort", None)
    return keys


def env_locked_notes(config: DayzConfig) -> dict[str, str]:
    """``{file key: why it is locked}`` for the serverDZ keys the container's
    environment sets. Quick setup leaves these out of its card."""
    return {key: env_lock_note(var) for key, var in _env_cfg_keys(config).items()}


def _mark_env_locked(fields: list[dict], env_keys: dict[str, str]) -> None:
    for field in fields:
        var = env_keys.get(field["key"])
        if var:
            field["locked"] = True
            field["locked_note"] = env_lock_note(var)


def _refuse_env_changes(env_keys: dict[str, str], labels: dict[str, str], changed: list[str]) -> None:
    problem = env_lock_problem(env_keys, changed, labels)
    if problem:
        raise ValueError(problem)


def _serverdz_exists(config: DayzConfig) -> bool:
    return config.server_cfg_path.is_file()


# "[dim]default: 20[/dim]" at the end of a serverDZ.cfg key's help when Bohemia
# names the default (the wiki's line, or a script's ServerConfigGetInt, 0 when
# missing), "[dim]example: 60[/dim]" when it is only the value in the wiki's
# example file.
_DEFAULT_RE = re.compile(r"\b(default|example):\s*([^\[\n]+)")


def _serverdz_default(key: str, kind: str) -> tuple[Any, bool]:
    """What the server uses for *key* when serverDZ.cfg leaves it out, as the
    field's own type, and whether that is only Bohemia's example value. The
    value is None when the help names none ("not set"). Shown by the form while
    the key is absent, and written only when the admin changes the field."""
    match = _DEFAULT_RE.search(_SERVERDZ_DESC.get(key, ""))
    text = match.group(2).strip() if match else ""
    example = bool(match) and match.group(1) == "example"
    if not text or text == "not set":
        return None, False
    value = _coerce(kind, text)
    return (None if kind in ("int", "float") and isinstance(value, str) else value), example


def _serverdz_field(key: str, label: str, kind: str, raw: str | None) -> dict:
    from app.commands.config_cmd import cfg_choice_help, cfg_choices, cfg_open_choices, cfg_range

    field = {
        "key": key,
        "label": label_without_unit(label or key, _SERVERDZ_UNITS.get(key)),
        "type": kind,
        "value": _coerce(kind, raw),
        "help": field_help(_SERVERDZ_DESC, key),
        **unit_attr(_SERVERDZ_UNITS, key),
    }
    if kind == "list":
        # A root array (motd[]): its strings, one row each.
        field["item"] = "str"
        field["value"] = raw if isinstance(raw, list) else []
    if kind == "mission":
        # Read-only: the Maps page is the only safe way to change it.
        field["readonly"] = True
        field["link"] = {"to": "/maps", "label": "Change on the Maps page"}
    if key == "instanceId":
        # Read as Zedstead's own instances, which it is not: it names the world's
        # save folder (storage_<id>). Kept editable, with the difference said.
        field["note"] = "Names the world's save folder, storage_<id>. Not a Zedstead server instance:"
        field["link"] = {"to": "/instances", "label": "those are on the Instances page"}
    if kind in ("int", "float"):
        low, high = cfg_range(key)
        if low is not None:
            field["min"] = low
        if high is not None:
            field["max"] = high
    if key == "networkObjectBatchBandwidthLimit":
        # A 0-1 share while this switch is on (a percentage slider in the web
        # form), a value of 1 or more while it is off (servercfg's help).
        field["share_when"] = "networkObjectBatchUseDynamicMaximumBandwidth"
    choices = cfg_choices(key)
    _add_choices(field, choices, cfg_open_choices(key), cfg_choice_help(key))
    default, example = _serverdz_default(key, kind)
    if default is not None and (choices is None or str(default) in choices):
        field["default"] = default
        if example:
            # The form shows it as a placeholder, with no "Default" pill.
            field["default_is_example"] = True
    return field


def _add_choices(
    field: dict, closed: tuple[str, ...] | None, open_: tuple[str, ...] | None,
    meanings: dict[str, str] | None = None,
) -> None:
    """Give a form field its picker: *closed* values only, or *open_* known
    values with `open` set, so the picker also takes a typed value.
    *meanings* ({value: text}) becomes `choice_help`, the line under each value."""
    if closed is not None:
        field["choices"] = list(closed)
    elif open_ is not None:
        field["choices"] = list(open_)
        field["open"] = True
    if meanings and "choices" in field:
        field["choice_help"] = dict(meanings)


def _serverdz_load(config: DayzConfig) -> dict:
    from app.commands.config_cmd import _CFG_CATEGORIES
    from app.modmgr.mods import get_current_mission

    fields = _all_cfg_fields()
    values: dict = read_cfg_values(config.server_cfg_path, [key for _l, key, kind in fields if kind != "list"])
    for _label, key, kind in fields:
        if kind == "list":
            values[key] = read_cfg_list(config.server_cfg_path, key)
    # `template` lives in `class Missions { class DayZ { ... } }`, which
    # read_cfg_values (root level only) never sees, so the field was empty (F31).
    values["template"] = get_current_mission(config.server_cfg_path) or None
    env_keys = _env_cfg_keys(config)
    # An old key a newer one replaced (disable3rdPerson) is left off the form
    # once the file holds the new key, and a save of the new key removes it.
    # Without the new key it stays, with a note, for a server before 1.30.
    replaced = {old: new for new, old in REPLACED_CFG_KEYS.items()}
    hidden = {old for old, new in replaced.items() if values.get(new) is not None}
    sections = []
    for section, items in _CFG_CATEGORIES.items():
        fields = [_serverdz_field(key, label, kind, values.get(key)) for label, key, kind in items if key not in hidden]
        for field in fields:
            if field["key"] in replaced:
                # The form shows a "Before <version>" pill from replaced_in.
                version = REPLACED_IN[field["key"]]
                new_label = next(label or key for label, key, _k in _all_cfg_fields() if key == replaced[field["key"]])
                field["replaced_in"] = version
                field["note"] = f"For a server before DayZ {version}. A save of {new_label} removes this key."
        _mark_env_locked(fields, env_keys)
        sections.append({"title": section, "fields": fields})
    return {"kind": "form", "sections": sections}


def _refuse_missing_server_cfg(config: DayzConfig) -> None:
    """Refuse a save into a serverDZ.cfg that is not there.

    ``write_cfg_value`` skips a missing file without a word, so on an instance
    with no server installed the Server form answered "Saved." and wrote
    nothing.
    """
    if not config.server_cfg_path.is_file():
        raise ValueError(
            f"{config.server_cfg_path} does not exist yet, so nothing can be saved into it. "
            "Install the server first (Install server on the Dashboard)."
        )


def check_serverdz_values(values: dict) -> list[tuple[str, str | list[str] | None]]:
    """Check *values* against Bohemia's ranges and return the texts to write,
    as ``(key, text)``. A text of None is a number field left empty: the key
    is to be removed. Raises ValueError with every problem at once. It reads
    no file, so Quick setup also checks its fields before the server exists."""
    known = {key: (label or key, kind) for label, key, kind in _all_cfg_fields()}
    writes: list[tuple[str, str | list[str] | None]] = []
    problems: list[str] = []
    for key, value in values.items():
        if key not in known:
            continue
        label, kind = known[key]
        if kind == "mission":
            # Never written from here, even when a body sends it: a map switch
            # also rewrites the mission folder, and write_cfg_value targets
            # the root level, not `class Missions`.
            continue
        try:
            writes.append((key, _checked_value(label, key, kind, value)))
        except ValueError as exc:
            problems.append(str(exc))
    if problems:
        raise ValueError("; ".join(problems))
    return writes


def save_serverdz(config: DayzConfig, values: dict) -> None:
    """Write *values* into serverDZ.cfg, the Server form's save. Every field is
    checked before the first write, so a body with one bad value changes nothing.
    A number field left empty removes its key, so the server uses its default.
    A key a newer one replaced (REPLACED_CFG_KEYS) is removed when the newer
    key is written, and is not written itself in the same save."""
    _refuse_missing_server_cfg(config)
    known = {key: (label or key, kind) for label, key, kind in _all_cfg_fields()}
    writes = check_serverdz_values(values)
    old_keys = replaced_keys(k for k, text in writes if text is not None)
    writes = [(k, text) for k, text in writes if k not in old_keys]
    bools = [k for k, _text in writes if known[k][1] == "bool"]
    if bools:
        current = read_cfg_values(config.server_cfg_path, bools)
        writes = [(k, _bool_in_style(k, text, current.get(k)) if k in current else text) for k, text in writes]
    env_keys = _env_cfg_keys(config)
    if env_keys:
        current = read_cfg_values(config.server_cfg_path, list(env_keys))
        _refuse_env_changes(env_keys, {k: label for k, (label, _kind) in known.items()},
                            [k for k, text in writes if k in env_keys and text != current.get(k)])
    snapshot(config.server_cfg_path, trigger="editor")
    for key, text in [*writes, *((k, None) for k in sorted(old_keys))]:
        if text is None:
            remove_cfg_key(config.server_cfg_path, key)
        elif isinstance(text, list):
            write_cfg_list(config.server_cfg_path, key, text)
        else:
            # Quoted exactly where Bohemia's file quotes the key (quoted_like_stock).
            write_cfg_value(config.server_cfg_path, key, text)


register(
    EditorSpec(
        id="serverdz",
        title="Server",
        group="Server",
        kind="form",
        exists=_serverdz_exists,
        load=_serverdz_load,
        save=save_serverdz,
        raw_targets=raw_targets_from(_SERVER_CFG_RAW),
    )
)


# ---------------------------------------------------------------------------
# BattlEye (beserver_x64.cfg + serverDZ.cfg's BattlEye key)
# ---------------------------------------------------------------------------

_BATTLEYE_FIELDS = [
    ("Enable BattlEye", "BattlEye", "bool"),
    ("RCon password", "RConPassword", "str"),
    ("RCon port", "RConPort", "int"),
    ("RCon IP (optional)", "RConIP", "str"),
    ("Restrict RCon functions", "RestrictRCon", "bool"),
]


def _battleye_exists(config: DayzConfig) -> bool:
    return live_beserver_cfg(config).is_file() or config.server_cfg_path.is_file()


def _default_rcon_port(config: DayzConfig) -> str | None:
    """The RConPort a beserver_x64.cfg without one gets: this instance's own
    ``config.rcon_port`` (2305, or the port a new instance was given).
    ``None`` when RCon dials a remote host, whose port is not this file's."""
    from app.rcon.rcon_ops import is_remote_rcon, rcon_port_number

    if is_remote_rcon(config):
        return None
    port = rcon_port_number(config.rcon_port)
    return str(port) if port is not None else None


def _battleye_load(config: DayzConfig) -> dict:
    be_data = read_beserver_cfg(live_beserver_cfg(config))
    battleye_enabled = read_cfg_value(config.server_cfg_path, "BattlEye")
    values = {
        "BattlEye": _coerce("bool", battleye_enabled),
        "RConPassword": be_data.get("RConPassword", ""),
        # A file without RConPort shows the port the save will write (_battleye_save).
        "RConPort": _coerce("int", be_data.get("RConPort") or _default_rcon_port(config)),
        "RConIP": be_data.get("RConIP", ""),
        "RestrictRCon": _coerce("bool", be_data.get("RestrictRCon", "0")),
    }
    fields = [
        {
            "key": key,
            "label": label,
            "type": kind,
            "value": values.get(key),
            "help": field_help(_BATTLEYE_HELP, key),
            # The switch for the whole card: FormEditor draws it as an On/Off
            # button first among the card's inputs, saved by Save like any field.
            **({"widget": "header"} if key == "BattlEye" else {}),
        }
        for label, key, kind in _BATTLEYE_FIELDS
    ]
    _mark_env_locked(fields, _env_cfg_keys(config))
    # Zedstead's own rcon_host (where it dials RCon) sits beside the password and
    # port it dials with. It is config.json's, not the file's (zsm_forms.py).
    from app.web.editors.zsm_forms import settings_fields

    fields += settings_fields(config, "battleye")
    return {"kind": "form", "sections": [{"title": "BattlEye", "fields": fields}]}


def _battleye_save(config: DayzConfig, values: dict) -> Callable[[], None]:
    """Copy the RCon password and port into *config*, and return the writes
    of the two files.

    ``mutates_app_config`` runs this through ``WebState.mutate_config``, so the
    files and zsm's own ``rcon_password``/``rcon_port`` change in one save —
    outside Docker nothing else sets that pair, and without it zsm dials with
    an empty password while the server waits for the one written here. Every
    value is checked before the first write, so a refused save changes neither
    the files nor the config.

    **The files are written by the function this returns**, which
    ``mutate_config`` runs only once the path lock has let the config change
    through: a save the lock refuses writes no file and takes no snapshot.

    The form also carries Zedstead's ``rcon_host``, set here first. A save that
    changes only it writes no file, so it is never refused for the reasons
    below.

    **Refused while the server runs** (``BeserverCfgInUse``, a 409): BattlEye
    renames the cfg away then and back over whatever is there at the next
    stop, so the save would be lost (measured in 2026 Q3). The check comes
    first, before the snapshots, and raising inside ``mutate_config`` means
    config.json is not saved either.
    """
    from app.rcon.rcon_ops import adopt_beserver_rcon, rcon_port_number
    from app.web.editors.zsm_forms import apply_settings

    # rcon_host first: the adopt below is a no-op for a remote host, so a save
    # that points Zedstead at a rented server never copies this machine's password.
    apply_settings(config, "battleye", values)
    if not any(k in values for _l, k, _t in _BATTLEYE_FIELDS):
        # Only Zedstead's own value changed: no file is touched, so it saves while
        # the server runs and on a box without the BattlEye file.
        return lambda: None

    refuse_while_running(config)
    kinds = {k: t for _l, k, t in _BATTLEYE_FIELDS}
    updates = {
        key: _format_value(kinds[key], values[key])
        for key in ("RConPassword", "RConPort", "RConIP", "RestrictRCon")
        if key in values
    }
    if "RConPort" in updates:
        port = rcon_port_number(updates["RConPort"])
        if port is None:
            raise ValueError("RCon port must be a whole number from 1 to 65535")
        updates["RConPort"] = str(port)
    battleye = _format_value("bool", values["BattlEye"]) if "BattlEye" in values else None
    if battleye is not None:
        _refuse_missing_server_cfg(config)  # the BattlEye switch lives in serverDZ.cfg

    # The same file the form was loaded from, so the keys it did not show are
    # kept as the admin saw them.
    be_data = read_beserver_cfg(live_beserver_cfg(config))
    _refuse_env_changes(_env_cfg_keys(config), {k: label for label, k, _t in _BATTLEYE_FIELDS},
                        [k for k, v in updates.items() if be_data.get(k) != v])
    be_data.update(updates)
    if not be_data.get("RConPort") and (default_port := _default_rcon_port(config)):
        # The form showed this port as the value (_battleye_load), so a save,
        # such as the one that turns BattlEye on, writes it.
        be_data["RConPort"] = default_port
    if not be_data.get("RConIP"):
        be_data.pop("RConIP", None)
    # write_beserver_cfg refuses the same; asked before the adopt, so a
    # refused value never becomes zsm's own RCon password (plans/68 LB-5).
    check_beserver_values(be_data, {k: label for label, k, _t in _BATTLEYE_FIELDS})
    adopt_beserver_rcon(config, be_data)

    def write() -> None:
        snapshot(config.beserver_cfg_path, trigger="editor")
        snapshot(config.server_cfg_path, trigger="editor")
        if battleye is not None:
            write_cfg_value(config.server_cfg_path, "BattlEye", battleye)
        write_beserver_cfg(config.beserver_cfg_path, be_data)

    return write


register(
    EditorSpec(
        id="battleye",
        title="BattlEye",
        group="Server",
        kind="form",
        exists=_battleye_exists,
        load=_battleye_load,
        save=_battleye_save,
        # The save also sets config.rcon_password/rcon_port (see _battleye_save).
        mutates_app_config=True,
        raw_targets=raw_targets_from(_BESERVER_CFG_RAW, _SERVER_CFG_RAW),
    )
)


# ---------------------------------------------------------------------------
# cfggameplay.json (flattened, dotted keys)
# ---------------------------------------------------------------------------


def _flatten(data: dict, prefix: str = "") -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    for key, value in data.items():
        full_key = f"{prefix}{key}"
        if isinstance(value, dict):
            out.extend(_flatten(value, f"{full_key}."))
        else:
            out.append((full_key, value))
    return out


def _unflatten_set(data: dict, dotted_key: str, value: Any) -> None:
    parts = dotted_key.split(".")
    node = data
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def _field_type(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, list):
        # Edited item by item and sent as a real array (plans/76 step 1). It
        # was a comma-joined string, and the save wrote every item back as a
        # string: `environmentMinTemps` [-3, …] became ["-4", …].
        return "list" if list_item_type(value) is not None else "str"
    return "str"


def _to_form_value(value: Any) -> Any:
    if isinstance(value, list):
        if list_item_type(value) is not None:
            return list(value)
        return json.dumps(value)
    return value


def _gameplay_mission_path(config: DayzConfig):
    m_dir = mission_dir(config)
    return get_cfggameplay_path(m_dir) if m_dir else None


def _gameplay_exists(config: DayzConfig) -> bool:
    path = _gameplay_mission_path(config)
    return path is not None and path.is_file()


# Bohemia's own top-level objects, in the order the stock file lists them. The
# raw key is the fallback title, so a group added in a future patch shows up
# named rather than silently swept into the ungrouped section.
_GAMEPLAY_SECTION_TITLES = {
    "GeneralData": "General",
    "PlayerData": "Player",
    "WorldsData": "World",
    "BaseBuildingData": "Base building",
    "UIData": "UI",
    "MapData": "Map",
    "VehicleData": "Vehicles",
    # Badlands' stock file only (DayZ 1.30).
    "ExternalLockData": "External locks",
    "SandstormData": "Sandstorms",
}


def _gameplay_sections(fields: list[dict]) -> list[dict]:
    """Group flattened fields under the object they came from.

    cfggameplay.json is not flat — the stock file has seven top-level objects
    and nests further inside them (PlayerData.StaminaData, and so on). This
    rendered all 65 leaves as **one** section titled with the filename, which
    threw away the only structure the file has and made it the widest wall of
    fields in the app (measured in 2026 Q3, TODO H4).

    `key` is untouched: it is the dotted path `_gameplay_save` writes back
    through `_unflatten_set`, so the wire format is the same as before. Only
    the label shortens, to the part below its section — the section already
    says the rest. A label still dotted (`StaminaData.meleeStaminaModifier`)
    is drawn by the form as a sub-group per object (FormEditor `nestFields`).
    """
    grouped: dict[str, list[dict]] = {}
    for field in fields:
        head, _, rest = field["key"].partition(".")
        if rest:
            grouped.setdefault(head, []).append({**field, "label": rest})
        else:
            grouped.setdefault("", []).append(field)

    sections = []
    for name, group in grouped.items():
        # Top-level scalars (`version`) belong to no object and would be
        # invisible under any of the seven names.
        title = _GAMEPLAY_SECTION_TITLES.get(name, name) if name else "File"
        sections.append({"title": title, "fields": group})
    return sections


def _gameplay_field(key: str, value: Any) -> dict:
    """One form field, typed from Bohemia's wiki (`cfggameplay.value_kind`),
    with the wiki's ranges and settings as ``min``/``max``/``choices``."""
    leaf = key.rsplit(".", 1)[-1]
    kind = _gp_kind(leaf, value)
    field: dict = {"key": key, "label": key, "help": field_help(_GP_DESC, leaf), **unit_attr(_GP_UNITS, leaf)}
    if kind in ("float list", "str list"):
        field.update(type="list", item=kind.split()[0], value=list(value) if isinstance(value, list) else [])
    elif kind == "list":
        field.update(type=_field_type(value), value=_to_form_value(value))
        if field["type"] == "list":
            field["item"] = list_item_type(value)
    else:
        field.update(type=kind, value=value)
    low, high = _GP_RANGES.get(leaf, (None, None))
    if low is not None:
        field["min"] = low
    if high is not None:
        field["max"] = high
    _add_choices(field, _GP_CHOICES.get(leaf), _GP_OPEN_CHOICES.get(leaf), _GP_CHOICE_HELP.get(leaf))
    return field


def _gameplay_value(dotted_key: str, raw: Any, existing: Any) -> Any:
    """The form's *raw* value as the file holds it; ValueError (400) naming the field."""
    leaf = dotted_key.rsplit(".", 1)[-1]
    kind = _gp_kind(leaf, existing)
    try:
        if kind == "float list":
            value = coerce_list(raw, "float", existing)
            if len(value) != _GP_LIST_SIZES[leaf]:
                raise ValueError(f"needs exactly {_GP_LIST_SIZES[leaf]} values, not {len(value)}")
            return value
        if kind == "str list":
            return coerce_list(raw, "str", existing)
        if kind == "list":
            item = list_item_type(existing)
            if item is not None:
                return coerce_list(raw, item, existing)
            value = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(value, list):
                raise ValueError("expected a JSON list")
            return value
        return _gp_scalar(leaf, raw, existing)
    except ValueError as exc:
        raise ValueError(f"{dotted_key}: {exc}") from None


def _gameplay_load(config: DayzConfig) -> dict:
    path = _gameplay_mission_path(config)
    data = read_cfggameplay(path) if path else {}
    fields = [_gameplay_field(key, value) for key, value in _flatten(data)]
    return {"kind": "form", "sections": _gameplay_sections(fields)}


def _gameplay_save(config: DayzConfig, values: dict) -> None:
    path = _gameplay_mission_path(config)
    if path is None:
        raise RuntimeError("Could not determine mission folder from serverDZ.cfg")
    data = read_cfggameplay(path)
    before = copy.deepcopy(data)
    for dotted_key, raw in values.items():
        node: Any = data
        for part in dotted_key.split(".")[:-1]:
            node = node.get(part, {}) if isinstance(node, dict) else {}
        existing = node.get(dotted_key.split(".")[-1]) if isinstance(node, dict) else None
        _unflatten_set(data, dotted_key, _gameplay_value(dotted_key, raw, existing))

    if same_value(before, data) and path.is_file():
        return  # nothing changed: no version, no write
    if path.is_file():
        snapshot(path, trigger="editor")  # after every check: a refused save leaves no version
    write_cfggameplay(path, data)


register(
    EditorSpec(
        id="gameplay",
        title="Gameplay",
        group="Mission",
        kind="form",
        exists=_gameplay_exists,
        load=_gameplay_load,
        save=_gameplay_save,
        raw_targets=raw_targets_from((_gameplay_mission_path, "json")),
    )
)


# ---------------------------------------------------------------------------
# Run Command (launch line — config.json fields + server_args flags)
# ---------------------------------------------------------------------------

# (section, [(label, key, type)]). Keys config_path/profiles_path/
# battleye_path/server_port write DayzConfig fields directly; the rest are
# parsed into / serialized out of server_args (see app.shared.server_args).
_RUNCOMMAND_SECTIONS: list[tuple[str, list[tuple[str, str, str]]]] = [
    ("Paths", [
        ("Server config (-config=PATH)", "config_path", "str"),
        ("Profiles dir (-profiles=PATH)", "profiles_path", "str"),
        ("BattlEye dir (-BEpath=PATH)", "battleye_path", "str"),
    ]),
    ("Network", [
        ("Game port (-port=N)", "server_port", "int"),
    ]),
    ("Logging", [
        ("-adminLog", "adminLog", "bool"),
        ("-netLog", "netLog", "bool"),
        ("-doLogs", "doLogs", "bool"),
        ("-freezeCheck", "freezeCheck", "bool"),
    ]),
    ("Performance", [
        ("CPU count (-cpuCount=N, empty = default)", "cpuCount", "int"),
        ("Limit FPS (-limitFPS=N, empty = no cap)", "limitFPS", "int"),
    ]),
    ("Other", [
        ("-filePatching", "filePatching", "bool"),
        ("Server mods (-serverMod=A;B)", "serverMod", "str"),
        ("Storage (-storage=PATH)", "storage", "str"),
        ("Mission override (-mission=NAME, conflicts with Change Map)", "mission", "str"),
    ]),
    ("Extra args", [
        ("Extra args (unrecognized flags, kept verbatim)", "_extras", "str"),
    ]),
]

# Moved to app/shared/server_args.py so the settings registry (app/shared) can
# read it for the web path lock; re-bound here under the old name for readers.
from app.shared.server_args import RUNCOMMAND_CONFIG_FIELDS as _RUNCOMMAND_CONFIG_FIELDS

_RUNCOMMAND_LABELS = {
    **{key: label for _title, items in _RUNCOMMAND_SECTIONS for label, key, _kind in items},
    "server_args": "Launch flags",
}


# Launch flags another page now sets: the Mods page marks a mod "Server only"
# and the launch builder writes -serverMod= from it (service_control.
# _get_server_mod_parts, which still merges a typed list in), and the Maps
# page switches the mission with its folder and mod XML. The form shows such a
# field as a note and a link while it is empty, and keeps the box for a value
# already set, so a hand-typed flag still works and can be cleared.
_RUNCOMMAND_ELSEWHERE: dict[str, tuple[str, dict[str, str]]] = {
    "serverMod": (
        "Mark a mod Server only on the Mods page; Zedstead adds it to -serverMod=.",
        {"to": "/mods", "label": "Open the Mods page"},
    ),
    "mission": (
        "Change the map on the Maps page, which also moves the mission folder and its mods' XML.",
        {"to": "/maps", "label": "Open the Maps page"},
    ),
}


def _runcommand_field(key: str) -> str:
    """The config field a Run Command key is saved into."""
    if key == "server_port" or key in _RUNCOMMAND_CONFIG_FIELDS:
        return key
    return "server_args"


def _runcommand_load(config: DayzConfig) -> dict:
    from app.shared.server_args import parse_server_args

    parsed = parse_server_args(config.server_args)
    # While the path lock is on, config_path/profiles_path/battleye_path can't be
    # saved (mutate_config raises ConfigLocked); mark them so B renders them
    # read-only rather than letting the save 403. plans/58 B. Read from the
    # file, which mutate_config checks too: the CLI sets the lock.
    locked = bool((config.reloaded() or config).web_lock_paths)
    # Set by the container's environment at every start: shown read-only, as
    # an edit would be undone by the next start.
    env_set = env_locked_fields(config)
    sections = []
    for title, items in _RUNCOMMAND_SECTIONS:
        fields = []
        for label, key, kind in items:
            if key == "server_port":
                value: Any = config.server_port
            elif key in _RUNCOMMAND_CONFIG_FIELDS:
                value = getattr(config, key, "") or ""
            else:
                value = parsed.get(key)
                if kind == "str":
                    value = value or ""
            field = {
                "key": key,
                "label": label,
                "type": kind,
                "value": value,
                "help": field_help(_RUNCOMMAND_HELP, key),
            }
            if key in _RUNCOMMAND_ELSEWHERE:
                field["note"], field["link"] = _RUNCOMMAND_ELSEWHERE[key]
                field["elsewhere"] = True
            if locked and key in _RUNCOMMAND_CONFIG_FIELDS:
                field["locked"] = True
            var = env_set.get(_runcommand_field(key))
            if var:
                field["locked"] = True
                field["locked_note"] = env_lock_note(var)
            fields.append(field)
        sections.append({"title": title, "fields": fields})
    return {"kind": "form", "sections": sections}


def _launch_flag_range(key: str) -> tuple[int | None, int | None]:
    """What the server takes for a number launch flag
    (server-configuration.wiki:181-182)."""
    if key == "limitFPS":
        return 1, 200                       # "(current max is 200)"
    if key == "cpuCount":
        # "less or equal than the numbers of available cores"
        return 1, os.cpu_count()
    return 0, None


def _runcommand_save(config: DayzConfig, values: dict) -> None:
    from app.shared.server_args import (
        KNOWN_BOOL_FLAGS,
        KNOWN_INT_FLAGS,
        KNOWN_STR_FLAGS,
        extras_problem,
        parse_server_args,
        serialize_server_args,
    )

    # Start from the current args so keys absent from the payload keep their value.
    parsed = parse_server_args(config.server_args)
    before = {f: getattr(config, f) for f in ("server_port", *_RUNCOMMAND_CONFIG_FIELDS)}
    before_args = dict(parsed)

    from app.server.service_control import (
        BATTLEYE_PATH_FIELD,
        CONFIG_PATH_FIELD,
        PROFILES_PATH_FIELD,
    )

    path_fields = {
        "config_path": CONFIG_PATH_FIELD,
        "profiles_path": PROFILES_PATH_FIELD,
        "battleye_path": BATTLEYE_PATH_FIELD,
    }

    for key, value in values.items():
        if key == "server_port":
            s = "" if value is None else str(value).strip()
            # An emptied field used to be skipped: the save answered 200 and
            # the old port came back. The launch line always carries -port.
            if not s:
                raise ValueError("Game port (-port) can't be empty.")
            try:
                port = int(s)
            except ValueError:
                raise ValueError("Game port must be an integer.")
            if not 1 <= port <= 65535:
                raise ValueError("Game port must be 1-65535.")
            config.server_port = port
        elif key in _RUNCOMMAND_CONFIG_FIELDS:
            s = "" if value is None else str(value).strip()
            # Each of these is always on the launch line, joined onto the
            # server dir; an empty one would point it at the server dir itself
            # (or at nothing for -config). It used to be skipped silently, so
            # the save answered 200 and the old value came back. Refused, with
            # the default named, rather than reset to a value nobody typed.
            if not s:
                spec = DayzConfig.__dataclass_fields__[key]
                default = spec.default_factory() if callable(spec.default_factory) else spec.default
                raise ValueError(f"{path_fields.get(key, key)} can't be empty; the default is {default}.")
            setattr(config, key, s)
        elif key in KNOWN_BOOL_FLAGS:
            parsed[key] = bool(value)
        elif key in KNOWN_INT_FLAGS:
            s = "" if value is None else str(value).strip()
            if not s:
                parsed[key] = None
            else:
                try:
                    number = int(s)
                except ValueError:
                    raise ValueError(f"Invalid integer for -{key}.")
                problem = _range_problem(f"-{key}", number, *_launch_flag_range(key))
                if problem:
                    raise ValueError(f"{problem}.")
                parsed[key] = number
        elif key in KNOWN_STR_FLAGS or key == "_extras":
            text = "" if value is None else str(value).strip()
            if key == "_extras":
                problem = extras_problem(text)
                if problem:
                    raise ValueError(problem)
            parsed[key] = text

    # A field the container's environment sets would be undone at the next
    # start; refused rather than saved and lost.
    changed = [f for f, v in before.items() if getattr(config, f) != v]
    if parsed != before_args:
        changed.append("server_args")
    _refuse_env_changes(env_locked_fields(config), _RUNCOMMAND_LABELS, changed)

    config.server_args = serialize_server_args(parsed)

    # Refuse a path or flag that the server's launch command would run as shell
    # syntax, naming the field, so the admin sees it on save rather than at the
    # next unit install. The same check runs when the unit/.bat is rendered.
    from app.server.service_control import _validate_launch_command

    _validate_launch_command(config)


register(
    EditorSpec(
        id="runcommand",
        title="Run Command",
        group="Server",
        kind="form",
        exists=lambda config: True,
        load=_runcommand_load,
        save=_runcommand_save,
        mutates_app_config=True,
        # No Raw tab: this editor is config.json-backed (launch flags), not a
        # single server file. Edit config.json via App Settings if needed.
    )
)


# ---------------------------------------------------------------------------
# dayzsetting.xml (Core Settings — attributes on <video>/<render>/<jobsystem>/<pc>)
# ---------------------------------------------------------------------------

_CORESETTINGS_SECTIONS: list[tuple[str, str, list[str]]] = [
    # (title, element key prefix, attrs)
    ("Video (ignored on dedicated server)", "video",
     ["noshadergen", "screenwidth", "screenheight", "vsync", "fullscreen", "gamma", "shadow"]),
    ("Render (ignored on dedicated server)", "render",
     ["geometricdetail", "postprocess", "reflections", "waterdetail", "terraindetail"]),
    ("Job System (needs multithreadedReplication=1)", "jobsystem",
     ["globalqueue", "threadqueue"]),
    ("Job System — PC", "pc", ["maxcores", "reservedcores"]),
]


# Sections only the game client reads. The form puts them behind a "Client
# only" tab after the settings that change the server (FormEditor's `tab`).
_CORESETTINGS_CLIENT_ONLY = frozenset({"video", "render"})

# Readable labels for dayzsetting.xml's attributes, which are run-together
# lower-case words (noshadergen). The attribute itself goes into the help.
_CORESETTINGS_LABELS = {
    "noshadergen": "No shader generation",
    "screenwidth": "Screen width",
    "screenheight": "Screen height",
    "vsync": "Vertical sync",
    "fullscreen": "Full screen",
    "gamma": "Gamma",
    "shadow": "Shadow quality",
    "geometricdetail": "Geometric detail",
    "postprocess": "Post-processing",
    "reflections": "Reflections",
    "waterdetail": "Water detail",
    "terraindetail": "Terrain detail",
    "globalqueue": "Global queue size",
    "threadqueue": "Per-thread queue size",
    "maxcores": "Max cores",
    "reservedcores": "Reserved cores",
}


def _coresettings_help(attr: str) -> str:
    """The attribute's help, then its name as the file spells it."""
    text = field_help(_CORE_DESC, attr)
    return f"{text}\n\nIn the file: {attr}" if text else f"In the file: {attr}"


def _coresettings_path(config: DayzConfig):
    return config.server_dir_path / "dayzsetting.xml"


def _coresettings_elem(root, prefix: str):
    if prefix == "pc":
        return root.find(".//pc")
    return root.find(prefix)


def _coresettings_load(config: DayzConfig) -> dict:
    from xml.etree import ElementTree as ET

    from app.xml.xml_utils import load_xml

    path = _coresettings_path(config)
    if not path.is_file():
        raise RuntimeError("dayzsetting.xml not found in the server directory.")
    try:
        root = load_xml(path).getroot()
    except ET.ParseError as e:
        raise RuntimeError(f"Failed to parse dayzsetting.xml: {e}")

    sections = []
    for title, prefix, attrs in _CORESETTINGS_SECTIONS:
        elem = _coresettings_elem(root, prefix)
        fields = [
            {
                "key": f"{prefix}--{attr}",
                "label": _CORESETTINGS_LABELS.get(attr, attr),
                "type": "str",
                "value": elem.get(attr, "") if elem is not None else "",
                "help": _coresettings_help(attr),
            }
            for attr in attrs
        ]
        section = {"title": title, "fields": fields}
        if prefix in _CORESETTINGS_CLIENT_ONLY:
            section["tab"] = "Client only"
        sections.append(section)
    return {"kind": "form", "sections": sections}


def _check_cores(pc, values: dict) -> None:
    """Refuse a ``<pc>`` whose core counts the job system cannot use.

    server-configuration.wiki:146-151: maxcores is the most CPU cores the job
    system uses and reservedcores the cores kept for other threads, so both
    are whole numbers and maxcores is at least 1. The workers are
    maxcores - reservedcores, "but still at least one worker thread is
    allocated", so a reservedcores at or above maxcores is valid: one worker.
    """
    keys = ("pc--maxcores", "pc--reservedcores")
    if pc is None or not any(k in values for k in keys):
        return
    counts = {}
    for key in keys:
        attr = key.split("--", 1)[1]
        text = str(values[key]).strip() if key in values else pc.get(attr, "").strip()
        if not text.isdigit():
            raise ValueError(f"{attr} must be a whole number of CPU cores.")
        counts[attr] = int(text)
    if counts["maxcores"] < 1:
        raise ValueError("maxcores must be at least 1.")


def _coresettings_save(config: DayzConfig, values: dict) -> None:
    from app.xml.xml_utils import load_xml, save_xml

    path = _coresettings_path(config)
    if not path.is_file():
        raise RuntimeError("dayzsetting.xml not found in the server directory.")
    tree = load_xml(path, preserve_comments=True)
    root = tree.getroot()

    known = {
        f"{prefix}--{attr}": prefix
        for _title, prefix, attrs in _CORESETTINGS_SECTIONS
        for attr in attrs
    }
    _check_cores(_coresettings_elem(root, "pc"), values)
    changed = False
    for key, value in values.items():
        prefix = known.get(key)
        if prefix is None:
            continue
        elem = _coresettings_elem(root, prefix)
        if elem is None:
            continue
        attr = key.split("--", 1)[1]
        new_val = str(value).strip()
        if new_val != elem.get(attr, ""):
            elem.set(attr, new_val)
            changed = True

    if changed:
        save_xml(tree, path)


register(
    EditorSpec(
        id="coresettings",
        title="Core Settings",
        group="Server",
        kind="form",
        exists=lambda config: _coresettings_path(config).is_file(),
        load=_coresettings_load,
        save=_coresettings_save,
        raw_targets=raw_targets_from((_coresettings_path, "xml")),
    )
)
