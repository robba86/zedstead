"""Steam Workshop Web API client for mod update detection.

Queries IPublishedFileService/GetDetails to check if installed mods
have newer versions available on the Workshop.

**GetDetails is a GET.** Until plans/50 B both queries POSTed, and Steam
answers a POST to this method with HTTP 405 whatever the key (measured
2026 Q3 with a valid key: POST 405, GET 200). Every caller swallowed the
error, so the update check, the Workshop dependency lookup and the title
lookup had never returned anything, and the Updates tab said "No updates
detected." A check that fails now says so: ``check_for_updates`` raises
``UpdateCheckError`` rather than returning an empty list that reads as
"up to date".
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from app.modmgr.mods import is_workshop_id

GETDETAILS_URL = (
    "https://api.steampowered.com/IPublishedFileService/GetDetails/v1/"
)
BATCH_SIZE = 100
# The older details call, which answers without an API key (a POST). Used
# only for the Maps page's download sizes, on an install that may have no key.
PUBLIC_DETAILS_URL = (
    "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/"
)

# UpdateCheckError.reason values.
NO_API_KEY = "no_api_key"
REJECTED = "rejected"
UNREACHABLE = "unreachable"


class UpdateCheckError(Exception):
    """An update check that did not happen, and why.

    ``reason`` is one of ``NO_API_KEY``, ``REJECTED``, ``UNREACHABLE``;
    ``message`` is shown to the user as is (CLI, toast, job log), so it never
    contains the key.
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass
class WorkshopModInfo:
    """Metadata for a single workshop mod from the Steam API."""

    mod_id: str
    title: str
    time_updated: int
    time_created: int = 0
    file_size: int = 0
    subscriptions: int = 0
    tags: list[str] | None = None


@dataclass
class ModUpdateInfo:
    """A mod that has an update available."""

    mod_id: str
    title: str
    remote_updated: int
    local_updated: int

    @property
    def age_seconds(self) -> int:
        return max(0, int(time.time()) - self.remote_updated)

    @property
    def age_display(self) -> str:
        s = self.age_seconds
        if s < 3600:
            return f"{s // 60}m ago"
        if s < 86400:
            return f"{s // 3600}h ago"
        return f"{s // 86400}d ago"


@dataclass
class UpdateCheck:
    """The result of one update check, or the last one on disk.

    ``checked`` counts the mods Steam answered for; ``total`` the mod IDs
    asked about. A mod deleted, hidden or made private on the Workshop answers
    ``result != 1`` and is in ``total`` only, so the difference is visible
    instead of the mod silently dropping out. ``checked_at`` is ``None`` when
    no check has been recorded (no cache file, or the pre-plans/50 list).
    """

    updates: list[ModUpdateInfo] = field(default_factory=list)
    checked: int = 0
    total: int = 0
    checked_at: int | None = None


def get_workshop_details(
    mod_ids: list[str],
    api_key: str,
    timeout: int = 30,
    *,
    strict: bool = False,
) -> dict[str, WorkshopModInfo]:
    """Query Steam API for workshop item details. Batches automatically.

    Returns ``{mod_id: WorkshopModInfo}`` for successfully queried mods. With
    ``strict=False`` (best-effort callers such as the title lookup) a failed
    request contributes nothing; with ``strict=True`` it raises
    ``UpdateCheckError``.
    """
    result: dict[str, WorkshopModInfo] = {}
    for batch_start in range(0, len(mod_ids), BATCH_SIZE):
        batch = mod_ids[batch_start : batch_start + BATCH_SIZE]
        batch_result = _query_batch(batch, api_key, timeout, strict=strict)
        result.update(batch_result)
    return result


def get_workshop_children(
    mod_ids: list[str],
    api_key: str,
    timeout: int = 30,
) -> dict[str, list[str]]:
    """Query Steam for each item's Workshop dependencies ("Required Items").

    Returns ``{mod_id: [dependency_id, ...]}``. Uses ``includechildren=1`` on
    GetDetails; for a non-collection item the ``children`` list is the soft
    web-declared dependency set. Collection items (``file_type == 2``) return an
    empty list — their children are members, not dependencies.
    """
    result: dict[str, list[str]] = {}
    for batch_start in range(0, len(mod_ids), BATCH_SIZE):
        batch = mod_ids[batch_start : batch_start + BATCH_SIZE]
        result.update(_query_children_batch(batch, api_key, timeout))
    return result


