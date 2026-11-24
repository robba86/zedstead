"""Parse/serialize the ``server_args`` launch-flags string.

Known DayZ launch flags are surfaced as structured fields; anything not
recognized is preserved verbatim in the ``_extras`` key so the load → save
round-trip never silently drops user-supplied flags.

Bool flag matching is case-insensitive on input but always emits the canonical
mixed-case spelling on save (``-adminLog``, ``-netLog``, ``-freezeCheck``), so
existing configs with lower-case spellings auto-migrate on first save.

Used by the web app's Run Command editor.
"""

from __future__ import annotations

# Run Command editor fields that write DayzConfig path attributes directly (as
# opposed to being parsed into/out of server_args). Lives here, in app/shared,
# so both the editor (app/web) and the settings registry (app/shared) can read
# it — the registry cannot import app/web. The web path lock (plans/58) locks
# these along with the registry's own path settings.
RUNCOMMAND_CONFIG_FIELDS: tuple[str, ...] = ("config_path", "profiles_path", "battleye_path")

# Launch flags whose value the engine reads a file or directory from. When the
# web path lock is on, a change to any of these inside server_args is refused
# too — otherwise `-BEpath=/elsewhere` would repoint the BattlEye directory the
# lock protects. Matched case-insensitively, like the rest of this module.
PATH_FLAG_PREFIXES: tuple[str, ...] = (
    "-config=",
    "-profiles=",
    "-bepath=",
    "-mod=",
    "-servermod=",
    "-mission=",
    "-storage=",
)


def path_flags_in(server_args: str) -> dict[str, str]:
    """Map each path-bearing launch flag present in *server_args* to its value.

    Used by the web path lock to catch a protected path smuggled into the launch
    flags. Keyed by the lowercased flag prefix so ``-BEpath=`` and ``-bepath=``
    compare equal; a flag repeated keeps the last value, which is enough to tell
    whether the set of path values changed.
    """
    found: dict[str, str] = {}
    for tok in (server_args or "").split():
        bare = tok.strip('"').strip("'")
        low = bare.lower()
        for prefix in PATH_FLAG_PREFIXES:
            if low.startswith(prefix):
                found[prefix] = bare[len(prefix):]
                break
    return found


KNOWN_BOOL_FLAGS: tuple[str, ...] = (
    "adminLog",
    "netLog",
    "doLogs",
    "freezeCheck",
    "filePatching",
)
KNOWN_INT_FLAGS: tuple[str, ...] = ("cpuCount", "limitFPS")
# Free-form value flags. -mission is intentionally included even though it
# conflicts with serverDZ.cfg's `template` (and the Change Map feature) —
# the field description warns the user; default is empty so we don't emit it.
KNOWN_STR_FLAGS: tuple[str, ...] = ("serverMod", "storage", "mission")


def _flag_lookup(flags: tuple[str, ...]) -> dict[str, str]:
    return {f.lower(): f for f in flags}


def parse_server_args(s: str) -> dict:
    """Parse ``server_args`` into a dict.

    Returns ``{<bool flag>: bool, <int flag>: int|None, <str flag>: str,
    "_extras": str}``. Unknown tokens are joined back into ``_extras``.

    Bool flag matching is case-insensitive: ``-adminlog``, ``-AdminLog`` and
    ``-adminLog`` all map to ``adminLog: True``.
    """
    result: dict = {flag: False for flag in KNOWN_BOOL_FLAGS}
    for flag in KNOWN_INT_FLAGS:
        result[flag] = None
    for flag in KNOWN_STR_FLAGS:
        result[flag] = ""
    extras: list[str] = []

    bool_lookup = _flag_lookup(KNOWN_BOOL_FLAGS)
    int_lookup = _flag_lookup(KNOWN_INT_FLAGS)
    str_lookup = _flag_lookup(KNOWN_STR_FLAGS)

    for tok in (s or "").split():
        if not tok.startswith("-"):
            extras.append(tok)
            continue

        consumed = False

        # int flags: -cpuCount=N
        for low, canonical in int_lookup.items():
            prefix = f"-{low}="
            if tok.lower().startswith(prefix):
                try:
                    result[canonical] = int(tok[len(prefix):])
                    consumed = True
                except ValueError:
                    pass
                break
        if consumed:
            continue

        # string flags: -serverMod=A;B
        for low, canonical in str_lookup.items():
            prefix = f"-{low}="
            if tok.lower().startswith(prefix):
                result[canonical] = tok[len(prefix):]
                consumed = True
                break
        if consumed:
            continue

        # bool flags: -adminLog (case-insensitive)
        canonical = bool_lookup.get(tok[1:].lower())
        if canonical is not None:
            result[canonical] = True
            continue

        extras.append(tok)

    result["_extras"] = " ".join(extras)
    return result


