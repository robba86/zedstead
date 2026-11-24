"""The runtimes ``DayZServer_x64.exe`` needs on Windows, and whether they are there.

A fresh Windows 11 has neither (measured in 2026 Q4, first-hour test): the
server exits the moment it is started, Windows names ``MSVCP140.dll`` and, once
the Visual C++ runtime is in, ``XAPOFX1_5.dll``. Through ``start-server.bat``
that box never shows: the watchdog window prints "closed or crashed, restarting
in 10 seconds" for ever and nothing says why, and doctor reported a healthy
server. So doctor has a row for it, and a start that does not stay up says it.

One DLL per runtime, the one Windows itself named. Looked for where the loader
looks: the server's folder, the system folders and ``PATH``. A file found in
any of them counts, so this can miss a broken install but does not call a
working one broken.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from app.shared.platform import IS_WINDOWS

if TYPE_CHECKING:
    from app.shared.config import DayzConfig

DIRECTX_URL = "https://www.microsoft.com/en-us/download/details.aspx?id=8109"
#: Inside the server folder: SteamCMD downloads it with the server.
VC_REDIST = Path("_CommonRedist") / "vcredist" / "2019" / "VC_redist.x64.exe"


@dataclass(frozen=True)
class Runtime:
    dll: str
    name: str

    def install_hint(self, config: "DayzConfig") -> str:
        """One sentence per runtime, with the path or the URL last: the panel
        shows a path as a code block and a URL as a link (``DetailText.jsx``)."""
        if self.dll == VISUAL_CPP.dll:
            return f"To install the {self.name}, run {Path(config.server_dir) / VC_REDIST}"
        return f"To install the {self.name}, download it from {DIRECTX_URL}"


VISUAL_CPP = Runtime("MSVCP140.dll", "Microsoft Visual C++ 2015-2019 Redistributable (x64)")
DIRECTX = Runtime("XAPOFX1_5.dll", "DirectX End-User Runtimes (June 2010)")
RUNTIMES = (VISUAL_CPP, DIRECTX)


def _search_dirs(config: "DayzConfig") -> list[Path]:
    """Where Windows looks for a DLL the server imports: the exe's folder
    (also the watchdog's working directory), the system folders, ``PATH``."""
    root = Path(os.environ.get("SystemRoot") or os.environ.get("WINDIR") or r"C:\Windows")
    dirs = [Path(config.server_dir), root / "System32", root / "System", root]
    dirs += [Path(entry) for entry in os.environ.get("PATH", "").split(os.pathsep) if entry]
    return dirs


def _is_present(dll: str, dirs: list[Path]) -> bool:
    for folder in dirs:
        try:
            if (folder / dll).is_file():
                return True
        except OSError:
            continue
    return False


def missing_runtimes(config: "DayzConfig") -> list[Runtime]:
    """The runtimes whose DLL is nowhere the loader looks. Empty off Windows."""
    if not IS_WINDOWS:
        return []
    dirs = _search_dirs(config)
    return [runtime for runtime in RUNTIMES if not _is_present(runtime.dll, dirs)]


def runtime_problem(config: "DayzConfig") -> str | None:
    """One sentence for doctor and for a start that did not stay up, or None."""
    missing = missing_runtimes(config)
    if not missing:
        return None
    dlls = " and ".join(runtime.dll for runtime in missing)
    hints = " ".join(f"{runtime.install_hint(config)}." for runtime in missing)
    return (
        f"DayZServer_x64.exe cannot start on this computer: {dlls} "
        f"{'is' if len(missing) == 1 else 'are'} missing. {hints} Then start the server again."
    )
