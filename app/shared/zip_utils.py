"""Path containment helpers — zip members, names that become a path part, and
copies of a tree someone else wrote — the owner-only zip writer for archives
that carry credentials, and size limits for an archive fetched from a URL."""

from __future__ import annotations

import os
import shutil
import zipfile
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from app.shared.platform import IS_WINDOWS


@contextmanager
def private_zip(path: Path, **kwargs) -> Iterator[zipfile.ZipFile]:
    """``zipfile.ZipFile(path, "w", **kwargs)``, readable by its owner only.

    A bundle and a full backup both carry ``config.json`` (the RCon password,
    the Discord webhook URL, the Steam API key, the panel's password hash, the
    zedstead.com listing key), which zsm otherwise keeps at 0600. A plain
    ``ZipFile(path, "w")`` creates the file with the process umask, 0644 under
    the usual 022, so the same values were readable by every local user who
    could reach the folder.

    0600 at open, like ``users.json`` and ``rclone.conf``, so there is no
    window. ``fchmod`` as well, because ``O_CREAT`` leaves an existing file's
    mode as it was; best effort, since a backup folder on a filesystem with no
    modes (vfat, exFAT) refuses it, and that must not cost the backup. On
    Windows the mode only sets the read-only bit and the file takes the
    folder's ACL, as every other zsm file does there.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags, 0o600)
    try:
        if not IS_WINDOWS:
            try:
                os.fchmod(fd, 0o600)
            except OSError:
                pass
        fh = os.fdopen(fd, "wb")
    except BaseException:
        os.close(fd)
        raise
    with fh, zipfile.ZipFile(fh, "w", **kwargs) as zf:
        yield zf


def is_bare_name(name: str) -> bool:
    """True when *name* is one path component naming a child of a directory.

    A mission folder, a mod folder or a workshop item is joined onto a root as
    a single part, so ``root / name`` must stay a child of ``root``. Refused:
    empty, ``.`` and ``..``, any ``/`` or ``\\`` (a Windows separator, refused
    on every platform so a value can't mean one thing on Linux and another on
    Windows), a NUL byte, and anything the platform's own path parser would
    not keep as a single name (``C:x`` on Windows).
    """
    return (
        bool(name)
        and name not in (".", "..")
        and not any(c in name for c in ("/", "\\", "\0"))
        and Path(name).name == name
    )


def safe_extract_path(dest_dir: Path, member_name: str) -> Path:
    """Resolve a zip member to a path guaranteed to sit under ``dest_dir``.

    Raises ``ValueError`` when the member would escape ``dest_dir`` — e.g. an
    entry like ``../../etc/cron.d/x`` or an absolute path. ``ZipFile.extract``
    sanitizes such names, but manual extraction (``zf.open`` + ``open(target)``)
    bypasses that, so any code joining a member name to a destination must
    validate containment itself.
    """
    base = dest_dir.resolve()
    target = (base / member_name).resolve()
    if target != base and base not in target.parents:
        raise ValueError(
            f"unsafe zip entry escapes destination: {member_name!r}"
        )
    return target


def copytree_without_links(src: Path, dst: Path, *, dirs_exist_ok: bool = False) -> int:
    """`shutil.copytree` that leaves out every symlink in *src*; returns how many.

    For a tree someone else wrote (a workshop item). copytree's default copies
    what a link points at, so a link would bring a file from anywhere on this
    machine into the copy, and a link to a parent directory would make it
    recurse. A mod's files never need to be links.
    """
    skipped = 0

    def _links(directory: str, names: list[str]) -> set[str]:
        nonlocal skipped
        links = {n for n in names if os.path.islink(os.path.join(directory, n))}
        skipped += len(links)
        return links

    shutil.copytree(src, dst, ignore=_links, dirs_exist_ok=dirs_exist_ok)
    return skipped


# ---------------------------------------------------------------------------
# Size limits for an archive fetched from a URL (plans/68 NEW-7)
#
# A downloaded archive is a third party's file: a map's mission repository on
# GitHub follows its branch head, so what arrives is whatever is there today.
# Without a ceiling a repository that changed (or was taken over) could fill
# the temp dir and then the disk mpmissions/ is on, which is the server's.
# ---------------------------------------------------------------------------


class ArchiveTooLarge(ValueError):
    """An archive, or what would be unpacked from it, is past a limit."""


@dataclass(frozen=True)
class ArchiveLimits:
    """Ceilings for an archive that is downloaded and then partly unpacked."""

    download_bytes: int  # the archive itself, counted as it streams in
    total_bytes: int  # everything unpacked, uncompressed
    member_bytes: int  # any one unpacked file, uncompressed
    members: int  # how many entries are unpacked


def _mb(n: int) -> str:
    return f"{n // (1024 * 1024)} MB"


def save_capped(chunks: Iterable[bytes], dest: Path, max_bytes: int, *, source: str) -> int:
    """Write *chunks* to *dest*, refusing past *max_bytes*; returns the size.

    Counted as the bytes arrive, never from a Content-Length the sender
    chooses (and GitHub's generated archives send none). Raises
    `ArchiveTooLarge` before a chunk that would pass the limit is written, so
    *dest* never holds more than *max_bytes*.
    """
    total = 0
    with open(dest, "wb") as fh:
        for chunk in chunks:
            total += len(chunk)
            if total > max_bytes:
                raise ArchiveTooLarge(
                    f"{source} is larger than {_mb(max_bytes)}, so Zedstead stopped downloading it."
                )
            fh.write(chunk)
    return total


def check_unpack_size(
    infos: Sequence[zipfile.ZipInfo], limits: ArchiveLimits, *, source: str,
) -> None:
    """Refuse, before anything is written, to unpack more than *limits* allow.

    Reads each member's uncompressed size from the archive's directory. That
    is the archive's own claim, and it holds: `zipfile` stops reading a member
    at its stated size and then fails its CRC check, so a member that
    understates its size cannot write more than it said.
    """
    if len(infos) > limits.members:
        raise ArchiveTooLarge(
            f"{source} would unpack {len(infos)} entries, more than the {limits.members} Zedstead allows."
        )
    for info in infos:
        if info.file_size > limits.member_bytes:
            raise ArchiveTooLarge(
                f"{source} holds {info.filename!r}, larger than {_mb(limits.member_bytes)} "
                "unpacked; nothing was unpacked."
            )
    total = sum(info.file_size for info in infos)
    if total > limits.total_bytes:
        raise ArchiveTooLarge(
            f"{source} would unpack to more than {_mb(limits.total_bytes)}; nothing was unpacked."
        )
