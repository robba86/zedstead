"""The ZEDSTEAD logo the three install scripts print.

Each script runs before the repo exists on the box, so each carries its own
copy of the logo. These tests keep the three copies the same, read as text.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(name: str) -> str:
    return (REPO_ROOT / name).read_text(encoding="utf-8")


def _sh_rows() -> list[str]:
    """The rows in install.sh's banner(), the reference copy."""
    body = _read("install.sh").split("banner() {", 1)[1].split("SERVER MANAGER", 1)[0]
    return re.findall(r"^        '(.*)'(?: \\)?$", body, re.M)


def test_install_sh_has_five_rows_and_the_subtitle():
    assert len(_sh_rows()) == 5
    assert "SERVER MANAGER" in _read("install.sh").split("banner() {", 1)[1]


def test_the_powershell_logo_matches_install_sh():
    text = _read("install.ps1")
    rows = re.findall(r"^    '(.*)'$", text.split("$BannerRows = @(", 1)[1].split("\n)", 1)[0], re.M)
    assert rows == _sh_rows()
    assert "SERVER MANAGER" in text


def test_the_bat_logo_matches_install_sh():
    """cmd needs "|" written as "^|", or it reads the row as a pipe."""
    text = _read("install.bat")
    rows = [
        line.removeprefix("echo %BLUE%").removesuffix("%R%").replace("^|", "|")
        for line in text.splitlines()
        if line.startswith("echo %BLUE%")
    ]
    assert rows == _sh_rows()
    assert "SERVER MANAGER" in text