# Flags zsm writes onto the launch line itself, and where each is set. One
# typed into the extra args as well would put it there twice, and the server
# would take one of the two (server-configuration.wiki:171-184 lists them).
SET_ELSEWHERE: dict[str, str] = {
    "port": "Game port (-port=N)",
    "config": "Server config (-config=PATH)",
    "profiles": "Profiles dir (-profiles=PATH)",
    "bepath": "BattlEye dir (-BEpath=PATH)",
    "mod": "the mod list on the Mods page",
    "servermod": "Server mods (-serverMod=A;B), or a mod marked server-side on the Mods page",
}


def extras_problem(extras: str) -> str | None:
    """Why *extras* (the Run Command's extra args) cannot be saved, or None:
    it carries a flag zsm sets from a field of its own (`SET_ELSEWHERE`)."""
    for tok in (extras or "").split():
        bare = tok.strip('"').strip("'")
        if not bare.startswith("-"):
            continue
        name = bare[1:].split("=", 1)[0].lower()
        where = SET_ELSEWHERE.get(name)
        if where:
            return f"Extra args can't hold {bare.split('=', 1)[0]}: Zedstead sets it from {where}."
    return None


def is_known_flag_token(tok: str) -> bool:
    if not tok.startswith("-"):
        return False
    name_low = tok[1:].lower()
    if name_low in _flag_lookup(KNOWN_BOOL_FLAGS):
        return True
    for flag in KNOWN_INT_FLAGS + KNOWN_STR_FLAGS:
        if tok.lower().startswith(f"-{flag.lower()}="):
            return True
    return False


def serialize_server_args(parsed: dict) -> str:
    """Reassemble ``server_args`` from a parsed dict.

    Bool flags emit only when ``True``. Int flags emit only when not ``None``.
    String flags emit only when non-empty. Tokens in ``_extras`` that match a
    known flag are dropped (defensive against the user re-typing a known flag
    into the Extras field).
    """
    parts: list[str] = []
    for flag in KNOWN_BOOL_FLAGS:
        if parsed.get(flag, False):
            parts.append(f"-{flag}")
    for flag in KNOWN_INT_FLAGS:
        v = parsed.get(flag)
        if v is None:
            continue
        parts.append(f"-{flag}={v}")
    for flag in KNOWN_STR_FLAGS:
        v = (parsed.get(flag) or "").strip()
        if v:
            parts.append(f"-{flag}={v}")
    extras = (parsed.get("_extras") or "").strip()
    if extras:
        kept = [t for t in extras.split() if not is_known_flag_token(t)]
        if kept:
            parts.append(" ".join(kept))
    return " ".join(parts)


def split_server_mod_flag(s: str) -> tuple[list[str], str]:
    """Take any hand-typed ``-serverMod=A;B`` out of a launch-flags string.

    Returns ``(entries, rest)``: the mod names it listed (quotes stripped,
    empties dropped, order kept) and the string without that token, every other
    token left in its place. The launch builder uses it to merge a typed list
    with the generated one into a **single** ``-serverMod=`` (plans/47 D1). Two
    of the same flag on one command line would leave it to the engine to pick
    one. Matching is case-insensitive, like the parser above.
    """
    entries: list[str] = []
    rest: list[str] = []
    for tok in (s or "").split():
        bare = tok.strip('"').strip("'")
        if bare.lower().startswith("-servermod="):
            value = bare[len("-servermod="):]
            entries.extend(m.strip() for m in value.split(";") if m.strip())
            continue
        rest.append(tok)
    return entries, " ".join(rest)
