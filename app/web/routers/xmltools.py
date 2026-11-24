"""POST /api/xml — check and re-indent a string of XML.

Backs the Format / Check buttons on every raw XML editor in the web UI (the
manual-link content box, the `/configs/:id` Raw tab, the generated `custom/`
file Raw tab). Content in, content out: neither endpoint takes a path, a
filename or an id, so there is nothing here to traverse.

**Malformed input is a 200 with `ok: false`, never a 400.** Bad XML is the
expected case for these two — that is what the buttons are for — and a 400
goes through the frontend's throw path, which every call site would then have
to translate back into an inline message.

All the actual work is `app/xml/xml_text.py`; this is the transport.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Body, Depends

from app.web.deps import require_session

router = APIRouter(prefix="/api/xml", tags=["xml"], dependencies=[Depends(require_session)])

# A modded db/types.xml runs to tens of megabytes. Formatting one in a browser
# textarea is not a workflow worth blocking an event loop for.
MAX_XML_BYTES = 5_000_000

_TOO_BIG = {
    "line": 0,
    "column": 0,
    "message": "content too large",
    "hint": "This file is too large to format in the browser.",
    "summary": "This file is too large to format in the browser.",
}


def _too_big(content: str) -> bool:
    return len(content.encode("utf-8", "ignore")) > MAX_XML_BYTES


@router.post("/check")
async def check_xml(body: dict = Body(...)) -> dict:
    """Report whether *content* is well-formed, and where it isn't.

    ``fragment`` accepts a rootless fragment (several sibling elements) — the
    manual-link box passes it, because that is exactly what a link may store.
    It is looser than a document parse, which is why the UI says "well-formed"
    rather than "valid".
    """
    from app.xml.xml_text import inspect_xml_text

    content = str(body.get("content", ""))
    fragment = bool(body.get("fragment", False))
    if _too_big(content):
        return {"ok": False, "fragment": False, "root_tag": "", "elements": 0, "error": _TOO_BIG}

    info = await asyncio.to_thread(inspect_xml_text, content, fragment=fragment)
    return {
        "ok": info.ok,
        "fragment": info.fragment,
        "root_tag": info.root_tag,
        "elements": info.elements,
        "error": info.problem.as_dict() if info.problem else None,
    }


@router.post("/format")
async def format_xml(body: dict = Body(...)) -> dict:
    """Re-indent *content* with tabs, keeping its comments and declaration.

    On failure ``content`` comes back byte-identical to what was sent, so a
    caller can assign the response without checking ``ok`` first.
    """
    from app.xml.xml_text import format_xml_text

    content = str(body.get("content", ""))
    if _too_big(content):
        return {
            "ok": False, "content": content, "changed": False,
            "root_tag": "", "elements": 0, "error": _TOO_BIG,
        }

    result = await asyncio.to_thread(format_xml_text, content)
    return {
        "ok": result.ok,
        "content": result.text,
        "changed": result.changed,
        "root_tag": result.root_tag,
        "elements": result.elements,
        "error": result.problem.as_dict() if result.problem else None,
    }
