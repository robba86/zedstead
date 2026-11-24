"""Files TheDmitri's mods stopped shipping, from his GitHub repositories.

The TraderPlus Workshop update of 2026-11-09 ships only ``Addons``, ``Keys``
and ``meta.cpp``: no ``ServerProfile`` with the default JSON configs and no
``Centraleconomy`` with ``TraderPlus_types.xml``. The author keeps both in
``TheDmitri/TraderPlusV1`` (``serverProfileFiles/`` and ``economy/``), so when
the installed mod has none, the reset buttons and the install offer fetch them
from there at the admin's request.

ToxicZone still ships its ``types.xml`` and ``Trader.txt`` (2026-11-14), and
``TheDmitri/ToxicZone`` holds the same files. `restore_missing_mod_files`
puts them back into the server's copy of the mod after each copy if a later
update drops them, so the XML merge and the Trader.txt prices keep working.
RadZone has no public repository, so it has no fallback.

Nothing of the mods is shipped in Zedstead: the files go straight from the
author's repository to the admin's server, as the Expansion templates do
(`expansion_ops.download_templates`).

Every URL here is fixed. A file path from GitHub's tree listing must be a
bare name directly inside the expected folder before it is joined onto the
raw URL. Each response is capped in size.
"""

from __future__ import annotations

import json
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable
from urllib.parse import quote

from app.shared.zip_utils import save_capped


@dataclass(frozen=True)
class Repo:
    """One public GitHub repository and the branch Zedstead reads."""

    name: str  # "owner/repo"
    branch: str = "main"

    @property
    def url(self) -> str:
        """The repository page, shown to the admin as where the files came from."""
        return f"https://github.com/{self.name}"

    @property
    def tree_url(self) -> str:
        """GitHub's list of every file on the branch."""
        return f"https://api.github.com/repos/{self.name}/git/trees/{self.branch}?recursive=1"

    @property
    def raw_base(self) -> str:
        """The prefix of a file's raw download URL."""
        return f"https://raw.githubusercontent.com/{self.name}/{self.branch}/"

    def raw_url(self, path: str) -> str:
        """The raw download URL of *path* in the repository."""
        return self.raw_base + quote(path)


TRADERPLUS = Repo("TheDmitri/TraderPlusV1")
TOXICZONE = Repo("TheDmitri/ToxicZone")

#: Shown to the admin as where the TraderPlus defaults came from.
UPSTREAM_URL = TRADERPLUS.url
#: The repo's copy of the mod's former ``ServerProfile/TraderPlus/TraderPlusConfig``.
CONFIG_FOLDER = "serverProfileFiles/TraderPlus/TraderPlusConfig"
TYPES_FILE_NAME = "traderplus_types.xml"

_TIMEOUT = 30
_MB = 1024 * 1024
_TREE_MAX_BYTES = 20 * _MB
_FILE_MAX_BYTES = 10 * _MB
_MAX_CONFIG_FILES = 50
_BARE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_. -]{0,127}$")


class UpstreamError(RuntimeError):
    """The author's repository could not be read, or lacks the expected files."""


def _get(url: str, dest: Path, max_bytes: int) -> None:
    """Download *url* into *dest*, within *max_bytes*. Raises `UpstreamError`."""
    import requests

    try:
        with requests.get(url, timeout=_TIMEOUT, stream=True) as resp:
            resp.raise_for_status()
            save_capped(resp.iter_content(chunk_size=64 * 1024), dest, max_bytes, source=url)
    except (requests.RequestException, ValueError, OSError) as e:
        raise UpstreamError(f"Could not download {url}: {e}") from e


def _tree_paths(repo: Repo, tmp: Path) -> list[str]:
    """Every file path on *repo*'s branch, from GitHub's tree listing."""
    listing = tmp / "tree.json"
    _get(repo.tree_url, listing, _TREE_MAX_BYTES)
    try:
        tree = json.loads(listing.read_text(encoding="utf-8"))
        return [e["path"] for e in tree["tree"] if e.get("type") == "blob"]
    except (ValueError, KeyError, TypeError) as e:
        raise UpstreamError(f"GitHub's file list for {repo.url} is not readable: {e}") from e


def _bare_name_in(path: str, folder: str) -> str | None:
    """The file name of *path* when it sits directly in *folder* and is a safe name."""
    p = PurePosixPath(path)
    if str(p.parent) != folder or not _BARE_NAME.match(p.name) or p.name in (".", ".."):
        return None
    return p.name