def check_for_updates(
    mod_ids: list[str],
    local_timestamps: dict[str, int],
    api_key: str,
) -> UpdateCheck:
    """Check which mods have updates available.

    Compares remote ``time_updated`` from Steam API against local timestamps
    (from ACF file or stored timestamps). Raises ``UpdateCheckError`` when the
    check cannot run (no key) or Steam does not answer, so that an empty
    ``updates`` always means "checked, and nothing is newer".
    """
    if not api_key:
        raise UpdateCheckError(
            NO_API_KEY,
            "Update checks need a Steam Web API key. Set steam_api_key in Settings "
            "(get one at https://steamcommunity.com/dev/apikey).",
        )
    if not mod_ids:
        return UpdateCheck(checked_at=int(time.time()))

    details = get_workshop_details(mod_ids, api_key, strict=True)
    outdated: list[ModUpdateInfo] = []

    for mod_id, info in details.items():
        local_ts = local_timestamps.get(mod_id, 0)
        if info.time_updated > local_ts:
            outdated.append(
                ModUpdateInfo(
                    mod_id=mod_id,
                    title=info.title,
                    remote_updated=info.time_updated,
                    local_updated=local_ts,
                )
            )

    return UpdateCheck(
        updates=outdated,
        checked=len(details),
        total=len(set(mod_ids)),
        checked_at=int(time.time()),
    )


# ---------------------------------------------------------------------------
# Timestamp persistence
# ---------------------------------------------------------------------------


def load_stored_timestamps(path: Path) -> dict[str, int]:
    """Load locally stored mod timestamps from JSON file."""
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {k: v.get("time_updated", 0) for k, v in data.items()}
    except (json.JSONDecodeError, AttributeError):
        return {}


def save_stored_timestamps(
    path: Path, timestamps: dict[str, int]
) -> None:
    """Save mod timestamps to JSON file."""
    data = {
        mod_id: {"time_updated": ts, "saved_at": int(time.time())}
        for mod_id, ts in timestamps.items()
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2) + "\n", encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Detected updates persistence
# ---------------------------------------------------------------------------


