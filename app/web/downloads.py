"""One place for "hand the browser a file off disk".

Before this there was no ``FileResponse`` for *user* files at all — only
``routers/maps.py`` (a map image) and the SPA index — so backups were
reachable over SSH/SCP and nothing else (TODO C2). G1c (Download/Upload per
file in the editors) wants the identical three things, which is why the
headers are decided here rather than per route:

- ``Content-Disposition: attachment`` with a filename the browser can save,
- ``Cache-Control: no-store`` — these files change under the same name
  (``types.xml`` is edited in place; a bundle is re-exported), and a cached
  copy of yesterday's is worse than a slow download,
- a 404 rather than a 500 when the path went stale between the listing and
  the click.

**The path must come from a listing, never from the query string.** Every
caller resolves the user's name against the real set of files first
(``_find_backup``, ``list_available_bundles``), the same discipline
``/api/cleanup/migrate-players`` uses — so ``name=../../config.json`` is a
404, not a leak. This module deliberately offers no "join a name onto a
directory" helper, because that is the call that would get it wrong.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse


def file_attachment(path: Path, filename: str | None = None) -> FileResponse:
    """Serve *path* as a download named *filename* (default: its own name)."""
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {path.name}")
    return FileResponse(
        str(path),
        media_type="application/octet-stream",
        filename=filename or path.name,
        headers={"Cache-Control": "no-store"},
    )
