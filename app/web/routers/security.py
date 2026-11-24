"""GET /api/security, GET /api/security/access-log — TODO B6.

Two halves of the same question: *can anyone else reach this panel, and has
anyone tried?*

The panel is one password and no accounts, so the answer to the first is not
in the config alone — it is in how `zsm web` was started. `WebState.bind_host`
and `tls_enabled` are set by `start_server`, which is the only place that
knows both.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends

from app.web import access_log
from app.web.deps import get_panel_state, require_session
from app.web.state import WebState

router = APIRouter(prefix="/api/security", tags=["security"], dependencies=[Depends(require_session)])

# Addresses that reach no further than this machine. Everything else — a LAN
# address, a public one, and `0.0.0.0`/`::` which mean *every* interface —
# puts the panel on a network somebody else is on.
LOOPBACK_HOSTS = frozenset({"", "127.0.0.1", "::1", "localhost", "loopback"})


def exposure(state: WebState) -> dict:
    """``{exposed, bind_host, tls, reason}``.

    "Exposed" means **off-box and unencrypted**, which is the state worth a
    banner: the login password crosses the network in the clear and so does
    the session cookie. Either fix removes it — bind loopback and reach the
    panel over an SSH tunnel, or give it a certificate.

    TLS alone clears it even on `0.0.0.0`: at that point the panel is
    deliberately published and protected, which is a choice rather than an
    accident. A reverse proxy that terminates TLS in front of a loopback bind
    also clears it, because the bind is loopback.

    ``bind_host`` empty means the app was not started by ``start_server`` (a
    test, an embedding caller). Nothing is claimed in that case — a security
    warning raised on a default rather than on evidence trains people to
    ignore it.
    """
    host = (state.bind_host or "").strip().lower()
    off_box = bool(host) and host not in LOOPBACK_HOSTS
    exposed = off_box and not state.tls_enabled
    if not host:
        reason = ""
    elif not off_box:
        reason = f"Listening on {state.bind_host} — reachable only from this machine."
    elif state.tls_enabled:
        reason = f"Listening on {state.bind_host} over HTTPS."
    else:
        reason = (
            f"Listening on {state.bind_host} with no TLS. Anything that can reach this "
            "machine on the network can reach the app, and the password and session "
            "cookie cross it in the clear."
        )
    return {
        "exposed": exposed,
        "bind_host": state.bind_host,
        "tls": state.tls_enabled,
        "reason": reason,
    }


@router.get("")
async def get_security(state: WebState = Depends(get_panel_state)) -> dict:
    return exposure(state)


@router.get("/access-log")
async def get_access_log(limit: int = 100, state: WebState = Depends(get_panel_state)) -> dict:
    """Recent logins, failed logins and logouts, newest first."""
    limit = max(1, min(limit, access_log.MAX_ENTRIES))
    entries = await asyncio.to_thread(access_log.read_recent, limit)
    return {"entries": entries, "path": str(access_log.access_log_path())}