def load_update_check(path: Path) -> UpdateCheck:
    """The last recorded update check, or an empty one with ``checked_at=None``.

    Reads both ``{"checked_at", "checked", "total", "updates"}`` and the bare
    list written before plans/50. A bare list has no timestamp, so it loads
    as "not checked yet" with its updates kept.
    """
    if not path.is_file():
        return UpdateCheck()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data if isinstance(data, list) else data["updates"]
        updates = [
            ModUpdateInfo(
                mod_id=item["mod_id"],
                title=item["title"],
                remote_updated=item["remote_updated"],
                local_updated=item["local_updated"],
            )
            for item in items
        ]
        if isinstance(data, list):
            return UpdateCheck(updates=updates)
        return UpdateCheck(
            updates=updates,
            checked=int(data.get("checked", 0)),
            total=int(data.get("total", 0)),
            checked_at=data.get("checked_at"),
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return UpdateCheck()


def load_detected_updates(path: Path) -> list[ModUpdateInfo]:
    """Load previously detected mod updates from JSON file."""
    return load_update_check(path).updates


def save_detected_updates(path: Path, check: UpdateCheck) -> None:
    """Record an update check. Call it only for a check that succeeded:
    a failed one must leave the last good answer in place."""
    data = {
        "checked_at": check.checked_at,
        "checked": check.checked,
        "total": check.total,
        "updates": [
            {
                "mod_id": u.mod_id,
                "title": u.title,
                "remote_updated": u.remote_updated,
                "local_updated": u.local_updated,
                "detected_at": check.checked_at or int(time.time()),
            }
            for u in check.updates
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def remove_detected_updates(path: Path, mod_ids: set[str]) -> None:
    """Remove mods from the detected updates list in a single read/write."""
    check = load_update_check(path)
    check.updates = [u for u in check.updates if u.mod_id not in mod_ids]
    save_detected_updates(path, check)


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------


def _get_details(
    mod_ids: list[str], api_key: str, timeout: int, *, strict: bool, **extra: int
) -> dict:
    """One GetDetails request (a GET: a POST is answered 405), parsed.

    Non-strict: any failure is ``{}``. Strict: a 401/403 is ``REJECTED`` and
    names the setting; anything else is ``UNREACHABLE``. The messages never
    include the URL, which carries the key.
    """
    params: dict[str, str | int] = {"key": api_key, **extra}
    for i, mod_id in enumerate(mod_ids):
        params[f"publishedfileids[{i}]"] = mod_id
    req = urllib.request.Request(
        f"{GETDETAILS_URL}?{urllib.parse.urlencode(params)}",
        headers={"User-Agent": "zsm"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        if not strict:
            return {}
        if exc.code in (401, 403):
            raise UpdateCheckError(
                REJECTED,
                f"Steam rejected the Web API key (HTTP {exc.code}). "
                "Check steam_api_key in Settings.",
            ) from None
        raise UpdateCheckError(UNREACHABLE, f"The Steam Web API answered HTTP {exc.code}.") from None
    except ValueError:
        if not strict:
            return {}
        raise UpdateCheckError(UNREACHABLE, "The Steam Web API's answer was not valid JSON.") from None
    except Exception as exc:  # URLError, timeouts, resets
        if not strict:
            return {}
        reason = getattr(exc, "reason", None) or exc
        raise UpdateCheckError(UNREACHABLE, f"Could not reach the Steam Web API ({reason}).") from None


def _query_batch(
    mod_ids: list[str], api_key: str, timeout: int, *, strict: bool = False
) -> dict[str, WorkshopModInfo]:
    """Query a single batch of mod IDs."""
    data = _get_details(mod_ids, api_key, timeout, strict=strict)

    result: dict[str, WorkshopModInfo] = {}
    for item in data.get("response", {}).get("publishedfiledetails", []):
        if item.get("result") != 1:
            continue
        mod_id = item.get("publishedfileid", "")
        # An ID from the answer goes to SteamCMD and onto the workshop dir,
        # so it is held to the same rule as one from a file (plans/68 CON-12).
        if not is_workshop_id(mod_id):
            continue
        tags = [t["tag"] for t in item.get("tags", []) if "tag" in t]
        result[mod_id] = WorkshopModInfo(
            mod_id=mod_id,
            title=item.get("title", f"Mod {mod_id}"),
            time_updated=item.get("time_updated", 0),
            time_created=item.get("time_created", 0),
            file_size=int(item.get("file_size", 0)),
            subscriptions=item.get("subscriptions", 0),
            tags=tags or None,
        )
    return result


def _public_details(ids: list[str], timeout: int) -> list[dict]:
    """The ``publishedfiledetails`` items Steam answers for *ids* without an
    API key (``PUBLIC_DETAILS_URL``, a POST), only those with ``result == 1``
    and an ID that was asked for. Best effort: any failure or other shape is []."""
    if not ids:
        return []
    form: dict[str, str | int] = {"itemcount": len(ids)}
    for i, mod_id in enumerate(ids):
        form[f"publishedfileids[{i}]"] = mod_id
    req = urllib.request.Request(
        PUBLIC_DETAILS_URL,
        data=urllib.parse.urlencode(form).encode(),
        headers={"User-Agent": "zsm"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
    except Exception:  # HTTP errors, bad JSON, URLError, timeouts, resets
        return []
    # Any other shape is a failure too, never an exception: the Maps page
    # route calls this on a viewer's page view.
    response = data.get("response") if isinstance(data, dict) else None
    items = response.get("publishedfiledetails") if isinstance(response, dict) else None
    if not isinstance(items, list):
        return []
    return [
        item for item in items
        if isinstance(item, dict) and item.get("result") == 1 and item.get("publishedfileid") in ids
    ]


def get_public_file_sizes(mod_ids: list[str], timeout: int = 15) -> dict[str, int]:
    """``{mod_id: bytes}`` for Workshop items, asked without an API key.

    Best effort: any failure is ``{}``, and an item Steam does not answer for
    (removed, private) is left out, so a caller shows no size rather than a
    wrong one. Only IDs that are Workshop IDs are sent.
    """
    ids = [mod_id for mod_id in mod_ids if is_workshop_id(mod_id)]
    sizes: dict[str, int] = {}
    for item in _public_details(ids, timeout):
        try:
            size = int(item.get("file_size", 0))
        except (TypeError, ValueError, OverflowError):
            continue
        if size >= 0:
            sizes[item["publishedfileid"]] = size
    return sizes


def get_public_description(mod_id: str, timeout: int = 15) -> str | None:
    """A Workshop item's description (BBCode text as the author wrote it),
    asked without an API key, or None on any failure. Third-party text: a
    caller treats it as untrusted input."""
    if not is_workshop_id(mod_id):
        return None
    for item in _public_details([mod_id], timeout):
        text = item.get("description")
        return text if isinstance(text, str) else None
    return None


# EWorkshopFileType.Collection — its children are members, not dependencies.
_COLLECTION_FILE_TYPE = 2


def _query_children_batch(
    mod_ids: list[str], api_key: str, timeout: int
) -> dict[str, list[str]]:
    """Query one batch of IDs for their dependency children."""
    data = _get_details(mod_ids, api_key, timeout, strict=False, includechildren=1)

    result: dict[str, list[str]] = {}
    for item in data.get("response", {}).get("publishedfiledetails", []):
        if item.get("result") != 1:
            continue
        mod_id = item.get("publishedfileid", "")
        if not is_workshop_id(mod_id):
            continue
        if int(item.get("file_type", 0)) == _COLLECTION_FILE_TYPE:
            result[mod_id] = []
            continue
        result[mod_id] = [
            c["publishedfileid"]
            for c in item.get("children", []) or []
            if is_workshop_id(c.get("publishedfileid"))
        ]
    return result
