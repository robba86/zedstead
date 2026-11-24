"""Credential values the panel serves to admins only.

Roles made `/api/configs/{id}/raw` and `/download` admin-only because
serverDZ.cfg and beserver_x64.cfg hold passwords (`permissions.NON_VIEWER_READS`).
The same values also reach a browser two other ways, both viewer-level, and
the plans/43 browser pass (2026 Q3) found them: the **structured** editor
(`GET /api/configs/battleye` handed a viewer the live RCon password, on the page
that warns "anyone holding this password can run admin commands") and the
**versions diff**, whose context lines print whatever sits beside a change.

Both routes stay readable — §2 of plans/43 gives every role the configs — and
redact the values instead. `SECRET_KEYS` is the one list: a file-native key
name, the same spelling in the editor field and in the file line.

The change report (`/api/changes`, `/api/changes/baseline`) is the third way:
it tracks serverDZ.cfg's `password` and `passwordAdmin` and prints the old and
new value of each. `redact_changes` keeps the line, so a viewer still sees
that a password changed, and hides both values.
"""

from __future__ import annotations

import copy
import re

from app.web.users import role_at_least

__all__ = ["HIDDEN", "SECRET_KEYS", "is_admin", "mark_secrets", "redact_changes", "redact_diff", "redact_form"]

# serverDZ.cfg's join and admin passwords, and BattlEye's RCon password — the
# RCon one is worth more than a moderator: kick, ban and #shutdown with no role
# check at all. A new credential field must be added here; the test that walks
# every editor's field names fails until it is.
SECRET_KEYS = frozenset({"password", "passwordAdmin", "RConPassword"})

HIDDEN = "<hidden: admin only>"

# `password = "x";` (serverDZ.cfg) and `RConPassword x` (beserver_x64.cfg), with
# the leading +/-/space a unified diff puts on every line.
_LINE_RE = re.compile(
    r"^([+\- ]?\s*(?:%s)\b\s*=?\s*)(\S.*)$" % "|".join(sorted(SECRET_KEYS, key=len, reverse=True)),
    re.IGNORECASE,
)

# `  password: old → new`, the change report's serverDZ.cfg line
# (`changeset_ops._diff_snapshots`). The key is matched whole, so `password`
# never matches the start of `passwordAdmin`.
_CHANGE_RE = re.compile(r"^(\s*(?:%s):)(?=\s)" % "|".join(sorted(SECRET_KEYS, key=len, reverse=True)))


def is_admin(request) -> bool:
    """Whether this request may see the values. No role on the request — a
    route reached some way `require_session` did not stamp — reads as no."""
    return role_at_least(getattr(request.state, "role", "") or "", "admin")


def mark_secrets(body: dict) -> dict:
    """Set ``secret: True`` on every form field whose key is in SECRET_KEYS,
    so the form draws it as a password box with a show button. The one list
    decides both this and what a non-admin never sees. Mutates and returns
    *body*, a payload loaded for this request."""
    if body.get("kind") == "form":
        for section in body.get("sections", []):
            for field in section.get("fields", []):
                if field.get("key") in SECRET_KEYS:
                    field["secret"] = True
    return body


def redact_form(body: dict) -> dict:
    """A copy of a form editor's payload with every secret field's value blanked.

    The field stays, with ``redacted: True``, so the page can say why it is
    empty rather than show a password that looks unset. A table's rows carry
    no secret field, but each row's ``nested`` tree goes through the same walk
    as a form's (plans/76 step 2b).
    """
    if body.get("kind") == "table":
        if not any(row.get("nested") for row in body.get("rows", [])):
            return body
        out = copy.deepcopy(body)
        for row in out.get("rows", []):
            _redact_tree(row.get("nested"))
        return out
    if body.get("kind") != "form":
        return body
    out = copy.deepcopy(body)
    for section in out.get("sections", []):
        for field in section.get("fields", []):
            if field.get("key") in SECRET_KEYS:
                field["value"] = ""
                field["redacted"] = True
    _redact_tree(out.get("nested"))
    return out


def _redact_tree(node) -> None:
    """Blank a secret key anywhere in a ``nested`` tree (plans/76)."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key in SECRET_KEYS and not isinstance(value, (dict, list)):
                node[key] = HIDDEN
            else:
                _redact_tree(value)
    elif isinstance(node, list):
        for item in node:
            _redact_tree(item)


def redact_changes(report: dict) -> dict:
    """A copy of a serialized change report with every secret cfg value hidden.

    The line stays, as ``password: <hidden: admin only>``: that the join
    password changed since the last start is worth a viewer knowing.
    """
    out = copy.deepcopy(report)
    for change in out.get("changes", []):
        if change.get("category") != "cfg":
            continue
        m = _CHANGE_RE.match(change.get("description", ""))
        if m:
            change["description"] = f"{m.group(1)} {HIDDEN}"
    return out


def redact_diff(text: str) -> str:
    """*text* with the value on every line that sets a secret key replaced."""
    return "\n".join(
        m.group(1) + HIDDEN if (m := _LINE_RE.match(line)) else line
        for line in text.split("\n")
    )
