"""Reading an uploaded file from a request body, with a size cap.

An upload is the request body itself, not a multipart form: one file going to a
destination the route already knows needs no form fields, multipart would mean
adding ``python-multipart`` to every install, and no client-supplied filename
ever comes near a path (TODO G1c, ``app/web/CLAUDE.md``).
"""

from __future__ import annotations

from fastapi import HTTPException, Request

# A config file is a few MB at the very worst (a heavily modded types.xml is
# 1-3). This bounds what one POST to `/api/configs/{id}/upload` can make the
# panel hold before anything has validated it. `body_limit` reads it too, so
# no body of any type gets past it on that route.
MAX_UPLOAD_BYTES = 16 * 1024 * 1024


async def read_bounded(request: Request, limit: int) -> bytes:
    """Read the request body, refusing anything past *limit* bytes with a 413.

    Streamed rather than `await request.body()`: that buffers whatever was
    sent before anyone can object, so a multi-GB POST would be the panel's
    problem rather than the sender's.
    """
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise HTTPException(
                status_code=413,
                detail=f"That file is larger than {limit // (1024 * 1024)} MB.",
            )
        chunks.append(chunk)
    return b"".join(chunks)
