"""zsm has to start on Windows, and no test ran there.

`import resource` at the top of `app/ops/vps_readiness.py` stopped every
command on Windows, `zsm --version` included, from the day `app.__main__`
began importing that module, and the suite stayed green: the module exists on
the machines the suite runs on. Found by the first-hour test on a fresh
Windows 11 (2026 Q4).

So this imports every module of `app` in a fresh interpreter in which the
standard library looks like Windows' to zsm's own code: the Unix-only modules
cannot be imported from an `app` module, and the `os` and `signal` names
Windows lacks are gone. An import inside a function is not reached, and an
import in a `try` is caught, which are the two ways such a module may be used.
It is not Windows: it finds what breaks at import, not what a command does.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

_PROBE = r"""
import builtins, importlib, os, pkgutil, signal, sys

# Third-party packages first: on this machine they may need these modules.
for dep in ("click", "rich.console", "InquirerPy", "fastapi", "uvicorn", "jinja2", "croniter", "berconpy"):
    try:
        importlib.import_module(dep)
    except Exception:
        pass

UNIX_ONLY = {"resource", "fcntl", "pwd", "grp", "termios", "tty", "pty", "syslog", "crypt", "posix"}
real_import = builtins.__import__

def guarded(name, globals=None, locals=None, fromlist=(), level=0):
    importer = (globals or {}).get("__name__", "")
    if level == 0 and name.split(".")[0] in UNIX_ONLY and (importer == "app" or importer.startswith("app.")):
        raise ModuleNotFoundError(f"No module named {name!r}")
    return real_import(name, globals, locals, fromlist, level)

builtins.__import__ = guarded
for module, names in (
    (os, ("geteuid", "getuid", "getgid", "getegid", "fork", "setsid", "killpg", "getpgid", "sysconf",
          "uname", "chown", "fchown", "fchmod", "getpgrp", "getgroups", "O_NOFOLLOW", "O_DIRECTORY")),
    (signal, ("SIGKILL", "SIGHUP", "SIGUSR1", "SIGUSR2", "SIGQUIT", "SIGCHLD", "SIGPIPE", "SIGALRM")),
):
    for name in names:
        if hasattr(module, name):
            delattr(module, name)

import app.shared.platform as platform
platform.IS_WINDOWS = True

import app
failed = []
for found in pkgutil.walk_packages(app.__path__, "app."):
    try:
        importlib.import_module(found.name)
    except BaseException as e:
        failed.append(f"{found.name}: {type(e).__name__}: {e}")
print("\n".join(failed) if failed else "all app modules import")
"""


def _run_probe(probe: str, home: Path) -> subprocess.CompletedProcess:
    """A fresh interpreter, so this run's own imports cannot hide a failure.
    Importing writes nothing; the throwaway HOME is for the day one does."""
    return subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(REPO_ROOT),
        env={**os.environ, "HOME": str(home)},
    )


def test_every_app_module_imports_without_the_unix_only_stdlib(tmp_path):
    result = _run_probe(_PROBE, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "all app modules import", result.stdout


def test_the_probe_notices_a_unix_only_import(tmp_path):
    """A guard on the probe: it has to fail for the import it exists to catch."""
    probe = _PROBE.replace(
        "import app\nfailed = []",
        "import app\nexec('import resource', {'__name__': 'app.fake'})\nfailed = []",
    )
    assert probe != _PROBE
    result = _run_probe(probe, tmp_path)
    assert result.returncode != 0
    assert "No module named 'resource'" in result.stderr
