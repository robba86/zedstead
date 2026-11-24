"""Update check — silently returns the newer zsm version if one exists.

The answer comes from the latest GitHub release of the repository zsm is
installed from (``install.sh`` clones it), not from PyPI: zsm is not
published there, so whoever held the name would decide what every panel
advertises. No release yet (or a private repository) answers 404, which is
"no update".

The cache path is resolved at call time from ``app.shared.paths.CONFIG_DIR``
so test monkeypatching of the config dir propagates.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Optional

RELEASES_URL = "https://api.github.com/repos/robba86/zedstead/releases/latest"
CACHE_TTL_SECONDS = 24 * 3600  # 24 hours

# A release tag is shown in the panel's banner, so only a plain version is
# taken from it ("v1.2.3" or "1.2.3"); anything else reads as no update.
_TAG_RE = re.compile(r"v?(\d+(?:\.\d+){0,3})")


def _cache_file() -> Path:
    from app.shared.paths import CONFIG_DIR

    return CONFIG_DIR / "version-check.json"


def _parse_version(v: str) -> tuple[int, ...]:
    """Parse "1.2.3" -> (1, 2, 3). Returns (0,) on parse error."""
    try:
        return tuple(int(x) for x in v.strip().split("."))
    except (ValueError, AttributeError):
        return (0,)


def _read_cache() -> Optional[dict]:
    """Return cached JSON dict if fresh and from the current source, else None."""
    try:
        cache = _cache_file()
        if not cache.exists():
            return None
        data = json.loads(cache.read_text(encoding="utf-8"))
        if data.get("source") != RELEASES_URL:
            return None
        if time.time() - data.get("ts", 0) < CACHE_TTL_SECONDS:
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return None


def _write_cache(latest: str) -> None:
    try:
        cache = _cache_file()
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(
            json.dumps({"latest": latest, "ts": time.time(), "source": RELEASES_URL}),
            encoding="utf-8",
        )
    except OSError:
        pass


def _version_from_tag(tag: object) -> str:
    """``"v1.2.3"`` -> ``"1.2.3"``; ``""`` for anything that is not a plain version."""
    if not isinstance(tag, str):
        return ""
    m = _TAG_RE.fullmatch(tag.strip())
    return m.group(1) if m else ""


def check_for_update(current: str, config=None) -> Optional[str]:
    """Return the latest release's version if newer than *current*, else None.

    Reads from a 24-hour cache file to avoid hammering the GitHub API.
    Silent on any network or parse failure. With *config*'s
    ``version_check_enabled`` off it returns None and makes no request.
    """
    if config is not None and not getattr(config, "version_check_enabled", True):
        return None
    cached = _read_cache()
    if cached:
        latest = cached.get("latest", "")
    else:
        try:
            import requests  # noqa: PLC0415
            resp = requests.get(
                RELEASES_URL,
                timeout=3,
                headers={"Accept": "application/vnd.github+json"},
            )
            if resp.status_code == 404:
                latest = ""
            else:
                resp.raise_for_status()
                latest = _version_from_tag(resp.json().get("tag_name"))
            _write_cache(latest)
        except (OSError, KeyError, ValueError, AttributeError):
            return None

    if not latest:
        return None

    if _parse_version(latest) > _parse_version(current):
        return latest
    return None