# ---------------------------------------------------------------------------
# TraderPlus: the defaults behind the reset buttons and the install offer
# ---------------------------------------------------------------------------


def fetch_config_files(dest: Path) -> list[Path]:
    """Download the repo's default ``TraderPlusConfig/*.json`` files into *dest*.

    Only ``.json`` files: the folder also holds the author's
    ``TraderPlusPriceConfig.json_stock``, which TraderPlus never reads.
    Returns the written files, sorted. Raises `UpstreamError` when the
    repository cannot be read or the folder holds no JSON file.
    """
    with tempfile.TemporaryDirectory() as t:
        paths = _tree_paths(TRADERPLUS, Path(t))
    names = sorted(
        name for path in paths
        if (name := _bare_name_in(path, CONFIG_FOLDER)) and name.lower().endswith(".json")
    )
    if not names:
        raise UpstreamError(f"{TRADERPLUS.url} has no JSON file in {CONFIG_FOLDER}")
    if len(names) > _MAX_CONFIG_FILES:
        raise UpstreamError(f"{TRADERPLUS.url} lists {len(names)} files in {CONFIG_FOLDER}, too many")
    dest.mkdir(parents=True, exist_ok=True)
    written = []
    for name in names:
        target = dest / name
        _get(TRADERPLUS.raw_url(f"{CONFIG_FOLDER}/{name}"), target, _FILE_MAX_BYTES)
        written.append(target)
    return written


def fetch_types_xml() -> str:
    """The repo's ``TraderPlus_types.xml`` as text (the first match by name, in any folder).

    Raises `UpstreamError` when the repository cannot be read or has no such file.
    """
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        matches = sorted(
            p for p in _tree_paths(TRADERPLUS, tmp)
            if PurePosixPath(p).name.lower() == TYPES_FILE_NAME
            and all(_BARE_NAME.match(part) for part in PurePosixPath(p).parts)
        )
        if not matches:
            raise UpstreamError(f"{TRADERPLUS.url} has no TraderPlus_types.xml")
        target = tmp / "types.xml"
        _get(TRADERPLUS.raw_url(matches[0]), target, _FILE_MAX_BYTES)
        try:
            return target.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as e:
            raise UpstreamError(f"{matches[0]} in {TRADERPLUS.url} is not UTF-8 text") from e


# ---------------------------------------------------------------------------
# Files put back into a mod's server copy
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModFiles:
    """Files a mod ships today that Zedstead reads, and the repository that also holds them."""

    repo: Repo
    #: Paths in the repository, written at the same path inside the mod folder.
    files: tuple[str, ...]


def _mod_files() -> dict[str, ModFiles]:
    """``{workshop id: ModFiles}`` for every mod with a fallback."""
    from app.modmgr.recommended_mods import TOXICZONE as TOXICZONE_ID

    return {
        TOXICZONE_ID: ModFiles(TOXICZONE, (
            "Xmls & Trader & Tools/types.xml",  # the XML merge links it
            "Xmls & Trader & Tools/Trader.txt",  # zone_trader reads its prices
        )),
    }


def restore_missing_mod_files(
    mod_dir: Path, workshop_id: str, log: Callable[[str], None] | None = None,
) -> list[str]:
    """Download each fallback file *mod_dir* lacks; returns the paths written.

    A file counts as present when a file of that name (any case) is anywhere
    in the mod folder, since a mod moves files between folders. Runs after
    every copy of a mod into the server dir (`fileops.copy_mod_to_server`),
    so a mod that still ships the files is never touched. A failed download
    is logged and skipped: the copy itself has succeeded.
    """
    spec = _mod_files().get(workshop_id)
    if spec is None or not mod_dir.is_dir():
        return []
    present = {p.name.lower() for p in mod_dir.rglob("*") if p.is_file()}
    written: list[str] = []
    for path in spec.files:
        if PurePosixPath(path).name.lower() in present:
            continue
        target = mod_dir.joinpath(*PurePosixPath(path).parts)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            _get(spec.repo.raw_url(path), target, _FILE_MAX_BYTES)
        except (UpstreamError, OSError) as e:
            target.unlink(missing_ok=True)
            if log:
                log(f"  Warning: {mod_dir.name} ships no {path}, and the download from {spec.repo.url} failed: {e}")
            continue
        written.append(path)
        if log:
            log(f"  {mod_dir.name} ships no {path}, so Zedstead downloaded it from {spec.repo.url}")
    return written
