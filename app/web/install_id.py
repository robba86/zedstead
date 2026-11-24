"""The panel's install id: a random value made once per config dir.

The browser caches finished jobs in localStorage, which belongs to the browser
and not to the server. After a reinstall, or with a second server reached on
the same host and port, the Activity drawer showed jobs that never ran there.
`/api/auth/me` serves this id, and `webui/src/jobs.jsx` drops a cache made
under another one.
"""

from __future__ import annotations

import secrets

from app.shared import paths
from app.shared.json_utils import atomic_write_text


def install_id() -> str:
    """The id from `paths.install_id_file()`, made on first use. A file that
    cannot be read or written gives an empty id, which the browser treats as
    one more id: the cache is kept only while the id stays the same."""
    path = paths.install_id_file()
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        value = ""
    if value:
        return value
    value = secrets.token_hex(8)
    try:
        atomic_write_text(path, value + "\n")
    except OSError:
        return ""
    return value
