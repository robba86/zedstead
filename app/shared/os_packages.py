"""Reads app/data/os-packages.txt — the single source for OS package names.

See plans/21 for why this file exists and what else reads it: install.sh (and its
--check-deps), `zsm doctor`, and tests/test_os_packages.py,
which fails when docker/Dockerfile drifts from the [runtime] section.

Stdlib only, and every probe here *reports* rather than guesses: on a host
with no dpkg or no ldd the functions return empty lists instead of claiming
everything is missing.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Iterable, Optional

PACKAGE_LIST_PATH = Path(__file__).resolve().parents[1] / "data" / "os-packages.txt"

# .so name -> apt package. Only entries we can justify; unknown libs are
# reported by name with no package suggestion rather than mapped by guess.
SO_TO_PACKAGE: dict[str, str] = {
    "libstdc++.so.6": "libstdc++6:i386",
    "libgcc_s.so.1": "lib32gcc-s1",
    "libcurl.so.4": "libcurl4",
}

_NOT_FOUND_RE = re.compile(r"^\s*(\S+)\s*=>\s*not found\s*$")

_SUBPROCESS_ERRORS = (OSError, subprocess.SubprocessError)


def read_sections(path: Optional[Path] = None) -> dict[str, list[str]]:
    """Parse os-packages.txt into {section: [package, ...]}."""
    path = path or PACKAGE_LIST_PATH
    sections: dict[str, list[str]] = {}
    current: Optional[list[str]] = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = sections.setdefault(line[1:-1], [])
            continue
        if current is not None:
            current.append(line)
    return sections


def bootstrap_packages() -> list[str]:
    """Packages install.sh needs before it can clone the repo."""
    return read_sections().get("bootstrap", [])


def runtime_packages() -> list[str]:
    """Packages SteamCMD and the DayZServer binary need."""
    return read_sections().get("runtime", [])


def needs_i386_architecture(names: Iterable[str]) -> bool:
    """True when a name uses the multiarch 32-bit route (`<pkg>:i386`).

    Those names are unresolvable until `dpkg --add-architecture i386` has run,
    so every installer here checks this first — install.sh with the same
    trigger in ensure_packages. Lives beside the list so the route rule and the
    list cannot drift apart (see os-packages.txt's header and plans/26).
    """
    return any(":i386" in name for name in names)


def steamcmd_packages() -> list[str]:
    """Packages SteamCMD itself needs — a strict subset of the runtime list.

    Installed by app/shared/steam.py's auto-installer. See plans/26 for why
    this is a separate section rather than a reuse of runtime_packages().
    """
    return read_sections().get("steamcmd", [])


def _provided_names() -> set[str]:
    """Virtual package names that an *installed* package Provides.

    Ubuntu 24.04's 64-bit ``time_t`` transition renamed ``libcurl4`` to
    ``libcurl4t64``, which merely *Provides* the old name. ``dpkg-query -W
    libcurl4`` then answers ``unknown ok not-installed`` (with exit 0), so a
    direct-name check reports it missing on a host where it is installed —
    forever, because ``apt-get install libcurl4`` resolves the virtual name and
    says "already the newest version". That loop is unescapable for the user,
    which is why this fallback exists rather than pinning ``libcurl4t64`` in
    the list: the virtual name is the correct thing to hand apt on both 22.04
    and 24.04.

    Empty set on any failure — a lookup that could not run must not turn into
    "everything is present".
    """
    try:
        proc = subprocess.run(
            ["dpkg-query", "-W", r"-f=${Status}|${Provides}\n"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, *_SUBPROCESS_ERRORS):
        return set()
    provided: set[str] = set()
    for line in (proc.stdout or "").splitlines():
        status, sep, provides = line.partition("|")
        if not sep or status.strip() != "install ok installed":
            continue
        for entry in provides.split(","):
            # "libcurl4 (= 8.18.0-1ubuntu2.4)" -> "libcurl4"
            name = entry.split("(")[0].strip()
            if name:
                provided.add(name)
    return provided


def missing_packages(names: Iterable[str]) -> list[str]:
    """Names not installed per `dpkg-query -W -f='${Status}'`.

    A name the direct query misses is re-checked against what installed
    packages *Provide* — see `_provided_names` for the libcurl4/libcurl4t64
    case that makes this necessary.

    Empty list on non-Debian hosts or when dpkg-query is absent — this reports,
    it never guesses.
    """
    names = list(names)
    if not names:
        return []
    missing: list[str] = []
    provided: Optional[set[str]] = None  # looked up once, and only if needed
    for name in names:
        try:
            proc = subprocess.run(
                ["dpkg-query", "-W", "-f=${Status}", name],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except FileNotFoundError:
            return []  # not a dpkg host — report nothing
        except _SUBPROCESS_ERRORS:
            return []
        if proc.returncode == 0 and "install ok installed" in (proc.stdout or ""):
            continue
        if ":" in name:
            # Arch-qualified (libstdc++6:i386). ${Provides} carries no
            # architecture, so a match there could not tell i386 from amd64 —
            # and claiming a 32-bit dep is present when it is not is the
            # failure this whole check exists to prevent.
            missing.append(name)
            continue
        if provided is None:
            provided = _provided_names()
        if name not in provided:
            missing.append(name)
    return missing


def missing_shared_libs(binary: Path) -> list[str]:
    """`ldd <binary>` -> the sonames it reports as 'not found'.

    [] if ldd is absent, the binary does not exist, or ldd fails (never raises).
    """
    binary = Path(binary)
    if not binary.exists():
        return []
    try:
        proc = subprocess.run(
            ["ldd", str(binary)],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except _SUBPROCESS_ERRORS:
        return []
    sonames: list[str] = []
    for line in (proc.stdout or "").splitlines():
        match = _NOT_FOUND_RE.match(line)
        if match:
            sonames.append(match.group(1))
    return sonames


def effective_utf8_locale() -> Optional[str]:
    """The first UTF-8 locale from `locale -a`, or None. Informational."""
    try:
        proc = subprocess.run(
            ["locale", "-a"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except _SUBPROCESS_ERRORS:
        return None
    for line in (proc.stdout or "").splitlines():
        name = line.strip()
        if "utf-8" in name.lower().replace("utf8", "utf-8"):
            return name
    return None
